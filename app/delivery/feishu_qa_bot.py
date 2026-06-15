"""飞书问答 bot（应用机器人 · 长连接 WebSocket）。@机器人提问 → RAG 作答。

省钱闸（全部可在 .env 调）：
  ① 群里只在 @机器人 时响应（私聊直接答）；
  ② 每人每天上限 QA_DAILY_LIMIT_PER_USER；
  ③ 全群每天上限 QA_DAILY_LIMIT_TOTAL；
  ④ 月度预算硬开关 QA_MONTHLY_BUDGET_USD（到额停答，下月恢复）；
  ⑤ 默认 haiku + 限制输出长度。
用量记 qa_logs 表（重启不丢，预算/上限据此统计）。

启用步骤：
  1. open.feishu.cn 开发者后台 → 创建「企业自建应用」→ 开启「机器人」能力；
  2. 权限：im:message（接收+发送消息）；事件订阅：im.message.receive_v1，方式选「长连接」；
  3. 发布应用，把机器人加进目标群；
  4. .env 填 FEISHU_APP_ID / FEISHU_APP_SECRET；
  5. docker compose --profile qa up -d qa-bot

依赖：lark-oapi（已在 pyproject）。
注：长连接为出站连接，无需公网/备案，本机即可跑。
首次启用若报 lark-oapi 接口名差异，按所装版本微调本文件 main() 内的注册/发送调用。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.config import settings
from app.curation.ask import answer_question
from app.db import SessionLocal
from app.models import QaLog

# 每百万 token 价（USD）：(input, output)
PRICING = {
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-8": (5.0, 25.0),
}
_MENTION = re.compile(r"@_(?:user_\d+|all)\s*")


def _cost(model: str, tin: int, tout: int) -> float:
    pin, pout = PRICING.get(model, PRICING["claude-haiku-4-5"])
    return tin / 1e6 * pin + tout / 1e6 * pout


def _day_start(tz) -> datetime:
    n = datetime.now(tz)
    return datetime(n.year, n.month, n.day, tzinfo=tz)


def _month_start(tz) -> datetime:
    n = datetime.now(tz)
    return datetime(n.year, n.month, 1, tzinfo=tz)


async def _guard(session, user_id: str) -> str | None:
    """返回拒答原因（None = 放行）。"""
    tz = ZoneInfo(settings.tz_default)
    spent = (
        await session.execute(
            select(func.coalesce(func.sum(QaLog.cost_usd), 0.0)).where(
                QaLog.created_at >= _month_start(tz)
            )
        )
    ).scalar_one()
    if spent >= settings.qa_monthly_budget_usd:
        return f"本月问答预算（${settings.qa_monthly_budget_usd:.0f}）已用完，下月自动恢复。"

    day = _day_start(tz)
    total = (
        await session.execute(
            select(func.count(QaLog.id)).where(
                QaLog.answered.is_(True), QaLog.created_at >= day
            )
        )
    ).scalar_one()
    if total >= settings.qa_daily_limit_total:
        return "今天全群问答额度已用完，明天再来～"

    mine = (
        await session.execute(
            select(func.count(QaLog.id)).where(
                QaLog.answered.is_(True),
                QaLog.user_id == user_id,
                QaLog.created_at >= day,
            )
        )
    ).scalar_one()
    if mine >= settings.qa_daily_limit_per_user:
        return f"你今天提问已达上限（{settings.qa_daily_limit_per_user} 次），明天再问吧。"
    return None


async def handle_question(chat_id: str, user_id: str, question: str) -> str:
    """守闸 → RAG 作答 → 记 qa_logs。返回要回群里的文本。"""
    async with SessionLocal() as session:
        reason = await _guard(session, user_id)
        if reason:
            session.add(
                QaLog(chat_id=chat_id, user_id=user_id, question=question, answered=False)
            )
            await session.commit()
            return reason

        res = await answer_question(session, question, model=settings.qa_model)
        u = res.get("usage", {})
        tin, tout = u.get("input_tokens", 0), u.get("output_tokens", 0)
        session.add(
            QaLog(
                chat_id=chat_id,
                user_id=user_id,
                question=question,
                answered=True,
                tokens_in=tin,
                tokens_out=tout,
                cost_usd=_cost(settings.qa_model, tin, tout),
            )
        )
        await session.commit()

        ans = res["answer"]
        srcs = res.get("sources", [])[:6]
        if srcs:
            lines = "\n".join(
                f"[{s['n']}] {s['title']}" + (f"  {s['url']}" if s.get("url") else "")
                for s in srcs
            )
            ans = f"{ans}\n\n— 来源 —\n{lines}"
        return ans


def _extract_text(content: str) -> str:
    try:
        text = json.loads(content).get("text", "")
    except (ValueError, AttributeError):
        text = ""
    return _MENTION.sub("", text).strip()


def main() -> None:
    import lark_oapi as lark
    from lark_oapi.api.im.v1 import (
        CreateMessageRequest,
        CreateMessageRequestBody,
        P2ImMessageReceiveV1,
    )

    if not (settings.feishu_app_id and settings.feishu_app_secret):
        raise SystemExit("未配置 FEISHU_APP_ID / FEISHU_APP_SECRET，无法启动问答 bot。")

    api = (
        lark.Client.builder()
        .app_id(settings.feishu_app_id)
        .app_secret(settings.feishu_app_secret)
        .build()
    )

    def on_message(data: P2ImMessageReceiveV1) -> None:
        msg = data.event.message
        # 群里只在被 @ 时响应；私聊直接答
        if msg.chat_type == "group" and not (msg.mentions or []):
            return
        if msg.message_type != "text":
            return
        question = _extract_text(msg.content)
        if not question:
            return
        chat_id = msg.chat_id
        user_id = data.event.sender.sender_id.open_id
        try:
            reply = asyncio.run(handle_question(chat_id, user_id, question))
        except Exception as e:  # noqa: BLE001
            reply = f"抱歉，出错了：{e}"
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(lark.JSON.marshal({"text": reply}))
                .build()
            )
            .build()
        )
        api.im.v1.message.create(req)

    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(on_message)
        .build()
    )
    client = lark.ws.Client(
        settings.feishu_app_id,
        settings.feishu_app_secret,
        event_handler=handler,
        log_level=lark.LogLevel.INFO,
    )
    print("[qa-bot] 飞书问答 bot 启动（长连接）。在群里 @机器人 提问即可。")
    client.start()


if __name__ == "__main__":
    main()
