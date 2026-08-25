from __future__ import annotations

import pytest
from policy_analysis.analysis import engine


def test_build_comparison_report_identifies_common_and_unique_keywords() -> None:
    policies = [
        {
            "id": 1,
            "title": "政策甲",
            "publisher": "部门甲",
            "published_at": "2026-08-01T00:00:00+00:00",
            "content_text": "人工智能 产业发展 科技创新",
        },
        {
            "id": 2,
            "title": "政策乙",
            "publisher": "部门乙",
            "published_at": "2026-08-02T00:00:00+00:00",
            "content_text": "人工智能 产业发展 数据安全",
        },
    ]

    report = engine.build_comparison_report(policies)

    assert len(report["policies"]) == 2
    assert len(report["pair_differences"]) == 1
    pair = report["pair_differences"][0]
    assert "人工智能" in pair["shared_keywords"]
    assert {"科技", "创新"} <= set(pair["left_only_keywords"])
    assert "数据安全" in pair["right_only_keywords"]
    assert 0 < pair["similarity"] < 1


def test_tokenize_drops_punctuation_digits_and_short_tokens() -> None:
    words = engine.tokenize("推动人工智能产业，2024年。a b")
    assert "人工智能" in words
    assert "，" not in words
    assert "2024" not in words
    assert "a" not in words
    assert "" not in words


def test_tokenize_empty_text_returns_empty_list() -> None:
    assert engine.tokenize("") == []
    assert engine.tokenize("   ") == []


def test_filter_stopwords_removes_configured_words() -> None:
    result = engine.filter_stopwords(["的", "人工智能", "了", "产业"], frozenset({"的", "了"}))
    assert result == ["人工智能", "产业"]


def test_filter_stopwords_uses_bundled_list_by_default() -> None:
    result = engine.analyze_text("推动人工智能产业发展。")
    assert "推动" not in result
    assert "人工智能" in result
    assert "产业" in result


def test_compute_tfidf_single_doc_idf_is_one() -> None:
    result = engine.compute_tfidf([["人工智能", "产业", "人工智能"]])
    assert result[0]["人工智能"] == (2, pytest.approx(2 / 3))
    assert result[0]["产业"] == (1, pytest.approx(1 / 3))


def test_compute_tfidf_multi_doc_is_non_negative() -> None:
    result = engine.compute_tfidf([["人工智能", "产业"], ["数字经济", "产业"]])
    for word_map in result:
        for _word, (_freq, tfidf) in word_map.items():
            assert tfidf >= 0


def test_compute_tfidf_empty_docs_returns_empty_maps() -> None:
    assert engine.compute_tfidf([]) == []
    assert engine.compute_tfidf([[], []]) == [{}, {}]


def test_aggregate_word_totals_sums_frequencies() -> None:
    tfidf = engine.compute_tfidf([["人工智能", "产业", "人工智能"], ["数字经济", "产业"]])
    totals = engine.aggregate_word_totals(tfidf)
    assert totals["人工智能"] == 2
    assert totals["产业"] == 2
    assert totals["数字经济"] == 1


def test_top_words_from_totals_respects_limit() -> None:
    totals = engine.aggregate_word_totals(
        engine.compute_tfidf([["人工智能", "产业", "人工智能", "发展"], ["数字经济", "产业"]])
    )
    top = engine.top_words_from_totals(totals, 2)
    assert len(top) == 2
    assert "人工智能" in top
    assert engine.top_words_from_totals(totals, 0) == []


def test_compute_cooccurrence_normalizes_word_order() -> None:
    doc_words = [["人工智能", "产业"], ["数字经济", "产业"]]
    top_words = ["产业", "人工智能", "数字经济"]
    relations = engine.compute_cooccurrence(doc_words, top_words)
    pairs = {(word1, word2): count for word1, word2, count in relations}
    assert pairs.get(("产业", "人工智能")) == 1
    assert pairs.get(("产业", "数字经济")) == 1
    assert ("人工智能", "数字经济") not in pairs
    for word1, word2, _ in relations:
        assert word1 < word2


def test_compute_cooccurrence_empty_top_words_returns_empty() -> None:
    assert engine.compute_cooccurrence([["人工智能", "产业"]], []) == []
