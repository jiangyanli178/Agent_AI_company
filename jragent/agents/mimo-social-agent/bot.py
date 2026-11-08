#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mimo-social-agent 工具脚本

在抖音 / B站 上：刷视频 -> 抓评论 -> 按关键词筛选 -> 回复评论。
由 MiMo agent 调用三个子命令完成一轮工作：

  python bot.py browse [--search 关键词] [--limit 5]   # 刷视频，列出视频链接
  python bot.py scan   --url <视频链接> [--max 20]      # 抓评论并按关键词筛选，输出候选(JSON)
  python bot.py reply  --index <候选序号> --text "回复内容"  # 对候选评论发布回复

安全设计（参考 dsh_douyin_tools）：
  - 每次回复只提交一次，失败/无回执不重发，状态记为 unknown
  - 单次/每日限额、最小间隔、cid 去重、违禁词拦截
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
    return {"replied": {}, "daily": {}, "last_scan": None}


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


# ---------------------------------------------------------------- 平台适配

class Platform:
    name = ""

    def __init__(self, page):
        self.page = page

    # -- 刷视频
    def collect_videos(self, limit: int, keyword: str = "") -> list:
        raise NotImplementedError

    # -- 评论
    def fetch_comments(self, url: str) -> list:
        """返回 [{cid, user, text, likes}]"""
        raise NotImplementedError

    def post_reply(self, url: str, comment: dict, text: str) -> dict:
        """返回 {status: sent|unknown|failed, detail}"""
        raise NotImplementedError


class DouyinPlatform(Platform):
    name = "douyin"
    HOME = "https://www.douyin.com/"
    SEARCH = "https://www.douyin.com/search/{}"

    VIDEO_LINK = re.compile(r"/(video|note)/(\d+)")

    def collect_videos(self, limit: int, keyword: str = "") -> list:
        url = self.SEARCH.format(keyword) if keyword else self.HOME
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(4000)
        results, seen = [], set()
        for _ in range(6):
            links = self.page.eval_on_selector_all(
                "a[href*='/video/'], a[href*='/note/']",
                "els => els.map(e => ({href: e.href, text: (e.innerText||'').slice(0,80)}))",
            )
            for it in links:
                m = self.VIDEO_LINK.search(it["href"])
                if not m:
                    continue
                vid = m.group(2)
                if vid in seen:
                    continue
                seen.add(vid)
                results.append({"url": f"https://www.douyin.com/video/{vid}",
                                "title": it["text"].strip() or "(无标题)"})
                if len(results) >= limit:
                    return results
            self.page.mouse.wheel(0, 1200)
            self.page.wait_for_timeout(1500)
        return results

    def _aweme_id(self, url: str) -> str:
        m = self.VIDEO_LINK.search(url)
        return m.group(2) if m else ""

    def fetch_comments(self, url: str) -> list:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3500)
        aweme_id = self._aweme_id(url)
        comments = []
        if aweme_id:
            # 优先走页面内接口（带登录态）
            api = ("/aweme/v1/web/comment/list/?device_platform=webapp&aid=6383"
                   f"&channel=channel_pc_web&aweme_id={aweme_id}&cursor={{c}}&count=20&item_type=0")
            cursor = 0
            for _ in range(3):
                try:
                    data = self.page.evaluate(
                        """async (u) => {
                            const r = await fetch(u, {credentials: 'include'});
                            return await r.json();
                        }""", api.format(c=cursor))
                except Exception:
                    break
                if not isinstance(data, dict) or data.get("status_code", 0) not in (0, None):
                    break
                items = data.get("comments") or []
                for c in items:
                    comments.append({
                        "cid": str(c.get("cid", "")),
                        "user": (c.get("user") or {}).get("nickname", ""),
                        "text": (c.get("text") or "").strip(),
                        "likes": int(c.get("digg_count", 0)),
                    })
                if not data.get("has_more"):
                    break
                cursor = data.get("cursor", 0) or 0
                self.page.wait_for_timeout(600)
        if not comments:
            comments = self._scrape_dom()
        return comments

    def _scrape_dom(self) -> list:
        try:
            self.page.wait_for_selector("[data-e2e='comment-list'], [data-e2e='comment-item']", timeout=8000)
        except Exception:
            return []
        rows = self.page.eval_on_selector_all(
            "[data-e2e='comment-item']",
            """els => els.map(e => {
                const u = e.querySelector('[data-e2e*="user"], a[href*="/user/"]');
                return {user: u ? (u.innerText||'').trim() : '',
                        text: (e.innerText||'').trim().slice(0, 300)};
            })""",
        )
        return [{"cid": f"dom-{i}", "user": r["user"], "text": r["text"], "likes": 0}
                for i, r in enumerate(rows)]

    def post_reply(self, url: str, comment: dict, text: str) -> dict:
        if url not in self.page.url:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(3500)
        # 打开评论编辑器
        box = None
        for sel in ["[data-e2e='comment-input']",
                    "div[contenteditable='true'][data-e2e*='comment']",
                    "div.public-DraftEditor-content",
                    "textarea[placeholder*='评论']"]:
            try:
                box = self.page.wait_for_selector(sel, timeout=3000)
                if box:
                    break
            except Exception:
                continue
        if not box:
            for ph in ["留下你的精彩评论", "写评论", "善语结善缘", "说点什么"]:
                try:
                    self.page.get_by_text(ph, exact=False).first.click(timeout=2000)
                    self.page.wait_for_timeout(1200)
                    box = self.page.query_selector("div.public-DraftEditor-content") \
                        or self.page.query_selector("div[contenteditable='true']")
                    if box:
                        break
                except Exception:
                    continue
        if not box:
            return {"status": "failed", "detail": "找不到评论输入框"}

        box.click()
        self.page.wait_for_timeout(400)
        # 回复某条评论：先按 cid/文本找到「回复」按钮并点击
        cid = str(comment.get("cid", ""))
        if not cid.startswith("dom-"):
            pass  # 顶层回复即可；抖音按回复按钮定位不稳定，简化为顶层评论
        self.page.keyboard.type(text, delay=30)
        self.page.wait_for_timeout(300)

        # 提交一次并等待回执，不重试
        try:
            with self.page.expect_response(
                    lambda r: "comment/publish" in r.url, timeout=8000) as ri:
                self.page.keyboard.press("Enter")
            resp = ri.value
            ok = resp.ok
            return {"status": "sent" if ok else "unknown",
                    "detail": f"publish http={resp.status}"}
        except Exception:
            pass
        # 无回执：对比列表里是否出现该文本
        self.page.wait_for_timeout(2500)
        try:
            body = self.page.inner_text("[data-e2e='comment-list']")[:2000]
            if text[:12] in body:
                return {"status": "sent", "detail": "评论列表已出现该文本"}
        except Exception:
            pass
        return {"status": "unknown", "detail": "已提交但没有平台回执，未自动重发"}


class BilibiliPlatform(Platform):
    name = "bilibili"
    HOME = "https://www.bilibili.com/"
    SEARCH = "https://search.bilibili.com/all?keyword={}"

    BV = re.compile(r"/video/(BV[\w]+)")

    def collect_videos(self, limit: int, keyword: str = "") -> list:
        url = self.SEARCH.format(keyword) if keyword else self.HOME
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3500)
        results, seen = [], set()
        for _ in range(5):
            links = self.page.eval_on_selector_all(
                "a[href*='/video/BV']",
                """els => els.map(e => {
                    const card = e.closest('.bili-video-card, li, [class*="card"]') || e;
                    return {href: e.href.split('?')[0],
                            text: (card.innerText||e.innerText||'').trim().slice(0,80)};
                })""",
            )
            for it in links:
                m = self.BV.search(it["href"])
                if not m or m.group(1) in seen:
                    continue
                seen.add(m.group(1))
                results.append({"url": f"https://www.bilibili.com/video/{m.group(1)}/",
                                "title": it["text"].strip().replace("\n", " ") or "(无标题)"})
                if len(results) >= limit:
                    return results
            self.page.mouse.wheel(0, 1400)
            self.page.wait_for_timeout(1500)
        return results

    def _fetch_aid(self) -> str:
        bvid = ""
        m = self.BV.search(self.page.url)
        if m:
            bvid = m.group(1)
        if not bvid:
            return ""
        try:
            data = self.page.evaluate(
                """async (bv) => {
                    const r = await fetch('https://api.bilibili.com/x/web-interface/view?bvid=' + bv,
                                          {credentials: 'include'});
                    return await r.json();
                }""", bvid)
            return str((data.get("data") or {}).get("aid", ""))
        except Exception:
            return ""

    def fetch_comments(self, url: str) -> list:
        self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self.page.wait_for_timeout(3500)
        aid = self._fetch_aid()
        comments = []
        if aid:
            for pn in (1, 2):
                try:
                    data = self.page.evaluate(
                        """async ([aid, pn]) => {
                            const u = `https://api.bilibili.com/x/v2/reply?type=1&oid=${aid}&sort=2&pn=${pn}&ps=20`;
                            const r = await fetch(u, {credentials: 'include'});
                            return await r.json();
                        }""", [aid, pn])
                except Exception:
                    break
                replies = ((data or {}).get("data") or {}).get("replies") or []
                for c in replies:
                    comments.append({
                        "cid": str(c.get("rpid", "")),
                        "user": (c.get("member") or {}).get("uname", ""),
                        "text": (c.get("content") or {}).get("message", "").strip(),
                        "likes": int(c.get("like", 0)),
                    })
                if len(replies) < 20:
                    break
                self.page.wait_for_timeout(600)
        if not comments:
            comments = self._scrape_dom()
        return comments

    def _scrape_dom(self) -> list:
        try:
            self.page.wait_for_selector(".reply-item, .reply-content", timeout=8000)
        except Exception:
            return []
        rows = self.page.eval_on_selector_all(
            ".reply-item, .reply-content",
            """els => els.slice(0, 60).map(e => {
                const u = e.querySelector('.user-name, .reply-user .name, a[href*="//space.bilibili.com"]');
                return {user: u ? (u.innerText||'').trim() : '',
                        text: (e.innerText||'').trim().slice(0, 300)};
            })""",
        )
        seen, out_rows = set(), []
        for i, r in enumerate(rows):
            if not r["text"] or r["text"] in seen:
                continue
            seen.add(r["text"])
            out_rows.append({"cid": f"dom-{i}", "user": r["user"], "text": r["text"], "likes": 0})
        return out_rows

    def post_reply(self, url: str, comment: dict, text: str) -> dict:
        if "bilibili.com/video" not in self.page.url:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self.page.wait_for_timeout(3500)
        cid = str(comment.get("cid", ""))
        target = comment.get("text", "")

        # 尽量定位到该条评论下的「回复」按钮（按文本片段 + 几何位置）
        clicked = False
        if not cid.startswith("dom-") or target:
            snippet = target[:12]
            try:
                item = self.page.locator(".reply-item, .reply-content").filter(
                    has_text=snippet).first
                btn = item.locator("text=回复").first
                btn.click(timeout=4000)
                clicked = True
                self.page.wait_for_timeout(1200)
            except Exception:
                clicked = False

        # 找输入框
        box = None
        for sel in ["textarea[placeholder*='评论']",
                    "textarea[placeholder*='友善']",
                    "div[contenteditable='true']",
                    "textarea"]:
            try:
                if clicked:
                    locs = self.page.locator(sel)
                    box = locs.last  # 回复框一般出现在列表之后
                else:
                    box = self.page.locator(sel).first
                if box.count() > 0:
                    box = box.last if clicked else box.first
                    box.wait_for(state="visible", timeout=3000)
                    break
                box = None
            except Exception:
                box = None
        if box is None:
            return {"status": "failed", "detail": "找不到评论输入框"}

        box.click()
        self.page.wait_for_timeout(300)
        box.fill("") if box.evaluate("e => e.tagName === 'TEXTAREA'") else None
        self.page.keyboard.type(text, delay=30)
        self.page.wait_for_timeout(300)

        # 提交一次
        try:
            with self.page.expect_response(
                    lambda r: "/x/v2/reply/add" in r.url or "/reply/add" in r.url,
                    timeout=8000) as ri:
                try:
                    self.page.locator("button:has-text('回复')").last.click(timeout=2000)
                except Exception:
                    self.page.keyboard.press("Control+Enter")
            resp = ri.value
            try:
                j = resp.json()
                code = j.get("code", -1)
            except Exception:
                code = -1
            return {"status": "sent" if code == 0 else "unknown",
                    "detail": f"reply/add code={code}"}
        except Exception:
            pass
        self.page.wait_for_timeout(2500)
        try:
            if text[:12] in self.page.content():
                return {"status": "sent", "detail": "页面已出现该文本"}
        except Exception:
            pass
        return {"status": "unknown", "detail": "已提交但没有平台回执，未自动重发"}


def get_platform(name: str, page) -> Platform:
    return {"douyin": DouyinPlatform, "bilibili": BilibiliPlatform}[name](page)


# ---------------------------------------------------------------- 安全闸门

def check_reply_allowed(cfg: dict, state: dict, comment: dict, text: str) -> None:
    limits = cfg.get("limits", {})
    if not text or not text.strip():
        die("回复内容为空")
    for bad in cfg.get("reply_rules", {}).get("avoid", []):
        if bad and bad in text:
            die(f"回复内容包含违禁词「{bad}」，已拦截")
    cid = str(comment.get("cid", ""))
    if cid and cid in state.get("replied", {}):
        die(f"该评论已回复过（cid={cid}），不重复回复")
    d = today()
    if int(state.get("daily", {}).get(d, 0)) >= int(limits.get("max_replies_per_day", 20)):
        die("已达到每日回复限额")
    skip = cfg.get("skip", {})
    if int(comment.get("likes", 0)) < int(skip.get("min_comment_likes", 0)):
        die("该评论点赞数低于阈值，跳过")


def match_comments(cfg: dict, comments: list, keywords: list, max_n: int) -> list:
    kws = [k for k in (cfg.get("keywords", []) if keywords is None else keywords) if k]
    matched = []
    for c in comments:
        text = c.get("text", "")
        if not text:
            continue
        if kws and not any(k in text for k in kws):
            continue
        if cfg.get("skip", {}).get("users") and c.get("user") in cfg["skip"]["users"]:
            continue
        matched.append(c)
        if len(matched) >= max_n:
            break
    return matched


# ---------------------------------------------------------------- 子命令

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


def cmd_browse(args, cfg):
    pw, browser, page = launch(cfg, args.headless)
    try:
        plat = get_platform(args.platform, page)
        videos = plat.collect_videos(args.limit, args.search or "")
        out({"platform": args.platform, "videos": videos})
    finally:
        browser.close()
        pw.stop()


def cmd_scan(args, cfg):
    pw, browser, page = launch(cfg, args.headless)
    try:
        plat = get_platform(args.platform, page)
        comments = plat.fetch_comments(args.url)
        if args.raw:
            # 纯抓取模式：不做关键词过滤，按抓取条数取前 N 条
            matched = comments[:args.max]
        else:
            matched = match_comments(cfg, comments,
                                     args.keywords.split(",") if args.keywords is not None else None,
                                     args.max)
        state = load_state()
        state["last_scan"] = {
            "platform": args.platform,
            "url": args.url,
            "candidates": [
                {"index": i + 1, "cid": c["cid"], "user": c["user"], "text": c["text"],
                 "likes": c.get("likes", 0)}
                for i, c in enumerate(matched)
            ],
        }
        save_state(state)
        out({"platform": args.platform, "url": args.url,
             "mode": "raw" if args.raw else "filter",
             "total_comments": len(comments),
             "matched": state["last_scan"]["candidates"]})
    finally:
        browser.close()
        pw.stop()


def cmd_reply(args, cfg):
    state = load_state()
    scan = state.get("last_scan") or {}
    cands = scan.get("candidates") or []
    if not scan or not cands:
        die("没有可用的候选评论，请先运行 scan")
    comment = None
    if args.index is not None:
        comment = next((c for c in cands if c["index"] == args.index), None)
        if comment is None:
            die(f"序号 {args.index} 不存在（候选共 {len(cands)} 条）")
    elif args.cid:
        comment = next((c for c in cands if str(c["cid"]) == str(args.cid)), None)
        if comment is None:
            die(f"cid={args.cid} 不在最近一次 scan 的候选里")
    else:
        die("需要 --index 或 --cid")

    check_reply_allowed(cfg, state, comment, args.text)

    # 最小间隔
    last_ts = int(state.get("last_reply_ts", 0))
    gap = int(cfg.get("limits", {}).get("min_interval_seconds", 30))
    wait = gap - int(time.time() - last_ts)
    if wait > 0:
        print(f"[等待] 距上次回复不足 {gap}s，等待 {wait}s ...")
        time.sleep(wait)

    pw, browser, page = launch(cfg, args.headless)
    try:
        plat = get_platform(scan.get("platform", args.platform), page)
        result = plat.post_reply(scan["url"], comment, args.text)
        if result["status"] in ("sent", "unknown"):
            state.setdefault("replied", {})[str(comment["cid"])] = {
                "time": datetime.datetime.now().isoformat(timespec="seconds"),
                "text": args.text,
                "status": result["status"],
            }
            state["last_reply_ts"] = int(time.time())
            d = today()
            state.setdefault("daily", {})[d] = int(state.get("daily", {}).get(d, 0)) + 1
            save_state(state)
        out({"platform": scan.get("platform"), "url": scan["url"],
             "comment": {"index": comment.get("index"), "cid": comment["cid"],
                         "user": comment["user"], "text": comment["text"]},
             "reply": args.text, "result": result})
    finally:
        browser.close()
        pw.stop()


LOGIN_PAGES = {
    "bilibili": "https://passport.bilibili.com/login",
    "douyin": "https://www.douyin.com/",
}
LOGIN_OK_COOKIE = {"bilibili": "SESSDATA", "douyin": "sessionid"}


def cmd_login(args, cfg):
    """打开登录页，自动填写账号密码（如果配置了），并等待登录完成。"""
    pw, browser, page = launch(cfg, headless=False)  # 登录必须可见窗口
    try:
        acct = cfg.get("account", {})
        platform = args.platform
        page.goto(LOGIN_PAGES[platform], wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        filled = False
        u = acct.get("username", "") if isinstance(acct, dict) else ""
        p = acct.get("password", "") if isinstance(acct, dict) else ""
        if u and p and args.fill:
            try:
                if platform == "bilibili":
                    page.fill("input[placeholder*='账号'], #login-username", u, timeout=4000)
                    page.fill("input[placeholder*='密码'], #login-passwd", p, timeout=4000)
                    page.click("#btn-login, button:has-text('登录')", timeout=4000)
                    filled = True
                else:  # douyin：点「登录」入口再切密码登录
                    for t in ["登录", "密码登录"]:
                        try:
                            page.get_by_text(t, exact=True).first.click(timeout=3000)
                            page.wait_for_timeout(1500)
                        except Exception:
                            pass
                    for sel in ["input[placeholder*='手机号']",
                                "input[placeholder*='账号']"]:
                        try:
                            page.fill(sel, u, timeout=3000)
                            break
                        except Exception:
                            continue
                    for sel in ["input[placeholder*='密码']",
                                "input[type='password']"]:
                        try:
                            page.fill(sel, p, timeout=3000)
                            break
                        except Exception:
                            continue
                    page.click("button:has-text('登录')", timeout=3000)
                    filled = True
            except Exception as e:
                print(f"[提示] 自动填写失败（{e}），请在窗口中手动登录", file=sys.stderr)

        # 等待登录成功（可能有滑块/短信验证，需人工完成）
        print("[等待] 请在弹出的浏览器窗口中完成登录（含滑块/验证码），最长等 300 秒 ...")
        deadline = time.time() + 300
        ok = False
        while time.time() < deadline:
            cookies = {c["name"]: c["value"] for c in browser.cookies()}
            if cookies.get(LOGIN_OK_COOKIE[platform]):
                ok = True
                break
            page.wait_for_timeout(2000)
        out({"platform": platform, "auto_filled": filled, "logged_in": ok,
             "detail": "登录成功，登录态已保存在 .browser-profile" if ok
             else "等待超时，未检测到登录态"})
    finally:
        browser.close()
        pw.stop()


def cmd_status(args, cfg):
    state = load_state()
    out({
        "platform_default": cfg.get("platform"),
        "replied_count": len(state.get("replied", {})),
        "replied_today": state.get("daily", {}).get(today(), 0),
        "last_scan": state.get("last_scan"),
    })


def _login_cookie_ok(browser, platform: str) -> bool:
    cookies = {c["name"]: c["value"] for c in browser.cookies()}
    return bool(cookies.get(LOGIN_OK_COOKIE[platform]))


def cmd_auth_status(args, cfg):
    """无头检测各平台登录态，不打开可见窗口。"""
    pw, browser, page = launch(cfg, headless=True)
    try:
        status = {}
        for plat in ("bilibili", "douyin"):
            try:
                page.goto(LOGIN_PAGES[plat], wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(1500)
                status[plat] = _login_cookie_ok(browser, plat)
            except Exception:
                status[plat] = False
        out({"logged_in": status, "current_platform": args.platform})
    finally:
        browser.close()
        pw.stop()


def cmd_logout(args, cfg):
    """清除指定平台的登录 Cookie，实现退出登录。"""
    pw, browser, page = launch(cfg, headless=True)
    try:
        all_cookies = browser.cookies()
        domains = {"bilibili": (".bilibili.com", "bilibili.com"),
                   "douyin": (".douyin.com", "douyin.com")}
        keep = [c for c in all_cookies
                if not any(d in (c.get("domain") or "") for d in domains[args.platform])]
        removed = len(all_cookies) - len(keep)
        browser.clear_cookies()
        for c in keep:
            try:
                browser.add_cookies([c])
            except Exception:
                pass
        out({"platform": args.platform, "logged_out": True,
             "detail": f"已清除 {removed} 个登录 Cookie"})
    finally:
        browser.close()
        pw.stop()


def cmd_pipeline(args, cfg):
    """每日策略回撤流水线：抓视频+评论 → 策略识别 → 回撤 → 模板回复。"""
    import strategy_pipeline
    if args.search:
        cfg.setdefault("pipeline", {})["search"] = args.search
    if args.max_videos is not None:
        cfg.setdefault("pipeline", {})["max_videos"] = args.max_videos
    if args.max_replies is not None:
        cfg.setdefault("pipeline", {})["max_replies_per_run"] = args.max_replies
    strategy_pipeline.run_pipeline(cfg, headless=args.headless, dry_run=args.dry_run)


# ---------------------------------------------------------------- 入口

def main():
    p = argparse.ArgumentParser(description="抖音/B站 刷视频、找关键词评论并回复")
    p.add_argument("--platform", default=None, choices=["douyin", "bilibili"],
                   help="平台（默认取 config.json）")
    p.add_argument("--headless", action="store_true", help="无头模式（调试时建议不加）")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("browse", help="刷视频，列出视频链接")
    b.add_argument("--search", default="", help="搜索关键词（空则看推荐流）")
    b.add_argument("--limit", type=int, default=5)

    s = sub.add_parser("scan", help="抓取视频评论并按关键词筛选")
    s.add_argument("--url", required=True)
    s.add_argument("--keywords", default=None, help="逗号分隔，覆盖 config.json 的 keywords；显式传空串表示不过滤（抓全部）")
    s.add_argument("--max", type=int, default=10, help="抓取/匹配条数上限")
    s.add_argument("--raw", action="store_true",
                   help="纯抓取模式：只抓评论，不做关键词筛选、不回复")

    r = sub.add_parser("reply", help="回复候选评论（只提交一次，失败不重试）")
    r.add_argument("--index", type=int, default=None, help="scan 输出的候选序号")
    r.add_argument("--cid", default=None, help="或指定评论 cid")
    r.add_argument("--text", required=True, help="回复内容")

    sub.add_parser("status", help="查看限额与最近扫描状态")

    l = sub.add_parser("login", help="打开浏览器登录（自动填账号密码，滑块/验证码需人工）")
    l.add_argument("--no-fill", dest="fill", action="store_false",
                   help="不自动填写账号密码，纯手动登录")
    l.set_defaults(fill=True)

    sub.add_parser("auth-status", help="无头检测各平台登录态")

    sub.add_parser("logout", help="清除当前平台的登录 Cookie（退出登录）")

    pl = sub.add_parser("pipeline", help="每日策略回撤流水线：抓视频+评论→策略识别→回撤→模板回复")
    pl.add_argument("--dry-run", action="store_true", help="只识别与回撤、生成模板，不实发回复")
    pl.add_argument("--search", default="", help="刷视频搜索词（默认取 pipeline.search/topic）")
    pl.add_argument("--max-videos", type=int, default=None, help="刷视频条数上限")
    pl.add_argument("--max-replies", type=int, default=None, help="本次回复条数上限")

    args = p.parse_args()
    cfg = load_config()
    if args.platform is None:
        args.platform = cfg.get("platform", "bilibili")

    {"browse": cmd_browse, "scan": cmd_scan,
     "reply": cmd_reply, "status": cmd_status,
     "login": cmd_login, "auth-status": cmd_auth_status,
     "logout": cmd_logout, "pipeline": cmd_pipeline}[args.cmd](args, cfg)


if __name__ == "__main__":
    main()
