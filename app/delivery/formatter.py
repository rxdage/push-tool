"""digest -> 飞书消息卡片 / dashboard payload。

中性视图 DigestView 解耦 pipeline 与渠道；新渠道（企业微信/服务号/邮件）只加 builder，
不动 pipeline。版权红线：只展示改写后的 summary，不堆原文。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Digest, DigestItem, Item, Subscription

LABEL_TEXT = {"action": "🔴 行动", "watch": "🟡 关注"}


@dataclass
class ItemView:
    title: str
    url: str | None
    summary: str
    classification: str | None
    action_label: str | None
    why_it_matters: str | None


@dataclass
class DigestView:
    subscription_name: str
    date_str: str
    deep: list[ItemView] = field(default_factory=list)
    brief: list[ItemView] = field(default_factory=list)
    classic: list[ItemView] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.deep) + len(self.brief) + len(self.classic)


async def build_view(session: AsyncSession, digest: Digest) -> DigestView:
    sub = await session.get(Subscription, digest.subscription_id)
    rows = (
        await session.execute(
            select(DigestItem, Item)
            .join(Item, DigestItem.item_id == Item.id)
            .where(DigestItem.digest_id == digest.id)
            .order_by(DigestItem.bucket, DigestItem.rank)
        )
    ).all()

    view = DigestView(
        subscription_name=sub.name if sub else "Digest",
        date_str=(digest.run_at.strftime("%Y-%m-%d") if digest.run_at else ""),
    )
    for di, it in rows:
        iv = ItemView(
            title=it.title or "(无标题)",
            url=it.url,
            summary=di.summary,
            classification=di.classification,
            action_label=di.action_label,
            why_it_matters=di.why_it_matters,
        )
        getattr(view, di.bucket).append(iv)
    return view


# ---- 飞书卡片（schema 2.0） ----

def _title_md(iv: ItemView) -> str:
    if iv.url:
        return f"[{iv.title}]({iv.url})"
    return iv.title


def _deep_block(iv: ItemView) -> str:
    lines = [f"**{_title_md(iv)}**"]
    if iv.action_label and iv.action_label in LABEL_TEXT:
        lines.append(LABEL_TEXT[iv.action_label])
    lines.append(iv.summary)
    if iv.why_it_matters:
        lines.append(f"💡 {iv.why_it_matters}")
    return "\n".join(lines)


def _brief_line(iv: ItemView) -> str:
    label = ""
    if iv.action_label and iv.action_label in LABEL_TEXT:
        label = LABEL_TEXT[iv.action_label] + " "
    return f"- {label}{_title_md(iv)} — {iv.summary}"


def _elements(view: DigestView) -> list[dict]:
    els: list[dict] = []

    def section(heading: str, body_md: str):
        els.append({"tag": "markdown", "content": f"**{heading}**"})
        els.append({"tag": "markdown", "content": body_md})
        els.append({"tag": "hr"})

    if view.deep:
        for iv in view.deep:
            els.append({"tag": "markdown", "content": _deep_block(iv)})
            els.append({"tag": "hr"})
    if view.brief:
        section("简报", "\n".join(_brief_line(iv) for iv in view.brief))
    if view.classic:
        section("经典回顾", "\n".join(_brief_line(iv) for iv in view.classic))

    if els and els[-1].get("tag") == "hr":
        els.pop()
    if not els:
        els.append({"tag": "markdown", "content": "本期没有命中精华条目。"})
    return els


def build_feishu_card(view: DigestView) -> dict:
    return {
        "schema": "2.0",
        "header": {
            "title": {
                "tag": "plain_text",
                "content": f"{view.subscription_name} · {view.date_str}",
            }
        },
        "body": {"elements": _elements(view)},
    }


def build_text(view: DigestView) -> str:
    """降级用纯文本。"""
    out = [f"{view.subscription_name} · {view.date_str}", ""]
    if view.deep:
        out.append("【深度】")
        for iv in view.deep:
            out.append(f"· {iv.title}")
            if iv.url:
                out.append(f"  {iv.url}")
            label = LABEL_TEXT.get(iv.action_label, "") if iv.action_label else ""
            out.append(f"  {label} {iv.summary}".strip())
            if iv.why_it_matters:
                out.append(f"  💡 {iv.why_it_matters}")
    if view.brief:
        out.append("\n【简报】")
        for iv in view.brief:
            out.append(f"· {iv.title} — {iv.summary}")
    if view.classic:
        out.append("\n【经典回顾】")
        for iv in view.classic:
            out.append(f"· {iv.title} — {iv.summary}")
    return "\n".join(out)


def to_dashboard_payload(view: DigestView) -> dict:
    """dashboard / API JSON。"""
    def items(lst: list[ItemView]):
        return [vars(iv) for iv in lst]

    return {
        "subscription": view.subscription_name,
        "date": view.date_str,
        "deep": items(view.deep),
        "brief": items(view.brief),
        "classic": items(view.classic),
    }
