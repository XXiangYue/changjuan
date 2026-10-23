"""Offline boundaries for context selection; no database/model/network required."""

import copy
import json

import pytest

from novel_agent.studio.context_budget import (
    MEMORY_DOCUMENTS,
    ContextBudgetExceeded,
    pack_context,
)


def serialized_bytes(value):
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def context(*, core_only=False):
    names = MEMORY_DOCUMENTS[:2] if core_only else MEMORY_DOCUMENTS
    return {
        "project_id": "story-01",
        "project_revision": 17,
        "memory": {name: name + "：完整确认原文。" for name in names},
        "previous_chapter": "上一章完整正文。\n他把铜铃交给洛蘅。",
        "recent_summaries": [],
        "evidence": [],
        "facts": [],
        "retrieval_mode": "混合检索",
    }


@pytest.mark.parametrize("role", ["writer", "editor", "memory"])
def test_roomy_budget_keeps_full_snapshot_and_does_not_share_mutable_values(role):
    original = context()
    original["facts"] = [
        {
            "id": "f1",
            "position": 100001,
            "subject": "洛蘅",
            "related": ["沈砚"],
            "value": "收到铜铃",
        }
    ]
    original["evidence"] = [
        {"id": "e1", "content": "他把铜铃交给洛蘅。", "score": 0.03}
    ]
    snapshot = copy.deepcopy(original)
    view, audit = pack_context(original, role=role)
    assert view == snapshot
    assert original == snapshot
    assert audit["serialized_bytes"] == serialized_bytes(view) <= 60000
    assert audit["scope"] == "context_subpackage_only"
    assert "非供应商精确token" in audit["token_estimate_note"]
    view["facts"][0]["related"].append("他人")
    view["memory"]["世界观"] = "仅改返回视图"
    assert original == snapshot


def test_exact_unicode_json_byte_boundary_counts_quotes_newlines_and_metadata():
    original = context(core_only=True)
    # UTF-8 bytes differ from both Python character count and ASCII-escaped JSON.
    original["previous_chapter"] = '界😀\\"\n第二行'
    fact = {"id": "f-unicode", "subject": "洛蘅", "value": '说："守住😀"\n再来。'}
    expected = copy.deepcopy(original)
    expected["facts"] = [fact]
    exact = serialized_bytes(expected)
    original["facts"] = [fact]
    view, audit = pack_context(original, role="writer", max_bytes=exact)
    assert view == expected and audit["serialized_bytes"] == exact
    smaller, smaller_audit = pack_context(original, role="writer", max_bytes=exact - 1)
    assert smaller["facts"] == []
    assert smaller["previous_chapter"] == original["previous_chapter"]
    assert smaller_audit["serialized_bytes"] <= exact - 1
    assert (
        smaller_audit["decisions"][0]["reason"]
        == "whole_entry_exceeds_remaining_budget"
    )


@pytest.mark.parametrize("role", ["writer", "editor", "memory"])
def test_mandatory_overflow_is_explicit_without_truncation_or_input_mutation(role):
    original = context()
    original["previous_chapter"] = "不可截断的上一章。" * 5000
    snapshot = copy.deepcopy(original)
    with pytest.raises(ContextBudgetExceeded) as exc:
        pack_context(original, role=role, max_bytes=60000)
    assert exc.value.required_bytes > exc.value.max_bytes == 60000
    assert exc.value.role == role and "不能截断" in str(exc.value)
    assert original == snapshot


def test_memory_role_keeps_all_ten_documents_and_author_extension_verbatim():
    original = context()
    original["memory"]["伏笔"] = "未回收①：铜铃；否定、换行和空白都保留。\n\n" * 150
    original["memory"]["作者补充/秘密"] = "未向主角透露的计划。\n不改写！"
    required = serialized_bytes(original)
    original["evidence"] = [{"id": "unneeded", "content": "额外资料" * 100}]
    view, audit = pack_context(original, role="memory", max_bytes=required)
    assert view["memory"] == original["memory"]
    assert set(MEMORY_DOCUMENTS) <= set(view["memory"])
    assert view["evidence"] == []
    assert audit["required_bytes"] == audit["serialized_bytes"] == required
    with pytest.raises(ContextBudgetExceeded):
        pack_context(original, role="memory", max_bytes=required - 1)


def test_relevant_older_fact_beats_new_unrelated_fact_under_tight_budget():
    original = context(core_only=True)
    relevant = {
        "id": "earlier-custody",
        "position": 100001,
        "subject": "铜铃",
        "predicate": "持有人",
        "value": "洛蘅保管，沈砚手中没有铜铃。",
    }
    newer = {
        "id": "later-weather",
        "position": 100099,
        "subject": "山间天气",
        "predicate": "状态",
        "value": "阴天，几朵白云缓缓飘过了山峰。",
    }
    expected = copy.deepcopy(original)
    expected["facts"] = [relevant]
    original["facts"] = [newer, relevant]
    view, audit = pack_context(
        original,
        role="editor",
        query="铜铃 持有人 洛蘅",
        max_bytes=serialized_bytes(expected),
    )
    assert view["facts"] == [relevant]
    assert any(
        d["source_id"] == "later-weather" and d["decision"] == "excluded"
        for d in audit["decisions"]
    )


def test_selected_facts_and_summaries_keep_chronology_without_rewriting_knowledge():
    original = context()
    original["facts"] = [
        {
            "id": "new",
            "position": 100010,
            "kind": "knowledge",
            "subject": "洛蘅",
            "value": "亲眼看见机关。",
        },
        {
            "id": "old",
            "position": 100002,
            "kind": "knowledge",
            "subject": "沈砚",
            "value": "未获告知，不知道机关。",
        },
    ]
    original["recent_summaries"] = [
        {"id": "v2", "volume": 2, "number": 1, "summary": "后卷"},
        {"id": "c10", "volume": 1, "number": 10, "summary": "第十章"},
        {"id": "c2", "volume": 1, "number": 2, "summary": "第二章"},
    ]
    view, _ = pack_context(original, role="writer", query="机关")
    assert view["facts"] == [original["facts"][1], original["facts"][0]]
    assert [item["id"] for item in view["recent_summaries"]] == ["c2", "c10", "v2"]
    assert view["facts"][0]["value"] == "未获告知，不知道机关。"
    assert len(view["facts"]) == 2  # Do not merge different observers' claims.


def test_duplicate_evidence_is_selected_once_and_audited_with_source_ids():
    original = context()
    original["evidence"] = [
        {
            "id": "low",
            "chapter_id": "c2",
            "position": 100002,
            "content": "铜铃\n交给  洛蘅。",
            "score": 0.01,
        },
        {
            "id": "high",
            "chapter_id": "c2",
            "position": 100002,
            "content": "铜铃 交给 洛蘅。",
            "score": 0.04,
        },
        {
            "id": "distinct",
            "chapter_id": "c3",
            "position": 100003,
            "content": "后来铜铃被放在铁箱中。",
            "score": 0.02,
        },
    ]
    view, audit = pack_context(original, role="editor", query="铜铃")
    assert {item["id"] for item in view["evidence"]} == {"high", "distinct"}
    dropped = next(d for d in audit["decisions"] if d["source_id"] == "low")
    assert (
        dropped["reason"] == "duplicate_evidence_text"
        and dropped["duplicate_of"] == "high"
    )
    assert dropped["locator"] == "/evidence/0"
    assert "content" not in json.dumps(audit["decisions"])


def test_oversized_optional_document_is_not_sliced_and_smaller_entries_still_fit():
    original = context()
    original["memory"]["事件"] = "旧事件不可截去一半。" * 10000
    original["facts"] = [{"id": "small", "position": 1, "value": "铜铃在洛蘅手中。"}]
    snapshot = copy.deepcopy(original)
    view, audit = pack_context(
        original, role="writer", query="事件 铜铃", max_bytes=2500
    )
    assert "事件" not in view["memory"]
    assert view["facts"] == original["facts"]
    assert view["previous_chapter"] == original["previous_chapter"]
    assert view["memory"]["世界观"] == original["memory"]["世界观"]
    assert audit["serialized_bytes"] <= 2500
    assert any(
        d["source_id"] == "事件"
        and d["reason"] == "whole_entry_exceeds_remaining_budget"
        for d in audit["decisions"]
    )
    assert original == snapshot


def test_large_candidate_set_is_bounded_deterministic_and_audit_does_not_copy_text():
    original = context()
    original["facts"] = [
        {
            "id": f"fact-{i}",
            "position": i,
            "subject": f"路人{i}",
            "value": "各自离开。" * 30,
        }
        for i in range(1200)
    ]
    original["evidence"] = [
        {
            "id": f"chunk-{i}",
            "position": i,
            "content": f"来源{i}：" + "旧炉的档案。" * 60,
            "score": 1 / (60 + i),
        }
        for i in range(300)
    ]
    before = copy.deepcopy(original)
    first, audit = pack_context(
        original, role="editor", query="旧炉档案", max_bytes=18000
    )
    second, repeat_audit = pack_context(
        original, role="editor", query="旧炉档案", max_bytes=18000
    )
    assert first == second and audit == repeat_audit
    assert serialized_bytes(first) == audit["serialized_bytes"] <= 18000
    assert audit["original_bytes"] > 600000
    assert (
        len(audit["decisions"]) == 1508
    )  # Every optional source has an explicit decision.
    assert any(d["decision"] == "excluded" for d in audit["decisions"])
    assert "旧炉的档案" not in json.dumps(audit, ensure_ascii=False)
    assert original == before


@pytest.mark.parametrize(
    "role,budget", [("unknown", 60000), ("writer", 0), ("writer", True)]
)
def test_invalid_configuration_is_rejected(role, budget):
    with pytest.raises(ValueError):
        pack_context(context(), role=role, max_bytes=budget)


def test_missing_required_document_is_not_silently_treated_as_empty():
    original = context()
    del original["memory"]["世界观"]
    with pytest.raises(ValueError, match="缺少必需记忆文档.*世界观"):
        pack_context(original, role="writer")
    original = context()
    del original["memory"]["伏笔"]
    with pytest.raises(ValueError, match="缺少必需记忆文档.*伏笔"):
        pack_context(original, role="memory")
