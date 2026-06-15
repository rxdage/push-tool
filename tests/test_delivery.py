"""Phase 4 纯逻辑单测：formatter 卡片 + 飞书签名 + 分片。不发网络。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json

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
