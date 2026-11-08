#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mimo-zxg-agent 工具脚本（腾讯自选股发布通道）

财经信息研究由两个独立 agent skill 完成（与刷视频助手 mimo-social-agent 完全隔离）：
  zxg-research.md   访问各大财经网站，总结对股市有影响且有用的信息 -> reports/YYYY-MM-DD-brief.md
  zxg-factcheck.md  对简报逐条真实性校验 -> reports/YYYY-MM-DD-verified.md + reports/YYYY-MM-DD-publish.txt

本脚本只负责把最终内容发布到腾讯自选股（gu.qq.com）：

  python bot.py zxg-login                                       # 登录腾讯自选股（QQ/微信）
  python bot.py zxg-publish [--text 文案 | --file 终稿] [--dry-run]  # 发帖（单次提交，不重试）
  python bot.py zxg-logout                                      # 清除登录 Cookie
  python bot.py auth-status                                     # 无头检测登录态

内容来源优先级：--text > --file > reports/ 最新 *-publish.txt（校验终稿）> zxg.content_pool 按日轮换。

安全设计：
  - 每次发布只提交一次，失败/无回执不重发，状态记为 unknown
  - 违禁词拦截、内容 MD5 去重（state.json 的 zxg_posted）、每日限额（zxg.max_per_day）
"""
import argparse
import datetime
import hashlib
import json
import re
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
STATE_PATH = ROOT / "state.json"
REPORTS_DIR = ROOT / "reports"

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 基础工具

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            with open(STATE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"zxg_posted": [], "fired_date": ""}


def save_state(state: dict) -> None:
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def out(obj) -> None:
    print(json.dumps(obj, ensure_ascii=False, indent=2))


def die(msg: str, code: int = 1) -> None:
    print(f"[错误] {msg}", file=sys.stderr)
    sys.exit(code)


def today() -> str:
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- 浏览器

def launch(cfg: dict, headless: bool):
    from playwright.sync_api import sync_playwright
    pw = sync_playwright().start()
    profile = str(ROOT / ".browser-profile")
    browser = pw.chromium.launch_persistent_context(
        profile,
        channel="chrome",
        headless=headless,
        viewport={"width": 1280, "height": 860},
        args=["--disable-blink-features=AutomationControlled"],
    )
    page = browser.pages[0] if browser.pages else browser.new_page()
    return pw, browser, page


# ---------------------------------------------------------------- 腾讯自选股发布

ZXG_HOME = "https://gu.qq.com/"
# QQ/微信登录凭证 Cookie（gu.qq.com / .qq.com 域）：身份 + 会话两组
ZXG_COOKIE_NAMES = ("p_uin", "uin", "luin", "superuin", "p_skey", "skey", "lskey", "superkey")
ZXG_OK_WORDS = ("发布成功", "发表成功", "提交成功", "发帖成功", "发布成功！")


def _zxg_logged_in(page, browser) -> bool:
    """腾讯自选股登录态检测：必须有身份凭证 Cookie 或页面正向登录证据，匿名访问不得判为已登录。"""
    try:
        cookies = {c["name"]: (c.get("value") or "").strip()
                   for c in browser.cookies() if "qq.com" in (c.get("domain") or "")}
    except Exception:
        cookies = {}
    # 1) 身份凭证：QQ/微信 uin 系列身份 Cookie + skey 系列会话 Cookie
    uid = ""
    for k in ("p_uin", "uin", "luin", "superuin"):
        v = cookies.get(k, "")
        if v and set(v) != {"0"}:
            uid = v
            break
    has_sess = any(cookies.get(k, "") not in ("", "@")
                   for k in ("p_skey", "skey", "lskey", "superkey"))
    if uid and has_sess:
        return True
    # 2) 页面正向登录证据：出现登录后专属入口才算已登录（不可用"没有登录字样"反推）
    try:
        text = page.evaluate("document.body ? document.body.innerText : ''") or ""
        return any(w in text for w in ("退出登录", "个人中心", "我的主页", "我的收藏"))
    except Exception:
        return False


def _zxg_fill_editor(page, text: str) -> bool:
    """打开发帖入口（如需要）并把文案填入编辑器。"""
    if not page.query_selector("textarea, div[contenteditable='true']"):
        for t in ("发帖", "发布动态", "写帖子", "发表新帖", "发讨论", "写讨论", "参与讨论"):
            try:
                page.get_by_text(t, exact=True).first.click(timeout=2500)
                page.wait_for_timeout(1500)
                break
            except Exception:
                continue
    for sel in ("textarea", "div[contenteditable='true']"):
        el = page.query_selector(sel)
        if not el:
            continue
        try:
            el.click()
            if sel == "textarea":
                el.fill(text)
            else:
                page.keyboard.insert_text(text)
            return True
        except Exception:
            continue
    # 富文本 iframe 兜底
    for fr in page.frames[1:]:
        try:
            body = fr.locator("body").first
            body.click(timeout=2000)
            page.keyboard.insert_text(text)
            return True
        except Exception:
            continue
    return False


def _zxg_submit(page) -> str:
    """只点击一次发布按钮，无回执记 unknown，不重试。"""
    clicked = False
    for name in ("发布", "发表", "发帖"):
        try:
            page.get_by_role("button", name=re.compile(f"^{name}$")).first.click(timeout=2500)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        for name in ("发布", "发表"):
            try:
                page.get_by_text(name, exact=True).first.click(timeout=2500)
                clicked = True
                break
            except Exception:
                continue
    if not clicked:
        return "failed"
    page.wait_for_timeout(3000)
    try:
        body = page.evaluate("() => document.body.innerText")
    except Exception:
        body = ""
    return "sent" if any(w in body for w in ZXG_OK_WORDS) else "unknown"


def _pick_text(args, zxg: dict) -> tuple:
    """解析发布内容：--text > --file > reports/ 最新 *-publish.txt > content_pool 按日轮换。"""
    text = (args.text or "").strip()
    if text:
        return text, "text"
    if args.file:
        p = Path(args.file)
        if not p.is_absolute():
            p = ROOT / p
        if not p.exists():
            die(f"文件不存在：{p}")
        return p.read_text(encoding="utf-8").strip(), f"file:{p.name}"
    if REPORTS_DIR.exists():
        latest = sorted(REPORTS_DIR.glob("*-publish.txt"))
        if latest:
            return latest[-1].read_text(encoding="utf-8").strip(), f"report:{latest[-1].name}"
    pool = [t.strip() for t in zxg.get("content_pool", []) if t.strip()]
    if not pool:
        die("没有可用文案：请用 --text / --file 指定，先跑 zxg-factcheck 生成校验终稿，或配置 zxg.content_pool")
    # 按日期轮换文案库，同一天内容稳定
    idx = int(datetime.date.today().strftime("%j")) % len(pool)
    return pool[idx], "content_pool"


def cmd_zxg_login(args, cfg):
    """打开腾讯自选股页面，人工完成登录（QQ/微信/滑块/验证码），登录态存入 .browser-profile。"""
    pw, browser, page = launch(cfg, headless=False)
    try:
        url = (cfg.get("zxg", {}) or {}).get("publish_url") or ZXG_HOME
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        print("[等待] 请在弹出的浏览器窗口中登录腾讯自选股（QQ/微信登录，含滑块/验证码），最长等 300 秒 ...")
        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            if _zxg_logged_in(page, browser):
                ok = True
                break
            page.wait_for_timeout(2000)
        out({"platform": "zxg", "logged_in": ok,
             "detail": "登录成功，登录态已保存在 .browser-profile" if ok
             else "等待超时，未检测到登录态"})
    finally:
        browser.close()
        pw.stop()


def cmd_zxg_publish(args, cfg):
    """向腾讯自选股发布一条帖子（每天定时由 webui 调度，或手动/agent 调用）。"""
    zxg = cfg.get("zxg", {}) or {}
    text, source = _pick_text(args, zxg)
    if not text:
        die("发布内容为空")
    for bad in zxg.get("avoid", []):
        if bad and bad in text:
            die(f"发帖内容包含违禁词「{bad}」，已拦截")

    state = load_state()
    d = today()
    posted = state.get("zxg_posted", [])
    h = hashlib.md5(text.encode("utf-8")).hexdigest()[:12]
    if any(p.get("hash") == h for p in posted):
        die("相同内容已发布过，不重复发布")
    if not args.dry_run and len([p for p in posted if p.get("date") == d]) >= int(zxg.get("max_per_day", 1)):
        die("已达到每日发帖限额")

    pw, browser, page = launch(cfg, args.headless)
    try:
        url = zxg.get("publish_url") or ZXG_HOME
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)
        if not _zxg_logged_in(page, browser):
            out({"platform": "zxg", "text": text[:120], "source": source,
                 "result": {"status": "failed",
                            "detail": "未检测到腾讯自选股登录态，请先运行 zxg-login"}})
            return
        if not _zxg_fill_editor(page, text):
            out({"platform": "zxg", "text": text[:120], "source": source,
                 "result": {"status": "failed",
                            "detail": "未找到发帖输入框，请确认 publish_url 是发帖/讨论页"}})
            return
        if args.dry_run:
            shot = str(Path(tempfile.gettempdir()) / "zxg_dryrun.png")
            try:
                page.screenshot(path=shot)
            except Exception:
                shot = ""
            out({"platform": "zxg", "text": text[:120], "source": source, "dry_run": True, "url": url,
                 "result": {"status": "dry-run",
                            "detail": f"内容已填入编辑器但未提交{('，截图 ' + shot) if shot else ''}"}})
            return
        status = _zxg_submit(page)
        if status in ("sent", "unknown"):
            state.setdefault("zxg_posted", []).append({
                "date": d, "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "hash": h, "source": source, "text": text[:200], "status": status,
            })
            save_state(state)
        out({"platform": "zxg", "url": url, "text": text[:120], "source": source,
             "result": {"status": status,
                        "detail": {"sent": "发布成功",
                                   "unknown": "已提交但未见成功回执，状态记为 unknown（不重发）",
                                   "failed": "未找到发布按钮"}[status]}})
    finally:
        browser.close()
        pw.stop()


def cmd_zxg_logout(args, cfg):
    """清除腾讯自选股（gu.qq.com / qq.com）登录 Cookie。"""
    pw, browser, page = launch(cfg, headless=True)
    try:
        all_cookies = browser.cookies()
        keep = [c for c in all_cookies
                if "qq.com" not in (c.get("domain") or "")]
        removed = len(all_cookies) - len(keep)
        browser.clear_cookies()
        for c in keep:
            try:
                browser.add_cookies([c])
            except Exception:
                pass
        out({"platform": "zxg", "logged_out": True,
             "detail": f"已清除 {removed} 个腾讯自选股登录 Cookie"})
    finally:
        browser.close()
        pw.stop()


def cmd_auth_status(args, cfg):
    """无头检测腾讯自选股登录态。"""
    pw, browser, page = launch(cfg, headless=True)
    try:
        status = {}
        try:
            url = (cfg.get("zxg", {}) or {}).get("publish_url") or ZXG_HOME
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(1500)
            status["zxg"] = _zxg_logged_in(page, browser)
        except Exception:
            status["zxg"] = False
        out({"logged_in": status})
    finally:
        browser.close()
        pw.stop()


# ---------------------------------------------------------------- 入口

def main():
    p = argparse.ArgumentParser(description="腾讯自选股 发布工具（财经简报由 zxg-research / zxg-factcheck agent 产出）")
    p.add_argument("--headless", action="store_true", help="无头模式（登录时不生效，登录必须可见窗口）")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("zxg-login", help="打开浏览器登录腾讯自选股（登录态存入 .browser-profile）")

    zp = sub.add_parser("zxg-publish", help="向腾讯自选股发布一条帖子（单次提交，不重试）")
    zp.add_argument("--text", default="", help="帖子内容（留空则依次取 --file / 校验终稿 / 文案库）")
    zp.add_argument("--file", default="", help="从文件读取发布内容（如 reports/YYYY-MM-DD-publish.txt）")
    zp.add_argument("--dry-run", action="store_true",
                    help="试运行：填入编辑器但不提交，并截图")

    sub.add_parser("zxg-logout", help="清除腾讯自选股登录 Cookie")
    sub.add_parser("auth-status", help="无头检测腾讯自选股登录态")

    args = p.parse_args()
    cfg = load_config()

    {"zxg-login": cmd_zxg_login, "zxg-publish": cmd_zxg_publish,
     "zxg-logout": cmd_zxg_logout, "auth-status": cmd_auth_status}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
