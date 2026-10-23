"""Offline checks for provenance, blinding boundaries and immutable exports."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build_live_story_packets.py"
SPEC = importlib.util.spec_from_file_location("live_story_packets", SCRIPT)
packets = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(packets)


def event(seq, kind, data):
    return {"seq": seq, "run_id": "one-run", "type": kind, "data": data}


def writer(seq, text):
    return [
        event(seq, "model.output", {"role": "作者", "text": text}),
        event(seq + 1, "draft.saved", {"words": len(text)}),
    ]


def test_local_pair_is_bound_to_semantic_review_not_first_draft():
    events = (
        writer(1, "长篇第一稿")
        + [event(3, "length_check", {"passed": False})]
        + writer(4, "篇幅修订稿")
        + [event(6, "length_check", {"passed": False})]
        + writer(7, "语义审核对象")
        + [event(9, "review", {"passed": False})]
        + writer(10, "一次语义改稿")
        + [event(12, "review", {"passed": True})]
        + writer(13, "以后版本不可代替")
    )
    original = copy.deepcopy(events)
    outputs, review, pair = packets.select_repair(events)
    assert len(outputs) == 5
    assert review["seq"] == 9
    assert [entry["seq"] for entry in pair] == [7, 10]
    assert events == original


@pytest.mark.parametrize(
    "bad",
    [
        "mixed_run",
        "duplicate_seq",
        "missing_save",
        "wrong_saved_length",
        "no_semantic_review",
    ],
)
def test_binding_rejects_ambiguous_or_incomplete_traces(bad):
    events = writer(1, "原文") + [event(3, "review", {})] + writer(4, "新文")
    if bad == "mixed_run":
        events[-1]["run_id"] = "other-run"
    elif bad == "duplicate_seq":
        events[-1]["seq"] = events[-2]["seq"]
    elif bad == "missing_save":
        events.pop()
    elif bad == "wrong_saved_length":
        events[-1]["data"]["words"] += 1
    else:
        events = [row for row in events if row["type"] != "review"]
    with pytest.raises(ValueError):
        packets.select_repair(events)


def test_public_packet_contains_only_anonymous_id_and_verbatim_chapters():
    text = "风吹过井台。\n\n‘还钳。’她说。"
    data = packets.packet("entry_abc123", {2: text, 1: "共同第一章"})
    assert set(data) == {"entry_id", "chapters"}
    assert data["chapters"][1] == {
        "number": 2,
        "body": text,
        "sha256": packets.sha(text),
    }
    assert all(set(row) == {"number", "body", "sha256"} for row in data["chapters"])


def test_primary_rejects_unconfirmed_stale_and_historical_versions():
    project = {
        "chapters": [
            {
                "number": i,
                "canonical_revision": 1,
                "canonical": "正文" + str(i),
                "content": "正文" + str(i),
                "stale": False,
            }
            for i in (1, 2, 3)
        ]
    }
    assert packets.canonical_chapters(project) == {
        i: "正文" + str(i) for i in (1, 2, 3)
    }
    for change in (
        {"canonical_revision": 0},
        {"canonical_revision": 2},
        {"stale": True},
        {"content": "未确认修改"},
    ):
        bad = copy.deepcopy(project)
        bad["chapters"][0].update(change)
        with pytest.raises(ValueError):
            packets.canonical_chapters(bad)


def test_sealed_packet_cannot_be_overwritten_and_mapping_stays_private(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(packets, "ROOT", tmp_path)
    output = tmp_path / "bundle"
    documents = {
        "public/entry_a.json": packets.packet("entry_a", {1: "小说正文"}),
        "operator-only/mapping.json": {"entry_a": "configuration"},
    }
    packets.seal_bundle(output, documents)
    raw = (output / "public/entry_a.json").read_bytes()
    assert "configuration" not in raw.decode()
    seal = json.loads((output / "seal.json").read_text())
    assert seal["files"]["public/entry_a.json"] == packets.sha(raw)
    with pytest.raises(ValueError, match="不得覆盖"):
        packets.seal_bundle(output, documents)
    assert (output / "public/entry_a.json").read_bytes() == raw


def valid_assessment():
    return {
        "entry_id": "entry_a",
        "checks": [
            {
                "id": check,
                "satisfied": True,
                "reason": "动作原文明确可见",
                "evidence": [
                    {
                        "chapter": 2,
                        "quote": "揭开湿泥后两短一长重新响起。",
                        "source_kind": "narrated_action",
                    }
                ],
            }
            for check in packets.CHECKS
        ],
    }


def test_local_validation_checks_quotes_without_claiming_semantics():
    data = packets.packet("entry_a", {1: "共有上文", 2: "揭开湿泥后两短一长重新响起。"})
    result = packets.validate_local(data, valid_assessment())
    assert result["satisfied_count"] == 5
    assert "程序不自动判兑现" in result["boundary"]
    # A reader may label true or false; this checker does not infer the label.
    assessment = valid_assessment()
    assessment["checks"][0].update(satisfied=False, evidence=[])
    assert packets.validate_local(data, assessment)["satisfied_count"] == 4


@pytest.mark.parametrize(
    "bad", ["quote", "first_chapter", "no_quote", "extra_check", "bad_hash", "wrong_id"]
)
def test_local_validation_rejects_invalid_evidence_or_scope(bad):
    data = packets.packet("entry_a", {1: "共有上文", 2: "揭开湿泥后两短一长重新响起。"})
    assessment = valid_assessment()
    if bad == "quote":
        assessment["checks"][0]["evidence"][0]["quote"] = "揭开湿泥后响声真的恢复了。"
    elif bad == "first_chapter":
        assessment["checks"][0]["evidence"][0]["chapter"] = 1
    elif bad == "no_quote":
        assessment["checks"][0]["evidence"] = []
    elif bad == "extra_check":
        assessment["checks"].append({"id": "P1.closure"})
    elif bad == "bad_hash":
        data["chapters"][1]["sha256"] = "incorrect"
    else:
        assessment["entry_id"] = "entry_other"
    with pytest.raises(ValueError):
        packets.validate_local(data, assessment)
