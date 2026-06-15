"""无 feed 的页面抓取（守 robots + 限速 + 关键词过滤）。

从列表页提取锚点链接，按 config.keywords 过滤链接文字，产出候选条目。
正文留待 pipeline 阶段按需用 trafilatura 抽取，避免一次抓全站。

config:
  url: 列表页地址（必填）
  keywords: 链接文字命中其一才保留（为空则全留）
  respect_robots: 默认 True
  rate_limit_s: 抓取后 sleep 秒数，默认 3
  max_results: 默认 40
"""
from __future__ import annotations

import asyncio
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from app.ingestion.base import RawItem, SourceAdapter

USER_AGENT = "push-tool/0.1 (personal digest bot)"


async def _allowed_by_robots(client: httpx.AsyncClient, url: str) -> bool:
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url, timeout=10)
        if resp.status_code >= 400:
            return True  # 没有 robots 视为允许
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except httpx.HTTPError:
        return True  # 取不到 robots 不阻断，但仍限速


class HtmlScrapeAdapter(SourceAdapter):
    kind = "html"

    async def fetch(self) -> list[RawItem]:
        url = self.config.get("url")
        if not url:
            raise ValueError(f"html source {self.name!r} 缺少 config.url")
        keywords = [k.lower() for k in (self.config.get("keywords") or [])]
        respect_robots = self.config.get("respect_robots", True)
        rate_limit_s = float(self.config.get("rate_limit_s", 3))
        max_results = int(self.config.get("max_results", 40))

        async with httpx.AsyncClient(
            timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True
        ) as client:
            if respect_robots and not await _allowed_by_robots(client, url):
                raise PermissionError(f"robots 禁止抓取 {url}")
            resp = await client.get(url)
            resp.raise_for_status()
            html = resp.text
            await asyncio.sleep(rate_limit_s)

        soup = await asyncio.to_thread(BeautifulSoup, html, "html.parser")
        items: list[RawItem] = []
        seen: set[str] = set()
        for a in soup.find_all("a", href=True):
            text = a.get_text(strip=True)
            if not text or len(text) < 6:
                continue
            if keywords and not any(k in text.lower() for k in keywords):
                continue
            href = urljoin(url, a["href"])
            if href in seen:
                continue
            seen.add(href)
            items.append(
                RawItem(
                    external_id=href,
                    url=href,
                    title=text,
                    raw_json={"source_name": self.name, "scraped_from": url},
                )
            )
            if len(items) >= max_results:
                break
        return items
