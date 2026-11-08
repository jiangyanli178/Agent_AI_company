# -*- coding: utf-8 -*-
"""
MiMo API 封装。

实测确认（2026-11-06）：
  BASE_URL  https://api.xiaomimimo.com/v1
  MODEL     mimo-v2.6-pro
  认证      api-key 头（OpenAI SDK 也能直接调通）
  参数      max_completion_tokens
  思考模式默认开启，响应带 reasoning_content，
  多轮工具调用时官方建议保留历史 reasoning_content 以获得最佳表现。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from openai import OpenAI

from ..config import settings

log = logging.getLogger("app.mimo")


@dataclass
class ToolCall:
    id: str
    name: str
    args: Dict[str, Any]
    raw_arguments: str = ""


@dataclass
class MimoResponse:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    finish_reason: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    def assistant_message(self) -> Dict[str, Any]:
        """
        回填进 messages 的 assistant 消息。
        必须带上 reasoning_content，否则下一轮工具调用的表现会明显下降。
        """
        msg: Dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.reasoning_content:
            msg["reasoning_content"] = self.reasoning_content
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": t.id, "type": "function",
                 "function": {"name": t.name, "arguments": t.raw_arguments}}
                for t in self.tool_calls
            ]
        return msg


def _client() -> OpenAI:
    if not settings.MIMO_API_KEY:
        raise RuntimeError("未配置 MIMO_API_KEY，请检查 backend/.env")
    return OpenAI(api_key=settings.MIMO_API_KEY, base_url=settings.MIMO_API_BASE)


def chat(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    timeout: float = 90.0,
) -> MimoResponse:
    """
    非流式对话。工具循环里用它，因为我们要先拿到完整的 tool_calls 才能执行。
    """
    kwargs: Dict[str, Any] = {
        "model": settings.MIMO_MODEL,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
        "timeout": timeout,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    resp = _client().chat.completions.create(**kwargs)
    choice = resp.choices[0]
    msg = choice.message

    calls: List[ToolCall] = []
    for t in (msg.tool_calls or []):
        raw = t.function.arguments or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            log.warning("工具参数不是合法 JSON：%s", raw[:200])
            args = {}
        calls.append(ToolCall(id=t.id, name=t.function.name, args=args, raw_arguments=raw))

    usage = getattr(resp, "usage", None)
    return MimoResponse(
        content=msg.content or "",
        tool_calls=calls,
        reasoning_content=getattr(msg, "reasoning_content", "") or "",
        finish_reason=choice.finish_reason or "",
        prompt_tokens=getattr(usage, "prompt_tokens", 0) if usage else 0,
        completion_tokens=getattr(usage, "completion_tokens", 0) if usage else 0,
    )


def chat_stream(
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    max_tokens: int = 2000,
    temperature: float = 0.7,
    timeout: float = 120.0,
):
    """
    流式对话。逐块 yield：
        ("text", str)        正文增量，直接推给前端
        ("done", MimoResponse) 本轮完整结果（含 tool_calls 与用量）

    注意：工具调用是分块到达的，args 需要按 index 拼接完整后才能解析。
    """
    kwargs: Dict[str, Any] = {
        "model": settings.MIMO_MODEL,
        "messages": messages,
        "max_completion_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
        "stream_options": {"include_usage": True},
        "timeout": timeout,
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"

    stream = _client().chat.completions.create(**kwargs)

    content_buf, reasoning_buf = "", ""
    tool_buf: Dict[int, Dict[str, str]] = {}
    finish = ""
    usage = None

    for chunk in stream:
        # 最后一个 chunk 只带 usage，choices 为空
        if getattr(chunk, "usage", None):
            usage = chunk.usage
        if not chunk.choices:
            continue

        choice = chunk.choices[0]
        delta = choice.delta

        rc = getattr(delta, "reasoning_content", None)
        if rc:
            reasoning_buf += rc
            # 思考内容边产生边推给上层：前端拿它当"还活着"的证明。
            # 之前只累加不输出，用户盯着空白的"正在理解你的问题…"，
            # 完全无法判断是在思考还是卡死了。
            yield ("thinking", rc)

        if delta.content:
            content_buf += delta.content
            yield ("text", delta.content)

        if getattr(delta, "tool_calls", None):
            for tc in delta.tool_calls:
                i = tc.index or 0
                slot = tool_buf.setdefault(i, {"id": "", "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function:
                    if tc.function.name:
                        slot["name"] += tc.function.name
                    if tc.function.arguments:
                        slot["args"] += tc.function.arguments

        if choice.finish_reason:
            finish = choice.finish_reason

    calls: List[ToolCall] = []
    for slot in tool_buf.values():
        raw = slot["args"] or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            log.warning("流式工具参数不是合法 JSON：%s", raw[:200])
            args = {}
        calls.append(ToolCall(id=slot["id"], name=slot["name"], args=args, raw_arguments=raw))

    # 流式下 usage 不一定给，缺失就估算，保证计费不会变成 0
    if usage:
        ti, to = getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0)
    else:
        ti = sum(estimate_tokens(str(m.get("content") or "")) for m in messages)
        to = estimate_tokens(content_buf) + estimate_tokens(reasoning_buf)

    yield ("done", MimoResponse(
        content=content_buf,
        tool_calls=calls,
        reasoning_content=reasoning_buf,
        finish_reason=finish,
        prompt_tokens=ti,
        completion_tokens=to,
    ))


def estimate_tokens(text: str) -> int:
    """
    粗略估算 token 数，用于调用前的输入截断。
    中文按 1 字 ≈ 1 token、英文按 4 字符 ≈ 1 token 估，宁可偏高不可偏低。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    return int(cjk + (len(text) - cjk) / 4 * 1.2)
