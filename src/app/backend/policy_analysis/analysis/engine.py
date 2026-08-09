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
    "filter_stopwords",
    "load_stopwords",
    "tokenize",
    "top_words_from_totals",
]

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
