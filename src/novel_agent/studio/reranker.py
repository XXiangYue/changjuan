"""Optional, explicitly prepared local cross-encoder reranking.

Scores order already eligible retrieval evidence. They are uncalibrated relevance
logits, not probabilities, truth checks, or a replacement for SQL source filters.
No model download or load is ever triggered by ``rerank``.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from numbers import Real
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / ".cache/fastembed"
MODEL_NAME = "BAAI/bge-reranker-v2-m3-int8"


class InvalidScores(ValueError):
    """The scorer did not return one finite numeric relevance score per row."""


class LocalReranker:
    """Bounded CPU reranker; enable only after independent task/latency evaluation.

    ``prepare(allow_download=True)`` is an explicit setup action and may block.
    Normal queries only use an already prepared session, or retain input ranking.
    Source fields and original channel/RRF scores are preserved in copied rows.
    The first ``max_candidates`` rows may be scored; any tail remains unscored
    after them. Oversized text is rejected rather than silently shortened.
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        cache_dir: Path = CACHE_DIR,
        max_candidates: int = 24,
        max_query_chars: int = 512,
        max_document_chars: int = 2000,
        max_total_chars: int = 24000,
        providers: Sequence[str] = ("CPUExecutionProvider",),
        scorer_factory: Callable[..., Any] | None = None,
    ):
        for name, value, maximum in (
            ("max_candidates", max_candidates, 64),
            ("max_query_chars", max_query_chars, 4096),
            ("max_document_chars", max_document_chars, 16000),
            ("max_total_chars", max_total_chars, 200000),
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= maximum
            ):
                raise ValueError(f"Invalid {name}")
        self.cache_dir = Path(cache_dir)
        self._check_cache()
        if not providers or any(not isinstance(p, str) or not p for p in providers):
            raise ValueError("An explicit nonempty ONNX provider list is required")
        self.enabled = enabled
        self.max_candidates = max_candidates
        self.max_query_chars = max_query_chars
        self.max_document_chars = max_document_chars
        self.max_total_chars = max_total_chars
        self.providers = tuple(providers)
        self._factory = scorer_factory
        self._scorer = None
        self._lock = threading.RLock()
        self._state = "unprepared"
        self._error_type = ""

    def _check_cache(self):
        if not self.cache_dir.is_absolute() or self.cache_dir.resolve() != CACHE_DIR:
            raise ValueError("Reranker cache must be workspace .cache/fastembed")

    def status(self) -> dict:
        return {
            "model": MODEL_NAME,
            "enabled": self.enabled,
            "state": self._state,
            "ready": self._scorer is not None,
            "error_type": self._error_type,
            "cache_dir": str(self.cache_dir),
            "threads": 2,
            "providers": list(self.providers),
        }

    def prepare(self, *, allow_download: bool = False) -> dict:
        """Explicit setup, local-only by default; never called during search."""
        start = time.monotonic()
        with self._lock:
            if self._scorer is not None:
                return self.status() | {
                    "elapsed_ms": 0.0,
                    "download_allowed": allow_download,
                }
            self._state, self._error_type = "preparing", ""
            try:
                self._check_cache()
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                factory = self._factory
                if factory is None:
                    from fastembed.rerank.cross_encoder import TextCrossEncoder

                    factory = TextCrossEncoder
                # lazy_load=False is deliberate: setup, not the first query, loads ONNX.
                scorer = factory(
                    model_name=MODEL_NAME,
                    cache_dir=str(self.cache_dir),
                    threads=2,
                    providers=list(self.providers),
                    lazy_load=False,
                    local_files_only=not allow_download,
                )
                if not callable(getattr(scorer, "rerank", None)):
                    raise TypeError("Scorer has no rerank method")
                self._scorer, self._state = scorer, "ready"
            except Exception as exc:  # noqa: BLE001 -- optional ML must not disable base retrieval
                self._scorer, self._state = None, "unavailable"
                # Avoid leaking tokens or signed download URLs in exception messages.
                self._error_type = type(exc).__name__
            return self.status() | {
                "elapsed_ms": round((time.monotonic() - start) * 1000, 3),
                "download_allowed": allow_download,
            }

    def _tokenization_audit(self, query, documents):
        # FastEmbed's configured tokenizer can truncate before ONNX inference.
        # Inspect exactly those encodings so even this limit is observable.
        tokenizer = getattr(getattr(self._scorer, "model", None), "tokenizer", None)
        if tokenizer is None:
            return {
                "checked": False,
                "truncated_indices": [],
                "reason": "scorer_has_no_tokenizer",
            }
        encodings = tokenizer.encode_batch(
            [(query, document) for document in documents]
        )
        return {
            "checked": True,
            "model_truncation": tokenizer.truncation,
            "truncated_indices": [i for i, e in enumerate(encodings) if e.overflowing],
            "encoded_tokens": sum(len(e.ids) for e in encodings),
        }

    def rerank(
        self, query: str, rows: Sequence[Mapping], *, limit: int = 8
    ) -> tuple[list[dict], dict]:
        if (
            isinstance(limit, bool)
            or not isinstance(limit, int)
            or not 1 <= limit <= 50
        ):
            raise ValueError("Rerank limit must be 1–50")
        if not isinstance(query, str):
            raise TypeError("Query must be text")
        original = [dict(row) for row in rows]
        start = time.monotonic()
        audit = self.status() | {
            "status": "fallback",
            "applied": False,
            "reason": "",
            "input_count": len(original),
            "candidate_count": min(len(original), self.max_candidates),
            "candidate_cap_applied": len(original) > self.max_candidates,
            "unscored_tail_count": max(0, len(original) - self.max_candidates),
            "query_chars": len(query),
            "limits": {
                "candidates": self.max_candidates,
                "query_chars": self.max_query_chars,
                "document_chars": self.max_document_chars,
                "total_chars": self.max_total_chars,
            },
            "input_truncated": False,
            "score_kind": "uncalibrated_cross_encoder_relevance_logit",
            "score_interpretation": "ranking signal only; not probability or faithfulness",
        }

        def done(reason, result=None):
            audit["reason"] = reason
            audit["elapsed_ms"] = round((time.monotonic() - start) * 1000, 3)
            selected = original if result is None else result
            audit["returned_count"] = min(len(selected), limit)
            return selected[:limit], audit

        if not original:
            audit["status"] = "empty"
            return done("empty_candidates")
        if not query.strip():
            return done("empty_query")
        if not self.enabled:
            return done("disabled")
        if self._scorer is None:
            return done("not_prepared" if self._state == "unprepared" else self._state)
        candidates = original[: self.max_candidates]
        documents = [row.get("content") for row in candidates]
        if any(not isinstance(doc, str) or not doc.strip() for doc in documents):
            return done("invalid_document")
        audit["document_chars"] = [len(doc) for doc in documents]
        # Pair-level work repeats the query for every candidate.
        audit["total_pair_chars"] = sum(len(query) + len(doc) for doc in documents)
        if len(query) > self.max_query_chars:
            return done("query_character_limit")
        if any(len(doc) > self.max_document_chars for doc in documents):
            return done("document_character_limit")
        if audit["total_pair_chars"] > self.max_total_chars:
            return done("total_character_limit")
        try:
            with self._lock:
                tokenization = self._tokenization_audit(query, documents)
                audit["tokenization"] = tokenization
                if tokenization["truncated_indices"]:
                    # Reject such inputs, retaining the complete original evidence.
                    return done("model_token_limit")
                values = list(self._scorer.rerank(query, documents, batch_size=4))
            if len(values) != len(candidates) or any(
                isinstance(score, bool)
                or not isinstance(score, Real)
                or not math.isfinite(score)
                for score in values
            ):
                raise InvalidScores("Expected one finite number per candidate")
            scores = [float(value) for value in values]
            ordered = sorted(range(len(candidates)), key=lambda index: -scores[index])
            ranked = [
                candidates[index] | {"rerank_score": scores[index]} for index in ordered
            ]
            ranked.extend(original[self.max_candidates :])
            audit.update(
                {
                    "status": "applied",
                    "applied": True,
                    "scores": [
                        {"input_rank": i + 1, "id": row.get("id"), "score": scores[i]}
                        for i, row in enumerate(candidates)
                    ],
                }
            )
            return done("", ranked)
        except Exception as exc:  # noqa: BLE001 -- optional ML must not disable base retrieval
            self._scorer, self._state = None, "unavailable"
            self._error_type = type(exc).__name__
            audit.update(
                {"state": self._state, "ready": False, "error_type": self._error_type}
            )
            return done("scoring_failed")
