"""抓取层基类：SourceAdapter ABC + RawItem。

每个 adapter 只负责 fetch() -> list[RawItem]；归一化/去重/入库交给 store.py。
"""
from __future__ import annotations

import calendar
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class RawItem:
    """源产出的归一化原始条目（未入库）。"""

    external_id: str | None = None
    url: str | None = None
    title: str = ""
    abstract: str | None = None
    raw_text: str | None = None
    published_at: datetime | None = None
    tags: list[str] = field(default_factory=list)
    raw_json: dict = field(default_factory=dict)


class SourceAdapter(ABC):
    """所有源适配器的接口。config 来自 Source.config_json。"""

    kind: str = "base"

    def __init__(self, config: dict | None = None, *, settings=None):
        self.config = config or {}
        self.settings = settings  # 注入 app.config.settings（拿 API key/email 等）

    @property
    def name(self) -> str:
        return self.config.get("name") or self.kind

    @abstractmethod
    async def fetch(self) -> list[RawItem]:
        """拉取并返回归一化条目。失败应抛异常，由调用方记录。"""
        raise NotImplementedError


def struct_time_to_dt(st: time.struct_time | None) -> datetime | None:
    """feedparser 的 *_parsed（UTC struct_time）-> tz-aware datetime。"""
    if not st:
        return None
    return datetime.fromtimestamp(calendar.timegm(st), tz=timezone.utc)
