"""Pure, auditable selection of a bounded *context subpackage*.

The byte ceiling covers the final compact JSON returned as ``view`` only. It
does not cover the calling prompt's manuscript, outline, instructions, schemas,
tools, or the audit log. UTF-8 bytes are measured exactly; ``estimated_tokens``
is a rough diagnostic, never the provider's tokenizer or a request token cap.

The authoritative context (notably the memory snapshot used by historical
commits) is never modified. Documents, facts, summaries and source chunks are
atomic: this module selects existing entries, and never summarizes, merges or
infers knowledge/state. Typed state reduction is a separate responsibility.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

MEMORY_DOCUMENTS = (
    "世界观",
    "主角形象状态",
    "前卷剧情概述",
    "本卷核心剧情",
    "下一卷剧情",
    "伏笔",
    "有关角色",
    "目前剧情",
    "事件",
    "当前章节设计规划",
)
ROLE_PRIORITIES = {
    "writer": {"memory": 0.5, "facts": 0.8, "evidence": 0.7, "recent_summaries": 0.4},
    "editor": {"memory": 0.5, "facts": 1.0, "evidence": 0.8, "recent_summaries": 0.3},
    "memory": {"memory": 1.0, "facts": 0.8, "evidence": 0.4, "recent_summaries": 0.6},
}


class ContextBudgetExceeded(ValueError):
    """The required, untruncated context alone exceeds the supplied ceiling."""

    def __init__(self, *, role: str, required_bytes: int, max_bytes: int):
        self.role = role
        self.required_bytes = required_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"{role} 必需上下文为 {required_bytes} UTF-8字节，超过子包预算 "
            f"{max_bytes}；完整核心记忆或上一章不能截断，请提高预算或先由作者整理资料。"
        )


def _json(value: Any) -> str:
    # Identical to studio.db.encode; allow_nan=False rejects non-JSON input.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _size(value: Any) -> int:
    return len(_json(value).encode("utf-8"))


def _terms(text: str) -> set[str]:
    terms = set(re.findall(r"[a-z0-9_]+", text.casefold()))
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        terms.update(run[i : i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            terms.add(run)
    return terms


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if math.isfinite(number):
            return number
    return default


def _position(item: dict, index: int) -> tuple[float, float, int]:
    if "position" in item:
        return (_number(item["position"]), 0, index)
    if "volume" in item and "number" in item:
        return (_number(item["volume"]), _number(item["number"]), index)
    return (0, 0, index)


def _source_id(category: str, value: Any, index: int, name: str = "") -> str:
    if category == "memory":
        raw = name
    else:
        raw = str(value.get("id") or value.get("chapter_id") or f"{category}:{index}")
    # Untrusted source metadata must not copy arbitrary manuscript-sized strings
    # into the audit. The JSON-pointer locator remains exact even without an ID.
    if len(raw) > 160:
        raw = raw[:120] + ":sha256:" + hashlib.sha256(raw.encode()).hexdigest()[:16]
    return raw


@dataclass
class _Candidate:
    category: str
    index: int
    value: Any
    source_id: str
    locator: str
    name: str = ""
    relevance: float = 0.0
    recency: float = 0.0
    retrieval_score: float = 0.0
    priority: float = 0.0
    fingerprint: str = ""

    def record(self, decision: str, reason: str, **extra: Any) -> dict:
        return {
            "category": self.category,
            "source_id": self.source_id,
            "locator": self.locator,
            "decision": decision,
            "reason": reason,
            "priority": round(self.priority, 6),
            "relevance": round(self.relevance, 6),
            "recency": round(self.recency, 6),
            **extra,
        }


def pack_context(
    context: dict, *, role: str, query: str = "", max_bytes: int = 60000
) -> tuple[dict, dict]:
    """Return an independent bounded view and an out-of-prompt selection audit.

    ``memory`` preserves every supplied memory document, including all ten core
    documents, plus the previous chapter in full. ``writer`` and ``editor``
    preserve 世界观, 主角形象状态 and the previous chapter in full; other memory
    documents are selected whole. All additional ContextPack metadata is kept.

    Optional entries use lexical query relevance, role priorities, recency and
    existing retrieval scores. Identical evidence text (NFC/whitespace folded
    only for comparison) is deduplicated. Selected summaries/facts are emitted
    chronologically; their values and knowledge claims remain verbatim.

    The original snapshot and returned view share no mutable values. Required
    data exceeding ``max_bytes`` raises ContextBudgetExceeded without truncation.
    """
    if role not in ROLE_PRIORITIES:
        raise ValueError("role 必须为 writer、editor 或 memory")
    if type(max_bytes) is not int or max_bytes <= 0:
        raise ValueError("max_bytes 必须为正整数UTF-8字节数")
    if not isinstance(context, dict) or not isinstance(context.get("memory"), dict):
        raise TypeError("context.memory 必须为完整记忆字典")
    required_names = (
        MEMORY_DOCUMENTS if role == "memory" else ("世界观", "主角形象状态")
    )
    missing = [name for name in required_names if name not in context["memory"]]
    if missing:
        raise ValueError("缺少必需记忆文档：" + "、".join(missing))
    if "previous_chapter" not in context or not isinstance(
        context["previous_chapter"], str
    ):
        raise ValueError("context.previous_chapter 必须为原文字符串（首章可为空）")
    for group in ("facts", "evidence", "recent_summaries"):
        if not isinstance(context.get(group, []), list) or any(
            not isinstance(item, dict) for item in context.get(group, [])
        ):
            raise ValueError(f"context.{group} 必须为字典列表")
    if any(not isinstance(value, str) for value in context["memory"].values()):
        raise ValueError("记忆文档必须为原文字符串")

    source = copy.deepcopy(context)
    view = {
        key: value
        for key, value in source.items()
        if key not in ("memory", "facts", "evidence", "recent_summaries")
    }
    view["memory"] = {
        name: value
        for name, value in source["memory"].items()
        if role == "memory" or name in required_names
    }
    for group in ("facts", "evidence", "recent_summaries"):
        view[group] = []
    required_bytes = _size(view)
    if required_bytes > max_bytes:
        raise ContextBudgetExceeded(
            role=role, required_bytes=required_bytes, max_bytes=max_bytes
        )

    query_terms = _terms(query)
    candidates: list[_Candidate] = []
    for index, (name, value) in enumerate(source["memory"].items()):
        if name not in view["memory"]:
            candidates.append(
                _Candidate(
                    "memory",
                    index,
                    value,
                    _source_id("memory", value, index, name),
                    "/memory/" + name.replace("~", "~0").replace("/", "~1"),
                    name=name,
                )
            )
    for group in ("facts", "evidence", "recent_summaries"):
        entries = source.get(group, [])
        chronology = sorted(range(len(entries)), key=lambda i: _position(entries[i], i))
        recency = {
            index: (rank + 1) / max(len(entries), 1)
            for rank, index in enumerate(chronology)
        }
        highest_score = max((_number(item.get("score")) for item in entries), default=0)
        for index, value in enumerate(entries):
            candidate = _Candidate(
                group,
                index,
                value,
                _source_id(group, value, index),
                f"/{group}/{index}",
                recency=recency[index],
                retrieval_score=max(0, _number(value.get("score"))) / highest_score
                if highest_score > 0
                else 0,
            )
            if group == "evidence" and value.get("content"):
                body = unicodedata.normalize("NFC", str(value["content"]))
                body = re.sub(r"\s+", " ", body).strip()
                candidate.fingerprint = hashlib.sha256(body.encode()).hexdigest()
            candidates.append(candidate)

    for candidate in candidates:
        text = (
            candidate.name
            + " "
            + (
                candidate.value
                if candidate.category == "memory"
                else _json(candidate.value)
            )
        )
        candidate.relevance = (
            len(query_terms & _terms(text)) / len(query_terms) if query_terms else 0
        )
        candidate.priority = (
            5 * candidate.relevance
            + ROLE_PRIORITIES[role][candidate.category]
            + 0.2 * candidate.recency
            + 0.25 * candidate.retrieval_score
        )
    candidates.sort(
        key=lambda x: (-x.priority, -x.relevance, -x.recency, x.category, x.index)
    )

    used = required_bytes
    selected: dict[str, list[_Candidate]] = {
        group: [] for group in ("facts", "evidence", "recent_summaries")
    }
    counts = {group: 0 for group in selected}
    memory_count = len(view["memory"])
    fingerprints: dict[str, str] = {}
    decisions = []
    for candidate in candidates:
        if candidate.fingerprint and candidate.fingerprint in fingerprints:
            decisions.append(
                candidate.record(
                    "excluded",
                    "duplicate_evidence_text",
                    duplicate_of=fingerprints[candidate.fingerprint],
                )
            )
            continue
        if candidate.category == "memory":
            incremental = (
                _size({candidate.name: candidate.value}) - 2 + bool(memory_count)
            )
        else:
            incremental = _size(candidate.value) + bool(counts[candidate.category])
        if used + incremental > max_bytes:
            decisions.append(
                candidate.record(
                    "excluded",
                    "whole_entry_exceeds_remaining_budget",
                    incremental_bytes=incremental,
                    remaining_bytes=max_bytes - used,
                )
            )
            continue
        used += incremental
        if candidate.category == "memory":
            view["memory"][candidate.name] = candidate.value
            memory_count += 1
        else:
            selected[candidate.category].append(candidate)
            counts[candidate.category] += 1
        if candidate.fingerprint:
            fingerprints[candidate.fingerprint] = candidate.source_id
        decisions.append(
            candidate.record(
                "selected", "ranked_whole_entry", incremental_bytes=incremental
            )
        )

    for group, entries in selected.items():
        if group in ("facts", "recent_summaries"):
            entries.sort(key=lambda x: _position(x.value, x.index))
        view[group] = [candidate.value for candidate in entries]
    # Canonical document order is restored independently of selection ranking.
    view["memory"] = {
        name: view["memory"][name]
        for name in source["memory"]
        if name in view["memory"]
    }
    actual_bytes = _size(view)
    if actual_bytes != used or actual_bytes > max_bytes:
        raise AssertionError("上下文JSON字节计量不一致")
    audit = {
        "version": "context-budget-v1",
        "role": role,
        "scope": "context_subpackage_only",
        "scope_note": "仅最终view JSON；不含正文、outline、instructions、schema、tools与audit，不是完整请求token上限。",
        "measurement": "compact_json_utf8_bytes_ensure_ascii_false",
        "max_bytes": max_bytes,
        "original_bytes": _size(context),
        "required_bytes": required_bytes,
        "serialized_bytes": actual_bytes,
        "within_budget": actual_bytes <= max_bytes,
        "estimated_tokens": math.ceil(actual_bytes / 3),
        "token_estimate_note": "UTF-8字节数/3的粗估，仅便于观察，非供应商精确token数或保守token上界。",
        "required_memory": list(view["memory"])
        if role == "memory"
        else list(required_names),
        "previous_chapter_preserved": True,
        "selected_counts": {"memory": len(view["memory"]), **counts},
        "decisions": decisions,
    }
    return view, audit
