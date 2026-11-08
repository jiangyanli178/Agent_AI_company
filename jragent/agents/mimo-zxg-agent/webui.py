#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mimo-zxg-agent 本地 Web 控制台（与刷视频回复助手完全隔离）

只监听 127.0.0.1，启动后浏览器打开 http://127.0.0.1:8766
页面上可：登录/退出腾讯自选股、查看双 agent 研究/校验报告、发布终稿、配置每日定时。
"""
import datetime
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
WEBUI_PATH = ROOT / "webui.html"
REPORTS_DIR = ROOT / "reports"
PORT = 8766

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def run_bot(args: list, timeout: int = 600) -> dict:
    """调用 bot.py，解析其 JSON 输出。"""
    try:
        p = subprocess.run(
            [sys.executable, str(ROOT / "bot.py")] + args,
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, cwd=str(ROOT),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "执行超时"}
    stdout = (p.stdout or "").strip()
    data = None
    # 取 stdout 里最后一段 JSON
    for chunk in reversed(stdout.split("\n{")):
        candidate = chunk if chunk.startswith("{") else "{" + chunk
        try:
            data = json.loads(candidate)
            break
        except Exception:
            continue
    return {"ok": p.returncode == 0, "data": data,
            "raw": stdout[-4000:], "error": (p.stderr or "").strip()[-2000:]}


def list_reports() -> list:
    """列出 reports/ 下的简报/校验稿/发布终稿。"""
    if not REPORTS_DIR.is_dir():
        return []
    items = []
    for f in sorted(REPORTS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() not in (".md", ".txt"):
            continue
        st = f.stat()
        kind = ("publish" if f.name.endswith("-publish.txt")
                else "brief" if f.name.endswith("-brief.md")
                else "verified" if f.name.endswith("-verified.md")
                else "other")
        items.append({"name": f.name, "kind": kind, "size": st.st_size,
                      "mtime": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")})
    return items


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception:
            return {}

    def log_message(self, fmt, *a):
        pass

    # ---------------- GET
    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path in ("/", "/index.html"):
            self._send(200, WEBUI_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/config":
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self._json(cfg)
        elif path == "/api/auth-status":
            self._json(run_bot(["auth-status"], timeout=180))
        elif path == "/api/reports":
            self._json({"ok": True, "reports": list_reports()})
        elif path == "/api/report":
            name = (parse_qs(parsed.query).get("name") or [""])[0]
            f = (REPORTS_DIR / name).resolve() if name else None
            if not name or REPORTS_DIR.resolve() not in f.parents or not f.is_file():
                self._json({"ok": False, "error": "报告不存在"}, 404)
                return
            self._json({"ok": True, "name": f.name,
                        "content": f.read_text(encoding="utf-8", errors="replace")})
        else:
            self._json({"ok": False, "error": "not found"}, 404)

    # ---------------- POST
    def do_POST(self):
        b = self._body()
        if self.path == "/api/config":
            # 保存配置（整体覆盖，前端负责带全字段）
            try:
                cfg = b.get("config", b)
                CONFIG_PATH.write_text(
                    json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
                self._json({"ok": True})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
        elif self.path == "/api/zxg-publish":
            args = []
            if b.get("headless", True):
                args.append("--headless")
            args.append("zxg-publish")
            if b.get("text"):
                args += ["--text", b["text"]]
            if b.get("file"):
                args += ["--file", b["file"]]
            if b.get("dry_run"):
                args.append("--dry-run")
            self._json(run_bot(args))
        elif self.path == "/api/zxg-login":
            self._json(run_bot(["zxg-login"], timeout=360))
        elif self.path == "/api/zxg-logout":
            self._json(run_bot(["zxg-logout"], timeout=120))
        else:
            self._json({"ok": False, "error": "not found"}, 404)


def daily_zxg_scheduler():
    """腾讯自选股每日定时发帖：zxg.daily.enabled 开启且到达设定时间且今天未发过。"""
    fired_date = ""
    while True:
        time.sleep(30)
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            daily = (cfg.get("zxg", {}) or {}).get("daily", {}) or {}
            if not daily.get("enabled"):
                continue
            now = datetime.datetime.now()
            if now.strftime("%H:%M") != str(daily.get("time", "")):
                continue
            if fired_date == now.date().isoformat():
                continue
            fired_date = now.date().isoformat()
            print(f"[webui] 腾讯自选股每日定时发布触发（{daily.get('time')}）")
            result = run_bot(["--headless", "zxg-publish"], timeout=300)
            data = result.get("data") or {}
            print("[webui] 定时发布结果:",
                  json.dumps(data.get("result", data), ensure_ascii=False)[:300])
        except Exception as e:
            print(f"[webui] 定时发布异常: {e}")


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # 禁止端口复用：防止多个实例静默抢端口，旧进程接住请求导致 404


def main():
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[webui] 端口 {PORT} 被占用（可能已有旧实例在运行）：{e}")
        print("[webui] 请先关闭旧的 webui 进程后重试")
        sys.exit(1)
    threading.Thread(target=daily_zxg_scheduler, daemon=True).start()
    print(f"[webui] 腾讯自选股控制台已启动: http://127.0.0.1:{PORT}")
    print("[webui] 按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
