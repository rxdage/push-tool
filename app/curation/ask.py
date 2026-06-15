"""文献问答（RAG）：从收集的文献里语义检索 + Claude 引用作答。

dashboard /ask 与 CLI 共用 answer_question()。每次问答花少量 token（检索本地免费，答用 haiku）。

用法：
    python -m app.curation.ask "controlled dielectric breakdown 制孔的优缺点?"
"""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.curation.embed import embed_texts
from app.db import SessionLocal

ANSWER_MODEL = "claude-haiku-4-5"

SYSTEM = """你是基于"用户私人文献库"作答的研究助手。只依据下面提供的检索片段回答；
片段不足以回答时，如实说"现有文献不足以回答"，不要编造。
用中文、简洁作答；每个具体结论后用 [n] 标注来源编号。结尾不要再列参考文献（系统会附上）。"""


@dataclass
class Source:
    n: int
    title: str
    url: str | None
    year: int | None


async def _retrieve(
    session: AsyncSession, query_vec: list[float], k: int, feed: str | None
) -> list[Source]:
    vec_literal = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
    feed_clause = ""
    params = {"vec": vec_literal, "k": k}
    if feed:
        feed_clause = """
          AND i.source_id IN (
            SELECT s.id FROM sources s JOIN subscriptions sub ON s.subscription_id=sub.id
            WHERE sub.feed_type = :feed)"""
        params["feed"] = feed
    sql = sql_text(
        f"""
        SELECT i.id, i.title, i.url, i.abstract, i.published_at
        FROM items i
        WHERE i.embedding IS NOT NULL AND i.abstract IS NOT NULL {feed_clause}
        ORDER BY i.embedding <=> CAST(:vec AS vector)
        LIMIT :k
        """
    )
    rows = (await session.execute(sql, params)).all()
    out = []
    for n, (iid, title, url, abstract, pub) in enumerate(rows, 1):
        out.append(
            (
                Source(n=n, title=title, url=url, year=(pub.year if pub else None)),
                abstract,
            )
        )
    return out


def _context(pairs) -> str:
    blocks = []
    for src, abstract in pairs:
        blocks.append(
            f"[{src.n}] ({src.year or '?'}) {src.title}\n{(abstract or '')[:700]}"
        )
    return "\n\n".join(blocks)


async def answer_question(
    session: AsyncSession,
    question: str,
    k: int = 8,
    feed: str | None = "academic",
    model: str = ANSWER_MODEL,
) -> dict:
    import anthropic

    qvec = (await embed_texts([question]))[0]
    pairs = await _retrieve(session, qvec, k, feed)
    if not pairs:
        return {"answer": "文献库里没有可检索的内容。", "sources": []}

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=model,
        max_tokens=1200,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": f"问题：{question}\n\n检索到的文献片段：\n{_context(pairs)}",
            }
        ],
    )
    answer = next((b.text for b in resp.content if b.type == "text"), "")
    sources = [vars(src) for src, _ in pairs]
    return {
        "answer": answer,
        "sources": sources,
        "usage": {
            "input_tokens": resp.usage.input_tokens,
            "output_tokens": resp.usage.output_tokens,
        },
    }


async def _main(question: str) -> None:
    async with SessionLocal() as session:
        res = await answer_question(session, question)
    print(res["answer"])
    print("\n— 来源 —")
    for s in res["sources"]:
        print(f"  [{s['n']}] ({s['year'] or '?'}) {s['title']}  {s['url'] or ''}")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("question")
    args = p.parse_args()
    asyncio.run(_main(args.question))
