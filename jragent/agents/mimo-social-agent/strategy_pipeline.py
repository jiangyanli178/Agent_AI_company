#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日策略回撤流水线（总 agent）

流程：
  ① 按配置刷视频 + 抓评论（复用 bot.py 的浏览器/平台层）
  ② 识别「策略相关」内容（视频标题或评论命中策略关键词）
  ③ 命中则调用另一项目 opc_quant_studio 对该策略回测，取最大回撤 / 收益
  ④ 把回撤结果简化为模板（只含回撤 + 收益 + 必要合规提示）
  ⑤ 将模板回复发给该用户（走 bot.py 的安全闸门：去重/限额/违禁词/最小间隔）

只在一次浏览器会话里完成 抓取→识别→回撤→回复，避免反复开关浏览器。
合规红线（与回撤系统一致）：不荐股、不承诺收益、回复必带最大回撤风险揭示 +
历史回测不代表未来 + 不构成投资建议。
"""
from __future__ import annotations

import datetime
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT.parent.parent / "backend"   # 合并后 opc_quant_studio 包位于 jragent/backend/
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))  # 便于 import opc_quant_studio

# 回撤系统（另一项目 opc_quant_studio）
from opc_quant_studio.agents import StrategyTranslator          # noqa: E402
from opc_quant_studio.backtest import run_backtest              # noqa: E402
from opc_quant_studio.context_hub import ContextHub             # noqa: E402
from opc_quant_studio.data_manager import (                     # noqa: E402
    fetch_baostock, fetch_cn, fetch_public, synthetic_ohlcv,
)

PIPELINE_HUB = ROOT / "pipeline_hub"   # 流水线卷宗/日志（审计留痕）

# 策略相关识别关键词（可在 config.json 的 pipeline.strategy_keywords 覆盖）
DEFAULT_STRATEGY_KEYWORDS = [
    "均线", "金叉", "死叉", "交叉", "突破", "止损", "止盈", "回测", "策略",
    "量化", "高抛低吸", "网格", "打板", "龙头", "MACD", "macd", "布林",
    "kdj", "KDJ", "趋势", "支撑位", "压力位", "年线", "月线", "周线",
]

# 提取 6 位 A 股代码
_CODE_RE = re.compile(r"(?<!\d)([036]\d{5})(?!\d)")


# ---------------------------------------------------------------- 回撤系统对接

def _get_hub() -> ContextHub:
    return ContextHub(PIPELINE_HUB)


def collect_snapshot(symbol: str, period: str = "2y"):
    """数据降级链（与 opc_quant_studio 数据收集师同口径）：真实公开数据 → 合成演示数据。"""
    n_days = {"1y": 252, "2y": 504}.get(period, 504)
    errors = []
    for fetcher in (fetch_cn, fetch_baostock, fetch_public):
        try:
            return fetcher(symbol, n_days), False
        except Exception as e:  # noqa: BLE001
            errors.append(f"{fetcher.__name__}: {e}")
    snap = synthetic_ohlcv(symbol=symbol, n_days=n_days)
    snap.meta = {**snap.meta, "fallback_errors": errors}
    return snap, True


def backtest_strategy(idea: str, symbol: str, period: str = "2y") -> dict | None:
    """自然语言策略 → opc_quant_studio 回测 → 指标 dict（含 max_drawdown / total_return）。"""
    try:
        hub = _get_hub()
        spec = StrategyTranslator(hub).translate(idea)
        snap, synthetic = collect_snapshot(symbol, period)
        result = run_backtest(spec, snap)
        metrics = dict(result.metrics)
        metrics["symbol"] = symbol
        metrics["idea"] = idea
        metrics["data_source"] = snap.source
        metrics["synthetic"] = synthetic
        metrics["spec_name"] = spec.name
        return metrics
    except Exception as e:  # noqa: BLE001
        print(f"[pipeline] 回撤失败（{symbol}）：{e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- 策略识别

def is_strategy_related(text: str, keywords: list) -> bool:
    if not text:
        return False
    return any(k and k in text for k in keywords)


def extract_symbol(text: str, default: str = "") -> str:
    m = _CODE_RE.search(text or "")
    return m.group(1) if m else (default or "")


def extract_idea(text: str, fallback: str = "") -> str:
    """取策略描述：优先含策略关键词的句子，否则整段文本。"""
    text = (text or "").strip()
    if not text:
        return fallback
    for sent in re.split(r"[。！？!?\n]", text):
        if is_strategy_related(sent, DEFAULT_STRATEGY_KEYWORDS):
            return sent.strip() or text
    return text


# ---------------------------------------------------------------- 模板回复

DEFAULT_TEMPLATE = (
    "【回测参考】收益 {total_return}｜最大回撤 {max_drawdown}"
    "（历史回测不代表未来，不构成投资建议）"
)


def _pct(x) -> str:
    try:
        return f"{float(x):.2%}"
    except Exception:  # noqa: BLE001
        return "—"


def render_reply(metrics: dict, template: str = "") -> str:
    """把回撤结果简化为模板（只含回撤 + 收益 + 必要合规提示）。"""
    tpl = template or DEFAULT_TEMPLATE
    mapping = {
        "total_return": _pct(metrics.get("total_return")),
        "max_drawdown": _pct(metrics.get("max_drawdown")),
        "annualized_return": _pct(metrics.get("annualized_return")),
        "sharpe": str(metrics.get("sharpe", "—")),
        "win_rate": _pct(metrics.get("win_rate")),
        "symbol": str(metrics.get("symbol", "")),
        "idea": str(metrics.get("idea", ""))[:20],
    }
    for k, v in mapping.items():
        tpl = tpl.replace("{" + k + "}", v)
    return tpl


# ---------------------------------------------------------------- 安全闸门（软版，不退出）

def soft_check_reply(cfg: dict, state: dict, comment: dict, text: str):
    """镜像 bot.check_reply_allowed，但返回 (ok, reason) 而非直接退出。"""
    limits = cfg.get("limits", {})
    if not text or not text.strip():
        return False, "回复内容为空"
    for bad in cfg.get("reply_rules", {}).get("avoid", []):
        if bad and bad in text:
            return False, f"命中违禁词「{bad}」"
    cid = str(comment.get("cid", ""))
    if cid and cid in state.get("replied", {}):
        return False, "该评论已回复过"
    d = datetime.date.today().isoformat()
    if int(state.get("daily", {}).get(d, 0)) >= int(limits.get("max_replies_per_day", 20)):
        return False, "已达每日回复限额"
    skip = cfg.get("skip", {})
    if int(comment.get("likes", 0)) < int(skip.get("min_comment_likes", 0)):
        return False, "点赞数低于阈值"
    return True, ""


# ---------------------------------------------------------------- 流水线主流程

def run_pipeline(cfg: dict, headless: bool = True, dry_run: bool = False) -> dict:
    """总 agent：抓视频+评论 → 策略识别 → 回撤 → 模板回复。"""
    import bot  # 复用浏览器/平台/状态层

    pcfg = cfg.get("pipeline", {}) or {}
    platform = cfg.get("platform", "bilibili")
    search = pcfg.get("search") or cfg.get("topic", "")
    max_videos = int(pcfg.get("max_videos", 3))
    max_comments = int(pcfg.get("max_comments_per_video", 20))
    max_replies = int(pcfg.get("max_replies_per_run", 5))
    strategy_kws = [k for k in (pcfg.get("strategy_keywords") or DEFAULT_STRATEGY_KEYWORDS) if k]
    default_symbol = pcfg.get("default_symbol", "")
    period = pcfg.get("period", "2y")
    template = pcfg.get("reply_template", "")

    state = bot.load_state()
    summary = {"platform": platform, "videos": 0, "comments": 0,
               "strategy_hits": 0, "backtested": 0, "replied": 0,
               "dry_run": dry_run, "items": []}

    pw, browser, page = bot.launch(cfg, headless)
    try:
        plat = bot.get_platform(platform, page)
        videos = plat.collect_videos(max_videos, search)
        summary["videos"] = len(videos)

        for video in videos:
            url, title = video.get("url", ""), video.get("title", "")
            try:
                comments = plat.fetch_comments(url)
            except Exception as e:  # noqa: BLE001
                print(f"[pipeline] 抓评论失败 {url}: {e}", file=sys.stderr)
                continue
            comments = comments[:max_comments]
            summary["comments"] += len(comments)

            for c in comments:
                if summary["replied"] >= max_replies:
                    break
                combined = f"{title} {c.get('text', '')}"
                if not is_strategy_related(combined, strategy_kws):
                    continue
                summary["strategy_hits"] += 1

                idea = extract_idea(c.get("text", "") or title, fallback=title)
                symbol = extract_symbol(combined, default_symbol)
                if not symbol:
                    summary["items"].append({"user": c.get("user"), "cid": c.get("cid"),
                                             "skipped": "未识别到股票代码"})
                    continue

                metrics = backtest_strategy(idea, symbol, period)
                if not metrics:
                    summary["items"].append({"user": c.get("user"), "cid": c.get("cid"),
                                             "skipped": "回撤失败"})
                    continue
                summary["backtested"] += 1

                reply = render_reply(metrics, template)
                ok, reason = soft_check_reply(cfg, state, c, reply)
                item = {"user": c.get("user"), "cid": c.get("cid"),
                        "symbol": symbol, "idea": idea[:30],
                        "total_return": _pct(metrics.get("total_return")),
                        "max_drawdown": _pct(metrics.get("max_drawdown")),
                        "reply": reply, "data_source": metrics.get("data_source", "")}
                if not ok:
                    item["skipped"] = reason
                    summary["items"].append(item)
                    continue

                if dry_run:
                    item["result"] = "dry-run（未实发）"
                    summary["replied"] += 1
                    summary["items"].append(item)
                    continue

                # 最小间隔
                gap = int(cfg.get("limits", {}).get("min_interval_seconds", 30))
                wait = gap - int(time.time() - int(state.get("last_reply_ts", 0)))
                if wait > 0:
                    time.sleep(wait)

                res = plat.post_reply(url, c, reply)
                item["result"] = res.get("status", "unknown")
                if res.get("status") in ("sent", "unknown"):
                    state.setdefault("replied", {})[str(c.get("cid"))] = {
                        "time": datetime.datetime.now().isoformat(timespec="seconds"),
                        "text": reply, "status": res.get("status"),
                    }
                    state["last_reply_ts"] = int(time.time())
                    d = datetime.date.today().isoformat()
                    state.setdefault("daily", {})[d] = int(state.get("daily", {}).get(d, 0)) + 1
                    bot.save_state(state)
                    summary["replied"] += 1
                summary["items"].append(item)
    finally:
        browser.close()
        pw.stop()

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary
