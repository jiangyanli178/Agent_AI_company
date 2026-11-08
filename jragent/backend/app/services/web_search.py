"""
联网搜索 —— 让 AI 能去外面找因子思路，而不是只靠训练数据里的记忆。

为什么需要：
内置因子库有 16 个，覆盖的是经典技术指标。但量化研究里新东西很多
（各种量价背离、筹码结构、波动率曲面……），模型记忆可能过时或者记错。
联网能拿到"现在别人在用什么东西"。

为什么不能只用联网：
搜回来的东西质量参差不齐，很多是营销文或者半截公式。
**所以这里只负责"找线索"，不负责"下结论"。**
AI 拿到线索后必须写成可计算公式、跑真回测验证 —— 算不出来或者回测没改善，
就如实说没用。这条约束在提示词里也写死了。

支持的 provider（都是为了国内能直连）：
  tavily  专为 AI 设计，有免费额度，注册即用
  bocha   博查，国内公司，按次计费，网络最稳
  serper  Google 结果，免费额度大，偶尔需要代理
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import requests

from ..config import settings

log = logging.getLogger(__name__)

TIMEOUT = 12


def provider() -> str:
    return (getattr(settings, "WEB_SEARCH_PROVIDER", "") or "").strip().lower()


def enabled() -> bool:
    return bool(provider() and getattr(settings, "WEB_SEARCH_API_KEY", ""))


def status() -> Dict[str, Any]:
    """给设置页/健康检查用，别把 key 返回出去"""
    p = provider()
    return {
        "enabled": enabled(),
        "provider": p or "未配置",
        "has_key": bool(getattr(settings, "WEB_SEARCH_API_KEY", "")),
        "supported": ["tavily", "bocha", "serper"],
        "hint": (
            "在 .env 里设置 WEB_SEARCH_PROVIDER 和 WEB_SEARCH_API_KEY 即可开启。"
            "推荐 tavily（有免费额度）或 bocha（国内最稳）。"
        ),
    }


def _tavily(q: str, n: int, key: str) -> List[Dict[str, str]]:
    url = "https://api.tavily.com/search"
    body = {"query": q, "max_results": n, "search_depth": "basic",
            "include_answer": False}
    headers = {"Authorization": f"Bearer {key}"}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        if r.status_code >= 400:
            # 老版本要 api_key 放 body 里
            body["api_key"] = key
            r = requests.post(url, json=body, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"tavily 请求失败：{e}")

    out = []
    for it in (data.get("results") or [])[:n]:
        out.append({
            "title": it.get("title") or "",
            "url": it.get("url") or "",
            "snippet": (it.get("content") or "")[:500],
            "source": "tavily",
        })
    return out


def _bocha(q: str, n: int, key: str) -> List[Dict[str, str]]:
    url = "https://api.bochaai.com/v1/web-search"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    body = {"query": q, "summary": True, "count": n, "page": 1}
    try:
        r = requests.post(url, json=body, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"bocha 请求失败：{e}")

    pages = (((data.get("data") or {}).get("webPages") or {}).get("value") or [])
    out = []
    for it in pages[:n]:
        out.append({
            "title": it.get("name") or "",
            "url": it.get("url") or "",
            "snippet": (it.get("summary") or it.get("snippet") or "")[:500],
            "source": "bocha",
        })
    return out


def _serper(q: str, n: int, key: str) -> List[Dict[str, str]]:
    url = "https://google.serper.dev/search"
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    try:
        r = requests.post(url, json={"q": q, "num": n}, headers=headers, timeout=TIMEOUT)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        raise RuntimeError(f"serper 请求失败：{e}")

    out = []
    for it in (data.get("organic") or [])[:n]:
        out.append({
            "title": it.get("title") or "",
            "url": it.get("link") or "",
            "snippet": (it.get("snippet") or "")[:500],
            "source": "serper",
        })
    return out


_ADAPTERS = {"tavily": _tavily, "bocha": _bocha, "serper": _serper}


def search(query: str, n: int = 5) -> Dict[str, Any]:
    """
    返回 {ok, provider, results, error}
    任何失败都不抛异常 —— 联网失败不该让整次对话崩掉，降级成"没搜到"就行。
    """
    q = (query or "").strip()
    if not q:
        return {"ok": False, "error": "搜索词为空", "results": []}

    p = provider()
    key = getattr(settings, "WEB_SEARCH_API_KEY", "")
    if not p or not key:
        return {
            "ok": False,
            "error": "没有配置联网搜索（WEB_SEARCH_PROVIDER / WEB_SEARCH_API_KEY）",
            "results": [],
            "hint": status()["hint"],
        }

    fn = _ADAPTERS.get(p)
    if not fn:
        return {"ok": False, "error": f"不支持的搜索服务：{p}", "results": []}

    try:
        results = fn(q, max(1, min(int(n), 8)), key)
    except Exception as e:
        log.warning("联网搜索失败：%s", e)
        return {"ok": False, "error": str(e)[:200], "results": []}

    return {
        "ok": True,
        "provider": p,
        "query": q,
        "results": results,
        "count": len(results),
    }
