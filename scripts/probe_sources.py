"""探测候选抓取源是否可用（只读，不写库）。

对 rss 候选跑 feedparser（和 RssAdapter 同样的 UA/超时），对 html/api 候选发 GET，
打印状态、条目数、前几条标题，用来决定要不要加进 sources yaml。

    docker compose run --rm app python scripts/probe_sources.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import feedparser  # noqa: E402
import httpx  # noqa: E402

from app.ingestion.rss import FETCH_TIMEOUT_S, USER_AGENT  # noqa: E402

RSS_CANDIDATES = [
    # 本业 / 竞对
    ("Silson (SiN 窗/膜)", "https://www.silson.com/feed/"),
    ("SPI Supplies", "https://www.2spi.com/blog/feed/"),
    ("Agar Scientific", "https://www.agarscientific.com/blog/feed"),
    ("EMS (Electron Microscopy Sciences)", "https://www.emsdiasum.com/feed"),
    ("Pacific Grid-Tech", "https://www.pacificgridtech.com/feed/"),
    ("Dune Sciences", "https://www.dunesciences.com/feed/"),
    ("Micro to Nano", "https://www.microtonano.com/feed/"),
    ("TEMwindows (SiMPore)", "https://www.temwindows.com/blog/feed/"),
    ("Norcada news", "https://www.norcada.com/feed/"),
    # 电镜 / 科学仪器行业媒体
    ("Microscopy Today", "https://www.cambridge.org/core/rss/product/id/A4B0E5D0F2E2C0F0"),
    ("SelectScience 新闻", "https://www.selectscience.net/rss/news.xml"),
    ("Labmate Online", "https://www.labmate-online.com/rss/news"),
    ("Phys.org nanotech", "https://phys.org/rss-feed/nanotech-news/"),
    ("AZoNano", "https://www.azonano.com/syndication.axd?format=rss"),
    ("AZoM 材料", "https://www.azom.com/syndication.axd?format=rss"),
    # 中文：国产替代 / 采购线索
    ("仪器信息网 资讯", "https://www.instrument.com.cn/rss/news.xml"),
    ("分析测试百科网", "https://www.antpedia.com/rss/news.xml"),
    ("中国粉体网 纳米", "https://www.cnpowder.com.cn/rss.xml"),
    # Google News 定向补充（竞对公司名）
    (
        "Google News 竞对公司名",
        "https://news.google.com/rss/search?q=%22Norcada%22+OR+%22SiMPore%22+OR+%22Quantifoil%22"
        "+OR+%22Protochips%22+OR+%22DENSsolutions%22+OR+%22Ted+Pella%22&hl=en-US&gl=US&ceid=US:en",
    ),
    (
        "Google News 电镜国产替代/采购",
        "https://news.google.com/rss/search?q=%E9%80%8F%E5%B0%84%E7%94%B5%E9%95%9C+OR+%E7%94%B5%E9%95%9C"
        "+OR+%22%E5%86%B7%E5%86%BB%E7%94%B5%E9%95%9C%22+%E9%87%87%E8%B4%AD+OR+%E5%9B%BD%E4%BA%A7"
        "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans",
    ),
    (
        "Google News 氮化硅膜/纳米孔",
        "https://news.google.com/rss/search?q=%22silicon+nitride+membrane%22+OR+%22TEM+grid%22"
        "+OR+%22cryo-EM+grid%22+OR+%22nanopore+chip%22&hl=en-US&gl=US&ceid=US:en",
    ),
]

HTTP_CANDIDATES = [
    ("Crossref ACS Nano (ISSN)", "https://api.crossref.org/journals/1936-086X/works?rows=5&sort=published&order=desc"),
    ("Crossref Nano Letters", "https://api.crossref.org/journals/1530-6992/works?rows=5&sort=published&order=desc"),
    ("Crossref ACS Sensors", "https://api.crossref.org/journals/2379-3694/works?rows=5&sort=published&order=desc"),
]


async def probe_rss(name: str, url: str) -> None:
    try:
        parsed = await asyncio.wait_for(
            asyncio.to_thread(feedparser.parse, url, agent=USER_AGENT),
            timeout=FETCH_TIMEOUT_S,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {name}: {type(e).__name__} {e}")
        return
    n = len(parsed.entries)
    if not n:
        bozo = parsed.get("bozo_exception")
        print(f"  ✗ {name}: 0 条 (bozo={bozo})")
        return
    titles = [(e.get('title') or '')[:52] for e in parsed.entries[:2]]
    print(f"  ✓ {name}: {n} 条 | {' / '.join(titles)}")


async def probe_http(name: str, url: str) -> None:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
            r = await c.get(url, headers={"User-Agent": USER_AGENT})
        ct = (r.headers.get("content-type") or "")[:30]
        ok = "✓" if r.status_code == 200 else "✗"
        extra = ""
        if r.status_code == 200 and "json" in ct:
            try:
                items = r.json()["message"]["items"]
                extra = f" | {len(items)} 条 | " + (items[0].get("title") or [""])[0][:50]
            except Exception:  # noqa: BLE001
                extra = " | JSON 结构不符预期"
        print(f"  {ok} {name}: HTTP {r.status_code} {ct}{extra}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ {name}: {type(e).__name__} {e}")


async def main() -> None:
    print("== RSS 候选 ==")
    await asyncio.gather(*(probe_rss(n, u) for n, u in RSS_CANDIDATES))
    print("\n== HTTP/API 候选 ==")
    await asyncio.gather(*(probe_http(n, u) for n, u in HTTP_CANDIDATES))


if __name__ == "__main__":
    asyncio.run(main())
