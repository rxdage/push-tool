"""Phase 3 纯逻辑单测：不碰 DB / 嵌入模型 / Anthropic API。"""
from __future__ import annotations

from types import SimpleNamespace

from app.curation.classic import rotate_classics, select_classics
from app.curation.dedup import dedup_candidates, normalize_title, normalize_url
from app.curation.pipeline import _bucket_deep_brief
from app.curation.score import _keyword_overlap
from app.curation.summarize import _normalize, build_system_prompt


def item(id, title="", url=None, embedding=None, abstract=None):
    return SimpleNamespace(
        id=id, title=title, url=url, embedding=embedding, abstract=abstract
    )


# ---- dedup ----

def test_normalize_url_strips_fragment_trailing_slash_case():
    assert normalize_url("https://A.com/x/#frag") == "https://a.com/x"
    assert normalize_url(None) is None


def test_normalize_title_collapses_punct_ws():
    assert normalize_title("  Hello,   World!! ") == "hello world"


def test_dedup_exact_url():
    items = [item(1, "A", "http://x/1"), item(2, "B", "http://x/1/")]
    res = dedup_candidates(items)
    assert [k.id for k in res.kept] == [1]
    assert res.dropped == [(2, 1)]


def test_dedup_exact_title():
    items = [item(1, "Same Title"), item(2, "same title!")]
    res = dedup_candidates(items)
    assert [k.id for k in res.kept] == [1]


def test_dedup_embedding_cosine():
    # 归一化向量：两个几乎相同方向 → 余弦≈1 应判重复
    a = [1.0, 0.0, 0.0]
    near = [0.98, 0.199, 0.0]  # 余弦 ≈ 0.98 >= 0.92
    far = [0.0, 1.0, 0.0]      # 余弦 0
    items = [
        item(1, "t1", "u1", embedding=a),
        item(2, "t2", "u2", embedding=near),
        item(3, "t3", "u3", embedding=far),
    ]
    res = dedup_candidates(items, cosine_threshold=0.92)
    assert [k.id for k in res.kept] == [1, 3]
    assert (2, 1) in res.dropped


# ---- summarize ----

def test_normalize_clamps_and_nulls():
    out = _normalize(
        {
            "relevance": 1.7,
            "classification": "new",
            "action_label": "none",
            "summary": "  s ",
            "why_it_matters": "  ",
        }
    )
    assert out["relevance"] == 1.0
    assert out["action_label"] is None
    assert out["summary"] == "s"
    assert out["why_it_matters"] is None


def test_normalize_keeps_action_and_why():
    out = _normalize(
        {"relevance": "0.5", "classification": "classic",
         "action_label": "action", "summary": "x", "why_it_matters": "重要"}
    )
    assert out["relevance"] == 0.5
    assert out["action_label"] == "action"
    assert out["why_it_matters"] == "重要"


def test_system_prompt_industry_vs_academic():
    prof = SimpleNamespace(
        description="d", keywords=["k1", "k2"], must_have=["m"], exclude=["e"]
    )
    industry = build_system_prompt(prof, "industry")
    academic = build_system_prompt(prof, "academic")
    assert "action_label 取 [action|watch]" in industry
    assert "意味着什么" in industry
    assert 'action_label 一律 "none"' in academic


# ---- classic ----

def test_rotate_classics_wraps():
    seeds = [{"t": 0}, {"t": 1}, {"t": 2}]
    assert rotate_classics(seeds, 2, 0) == [{"t": 0}, {"t": 1}]
    assert rotate_classics(seeds, 2, 2) == [{"t": 2}, {"t": 0}]
    assert rotate_classics([], 2, 0) == []
    assert rotate_classics(seeds, 0, 0) == []


# ---- score (keyword fallback) ----

def test_keyword_overlap_counts_hits():
    prof = SimpleNamespace(keywords=["nanopore", "graphene"], must_have=["dna"])
    it = item(1, "A graphene nanopore for DNA", abstract="ionic current")
    assert _keyword_overlap(it, prof) > 0
    it2 = item(2, "unrelated topic", abstract="nothing")
    assert _keyword_overlap(it2, prof) == 0.0


# ---- pipeline bucketing (deep/brief only; classic 单独) ----

def test_bucket_deep_brief_split():
    sub = SimpleNamespace(max_deep=2, max_brief=2)
    passed = [(item(i), {"classification": "new"}) for i in range(1, 6)]
    out = _bucket_deep_brief(passed, sub)
    by_bucket = {}
    for it, _r, bucket, rank in out:
        by_bucket.setdefault(bucket, []).append((it.id, rank))
    assert by_bucket["deep"] == [(1, 0), (2, 1)]
    assert by_bucket["brief"] == [(3, 0), (4, 1)]
    assert "classic" not in by_bucket  # 第 5 个被截掉，classic 不在此函数


# ---- classic 选择：轮换 + 高引用兜底 ----

def test_select_classics_rotation_then_fallback():
    classics = [item(10, "c0"), item(11, "c1")]  # 2 篇人工种子
    high = [
        item(20, "h-low", abstract=None),
        item(21, "h-high", abstract=None),
    ]
    high[0].raw_json = {"citationCount": 50}
    high[1].raw_json = {"citationCount": 500}
    # 需要 3 篇：轮换出 2 篇人工 + 1 篇最高引用兜底
    picked = select_classics(classics, high, max_classic=3, offset=0)
    assert [p.id for p in picked] == [10, 11, 21]


def test_select_classics_offset_rotates():
    classics = [item(10), item(11), item(12)]
    assert [p.id for p in select_classics(classics, [], 1, 0)] == [10]
    assert [p.id for p in select_classics(classics, [], 1, 1)] == [11]
    assert select_classics([], [], 2, 0) == []
