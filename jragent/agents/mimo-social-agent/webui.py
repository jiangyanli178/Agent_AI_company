#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mimo-social-agent 本地 Web 控制台

只监听 127.0.0.1，启动后浏览器打开 http://127.0.0.1:8765
页面上可填写账号密码、配置关键词/人设/限额，并一键刷视频、抓评论、回复。
"""
import json
import datetime
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
WEBUI_PATH = ROOT / "webui.html"
PORT = 8765

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
        if self.path in ("/", "/index.html"):
            self._send(200, WEBUI_PATH.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/config":
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            self._json(cfg)
        elif self.path == "/api/status":
            self._json(run_bot(["status"], timeout=60))
        elif self.path == "/api/auth-status":
            self._json(run_bot(["auth-status"], timeout=180))
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
        elif self.path == "/api/login":
            args = ["--platform", b.get("platform", "bilibili"), "login"]
            if not b.get("fill", True):
                args.append("--no-fill")
            self._json(run_bot(args, timeout=360))
        elif self.path == "/api/browse":
            args = ["--platform", b.get("platform", "bilibili")]
            if b.get("headless", True):
                args.append("--headless")
            args += ["browse", "--search", b.get("search", ""),
                     "--limit", str(int(b.get("limit", 5)))]
            self._json(run_bot(args))
        elif self.path == "/api/scan":
            args = ["--platform", b.get("platform", "bilibili")]
            if b.get("headless", True):
                args.append("--headless")
            args += ["scan", "--url", b.get("url", ""),
                     "--max", str(int(b.get("max", 10)))]
            if b.get("raw"):
                args.append("--raw")
            if "keywords" in b:
                args += ["--keywords", b.get("keywords") or ""]
            self._json(run_bot(args))
        elif self.path == "/api/logout":
            args = ["--platform", b.get("platform", "bilibili"), "logout"]
            self._json(run_bot(args, timeout=120))
        elif self.path == "/api/reply":
            args = ["--platform", b.get("platform", "bilibili")]
            if b.get("headless", True):
                args.append("--headless")
            args += ["reply", "--index", str(int(b.get("index", 0))),
                     "--text", b.get("text", "")]
            self._json(run_bot(args))
        elif self.path == "/api/pipeline":
            args = ["--platform", b.get("platform", "bilibili")]
            if b.get("headless", True):
                args.append("--headless")
            args.append("pipeline")
            if b.get("dry_run"):
                args.append("--dry-run")
            if b.get("search"):
                args += ["--search", b["search"]]
            if b.get("max_videos") is not None:
                args += ["--max-videos", str(int(b["max_videos"]))]
            if b.get("max_replies") is not None:
                args += ["--max-replies", str(int(b["max_replies"]))]
            self._json(run_bot(args, timeout=1200))
        else:
            self._json({"ok": False, "error": "not found"}, 404)


class Server(ThreadingHTTPServer):
    allow_reuse_address = False  # 禁止端口复用：防止多个实例静默抢端口，旧进程接住请求导致 404


def daily_pipeline_scheduler():
    """每日定时策略回撤流水线：pipeline.enabled 开启且到达设定时间且今天未跑过。"""
    fired_date = ""
    while True:
        time.sleep(30)
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            daily = cfg.get("pipeline", {}) or {}
            if not daily.get("enabled"):
                continue
            now = datetime.datetime.now()
            if now.strftime("%H:%M") != str(daily.get("time", "")):
                continue
            if fired_date == now.date().isoformat():
                continue
            fired_date = now.date().isoformat()
            print(f"[webui] 每日策略回撤流水线触发（{daily.get('time')}）")
            result = run_bot(["--headless", "pipeline"], timeout=1800)
            data = result.get("data") or {}
            print("[webui] 流水线结果:",
                  json.dumps(data, ensure_ascii=False)[:400])
        except Exception as e:
            print(f"[webui] 定时流水线异常: {e}")


def main():
    try:
        srv = Server(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"[webui] 端口 {PORT} 被占用（可能已有旧实例在运行）：{e}")
        print("[webui] 请先关闭旧的 webui 进程后重试")
        sys.exit(1)
    threading.Thread(target=daily_pipeline_scheduler, daemon=True).start()
    print(f"[webui] 控制台已启动: http://127.0.0.1:{PORT}")
    print("[webui] 按 Ctrl+C 停止")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
