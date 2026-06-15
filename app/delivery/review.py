"""半月度领域综述 → 飞书（个人群 + 公司群）。

复用 distill 的蒸馏正文，包成卡片推两个群；推送本身 0 额外 token（只重生成 skill 那步花）。

用法：
    python -m app.delivery.review --dry-run   # 只看会发什么，不发送
    python -m app.delivery.review             # 真发到两个群
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import Settings
from app.config import settings as default_settings
from app.curation.distill import distill
from app.delivery.feishu_bot import FeishuBot

# (feed_type, skill 名, 推送标题用的领域名)
REVIEW_TARGETS = [
    ("academic", "nanopore-radar", "固态纳米孔"),
    ("industry", "sin-membrane-radar", "超薄SiN膜/电镜耗材"),
]


def _groups(settings: Settings) -> list[tuple[str, str, str]]:
    """返回 (群名, webhook, secret) —— 个人群 + 公司群（配了才算）。"""
    out = []
    if settings.feishu_webhook_url:
        out.append(("个人群", settings.feishu_webhook_url, settings.feishu_webhook_secret))
    if settings.feishu_webhook_url_company:
        out.append(
            ("公司群", settings.feishu_webhook_url_company, settings.feishu_webhook_secret_company)
        )
    return out


async def push_review(
    settings: Settings | None = None, recent: int = 120, dry_run: bool = False
) -> list[dict]:
    settings = settings or default_settings
    date_str = datetime.now(ZoneInfo(settings.tz_default)).strftime("%Y-%m-%d")
    groups = _groups(settings)
    results: list[dict] = []

    for feed, name, display in REVIEW_TARGETS:
        res = await distill(feed, recent, name, Path("skills") / name)
        if res.get("status") != "ok" or not res.get("body"):
            results.append({"skill": name, "status": res.get("status"), "reason": res.get("reason")})
            continue

        title = f"{display} · 半月综述 · {date_str}"
        body = f"> 基于 {res['items']} 条策展文献蒸馏（改写归纳 + 链接）\n\n{res['body']}"

        if dry_run:
            results.append(
                {"skill": name, "title": title, "chars": len(body),
                 "groups": [g[0] for g in groups]}
            )
            continue

        sent = {}
        for gname, url, secret in groups:
            try:
                r = await FeishuBot(url, secret).send_markdown(title, body)
                sent[gname] = f"ok×{r.get('parts')}"
            except Exception as e:  # noqa: BLE001
                sent[gname] = f"err:{e}"
        results.append({"skill": name, "title": title, "sent": sent})

    return results


async def _main(dry_run: bool) -> None:
    for r in await push_review(default_settings, dry_run=dry_run):
        print(r)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    asyncio.run(_main(args.dry_run))
