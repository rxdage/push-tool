"""探测第二轮：修正后的 Google News 定向 query（第一轮竞对公司名撞名严重）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402

from app.ingestion.rss import FETCH_TIMEOUT_S, USER_AGENT  # noqa: E402


def gnews(q: str, lang: str = "en") -> str:
    tail = "hl=en-US&gl=US&ceid=US:en" if lang == "en" else "hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    return f"https://news.google.com/rss/search?q={quote(q)}&{tail}"


CANDIDATES = [
    (
        "竞对+限定词(en)",
        gnews(
            '("Norcada" OR "SiMPore" OR "Quantifoil" OR "Protochips" OR "DENSsolutions" '
            'OR "Ted Pella" OR "Norcada") AND (TEM OR microscopy OR membrane OR nanopore '
            'OR "silicon nitride" OR cryo-EM)'
        ),
    ),
    (
        "电镜招投标/采购(zh)",
        gnews("(透射电镜 OR 扫描电镜 OR 冷冻电镜) AND (招标 OR 采购 OR 中标 OR 验收)", "zh"),
    ),
    (
        "微纳代工/MPW(zh)",
        gnews("(MEMS代工 OR 微纳加工 OR MPW OR 流片) AND (氮化硅 OR 薄膜 OR 刻蚀 OR 键合)", "zh"),
    ),
    (
        "cryo-EM 载网/耗材(en)",
        gnews('("cryo-EM grid" OR "TEM support film" OR "holey carbon" OR "graphene grid")'),
    ),
    (
        "X-ray 窗/探测器(en)",
        gnews('("x-ray window" OR "silicon nitride window") AND (detector OR spectroscopy OR vacuum)'),
    ),
    (
        "液体池/原位TEM(en)",
        gnews('("in situ TEM" OR "liquid cell TEM" OR "liquid-cell electron microscopy")'),
    ),
]


async def probe(name: str, url: str) -> None:
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(feedparser.parse, url, agent=USER_AGENT),
            timeout=FETCH_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        print(f"✗ {name}: {type(e).__name__} {e}")
        return
    n = len(parsed.entries)
    print(f"{'✓' if n else '✗'} {name}: {n} 条")
    for e in parsed.entries[:4]:
        print(f"     · {(e.get('title') or '')[:78]}")
    print(f"   url={url}")


async def main() -> None:
    for n, u in CANDIDATES:
        await probe(n, u)


if __name__ == "__main__":
    asyncio.run(main())
