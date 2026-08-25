"""Pure NLP functions for policy text analysis.

These functions are I/O-free (aside from module-level jieba initialization and a
cached stopword set) so they can be unit-tested without a database. Future
analyzers (KeywordExtractor, TopicAnalyzer, PolicySimilarityAnalyzer, ...) can
be added alongside without coupling to the runner.
"""

from __future__ import annotations

import json
import math
import unicodedata
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from importlib import resources as importlib_resources

import jieba

__all__ = [
    "aggregate_word_totals",
    "analyze_text",
    "compute_cooccurrence",
    "compute_tfidf",
    "build_comparison_report",
    "filter_stopwords",
    "load_stopwords",
    "tokenize",
    "top_words_from_totals",
]


def _cosine_similarity(left: Counter[str], right: Counter[str]) -> float:
    common = set(left) & set(right)
    numerator = sum(left[word] * right[word] for word in common)
    left_norm = math.sqrt(sum(value * value for value in left.values()))
    right_norm = math.sqrt(sum(value * value for value in right.values()))
    if not left_norm or not right_norm:
        return 0.0
    return numerator / (left_norm * right_norm)


def build_comparison_report(
    policies: Sequence[Mapping[str, object]], *, min_word_length: int = 2, top_n: int = 12
) -> dict[str, object]:
    """Build a deterministic, structured difference report for two or more policies."""
    analyzed: list[dict[str, object]] = []
    counters: list[Counter[str]] = []
    for policy in policies:
        words = analyze_text(str(policy["content_text"]), min_word_length=min_word_length)
        counter = Counter(words)
        counters.append(counter)
        analyzed.append(
            {
                "id": int(policy["id"]),
                "title": str(policy["title"]),
                "publisher": str(policy["publisher"]),
                "published_at": policy["published_at"],
                "top_keywords": [word for word, _count in counter.most_common(top_n)],
            }
        )

    keyword_sets = [set(counter) for counter in counters]
    common = set.intersection(*keyword_sets) if keyword_sets else set()
    common_ranked = sorted(common, key=lambda word: (-sum(c[word] for c in counters), word))[:top_n]
    pairs: list[dict[str, object]] = []
    for left_index in range(len(analyzed)):
        for right_index in range(left_index + 1, len(analyzed)):
            left_counter = counters[left_index]
            right_counter = counters[right_index]
            shared = set(left_counter) & set(right_counter)
            left_only = set(left_counter) - set(right_counter)
            right_only = set(right_counter) - set(left_counter)
            pairs.append(
                {
                    "left_policy_id": analyzed[left_index]["id"],
                    "right_policy_id": analyzed[right_index]["id"],
                    "similarity": round(_cosine_similarity(left_counter, right_counter), 4),
                    "shared_keywords": sorted(
                        shared, key=lambda word: (-(left_counter[word] + right_counter[word]), word)
                    )[:top_n],
                    "left_only_keywords": sorted(left_only, key=lambda word: (-left_counter[word], word))[
                        :top_n
                    ],
                    "right_only_keywords": sorted(right_only, key=lambda word: (-right_counter[word], word))[
                        :top_n
                    ],
                }
            )
    similarities = [float(pair["similarity"]) for pair in pairs]
    average = sum(similarities) / len(similarities) if similarities else 0.0
    summary = (
        f"本报告比对 {len(analyzed)} 篇政策，共识关键词 {len(common)} 个，"
        f"两两文本特征平均相似度为 {average:.1%}。"
    )
    return {
        "summary": summary,
        "common_keywords": common_ranked,
        "policies": analyzed,
        "pair_differences": pairs,
    }


_stopwords_cache: frozenset[str] | None = None


def load_stopwords() -> frozenset[str]:
    """Load the bundled Chinese stopword list (cached after first call)."""
    global _stopwords_cache
    if _stopwords_cache is None:
        try:
            raw = (
                importlib_resources.files("policy_analysis.analysis.resources")
                .joinpath("stopwords.json")
                .read_text(encoding="utf-8")
            )
            words = {item.strip() for item in json.loads(raw) if isinstance(item, str) and item.strip()}
        except (FileNotFoundError, json.JSONDecodeError):
            words = set()
        _stopwords_cache = frozenset(words)
    return _stopwords_cache


def _is_punctuation_or_space(token: str) -> bool:
    return all(
        unicodedata.category(ch).startswith("P") or ch.isspace() or unicodedata.category(ch) == "Zs"
        for ch in token
    )


def tokenize(text: str, *, min_word_length: int = 2) -> list[str]:
    """Segment Chinese text with jieba, dropping whitespace/punctuation and short tokens."""
    if not text or not text.strip():
        return []
    tokens: list[str] = []
    for token in jieba.lcut(text):
        token = token.strip()
        if not token or _is_punctuation_or_space(token):
            continue
        if len(token) < min_word_length:
            continue
        if token.isdigit():
            continue
        tokens.append(token)
    return tokens


def filter_stopwords(words: Iterable[str], stopwords: frozenset[str] | None = None) -> list[str]:
    stop = stopwords if stopwords is not None else load_stopwords()
    return [word for word in words if word not in stop]


def analyze_text(text: str, *, min_word_length: int = 2) -> list[str]:
    """Tokenize then drop stopwords; returns the effective word list for one document."""
    return filter_stopwords(tokenize(text, min_word_length=min_word_length))


def compute_tfidf(
    doc_words: Sequence[Sequence[str]],
) -> list[dict[str, tuple[int, float]]]:
    """Compute per-document ``{word: (frequency, tfidf)}``.

    The corpus is the supplied document set. idf uses sklearn-style smoothing
    ``log((1 + N) / (1 + df)) + 1`` so values are always non-negative; a single
    document yields idf = 1 (tfidf == tf).
    """
    counters = [Counter(words) for words in doc_words]
    n_docs = len(counters)
    df: Counter = Counter()
    for counter in counters:
        for word in counter:
            df[word] += 1
    results: list[dict[str, tuple[int, float]]] = []
    for counter in counters:
        total = sum(counter.values())
        word_map: dict[str, tuple[int, float]] = {}
        for word, freq in counter.items():
            tf = freq / total if total > 0 else 0.0
            idf = math.log((1 + n_docs) / (1 + df[word])) + 1
            word_map[word] = (freq, tf * idf)
        results.append(word_map)
    return results


def aggregate_word_totals(
    doc_word_maps: Sequence[Mapping[str, tuple[int, float]]],
) -> Counter:
    """Sum per-document frequencies into a corpus-level Counter."""
    totals: Counter = Counter()
    for word_map in doc_word_maps:
        for word, (freq, _tfidf) in word_map.items():
            totals[word] += freq
    return totals


def top_words_from_totals(totals: Counter, top_n: int) -> list[str]:
    """Return the top-N words by aggregate frequency."""
    if top_n <= 0:
        return []
    return [word for word, _ in totals.most_common(top_n)]


def compute_cooccurrence(
    doc_words: Sequence[Sequence[str]],
    top_words: Sequence[str],
) -> list[tuple[str, str, int]]:
    """Count co-occurring document frequency for top-word pairs (word1 < word2)."""
    top_set = set(top_words)
    pair_counts: Counter = Counter()
    for words in doc_words:
        present = sorted({word for word in words if word in top_set})
        for i in range(len(present)):
            for j in range(i + 1, len(present)):
                pair_counts[(present[i], present[j])] += 1
    return [(word1, word2, count) for (word1, word2), count in pair_counts.items()]
