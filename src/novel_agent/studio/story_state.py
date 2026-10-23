"""Deterministic replay of a small, explicitly confirmed story-event vocabulary.

This reducer checks *event preconditions*, not whether prose entails an event.
``status='confirmed'`` must only be supplied after the application's author
approval step. Planned, negated, speculative and merely attempted actions are
not events in this vocabulary. A negative proposition can still be a learned
fact ("the door was not locked"); it is preserved verbatim, never inverted.
Source quotes are provenance, not a natural-language truth/entailment test.
Extraction accuracy and reducer correctness therefore require separate evals.

Resources are discrete units (use an explicit smallest unit for half-pills).
An absent holder/resource slot is UNKNOWN, not zero. Both transfer endpoints
must have an explicit resource.establish event; establish cannot top up/reset
an existing slot. No mint/reward operation is silently inferred. Every replay
is rebuilt from accepted events, so replacing a historical source version
removes its effects. A now-unsatisfied downstream event raises instead of
being quietly skipped.

``position`` is the global chapter position and ``sequence`` the chapter-local
event order, both supplied explicitly. Events sort by that pair; distinct
events cannot share it. ``before_position`` is an exclusive chapter boundary.
When provided, ``source_versions`` is the COMPLETE active chapter/version
manifest: absent or nonmatching sources are excluded and reported. Without a
manifest, mixed versions of one chapter are rejected. Seeds use the same
source/order/filter rules and may only explicitly establish initial resources.

Hook resolution records the author's interpretation plus its source and timing.
It does not prove that a payoff is artistically satisfying or meets the natural
language payoff condition. Early/late resolution is recorded, not rewritten.
This module performs no I/O, model calls, inference, or mutation of input data.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
    model_validator,
)

EntityID = Annotated[
    str,
    StringConstraints(
        min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
    ),
]
NonBlank = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Position = Annotated[int, Field(ge=0)]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]


class StrictStateModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )


class EventSource(StrictStateModel):
    chapter_id: EntityID
    version: PositiveInt
    quote: NonBlank


class ConfirmedEvent(StrictStateModel):
    id: EntityID
    position: Position
    sequence: PositiveInt
    source: EventSource
    status: Literal["confirmed"]


class ResourceEstablish(ConfirmedEvent):
    operation: Literal["resource.establish"]
    holder_id: EntityID
    resource_id: EntityID
    quantity: NonNegativeInt


class ResourceTransfer(ConfirmedEvent):
    operation: Literal["resource.transfer"]
    from_holder_id: EntityID
    to_holder_id: EntityID
    resource_id: EntityID
    quantity: PositiveInt


class ResourceConsume(ConfirmedEvent):
    operation: Literal["resource.consume"]
    holder_id: EntityID
    resource_id: EntityID
    quantity: PositiveInt


class KnowledgeLearn(ConfirmedEvent):
    operation: Literal["knowledge.learn"]
    actor_id: EntityID
    fact_id: EntityID
    fact: NonBlank


class HookPlant(ConfirmedEvent):
    operation: Literal["hook.plant"]
    hook_id: EntityID
    description: NonBlank
    payoff_condition: NonBlank
    window_start: Position
    window_end: Position

    @model_validator(mode="after")
    def valid_window(self):
        if not self.position <= self.window_start <= self.window_end:
            raise ValueError("伏笔窗口必须满足 position <= window_start <= window_end")
        return self


class HookResolve(ConfirmedEvent):
    operation: Literal["hook.resolve"]
    hook_id: EntityID
    explanation: NonBlank


StoryEvent = Annotated[
    ResourceEstablish
    | ResourceTransfer
    | ResourceConsume
    | KnowledgeLearn
    | HookPlant
    | HookResolve,
    Field(discriminator="operation"),
]
EVENT_ADAPTER = TypeAdapter(StoryEvent)


class InventoryState(StrictStateModel):
    quantity: NonNegativeInt
    established_by: EntityID
    updated_by: EntityID


class KnowledgeState(StrictStateModel):
    fact_id: EntityID
    learned_at: Position
    learned_sequence: PositiveInt
    learned_by_event: EntityID
    evidence_event_ids: list[EntityID]


class HookState(StrictStateModel):
    description: NonBlank
    payoff_condition: NonBlank
    window_start: Position
    window_end: Position
    status: Literal["open", "resolved"]
    planted_by: EntityID
    resolved_by: EntityID | None = None
    resolution_explanation: str | None = None
    resolution_timing: Literal["early", "on_time", "late"] | None = None


class ResourceTotals(StrictStateModel):
    established: NonNegativeInt
    remaining: NonNegativeInt
    consumed: NonNegativeInt


class ExcludedEvent(StrictStateModel):
    event_id: EntityID
    reason: Literal[
        "at_or_after_cutoff", "source_not_active", "source_version_obsolete"
    ]


class StorySnapshot(StrictStateModel):
    schema_version: Literal["story-state-v1"] = "story-state-v1"
    inventories: dict[EntityID, dict[EntityID, InventoryState]]
    facts: dict[EntityID, NonBlank]
    knowledge: dict[EntityID, dict[EntityID, KnowledgeState]]
    hooks: dict[EntityID, HookState]
    resource_totals: dict[EntityID, ResourceTotals]
    applied_event_ids: list[EntityID]
    event_sources: dict[EntityID, EventSource]
    excluded_events: list[ExcludedEvent]

    def quantity(self, holder_id: str, resource_id: str) -> int | None:
        """None is unknown; an explicitly established/depleted slot returns 0."""
        entry = self.inventories.get(holder_id, {}).get(resource_id)
        return entry.quantity if entry is not None else None

    def knows(self, actor_id: str, fact_id: str) -> bool:
        """Membership means acquired in the supplied confirmed history only."""
        return fact_id in self.knowledge.get(actor_id, {})


class StoryReplayError(ValueError):
    def __init__(
        self,
        event_id: str,
        code: str,
        precondition: str,
        *,
        details: dict | None = None,
    ):
        self.event_id = event_id
        self.code = code
        self.precondition = precondition
        self.details = details or {}
        super().__init__(f"事件 {event_id} [{code}]：前置条件不满足：{precondition}")

    def as_dict(self) -> dict:
        return {
            "event_id": self.event_id,
            "code": self.code,
            "precondition": self.precondition,
            "details": self.details,
        }


def validate_event(value: dict | StoryEvent) -> StoryEvent:
    """Strictly validate a proposal; validation does not authorize its approval."""
    try:
        return EVENT_ADAPTER.validate_python(value)
    except ValidationError as exc:
        event_id = str(
            value.get("id", "<unknown>")
            if isinstance(value, dict)
            else getattr(value, "id", "<unknown>")
        )
        raise StoryReplayError(
            event_id,
            "invalid_event_schema",
            "必须为有来源、明确已确认且字段类型严格的受支持事件",
            details={
                "validation": exc.errors(include_input=False, include_context=False)
            },
        ) from exc


def replay(
    events: Iterable[dict | StoryEvent],
    *,
    before_position: int | None = None,
    source_versions: Mapping[str, int] | None = None,
    seed_events: Iterable[dict | StoryEvent] = (),
) -> StorySnapshot:
    """Recompute state or raise with the failing event and its precondition.

    Identical event IDs are idempotent only when every typed field agrees.
    Conflicting duplicate IDs fail even if a version/cutoff would exclude one.
    A version manifest filters provenance; it does not silently repair events
    whose required earlier events vanished after a historical replacement.
    """
    if before_position is not None and (
        type(before_position) is not int or before_position < 0
    ):
        raise ValueError("before_position 必须为非负整数或None")
    if source_versions is not None and any(
        not isinstance(chapter, str) or type(version) is not int or version <= 0
        for chapter, version in source_versions.items()
    ):
        raise ValueError("source_versions 必须是完整的章节ID到正整数版本号映射")
    by_id: dict[str, StoryEvent] = {}
    for raw, is_seed in [(value, True) for value in seed_events] + [
        (value, False) for value in events
    ]:
        event = validate_event(raw)
        if is_seed and not isinstance(event, ResourceEstablish):
            raise StoryReplayError(
                event.id, "invalid_seed_operation", "seed_events 只能明确建立初始资源"
            )
        prior = by_id.get(event.id)
        if prior is not None and prior != event:
            raise StoryReplayError(
                event.id, "conflicting_event_id", "相同事件ID必须具有完全相同内容"
            )
        by_id[event.id] = event

    ordered = sorted(
        by_id.values(), key=lambda event: (event.position, event.sequence, event.id)
    )
    accepted: list[StoryEvent] = []
    excluded: list[dict] = []
    clocks: dict[tuple[int, int], str] = {}
    chapter_positions: dict[str, int] = {}
    position_chapters: dict[int, str] = {}
    chapter_versions: dict[str, int] = {}
    for event in ordered:
        reason = None
        if before_position is not None and event.position >= before_position:
            reason = "at_or_after_cutoff"
        elif source_versions is not None:
            if event.source.chapter_id not in source_versions:
                reason = "source_not_active"
            elif source_versions[event.source.chapter_id] != event.source.version:
                reason = "source_version_obsolete"
        if reason is not None:
            excluded.append({"event_id": event.id, "reason": reason})
            continue
        chapter = event.source.chapter_id
        if (
            chapter in chapter_versions
            and chapter_versions[chapter] != event.source.version
        ):
            raise StoryReplayError(
                event.id,
                "ambiguous_source_version",
                "同一章节只能回放一个有效版本；请提供完整source_versions",
            )
        if (
            chapter in chapter_positions
            and chapter_positions[chapter] != event.position
        ):
            raise StoryReplayError(
                event.id,
                "inconsistent_chapter_position",
                "同一章节的事件必须使用相同全局position",
            )
        if (
            event.position in position_chapters
            and position_chapters[event.position] != chapter
        ):
            raise StoryReplayError(
                event.id,
                "chapter_position_collision",
                "全局position必须唯一对应一个章节",
            )
        clock = (event.position, event.sequence)
        if clock in clocks:
            raise StoryReplayError(
                event.id,
                "ambiguous_event_order",
                "同章sequence不能重复",
                details={"other_event_id": clocks[clock]},
            )
        clocks[clock] = event.id
        chapter_versions[chapter] = event.source.version
        chapter_positions[chapter] = event.position
        position_chapters[event.position] = chapter
        accepted.append(event)

    inventories: dict[str, dict[str, dict]] = {}
    facts: dict[str, str] = {}
    knowledge: dict[str, dict[str, dict]] = {}
    hooks: dict[str, dict] = {}
    established: dict[str, int] = {}
    consumed: dict[str, int] = {}

    def inventory(event: StoryEvent, holder: str, resource: str) -> dict:
        entry = inventories.get(holder, {}).get(resource)
        if entry is None:
            raise StoryReplayError(
                event.id,
                "unknown_inventory",
                "库存未知，必须先有明确resource.establish，不能默认余额为0",
                details={"holder_id": holder, "resource_id": resource},
            )
        return entry

    def enough(event: ResourceTransfer | ResourceConsume, entry: dict):
        if entry["quantity"] < event.quantity:
            raise StoryReplayError(
                event.id,
                "insufficient_resource",
                "已确认库存必须足够支付本次转移或消耗",
                details={
                    "available": entry["quantity"],
                    "required": event.quantity,
                    "resource_id": event.resource_id,
                },
            )

    for event in accepted:
        if isinstance(event, ResourceEstablish):
            holder = inventories.setdefault(event.holder_id, {})
            if event.resource_id in holder:
                raise StoryReplayError(
                    event.id,
                    "inventory_already_established",
                    "初始库存只能建立一次，不能用establish覆盖或无证增资",
                )
            holder[event.resource_id] = {
                "quantity": event.quantity,
                "established_by": event.id,
                "updated_by": event.id,
            }
            established[event.resource_id] = (
                established.get(event.resource_id, 0) + event.quantity
            )
        elif isinstance(event, ResourceTransfer):
            if event.from_holder_id == event.to_holder_id:
                raise StoryReplayError(
                    event.id, "self_transfer", "转移双方必须是不同持有人"
                )
            source = inventory(event, event.from_holder_id, event.resource_id)
            destination = inventory(event, event.to_holder_id, event.resource_id)
            enough(event, source)
            source["quantity"] -= event.quantity
            destination["quantity"] += event.quantity
            source["updated_by"] = destination["updated_by"] = event.id
        elif isinstance(event, ResourceConsume):
            source = inventory(event, event.holder_id, event.resource_id)
            enough(event, source)
            source["quantity"] -= event.quantity
            source["updated_by"] = event.id
            consumed[event.resource_id] = (
                consumed.get(event.resource_id, 0) + event.quantity
            )
        elif isinstance(event, KnowledgeLearn):
            if event.fact_id in facts and facts[event.fact_id] != event.fact:
                raise StoryReplayError(
                    event.id,
                    "conflicting_fact_identity",
                    "稳定fact_id不能表示不同命题，请分配新的事实ID",
                )
            facts[event.fact_id] = event.fact
            actor = knowledge.setdefault(event.actor_id, {})
            if event.fact_id in actor:
                actor[event.fact_id]["evidence_event_ids"].append(event.id)
            else:
                actor[event.fact_id] = {
                    "fact_id": event.fact_id,
                    "learned_at": event.position,
                    "learned_sequence": event.sequence,
                    "learned_by_event": event.id,
                    "evidence_event_ids": [event.id],
                }
        elif isinstance(event, HookPlant):
            if event.hook_id in hooks:
                raise StoryReplayError(
                    event.id,
                    "hook_already_planted",
                    "同一hook_id只能埋设一次，复用意象应创建新伏笔ID",
                )
            hooks[event.hook_id] = {
                "description": event.description,
                "payoff_condition": event.payoff_condition,
                "window_start": event.window_start,
                "window_end": event.window_end,
                "status": "open",
                "planted_by": event.id,
            }
        elif isinstance(event, HookResolve):
            hook = hooks.get(event.hook_id)
            if hook is None:
                raise StoryReplayError(
                    event.id, "hook_not_planted", "兑现前必须存在同一hook_id的埋设事件"
                )
            if hook["status"] == "resolved":
                raise StoryReplayError(
                    event.id, "hook_already_resolved", "同一伏笔不能重复兑现"
                )
            timing = (
                "early"
                if event.position < hook["window_start"]
                else "late"
                if event.position > hook["window_end"]
                else "on_time"
            )
            hook.update(
                status="resolved",
                resolved_by=event.id,
                resolution_explanation=event.explanation,
                resolution_timing=timing,
            )

    totals = {
        resource: {
            "established": total,
            "remaining": sum(
                holder.get(resource, {}).get("quantity", 0)
                for holder in inventories.values()
            ),
            "consumed": consumed.get(resource, 0),
        }
        for resource, total in established.items()
    }
    if any(
        total["established"] != total["remaining"] + total["consumed"]
        for total in totals.values()
    ):
        raise AssertionError("资源回放内部守恒错误")
    return StorySnapshot(
        inventories=inventories,
        facts=facts,
        knowledge=knowledge,
        hooks=hooks,
        resource_totals=totals,
        applied_event_ids=[event.id for event in accepted],
        event_sources={event.id: event.source for event in accepted},
        excluded_events=excluded,
    )
