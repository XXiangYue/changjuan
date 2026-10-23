"""Contract tests use fake scores; they never download or load a model."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from novel_agent.studio.reranker import CACHE_DIR, LocalReranker

ROWS = [
    {
        "id": "a",
        "content": "丹药由洛蘅保管。",
        "chapter_id": "c1",
        "version": 2,
        "rrf_score": 0.03,
        "channel_scores": {"keyword": 3},
    },
    {
        "id": "b",
        "content": "铜铃挂在屋檐。",
        "chapter_id": "c2",
        "version": 1,
        "rrf_score": 0.02,
        "channel_scores": {"semantic": 0.8},
    },
    {
        "id": "c",
        "content": "沈砚继续寻找丹药。",
        "chapter_id": "c3",
        "version": 4,
        "rrf_score": 0.01,
    },
]


class FakeScorer:
    def __init__(self, scores=(0.5, 2, 2), error=None):
        self.scores, self.error, self.calls = scores, error, []

    def rerank(self, query, documents, **kwargs):
        self.calls.append((query, documents, kwargs))
        if self.error:
            raise self.error
        return iter(self.scores)


def ready(scorer=None, **kwargs):
    scorer = scorer or FakeScorer()
    received = []

    def factory(**arguments):
        received.append(arguments)
        return scorer

    reranker = LocalReranker(enabled=True, scorer_factory=factory, **kwargs)
    assert reranker.prepare()["ready"]
    return reranker, scorer, received


def test_stable_sort_preserves_sources_scores_and_does_not_mutate():
    reranker, scorer, _ = ready()
    before = deepcopy(ROWS)
    ranked, audit = reranker.rerank("谁持有丹药", ROWS)
    assert [row["id"] for row in ranked] == ["b", "c", "a"]
    for row in ranked:
        original = next(r for r in ROWS if r["id"] == row["id"])
        assert {key: row[key] for key in original} == original
    assert ROWS == before
    assert (
        audit["applied"]
        and audit["score_kind"] == "uncalibrated_cross_encoder_relevance_logit"
    )
    assert len(scorer.calls) == 1


def test_query_never_initializes_and_default_is_disabled():
    def fail_factory(**kwargs):
        pytest.fail("Queries must never initialize a model")

    reranker = LocalReranker(scorer_factory=fail_factory)
    assert reranker.rerank("丹药", ROWS)[1]["reason"] == "disabled"
    reranker.enabled = True
    assert reranker.rerank("丹药", ROWS)[1]["reason"] == "not_prepared"
    assert reranker.rerank("丹药", [])[1]["reason"] == "empty_candidates"
    assert reranker.rerank(" ", ROWS)[1]["reason"] == "empty_query"


def test_prepare_is_explicit_local_only_and_eager_with_two_threads():
    reranker, _, received = ready()
    assert received[0]["threads"] == 2
    assert received[0]["cache_dir"] == str(CACHE_DIR)
    assert received[0]["local_files_only"] is True
    assert received[0]["lazy_load"] is False
    assert reranker.prepare(allow_download=True)["ready"]
    assert len(received) == 1


def test_explicit_download_permission_and_load_failure_are_observable():
    received = []

    def unavailable(**kwargs):
        received.append(kwargs)
        raise OSError("sensitive raw transport details must not escape")

    reranker = LocalReranker(enabled=True, scorer_factory=unavailable)
    status = reranker.prepare(allow_download=True)
    assert received[0]["local_files_only"] is False
    assert status["state"] == "unavailable" and status["error_type"] == "OSError"
    assert "sensitive" not in str(status)
    ranked, audit = reranker.rerank("丹药", ROWS, limit=2)
    assert ranked == ROWS[:2] and audit["reason"] == "unavailable"


@pytest.mark.parametrize(
    "scores",
    [
        (0.1,),
        (0.1, 0.2, 0.3, 0.4),
        (0, float("nan"), 2),
        (0, float("inf"), 2),
        (0, "2", 2),
        (0, True, 2),
    ],
)
def test_malformed_scores_fail_closed_to_original_ranking(scores):
    reranker, _, _ = ready(FakeScorer(scores=scores))
    ranked, audit = reranker.rerank("丹药", ROWS)
    assert ranked == ROWS
    assert audit["error_type"] == "InvalidScores" and not audit["applied"]


def test_scoring_failure_does_not_retry_or_reload_on_query():
    reranker, scorer, received = ready(FakeScorer(error=RuntimeError("broken model")))
    assert reranker.rerank("丹药", ROWS)[0] == ROWS
    assert reranker.rerank("丹药", ROWS)[1]["reason"] == "unavailable"
    assert len(scorer.calls) == len(received) == 1


@pytest.mark.parametrize(
    "kwargs,query,rows,reason",
    [
        ({"max_query_chars": 2}, "谁持有丹药", ROWS, "query_character_limit"),
        ({"max_document_chars": 2}, "丹药", ROWS, "document_character_limit"),
        ({"max_total_chars": 5}, "丹药", ROWS, "total_character_limit"),
        ({}, "丹药", [{"id": "bad", "content": ""}], "invalid_document"),
    ],
)
def test_large_or_invalid_inputs_are_not_silently_truncated(
    kwargs, query, rows, reason
):
    reranker, scorer, _ = ready(**kwargs)
    ranked, audit = reranker.rerank(query, rows)
    assert ranked == rows and audit["reason"] == reason
    assert not audit["input_truncated"] and not scorer.calls


def test_candidate_cap_is_observable_and_unscored_tail_keeps_its_order():
    reranker, scorer, _ = ready(FakeScorer((0, 1)), max_candidates=2)
    ranked, audit = reranker.rerank("丹药", ROWS)
    assert [row["id"] for row in ranked] == ["b", "a", "c"]
    assert "rerank_score" not in ranked[-1]
    assert audit["candidate_cap_applied"] and audit["unscored_tail_count"] == 1
    assert scorer.calls[0][1] == [r["content"] for r in ROWS[:2]]


def test_model_tokenizer_overflow_is_observed_and_rejected():
    reranker, scorer, _ = ready()
    tokenizer = SimpleNamespace(
        truncation={"max_length": 2},
        encode_batch=lambda pairs: [
            SimpleNamespace(overflowing=[1], ids=[1, 2]) for _ in pairs
        ],
    )
    scorer.model = SimpleNamespace(tokenizer=tokenizer)
    ranked, audit = reranker.rerank("丹药", ROWS)
    assert ranked == ROWS and audit["reason"] == "model_token_limit"
    assert audit["tokenization"]["truncated_indices"] == [0, 1, 2]
    assert not scorer.calls


def test_cache_boundary_and_limit_validation():
    with pytest.raises(ValueError, match="cache"):
        LocalReranker(cache_dir=CACHE_DIR.parent / "elsewhere")
    with pytest.raises(ValueError):
        LocalReranker(max_candidates=0)
    for limit in (0, 51, True):
        with pytest.raises(ValueError):
            LocalReranker().rerank("丹药", ROWS, limit=limit)
