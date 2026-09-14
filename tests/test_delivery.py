"""Phase 4 纯逻辑单测：formatter 卡片 + 飞书签名 + 分片 + 推送自愈判定。不发网络。"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch

import anthropic
import httpx

from app.delivery import selfheal
from app.delivery.feishu_bot import _chunk_cards, gen_sign
from app.delivery.formatter import (
    DigestView,
    ItemView,
    build_feishu_card,
    build_text,
    to_dashboard_payload,
)


def _view():
    return DigestView(
        subscription_name="行业日报",
        date_str="2026-06-15",
        deep=[
            ItemView(
                title="某厂 SiN 膜量产",
                url="https://ex.com/a",
                summary="改写摘要A",
                classification="new",
                action_label="action",
                why_it_matters="影响采购",
            )
        ],
        brief=[
            ItemView("简报1", "https://ex.com/b", "摘要B", "new", "watch", None),
            ItemView("简报2", None, "摘要C", "new", None, None),
        ],
        classic=[],
    )


# ---- 签名 ----

def test_gen_sign_matches_feishu_algorithm():
    ts = 1599360473
    secret = "mysecret"
    expected = base64.b64encode(
        hmac.new(f"{ts}\n{secret}".encode(), b"", hashlib.sha256).digest()
    ).decode()
    assert gen_sign(ts, secret) == expected
    # 32 字节 HMAC -> 44 字符 base64
    assert len(gen_sign(ts, secret)) == 44


# ---- 卡片结构 ----

def test_build_feishu_card_schema_and_header():
    card = build_feishu_card(_view())
    assert card["schema"] == "2.0"
    assert card["header"]["title"]["content"] == "行业日报 · 2026-06-15"
    assert card["body"]["elements"]
    # 深度条目应含标题链接与行动标签
    blob = json.dumps(card, ensure_ascii=False)
    assert "[某厂 SiN 膜量产](https://ex.com/a)" in blob
    assert "行动" in blob
    assert "影响采购" in blob


def test_card_no_trailing_hr():
    card = build_feishu_card(_view())
    assert card["body"]["elements"][-1].get("tag") != "hr"


def test_empty_view_card_has_placeholder():
    card = build_feishu_card(DigestView("空", "2026-06-15"))
    blob = json.dumps(card, ensure_ascii=False)
    assert "没有命中" in blob


def test_build_text_contains_sections():
    txt = build_text(_view())
    assert "【深度】" in txt and "【简报】" in txt
    assert "https://ex.com/a" in txt


def test_dashboard_payload_shape():
    p = to_dashboard_payload(_view())
    assert p["subscription"] == "行业日报"
    assert len(p["deep"]) == 1 and len(p["brief"]) == 2
    assert p["deep"][0]["why_it_matters"] == "影响采购"


# ---- 分片 ----

def test_chunk_small_card_single():
    card = build_feishu_card(_view())
    assert len(_chunk_cards(card)) == 1


def test_chunk_large_card_splits():
    big = DigestView("大", "2026-06-15")
    big.brief = [
        ItemView(f"标题{i}", f"https://ex.com/{i}", "摘要" * 100, "new", None, None)
        for i in range(200)
    ]
    card = build_feishu_card(big)
    chunks = _chunk_cards(card, max_bytes=4000)
    assert len(chunks) > 1
    # 每片都带同一 header，且不超限
    for c in chunks:
        assert c["header"]["title"]["content"] == "大 · 2026-06-15"
        assert len(json.dumps(c, ensure_ascii=False).encode()) <= 4000 + 500


# ---- 推送自愈 ----

def test_heal_action_reruns_missing_or_failed_digest():
    assert selfheal.heal_action(None, delivered=False) == "rerun"
    # failed 那期没有条目，重投空壳没用，必须整套重跑
    assert selfheal.heal_action(SimpleNamespace(status="failed"), delivered=False) == "rerun"


def test_heal_action_redelivers_or_skips():
    assert selfheal.heal_action(SimpleNamespace(status="ready"), delivered=False) == "redeliver"
    assert selfheal.heal_action(SimpleNamespace(status="empty"), delivered=False) == "redeliver"
    assert selfheal.heal_action(SimpleNamespace(status="ready"), delivered=True) is None


def test_alert_body_adds_vpn_hint_only_for_network_failures():
    vpn = selfheal.alert_body([f"行业日报(公司群)：{selfheal.failure_reason('failed')}"])
    assert "Clash Verge" in vpn
    feishu = selfheal.alert_body([f"行业日报(公司群)：{selfheal.failure_reason('ready')}"])
    assert "Clash Verge" not in feishu


class _FakeAnthropic:
    """替身 AsyncAnthropic：models.list 抛指定异常（None 表示正常返回）。"""

    def __init__(self, exc):
        self._exc = exc
        self.models = SimpleNamespace(list=self._list)

    async def _list(self, **_):
        if self._exc is not None:
            raise self._exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


def _probe(exc) -> bool:
    settings = SimpleNamespace(anthropic_api_key="k")
    with patch.object(selfheal.anthropic, "AsyncAnthropic", lambda **_: _FakeAnthropic(exc)):
        return asyncio.run(selfheal.llm_reachable(settings))


def test_llm_reachable_false_when_connection_fails():
    req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    assert _probe(anthropic.APIConnectionError(request=req)) is False
    assert _probe(anthropic.APITimeoutError(request=req)) is False


def test_llm_reachable_true_on_any_http_response():
    req = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    resp = httpx.Response(401, request=req)
    assert _probe(anthropic.AuthenticationError("unauthorized", response=resp, body=None))
    assert _probe(None)
