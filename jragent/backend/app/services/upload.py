"""
对话附件解析 —— 让用户能把截图、CSV、Excel 直接丢进对话框。

设计原则：
1. **图片走视觉**。MiMo 支持 image_url 多模态输入，截图能真看懂。
2. **表格转文本**。Excel/CSV 不喂二进制，转成紧凑的表格文本，
   既省 token 又让模型能引用具体数字。
3. **不落盘**。附件在内存里处理完就丢，服务端不留用户文件。
4. **白名单 + 上限**。不做通用文件管理器，只接受量化场景用得到的几类。

安全边界：
- 只接受白名单扩展名，其它一律拒绝（不给 .exe/.html 之类留口子）
- 单文件 ≤ MAX_BYTES，超了直接报错，不静默截断
- 文本内容里的 HTML 不会被执行：它只作为模型输入，不写进页面
"""
from __future__ import annotations

import base64
import csv
import io
import os
from typing import Any, Dict, List, Optional, Tuple

MAX_BYTES = 8 * 1024 * 1024          # 单文件 8MB（前端会把图压到 1MB 内）
MAX_TEXT_CHARS = 12000               # 文本类最多给模型 1.2 万字
MAX_SHEET_ROWS = 60                  # Excel/CSV 最多读 60 行
MAX_SHEET_COLS = 12                  # 最多读 12 列

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
TEXT_EXTS = {".txt", ".md", ".csv", ".json", ".log", ".yaml", ".yml", ".py"}
SHEET_EXTS = {".xlsx", ".xlsm", ".xls"}

MIME = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
}


class UploadError(Exception):
    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


def _ext(name: str) -> str:
    return os.path.splitext(name or "")[1].lower()


def _sheet_to_text(data: bytes, name: str) -> Tuple[str, Dict[str, Any]]:
    """Excel → 表格文本。只读第一个 sheet 的前若干行，够模型理解结构就行。"""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows: List[List[str]] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i >= MAX_SHEET_ROWS:
            break
        cells = ["" if v is None else str(v) for v in row[:MAX_SHEET_COLS]]
        if any(c.strip() for c in cells):
            rows.append(cells)
    wb.close()

    if not rows:
        raise UploadError("这个表格里没有读到内容")

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["\t".join(r) for r in rows]
    meta = {"sheet": ws.title, "rows": len(rows), "cols": width}
    return "\n".join(lines), meta


def _csv_to_text(data: bytes) -> Tuple[str, Dict[str, Any]]:
    # 中文 CSV 常见 GBK/GB18030，先按 UTF-8 试，失败再退
    for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise UploadError("这个 CSV 编码认不出来，请另存为 UTF-8 再试")

    reader = csv.reader(io.StringIO(text))
    rows: List[List[str]] = []
    for i, row in enumerate(reader):
        if i >= MAX_SHEET_ROWS:
            break
        rows.append([c for c in row[:MAX_SHEET_COLS]])
    if not rows:
        raise UploadError("CSV 是空的")

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    return "\n".join("\t".join(r) for r in rows), {"rows": len(rows), "cols": width}


def parse(name: str, data: bytes, mime: str = "") -> Dict[str, Any]:
    """
    返回统一结构：
      {kind: "image"|"text", name, size, ...}
    图片：data_url（base64），带进模型做视觉输入，不回传前端存 localStorage
    文本：content（已截断），供拼进 prompt
    """
    if not data:
        raise UploadError("文件是空的")
    if len(data) > MAX_BYTES:
        raise UploadError(f"文件太大（{len(data) / 1024 / 1024:.1f}MB），最大支持 8MB")

    ext = _ext(name)
    base = {"name": name, "size": len(data), "ext": ext}

    # ---- 图片 ----
    if ext in IMAGE_EXTS:
        m = MIME.get(ext) or mime or "image/png"
        b64 = base64.b64encode(data).decode()
        return {
            **base,
            "kind": "image",
            "mime": m,
            "data_url": f"data:{m};base64,{b64}",
            "note": "图片已就绪，模型能直接看懂",
        }

    # ---- 表格 ----
    if ext in SHEET_EXTS:
        try:
            text, meta = _sheet_to_text(data, name)
        except UploadError:
            raise
        except Exception as e:
            raise UploadError(f"表格解析失败：{e}")
        return {
            **base, "kind": "text", "content": text[:MAX_TEXT_CHARS],
            "meta": meta,
            "note": f"读到 {meta['rows']} 行 × {meta['cols']} 列",
        }

    # ---- 纯文本 ----
    if ext in TEXT_EXTS:
        if ext == ".csv":
            text, meta = _csv_to_text(data)
            return {
                **base, "kind": "text", "content": text[:MAX_TEXT_CHARS],
                "meta": meta, "note": f"读到 {meta['rows']} 行 × {meta['cols']} 列",
            }
        for enc in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise UploadError("文本编码认不出来，请另存为 UTF-8")
        truncated = len(text) > MAX_TEXT_CHARS
        return {
            **base, "kind": "text", "content": text[:MAX_TEXT_CHARS],
            "note": (f"共 {len(text)} 字，已截取前 {MAX_TEXT_CHARS} 字"
                     if truncated else f"共 {len(text)} 字"),
        }

    raise UploadError(
        f"不支持 {ext or '这种'} 格式。可以传：图片（png/jpg/webp）、"
        f"表格（xlsx/csv）、文本（txt/md/json/py）"
    )


def build_user_content(text: str, attachments: Optional[List[Dict[str, Any]]]) -> Any:
    """
    把用户文字 + 附件拼成模型输入。

    - 有图片 → 返回 content 数组（OpenAI 兼容的多模态格式）
    - 只有文本附件 → 把附件正文拼进字符串
    - 都没有 → 原样返回字符串
    """
    atts = attachments or []
    if not atts:
        return text

    text_parts: List[str] = []
    image_parts: List[Dict[str, Any]] = []

    for a in atts:
        kind = a.get("kind")
        name = a.get("name") or "附件"
        if kind == "image" and a.get("data_url"):
            image_parts.append({
                "type": "image_url",
                "image_url": {"url": a["data_url"]},
            })
        elif kind == "text" and a.get("content"):
            text_parts.append(f"\n\n【附件：{name}】\n```\n{a['content']}\n```")

    if not image_parts:
        return text + "".join(text_parts)

    parts: List[Dict[str, Any]] = [{
        "type": "text",
        "text": text + "".join(text_parts),
    }]
    parts.extend(image_parts)
    return parts


def summary(attachments: Optional[List[Dict[str, Any]]]) -> str:
    """给数据库存一份可读的附件记录（不存 base64，否则库会被撑爆）"""
    atts = attachments or []
    if not atts:
        return ""
    bits = []
    for a in atts:
        tag = "图片" if a.get("kind") == "image" else "文件"
        bits.append(f"[{tag}：{a.get('name') or '附件'}]")
    return " ".join(bits)
