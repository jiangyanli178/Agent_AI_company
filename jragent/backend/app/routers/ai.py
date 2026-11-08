# -*- coding: utf-8 -*-
"""
AI 对话：工具循环，整个产品的发动机。

用户说一句人话
  → MiMo 决定调哪个工具
  → 后端执行（查库/跑回测）
  → 结果回填给模型
  → 模型组织人话回答
  → 合规终审后返回
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from ..config import settings
from ..db import engine
from ..deps import get_current_user
from ..exceptions import BizError, ok
from ..services import agent_log, critic, mimo
from ..services import upload as upload_svc
from ..services.agent_tools import TOOLS, SYSTEM_PROMPT, ToolExecutor
from ..services.context import context
from ..services.credits import (
    CreditsService, PRICE, MAX_INPUT_TOKENS, MAX_HISTORY_TURNS,
)

log = logging.getLogger("app.ai")
router = APIRouter(prefix="/ai", tags=["AI 对话"])

# 工具价格：检索类必须免费，否则用户连"你有什么策略"都不敢问
TOOL_PRICE = {
    "run_backtest": 10,
    "compare_strategies": 20,
    "analyze_pair": 20,
    "design_strategy": 5,
    "auto_search_factors": 3,
    "suggest_params": 20,
    "search_strategies": 0,
    "list_symbols": 0,
    "list_factors": 0,
    "prepare_deploy": 0,
    "search_web": 0,
    "build_report": 2,
    "save_strategy": 0,   # 存配置没有算力成本，不该收钱
    "save_factor_set": 0,
    "publish_strategy": 0,   # 发布只是写库 + 跑一次本地回测，不该收钱
    "publish_factor": 0,
    "check_robustness": 5,
    "explain_concept": 0,
}


class ChatIn(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000)
    conversation_id: int | None = None
    # 附件：图片走视觉输入，表格/文本转成文本拼进 prompt
    # 结构见 services/upload.py 的 parse() 返回值
    attachments: Optional[List[Dict[str, Any]]] = None


def _register_upload_route() -> bool:
    """
    单独注册上传接口。

    为什么不在模块顶层直接 @router.post：
    FastAPI 在**注册路由时**就检查 python-multipart，缺包会抛 RuntimeError，
    直接把整个 app 的导入搞崩 —— 表现是所有接口都 Failed to fetch，
    看起来像"网络问题"，实际是启动失败。

    所以这里摘出来，装不上就跳过这个接口，其余功能照常。
    """
    try:
        from fastapi import File, UploadFile

        @router.post("/upload")
        async def upload(
            file: UploadFile = File(...),
            user: dict = Depends(get_current_user),
        ):
            """
            对话附件上传。解析完立刻返回，服务端不落盘。

            前端拿到结果后：
            - 图片带 data_url，随 chat 请求一起回传（模型直接看）
            - 文本/表格带 content，同样随请求回传
            这样一张图不会在服务端存两份，也不占磁盘。
            """
            data = await file.read()
            try:
                r = upload_svc.parse(
                    file.filename or "file", data, file.content_type or "")
            except upload_svc.UploadError as e:
                raise BizError(40007, e.msg)
            return ok(r)

        return True
    except Exception as e:                       # 缺 python-multipart 等
        log.warning("附件上传接口未启用（缺 python-multipart？）：%s", e)
        return False


UPLOAD_ENABLED = _register_upload_route()


@router.get("/capabilities")
def capabilities():
    """
    产品能力自查：前端可以据此决定是否显示上传按钮，
    也方便排查"为什么这个功能没了"。
    """
    return ok({
        "upload": UPLOAD_ENABLED,
        "vision": True,          # MiMo 支持图片输入
        "report": True,
    })


@router.post("/chat")
def chat(body: ChatIn, user: dict = Depends(get_current_user)):
    uid = user["id"]
    svc = CreditsService(engine)
    text_in = body.message.strip()

    # ---- 成本防线：超长输入直接拒绝，不靠封顶兜底 ----
    est_tokens = mimo.estimate_tokens(text_in)
    if est_tokens > MAX_INPUT_TOKENS["AI_CHAT"]:
        raise BizError(40006,
                       f"内容太长了（约 {est_tokens} token），"
                       f"请分段提问，单次最多 {MAX_INPUT_TOKENS['AI_CHAT']} token")

    msg_id = uuid.uuid4().hex[:12]

    # ---- 会话归属校验：必须放在扣费之前 ----
    # 否则既能往别人会话写消息，还会把别人的历史对话带进上下文造成泄露。
    # 统一返回 404，不区分"不存在"与"不属于你"，避免暴露会话是否存在。
    if body.conversation_id:
        with engine.connect() as conn:
            owner = conn.execute(text(
                "SELECT user_id FROM ai_conversations WHERE id=:c"
            ), {"c": body.conversation_id}).scalar()
        if owner != uid:
            raise BizError(40410, "会话不存在")

    # ---- 预扣（封顶），失败则后面不再执行 ----
    with engine.begin() as conn:
        svc.preauth(conn, uid, "AI_CHAT", msg_id, remark="AI 问答预扣")

    total_in = total_out = 0
    cards: list = []
    rounds = 0
    charged_tools: list = []      # 记录已扣的工具费，异常时逐笔退回

    try:
        with engine.begin() as conn:
            conv_id = body.conversation_id
            if not conv_id:
                cur = conn.execute(text(
                    "INSERT INTO ai_conversations (user_id, title) VALUES (:u, :t)"
                ), {"u": uid, "t": text_in[:30]})
                conv_id = cur.lastrowid
            # 库里只存文字 + 附件名：图片 base64 落库会把表撑爆，模型也只需当轮看到
            att_note = upload_svc.summary(body.attachments)
            stored = (text_in + ("\n" + att_note if att_note else "")).strip()
            conn.execute(text(
                "INSERT INTO ai_messages (conversation_id, role, content) VALUES (:c,'user',:m)"
            ), {"c": conv_id, "m": stored})
            history = conn.execute(text(
                "SELECT role, content FROM ai_messages WHERE conversation_id=:c "
                "AND role IN ('user','assistant') ORDER BY id DESC LIMIT :n"
            ), {"c": conv_id, "n": MAX_HISTORY_TURNS * 2}).all()

        hist = [{"role": r[0], "content": r[1]} for r in reversed(history)]
        # 最后一条就是这次的提问：替换成「文字 + 图片」的多模态内容
        if hist and hist[-1]["role"] == "user":
            hist[-1]["content"] = upload_svc.build_user_content(text_in, body.attachments)
        messages = [{"role": "system", "content": SYSTEM_PROMPT}] + hist
        executor = ToolExecutor(context)
        final_text = ""
        produced = ""          # 模型真正产出的正文；空 = 没答出来，要退款

        # ============ 工具循环 ============
        while rounds < settings.AI_MAX_ROUNDS:
            rounds += 1
            resp = mimo.chat(messages, tools=TOOLS, max_tokens=2000)
            total_in += resp.prompt_tokens
            total_out += resp.completion_tokens

            if not resp.has_tool_calls:
                final_text = resp.content
                if (resp.content or "").strip():
                    produced = resp.content.strip()
                break

            messages.append(resp.assistant_message())
            for call in resp.tool_calls:
                # 工具单独计价，不让模型决定价格
                price = TOOL_PRICE.get(call.name, 0)
                if price > 0:
                    try:
                        with engine.begin() as conn:
                            svc.consume(conn, uid, price, call.name.upper(), msg_id,
                                        idem=f"tool:{msg_id}:{call.id}",
                                        remark=f"调用工具 {call.name}")
                        # 只有真正扣成功才记账，后续崩溃时按这张清单逐笔退
                        charged_tools.append({"idem": f"tool:{msg_id}:{call.id}",
                                              "points": price, "name": call.name})
                    except Exception as e:
                        # 积分不足时明确告诉模型，让它换个不需要花钱的回答方式
                        messages.append({
                            "role": "tool", "tool_call_id": call.id,
                            "content": json.dumps(
                                {"error": "用户积分不足，无法执行该工具。请直接说明情况，"
                                          "并告知用户可以充值后再试。"},
                                ensure_ascii=False),
                        })
                        continue

                result = executor.execute(call.name, call.args, user_id=uid)
                if result.ui_card:
                    cards.append({"type": result.ui_card, "tool": call.name,
                                  "data": result.payload})
                messages.append({
                    "role": "tool", "tool_call_id": call.id,
                    "content": result.for_model(),
                })
                agent_log.record(
                    "product", f"tool_{call.name}",
                    "SUCCESS" if result.ok else "FAILED",
                    biz_id=msg_id,
                    output_digest=json.dumps(result.payload, ensure_ascii=False)[:250]
                    if result.ok else "",
                    error_msg=result.error,
                )
        else:
            final_text = "这个问题我需要查的资料比较多，为了控制成本先到这里。你可以换个更具体的问法。"

        # ============ 合规终审 ============
        # 模型没产出正文（空回答）就不收钱：预扣全退，工具费也逐笔退
        no_answer = not produced
        if no_answer:
            final_text = ("这次没生成出回答（模型没有返回内容）。"
                          "本次积分已全额退回，换个问法再试一次。")

        review = critic.review(final_text, biz_id=msg_id)
        final_text = review.text

        # ============ 按实结算，多退不补 ============
        with engine.begin() as conn:
            if no_answer:
                entry = svc.fail_release(conn, uid, "AI_CHAT", msg_id)
                refunded_total = 0 if entry.duplicated else entry.amount
                for t in charged_tools:
                    try:
                        e2 = svc.refund(conn, uid, t["points"], source_idem=t["idem"],
                                        remark="AI 未生成回答，工具费退回")
                        if not e2.duplicated:
                            refunded_total += t["points"]
                    except Exception:
                        log.exception("工具费退回失败，需人工核对 %s", t["idem"])
                settle = {"charged": 0, "refunded": refunded_total,
                          "balance": svc.get_balance(conn, uid)}
                agent_log.record("support", "ai_chat", "DEGRADED", biz_id=msg_id,
                                 tokens_in=total_in, tokens_out=total_out,
                                 error_msg="模型未产出正文，已全额退款")
            else:
                settle = svc.settle(conn, uid, "AI_CHAT", msg_id, total_in, total_out)
            conn.execute(text(
                "INSERT INTO ai_messages (conversation_id, role, content, points_cost, "
                " tokens_in, tokens_out) VALUES (:c,'assistant',:m,:p,:ti,:to)"
            ), {"c": conv_id, "m": final_text, "p": settle["charged"],
                "ti": total_in, "to": total_out})

        agent_log.record("support", "ai_chat", "SUCCESS", biz_id=msg_id,
                         tokens_in=total_in, tokens_out=total_out,
                         output_digest=final_text[:200])

        return ok({
            "conversation_id": conv_id,
            "answer": final_text,
            "cards": cards,
            "rounds": rounds,
            "charged": settle["charged"],
            "refunded": settle["refunded"],
            "balance": settle["balance"],
            "compliance": {"rounds": review.rounds, "degraded": review.degraded},
        })

    except Exception as e:
        # 任何失败都全额退款，用户不为我们的故障付费。
        log.exception("AI 对话失败")

        # ① 先退已扣的工具费（preauth 之外的独立扣费）
        for t in charged_tools:
            try:
                with engine.begin() as conn:
                    svc.refund(conn, uid, t["points"], source_idem=t["idem"],
                               remark=f"调用失败退回 {t['name']}")
            except Exception as re:
                # 退款失败必须留痕，不能因为日志问题让用户亏钱
                log.error("工具费退款失败 %s：%s", t["idem"], re)
                agent_log.record("finance", "tool_refund", "FAILED",
                                 biz_id=msg_id, error_msg=f"{t['idem']}: {re}")

        # ② 再退对话预扣
        try:
            with engine.begin() as conn:
                svc.fail_release(conn, uid, "AI_CHAT", msg_id)
        except Exception as re:
            log.error("预扣退回失败 %s：%s", msg_id, re)

        agent_log.record("support", "ai_chat", "FAILED", biz_id=msg_id, error_msg=str(e))
        raise BizError(50002, f"AI 调用失败，已扣积分全部退回：{e}")


TOOL_LABELS = {
    "search_strategies": "正在查策略库…",
    "list_symbols": "正在看有哪些股票…",
    "run_backtest": "正在用历史数据试跑…",
    "compare_strategies": "正在同时对比几个策略…",
    "analyze_pair": "正在分析两个策略的互补性…",
    "design_strategy": "正在按你的想法改造策略并回测…",
    "auto_search_factors": "正在按你的诉求筛出候选因子，再逐个回测验证…",
    "list_factors": "正在查因子库…",
    "prepare_deploy": "正在整理部署方案…",
    "search_web": "正在联网查资料…",
    "build_report": "正在生成网页报告…",
    "save_strategy": "正在保存到我的策略…",
    "save_factor_set": "正在保存到我的因子…",
    "publish_strategy": "正在发布到公共策略库（先跑一遍验证）…",
    "publish_factor": "正在把因子补进公共因子库…",
    "check_robustness": "正在做样本外和参数敏感性检验…",
    "suggest_params": "正在试不同的参数组合…",
    "explain_concept": "正在查知识库…",
}


@router.post("/chat/stream")
def chat_stream(body: ChatIn, user: dict = Depends(get_current_user)):
    """
    流式对话（SSE）。事件类型：
      start   会话已建立
      delta   正文增量
      reset   这一轮其实是工具调用，请清掉已显示的文字
      tool    正在调用某个工具（附中文提示）
      card    工具产出的卡片
      replace 合规改写后的全文
      done    完成，带扣费与余额
      error   出错（预扣已全额退回）
    """
    uid = user["id"]
    text_in = body.message.strip()

    if mimo.estimate_tokens(text_in) > MAX_INPUT_TOKENS["AI_CHAT"]:
        raise BizError(40006,
                       f"内容太长了，请分段提问，单次最多 {MAX_INPUT_TOKENS['AI_CHAT']} token")

    if body.conversation_id:
        with engine.connect() as conn:
            owner = conn.execute(text(
                "SELECT user_id FROM ai_conversations WHERE id=:c"
            ), {"c": body.conversation_id}).scalar()
        if owner != uid:
            raise BizError(40410, "会话不存在")

    msg_id = uuid.uuid4().hex[:12]
    svc = CreditsService(engine)
    with engine.begin() as conn:
        svc.preauth(conn, uid, "AI_CHAT", msg_id, remark="AI 问答预扣")

    def sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    def gen():
        total_in = total_out = 0
        cards: list = []
        rounds = 0
        charged_tools: list = []
        conv_id = body.conversation_id

        try:
            # 注意：生成器里不能长时间持有事务，每步单独开
            with engine.begin() as conn:
                if not conv_id:
                    cur = conn.execute(text(
                        "INSERT INTO ai_conversations (user_id, title) VALUES (:u,:t)"
                    ), {"u": uid, "t": text_in[:30]})
                    conv_id = cur.lastrowid
                # 库里只存文字 + 附件名，图片 base64 不落库
                att_note = upload_svc.summary(body.attachments)
                stored = (text_in + ("\n" + att_note if att_note else "")).strip()
                conn.execute(text(
                    "INSERT INTO ai_messages (conversation_id, role, content) VALUES (:c,'user',:m)"
                ), {"c": conv_id, "m": stored})
                history = conn.execute(text(
                    "SELECT role, content FROM ai_messages WHERE conversation_id=:c "
                    "AND role IN ('user','assistant') ORDER BY id DESC LIMIT :n"
                ), {"c": conv_id, "n": MAX_HISTORY_TURNS * 2}).all()

            yield sse({"type": "start", "conversation_id": conv_id})

            hist = [{"role": r[0], "content": r[1]} for r in reversed(history)]
            if hist and hist[-1]["role"] == "user":
                hist[-1]["content"] = upload_svc.build_user_content(text_in, body.attachments)
            messages = [{"role": "system", "content": SYSTEM_PROMPT}] + hist
            executor = ToolExecutor(context)
            final_text = ""
            emitted = False        # 本轮是否已推过正文
            produced = ""          # 模型真正产出的正文（空 = 没答出来，要退钱）
            last_reasoning = ""    # 最后一轮的思考内容，用于判断"是空转还是撞上限"
            retried = 0            # 思考吃光预算后的重试次数
            budget = 2000          # 单轮输出上限（思考也算在里面）

            while rounds < settings.AI_MAX_ROUNDS:
                rounds += 1
                resp = None
                for kind, data in mimo.chat_stream(messages, tools=TOOLS, max_tokens=budget):
                    if kind == "text":
                        emitted = True
                        yield sse({"type": "delta", "text": data})
                    elif kind == "thinking":
                        # 思考内容边产生边下发。这是用户唯一能看出"它在想、没卡住"的信号——
                        # 实测首段思考 13 秒才出来，首段正文要 26 秒，
                        # 之前这 26 秒界面上只有"正在理解你的问题…"，看着就是卡死。
                        yield sse({"type": "thinking", "text": data})
                    elif kind == "done":
                        resp = data
                if resp is None:
                    break

                total_in += resp.prompt_tokens
                total_out += resp.completion_tokens
                last_reasoning = resp.reasoning_content or last_reasoning

                if not resp.has_tool_calls:
                    # 正文一个字都没有，但撞到了长度上限 → 预算被思考吃光了。
                    # 这时候直接告诉用户"没生成"是浪费一次完整推理，
                    # 换成更大的预算重来一次，多数情况第二次就能出结论。
                    if ((not (resp.content or "").strip())
                            and resp.finish_reason == "length"
                            and retried < 1):
                        retried += 1
                        budget = 4000
                        messages.append({
                            "role": "user",
                            "content": "（你上一次在思考阶段就把输出长度用完了，正文一个字都没出来。"
                                       "这次请压缩思考，直接给结论，控制在 300 字以内。）",
                        })
                        yield sse({"type": "note",
                                   "text": "思考把长度预算用完了，正用更大的预算重试一次…"})
                        continue

                    final_text = resp.content
                    # 记录模型真正产出的正文：空回答要退款，不能靠 final_text 判断——
                    # 它下面会被兜底话术覆盖，那样就永远看不到"其实没答"
                    if (resp.content or "").strip():
                        produced = resp.content.strip()
                    break

                # 这一轮要调工具，刚推的正文属于"思考过程"，让前端清掉
                if emitted:
                    yield sse({"type": "reset"})
                    emitted = False
                messages.append(resp.assistant_message())

                for call in resp.tool_calls:
                    yield sse({"type": "tool", "name": call.name,
                               "label": TOOL_LABELS.get(call.name, call.name)})

                    price = TOOL_PRICE.get(call.name, 0)
                    if price > 0:
                        try:
                            with engine.begin() as conn:
                                svc.consume(conn, uid, price, call.name.upper(), msg_id,
                                            idem=f"tool:{msg_id}:{call.id}",
                                            remark=f"调用工具 {call.name}")
                            charged_tools.append({"idem": f"tool:{msg_id}:{call.id}",
                                                  "points": price, "name": call.name})
                        except Exception:
                            messages.append({
                                "role": "tool", "tool_call_id": call.id,
                                "content": json.dumps(
                                    {"error": "用户积分不足，无法执行。请直接说明情况并建议充值。"},
                                    ensure_ascii=False),
                            })
                            continue

                    result = executor.execute(call.name, call.args, user_id=uid)
                    if result.ui_card:
                        card = {"type": result.ui_card, "tool": call.name, "data": result.payload}
                        cards.append(card)
                        yield sse({"type": "card", "card": card})
                    messages.append({"role": "tool", "tool_call_id": call.id,
                                     "content": result.for_model()})

                    agent_log.record("product", f"tool_{call.name}",
                                     "SUCCESS" if result.ok else "FAILED", biz_id=msg_id,
                                     output_digest=json.dumps(result.payload, ensure_ascii=False)[:250]
                                     if result.ok else "",
                                     error_msg=result.error)
            else:
                final_text = "这个问题要查的资料比较多，先到这里。你可以换个更具体的问法。"

            if not final_text:
                final_text = "这次没生成出回答，换个问法再试一次？"

            # ---- 没答出来就不收钱 ----
            # 模型返回空正文（超时、被截断、只思考没说结论）时，用户什么都没得到，
            # 不能按 token 结算。预扣全额退回，已扣的工具费也逐笔退。
            no_answer = not produced
            if no_answer and "积分已" not in final_text:
                final_text = ("这次没生成出回答（模型没有返回内容）。"
                              "本次积分已全额退回，换个问法再试一次。")

            # 合规终审；被改写就整体替换前端已经显示的内容
            review = critic.review(final_text, biz_id=msg_id)
            if review.text != final_text:
                yield sse({"type": "replace", "text": review.text})
            final_text = review.text

            with engine.begin() as conn:
                if no_answer:
                    entry = svc.fail_release(conn, uid, "AI_CHAT", msg_id)
                    refunded_total = 0 if entry.duplicated else entry.amount
                    for t in charged_tools:
                        try:
                            e2 = svc.refund(conn, uid, t["points"], source_idem=t["idem"],
                                            remark="AI 未生成回答，工具费退回")
                            if not e2.duplicated:
                                refunded_total += t["points"]
                        except Exception:
                            log.exception("工具费退回失败，需人工核对 %s", t["idem"])
                    settle = {"charged": 0, "refunded": refunded_total,
                              "balance": svc.get_balance(conn, uid)}
                    agent_log.record("support", "ai_chat", "DEGRADED", biz_id=msg_id,
                                     tokens_in=total_in, tokens_out=total_out,
                                     error_msg="模型未产出正文，已全额退款")
                else:
                    settle = svc.settle(conn, uid, "AI_CHAT", msg_id, total_in, total_out)
                conn.execute(text(
                    "INSERT INTO ai_messages (conversation_id, role, content, points_cost, "
                    "tokens_in, tokens_out) VALUES (:c,'assistant',:m,:p,:ti,:to)"
                ), {"c": conv_id, "m": final_text, "p": settle["charged"],
                    "ti": total_in, "to": total_out})

            agent_log.record("support", "ai_chat", "SUCCESS", biz_id=msg_id,
                             tokens_in=total_in, tokens_out=total_out,
                             output_digest=final_text[:200])

            # 空回答时前面什么都没推出去，这里补一条提示，否则前端是一片空白
            if no_answer:
                yield sse({"type": "replace", "text": final_text})

            yield sse({"type": "done", "conversation_id": conv_id,
                       "charged": settle["charged"], "refunded": settle["refunded"],
                       "balance": settle["balance"], "rounds": rounds,
                       "compliance": {"rounds": review.rounds, "degraded": review.degraded}})

        except Exception as e:
            log.exception("流式对话失败")
            # 中途出错也要把钱退干净
            for t in charged_tools:
                try:
                    with engine.begin() as conn:
                        svc.refund(conn, uid, t["points"], source_idem=t["idem"],
                                   remark=f"调用失败退回 {t['name']}")
                except Exception as re:
                    log.error("工具费退款失败 %s：%s", t["idem"], re)
            try:
                with engine.begin() as conn:
                    svc.fail_release(conn, uid, "AI_CHAT", msg_id)
            except Exception as re:
                log.error("预扣退回失败 %s：%s", msg_id, re)
            agent_log.record("support", "ai_chat", "FAILED", biz_id=msg_id, error_msg=str(e))
            yield sse({"type": "error", "msg": f"AI 调用失败，积分已退回：{e}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",      # 穿透/Nginx 下禁止缓冲，否则会憋到最后才吐
    })


@router.get("/conversations")
def conversations(user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, title, updated_at FROM ai_conversations "
            "WHERE user_id=:u ORDER BY updated_at DESC LIMIT 20"
        ), {"u": user["id"]}).mappings().all()
    return ok([{**dict(r), "updated_at": str(r["updated_at"])} for r in rows])


@router.get("/conversations/{conv_id}/messages")
def messages(conv_id: int, user: dict = Depends(get_current_user)):
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT m.role, m.content, m.points_cost, m.created_at "
            "FROM ai_messages m JOIN ai_conversations c ON c.id=m.conversation_id "
            "WHERE m.conversation_id=:c AND c.user_id=:u ORDER BY m.id"
        ), {"c": conv_id, "u": user["id"]}).mappings().all()
    return ok([{**dict(r), "created_at": str(r["created_at"])} for r in rows])
