"""Deterministic event semantics, not a test of prose extraction or story quality.

Development fixtures use pill units. Separate acceptance scenarios use caravan
water, library manuscripts and a glove clue, with independently written expected
balances/knowledge/lifecycle. These are offline program oracles, not an unseen
model holdout or evidence that an LLM extracts events correctly.
"""

import copy
import json
import random

import pytest

from novel_agent.studio.story_state import (
    ResourceEstablish,
    StoryReplayError,
    replay,
    validate_event,
)


def event(
    event_id, operation, *, position=1, sequence=1, chapter=None, version=1, **fields
):
    return {
        "id": event_id,
        "operation": operation,
        "position": position,
        "sequence": sequence,
        "source": {
            "chapter_id": chapter or f"chapter-{position}",
            "version": version,
            "quote": "离线测试资料中作者明确确认发生的事件。",
        },
        "status": "confirmed",
        **fields,
    }


def establish(event_id, holder, quantity, *, resource="resource:pill-unit", **clock):
    return event(
        event_id,
        "resource.establish",
        holder_id=holder,
        resource_id=resource,
        quantity=quantity,
        **clock,
    )


def transfer(
    event_id, source, destination, quantity, *, resource="resource:pill-unit", **clock
):
    return event(
        event_id,
        "resource.transfer",
        from_holder_id=source,
        to_holder_id=destination,
        resource_id=resource,
        quantity=quantity,
        **clock,
    )


def consume(event_id, holder, quantity, *, resource="resource:pill-unit", **clock):
    return event(
        event_id,
        "resource.consume",
        holder_id=holder,
        resource_id=resource,
        quantity=quantity,
        **clock,
    )


def development_sequence():
    return [
        establish("dev-a", "actor:apprentice", 2, sequence=1),
        establish("dev-b", "actor:healer", 0, sequence=2),
        transfer("dev-transfer", "actor:apprentice", "actor:healer", 1, sequence=3),
        consume("dev-consume", "actor:healer", 1, sequence=4),
    ]


def assert_error(events, code, event_id, **kwargs):
    with pytest.raises(StoryReplayError) as exc:
        replay(events, **kwargs)
    assert exc.value.code == code
    assert exc.value.event_id == event_id
    assert exc.value.precondition
    assert exc.value.as_dict()["event_id"] == event_id
    assert event_id in str(exc.value)
    return exc.value


def test_development_resource_transfer_then_consumption_conserves_exact_units():
    events = development_sequence()
    before = copy.deepcopy(events)
    state = replay(events)
    assert state.quantity("actor:apprentice", "resource:pill-unit") == 1
    assert state.quantity("actor:healer", "resource:pill-unit") == 0
    assert state.quantity("actor:visitor", "resource:pill-unit") is None
    assert state.resource_totals["resource:pill-unit"].model_dump() == {
        "established": 2,
        "remaining": 1,
        "consumed": 1,
    }
    assert state.event_sources["dev-transfer"].chapter_id == "chapter-1"
    assert (
        json.loads(state.model_dump_json())["inventories"]["actor:healer"][
            "resource:pill-unit"
        ]["quantity"]
        == 0
    )
    assert events == before


@pytest.mark.parametrize("missing", ["source", "destination"])
def test_unknown_inventory_is_not_implicitly_zero_at_either_transfer_endpoint(missing):
    known = "actor:b" if missing == "source" else "actor:a"
    events = [
        establish("known", known, 3),
        transfer("bad-transfer", "actor:a", "actor:b", 1, position=2),
    ]
    error = assert_error(events, "unknown_inventory", "bad-transfer")
    assert error.details["holder_id"] == (
        "actor:a" if missing == "source" else "actor:b"
    )


def test_known_zero_cannot_be_spent_and_establish_cannot_reset_existing_balance():
    assert_error(
        [establish("empty", "actor:a", 0), consume("spend", "actor:a", 1, position=2)],
        "insufficient_resource",
        "spend",
    )
    assert_error(
        [
            establish("first", "actor:a", 1),
            establish("unbacked-topup", "actor:a", 99, position=2),
        ],
        "inventory_already_established",
        "unbacked-topup",
    )


@pytest.mark.parametrize("quantity", [1.0, True, -1, "2"])
def test_resource_quantities_are_strict_discrete_units(quantity):
    assert_error(
        [establish("invalid-quantity", "actor:a", quantity)],
        "invalid_event_schema",
        "invalid-quantity",
    )


@pytest.mark.parametrize("operation", ["resource.consume", "resource.transfer"])
def test_zero_quantity_actions_are_not_accepted_as_happened_changes(operation):
    fields = (
        {"holder_id": "actor:a"}
        if operation.endswith("consume")
        else {"from_holder_id": "actor:a", "to_holder_id": "actor:b"}
    )
    action = event(
        "zero-action", operation, resource_id="resource:coin", quantity=0, **fields
    )
    assert_error([action], "invalid_event_schema", "zero-action")


@pytest.mark.parametrize("status", ["planned", "negated", "speculative", "attempted"])
def test_plans_denials_and_attempts_are_not_replayed_as_confirmed_events(status):
    action = transfer("not-happened", "actor:a", "actor:b", 1)
    action["status"] = status
    assert_error([action], "invalid_event_schema", "not-happened")


def test_source_and_stable_identity_are_required_and_unknown_fields_are_rejected():
    action = establish("no-source", "actor:a", 1)
    del action["source"]["quote"]
    assert_error([action], "invalid_event_schema", "no-source")
    action = establish("bad-identity", "名字 会变化", 1)
    assert_error([action], "invalid_event_schema", "bad-identity")
    action = establish("extra-field", "actor:a", 1)
    action["auto_infer"] = True
    assert_error([action], "invalid_event_schema", "extra-field")


def test_preconstructed_model_does_not_bypass_strict_replay_validation():
    trusted = validate_event(establish("constructed", "actor:a", 1))
    bypassed = ResourceEstablish.model_construct(
        **{**trusted.model_dump(), "source": trusted.source, "quantity": -10}
    )
    assert_error([bypassed], "invalid_event_schema", "constructed")


def test_replay_sorts_by_explicit_chapter_sequence_and_rejects_ties():
    events = development_sequence()
    shuffled = copy.deepcopy(events)
    random.Random(731).shuffle(shuffled)
    assert replay(events) == replay(shuffled)
    tied = establish("ambiguous", "actor:third", 0, sequence=4)
    assert_error(events + [tied], "ambiguous_event_order", "dev-consume")


def test_duplicate_ids_are_idempotent_only_for_identical_typed_content():
    events = development_sequence()
    assert replay(events + [copy.deepcopy(events[2])]) == replay(events)
    changed = copy.deepcopy(events[2])
    changed["quantity"] = 2
    # Even a future cutoff cannot hide a conflicting reuse of a stable event ID.
    assert_error(
        events + [changed], "conflicting_event_id", "dev-transfer", before_position=0
    )


def test_cutoff_is_exclusive_and_seeds_follow_the_same_order_and_source_rules():
    seed = establish("initial", "actor:a", 3, position=0)
    events = [
        consume("chapter1", "actor:a", 1),
        consume("chapter2", "actor:a", 2, position=2),
    ]
    state = replay(events, seed_events=[seed], before_position=2)
    assert state.quantity("actor:a", "resource:pill-unit") == 2
    assert state.applied_event_ids == ["initial", "chapter1"]
    assert state.excluded_events[0].model_dump() == {
        "event_id": "chapter2",
        "reason": "at_or_after_cutoff",
    }
    assert_error([], "invalid_seed_operation", "chapter1", seed_events=[events[0]])


def test_replacement_version_recomputes_from_sources_without_old_inventory_effects():
    old = establish("old-stock", "actor:a", 3, version=1)
    replacement = establish("new-stock", "actor:a", 2, version=2)
    spend = consume("spend-later", "actor:a", 1, position=2)
    raw = [old, replacement, spend]
    old_state = replay(raw, source_versions={"chapter-1": 1, "chapter-2": 1})
    new_state = replay(raw, source_versions={"chapter-1": 2, "chapter-2": 1})
    assert old_state.quantity("actor:a", "resource:pill-unit") == 2
    assert new_state.quantity("actor:a", "resource:pill-unit") == 1
    assert (
        new_state.inventories["actor:a"]["resource:pill-unit"].established_by
        == "new-stock"
    )
    assert new_state.excluded_events[0].reason == "source_version_obsolete"
    assert "old-stock" not in new_state.event_sources
    assert_error(raw, "ambiguous_source_version", "old-stock")


def test_historical_removal_exposes_downstream_precondition_failure_instead_of_skipping():
    events = [
        establish("removed-stock", "actor:a", 2),
        consume("dependent-spend", "actor:a", 1, position=2),
    ]
    error = assert_error(
        events, "unknown_inventory", "dependent-spend", source_versions={"chapter-2": 1}
    )
    assert error.details["holder_id"] == "actor:a"


def test_complete_source_manifest_reports_absent_chapters_as_inactive():
    state = replay([establish("inactive", "actor:a", 1)], source_versions={})
    assert state.inventories == {} and state.applied_event_ids == []
    assert state.excluded_events[0].reason == "source_not_active"


def test_actor_knowledge_is_additive_separate_and_keeps_first_acquisition():
    events = [
        event(
            "learn-a1",
            "knowledge.learn",
            actor_id="actor:a",
            fact_id="fact:door",
            fact="门并未上锁。",
            sequence=1,
        ),
        event(
            "learn-a2",
            "knowledge.learn",
            actor_id="actor:a",
            fact_id="fact:key",
            fact="钥匙在井底。",
            sequence=2,
        ),
        event(
            "learn-b2",
            "knowledge.learn",
            actor_id="actor:b",
            fact_id="fact:key",
            fact="钥匙在井底。",
            sequence=3,
        ),
        event(
            "repeat-a1",
            "knowledge.learn",
            actor_id="actor:a",
            fact_id="fact:door",
            fact="门并未上锁。",
            position=2,
        ),
    ]
    state = replay(events)
    assert state.knows("actor:a", "fact:door") and state.knows("actor:a", "fact:key")
    assert state.knows("actor:b", "fact:key") and not state.knows(
        "actor:b", "fact:door"
    )
    assert state.facts["fact:door"] == "门并未上锁。"
    record = state.knowledge["actor:a"]["fact:door"]
    assert record.learned_at == 1 and record.learned_sequence == 1
    assert record.evidence_event_ids == ["learn-a1", "repeat-a1"]
    conflict = event(
        "bad-fact",
        "knowledge.learn",
        actor_id="actor:b",
        fact_id="fact:key",
        fact="钥匙在门后。",
        position=3,
    )
    assert_error(events + [conflict], "conflicting_fact_identity", "bad-fact")


def plant(*, position=1, start=2, end=4):
    return event(
        "plant",
        "hook.plant",
        hook_id="hook:glove",
        description="现场遗落一只蓝手套。",
        payoff_condition="通过独特针脚识别手套的主人。",
        window_start=start,
        window_end=end,
        position=position,
    )


@pytest.mark.parametrize(
    "resolution_position,timing", [(1, "early"), (3, "on_time"), (6, "late")]
)
def test_hook_resolution_records_author_interpretation_and_window_timing(
    resolution_position, timing
):
    resolution = event(
        "resolve",
        "hook.resolve",
        hook_id="hook:glove",
        explanation="作者确认针脚与证人的手套一致，线索在此兑现。",
        position=resolution_position,
        sequence=2 if resolution_position == 1 else 1,
    )
    state = replay([plant(), resolution])
    hook = state.hooks["hook:glove"]
    assert hook.status == "resolved" and hook.resolution_timing == timing
    assert (
        hook.resolved_by == "resolve"
        and hook.resolution_explanation == resolution["explanation"]
    )
    assert hook.payoff_condition == "通过独特针脚识别手套的主人。"
    assert state.event_sources[hook.resolved_by].quote == resolution["source"]["quote"]


def test_hook_lifecycle_rejects_missing_plant_duplicate_resolution_and_empty_explanation():
    resolution = event(
        "resolve",
        "hook.resolve",
        hook_id="hook:glove",
        explanation="作者确认线索已兑现。",
        position=3,
    )
    assert_error([resolution], "hook_not_planted", "resolve")
    again = {
        **resolution,
        "id": "resolve-again",
        "position": 4,
        "source": {**resolution["source"], "chapter_id": "chapter-4"},
    }
    assert_error([plant(), resolution, again], "hook_already_resolved", "resolve-again")
    blank = {**resolution, "explanation": " \n "}
    assert_error([plant(), blank], "invalid_event_schema", "resolve")
    assert_error([plant(start=4, end=2)], "invalid_event_schema", "plant")
    duplicate_plant = {**plant(), "id": "plant-again", "sequence": 2}
    assert_error([plant(), duplicate_plant], "hook_already_planted", "plant-again")


def test_chapter_position_and_self_transfer_ambiguities_are_rejected():
    first = establish("first", "actor:a", 1)
    inconsistent = establish(
        "wrong-position", "actor:b", 0, position=2, chapter="chapter-1"
    )
    assert_error(
        [first, inconsistent], "inconsistent_chapter_position", "wrong-position"
    )
    collision = establish(
        "second-chapter", "actor:b", 0, sequence=2, chapter="another-chapter"
    )
    assert_error([first, collision], "chapter_position_collision", "second-chapter")
    self_transfer = transfer("self", "actor:a", "actor:a", 1, position=2)
    assert_error([first, self_transfer], "self_transfer", "self")


class TestIndependentAcceptanceScenarios:
    """Different stories and manual end-state oracles, separate from pill fixtures."""

    def test_caravan_water_accounting_and_knowledge_boundary(self):
        initial = [
            establish(
                "water-owner",
                "person:guide",
                8,
                resource="item:water-flask",
                position=0,
                sequence=1,
            ),
            establish(
                "water-carrier",
                "person:carrier",
                3,
                resource="item:water-flask",
                position=0,
                sequence=2,
            ),
        ]
        actions = [
            transfer(
                "water-gift",
                "person:guide",
                "person:carrier",
                3,
                resource="item:water-flask",
                sequence=1,
            ),
            consume(
                "water-camp",
                "person:carrier",
                4,
                resource="item:water-flask",
                sequence=2,
            ),
            event(
                "guide-route",
                "knowledge.learn",
                actor_id="person:guide",
                fact_id="truth:dry-well",
                fact="东井已经干涸。",
                sequence=3,
            ),
        ]
        snapshot = replay(actions, seed_events=initial)
        assert snapshot.quantity("person:guide", "item:water-flask") == 5
        assert snapshot.quantity("person:carrier", "item:water-flask") == 2
        assert snapshot.quantity("person:scout", "item:water-flask") is None
        assert snapshot.resource_totals["item:water-flask"].model_dump() == {
            "established": 11,
            "remaining": 7,
            "consumed": 4,
        }
        assert snapshot.knows("person:guide", "truth:dry-well")
        assert not snapshot.knows("person:carrier", "truth:dry-well")

    def test_library_manuscript_and_separate_information_acquisition(self):
        events = [
            establish(
                "library-initial",
                "role:librarian",
                4,
                resource="item:blank-page",
                sequence=1,
            ),
            establish(
                "scribe-initial",
                "role:scribe",
                0,
                resource="item:blank-page",
                sequence=2,
            ),
            transfer(
                "pages-issued",
                "role:librarian",
                "role:scribe",
                2,
                resource="item:blank-page",
                sequence=3,
            ),
            consume(
                "page-written", "role:scribe", 1, resource="item:blank-page", sequence=4
            ),
            event(
                "scribe-seal",
                "knowledge.learn",
                actor_id="role:scribe",
                fact_id="truth:seal",
                fact="封印从未被打开。",
                position=2,
                sequence=1,
            ),
            event(
                "scribe-name",
                "knowledge.learn",
                actor_id="role:scribe",
                fact_id="truth:owner",
                fact="卷轴主人姓林。",
                position=2,
                sequence=2,
            ),
            event(
                "messenger-name",
                "knowledge.learn",
                actor_id="role:messenger",
                fact_id="truth:owner",
                fact="卷轴主人姓林。",
                position=2,
                sequence=3,
            ),
        ]
        snapshot = replay(events)
        assert snapshot.quantity("role:librarian", "item:blank-page") == 2
        assert snapshot.quantity("role:scribe", "item:blank-page") == 1
        assert snapshot.resource_totals["item:blank-page"].consumed == 1
        assert set(snapshot.knowledge["role:scribe"]) == {"truth:seal", "truth:owner"}
        assert set(snapshot.knowledge["role:messenger"]) == {"truth:owner"}
        assert snapshot.facts["truth:seal"] == "封印从未被打开。"

    def test_glove_payoff_claim_is_not_a_programmatic_literary_quality_score(self):
        plant_event = event(
            "glove-planted",
            "hook.plant",
            hook_id="clue:blue-glove",
            description="雨夜门槛上留下半只蓝手套。",
            payoff_condition="独特针脚应帮助确认来访者。",
            window_start=2,
            window_end=5,
        )
        resolution = event(
            "glove-confirmed",
            "hook.resolve",
            hook_id="clue:blue-glove",
            position=7,
            explanation="作者认为证人认出针脚已完成这条线索；是否充分说服读者需另评。",
        )
        resolution["source"]["quote"] = "她指着针脚说，这只手套是我缝给兄长的。"
        snapshot = replay([plant_event, resolution])
        hook = snapshot.hooks["clue:blue-glove"]
        assert hook.status == "resolved" and hook.resolution_timing == "late"
        assert hook.resolution_explanation == resolution["explanation"]
        assert "quality_score" not in snapshot.model_dump()
        assert (
            validate_event(plant_event).source.quote == plant_event["source"]["quote"]
        )
