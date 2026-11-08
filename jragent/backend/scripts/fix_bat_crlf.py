"""
把 .bat 文件统一成「GBK 编码 + CRLF 换行（无 BOM）」。

两个必须处理的坑（都会导致「双击直接闪退」或「提示变成乱码命令」）：

1. **换行必须是 CRLF**
   cmd 解析批处理用 LF-only 时，遇到 `goto :label`、`if ... (` 括号块
   会解析错乱，直接静默退出，什么都不显示。

2. **中文必须用 GBK 编码，不要 chcp 65001**
   `chcp 65001` + UTF-8 的 bat 有一个经典 bug：
   cmd 按字节偏移切行，含中文的长行会被从中间切断，
   后半截被当成命令执行，报「'xxx' 不是内部或外部命令」。
   中文 Windows 默认代码页就是 936，直接用 GBK 反而最稳，
   中文能正常显示，也不会有解析错位。

用法：
    python scripts/fix_bat_crlf.py              # 修项目根目录所有 .bat
    python scripts/fix_bat_crlf.py a.bat b.bat  # 修指定文件
"""
import glob
import os
import sys


def decode(raw: bytes) -> tuple:
    """按 utf-8 → gbk 的顺序解，返回 (文本, 是否带 BOM)"""
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    for enc in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(enc), had_bom, enc
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), had_bom, "utf-8?"


def fix(path: str) -> tuple:
    with open(path, "rb") as f:
        raw = f.read()

    text, had_bom, enc = decode(raw)

    # UTF-8 代码页会让中文行被切断，换成 936（中文 Windows 默认值）
    text = text.replace("chcp 65001", "chcp 936")

    # 统一换行
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\r\n")

    # 写回 GBK；实在编不了的字符替换掉并提示，不让文件写不出来
    lost = ""
    try:
        data = text.encode("gbk")
    except UnicodeEncodeError as e:
        bad = text[e.start:e.end]
        lost = bad
        data = text.encode("gbk", errors="replace")

    changed = data != raw or had_bom
    if changed:
        with open(path, "wb") as f:
            f.write(data)

    return changed, had_bom, len(data), data.count(b"\r\n"), enc, lost


def main() -> int:
    if len(sys.argv) > 1:
        targets = sys.argv[1:]
    else:
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        targets = sorted(glob.glob(os.path.join(root, "*.bat")))

    if not targets:
        print("没找到 .bat 文件")
        return 0

    bad = 0
    for p in targets:
        changed, had_bom, size, crlf, enc, lost = fix(p)
        name = os.path.basename(p)
        note = []
        if had_bom:
            note.append("去掉 BOM")
        if enc not in ("gbk",):
            note.append(f"从 {enc} 转 GBK")
        if lost:
            note.append(f"有字符 GBK 编不了，已替换：{lost!r}")
        tail = ("，" + "，".join(note)) if note else ""
        if changed:
            bad += 1
            print(f"已修正  {name}  ({size} 字节 / {crlf} 行 CRLF{tail})")
        else:
            print(f"本来就对 {name}")

    print()
    print(f"共 {len(targets)} 个文件，{bad} 个被修正。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
