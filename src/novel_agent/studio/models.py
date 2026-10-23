from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    computed_field,
    model_validator,
)

from .story_state import EntityID, NonBlank, NonNegativeInt, PositiveInt, Position

MEMORY_NAMES = [
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
]
WEIGHTS = {"剧情一致性": 0.30, "文笔流畅度": 0.25, "人设吻合度": 0.25, "原创性": 0.20}


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def numbered_paragraphs(text: str):
    return [
        {"paragraph": i + 1, "text": p}
        for i, p in enumerate(p for p in text.split("\n") if p.strip())
    ]


def ground_citations(items, body: str):
    paragraphs = numbered_paragraphs(body)
    for item in items:
        if item.paragraph is not None:
            if item.paragraph > len(paragraphs):
                raise ValueError("引用段落编号超出正文范围")
            source = paragraphs[item.paragraph - 1]["text"]
            # Exact source text is selected by the application, never reconstructed
            # from the model's recollection of a sentence.
            if not item.quote or item.quote not in source:
                item.quote = source
        if not item.quote or item.quote not in body:
            raise ValueError(
                "引文不在正文中，请填写该段的 paragraph 编号，由程序取得原文"
            )


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectCreate(StrictModel):
    title: str = Field(min_length=1, max_length=80)
    genre: str = Field(default="玄幻成长", max_length=80)
    premise: str = Field(min_length=5, max_length=20000)
    style: str = Field(
        default="克制、有画面感。以行动与对话展示人物；成长必须付出代价。",
        max_length=8000,
    )
    target_chapters: int = Field(default=12, ge=1, le=3000)
    chapter_words: int = Field(default=2500, ge=200, le=12000)


class DirectionUpdate(StrictModel):
    premise: str = Field(min_length=5, max_length=20000)
    style: str = Field(min_length=1, max_length=8000)
    project_revision: int = Field(ge=0)


class AppendChapter(StrictModel):
    volume: int = Field(ge=1, le=9999)
    project_revision: int = Field(ge=0)
    title: str = Field(default="", max_length=150)
    goal: str = Field(default="", max_length=8000)
    target_words: int | None = Field(default=None, ge=200, le=12000)


class Scene(StrictModel):
    title: str
    goal: str
    conflict: str
    cost: str = ""
    change: str = ""


class ChapterPlan(StrictModel):
    title: str
    goal: str
    pov: str = "第三人称有限视角"
    characters: list[str] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    target_words: int = Field(default=2500, ge=200, le=12000)
    min_characters: int | None = Field(default=None, ge=100, le=20000)
    max_characters: int | None = Field(default=None, ge=100, le=20000)

    @model_validator(mode="after")
    def validate_character_range(self):
        if (self.min_characters is None) != (self.max_characters is None):
            raise ValueError("篇幅下限与上限需一起填写")
        if self.min_characters is not None and not (
            self.min_characters <= self.target_words <= self.max_characters
        ):
            raise ValueError("篇幅目标需位于下限和上限之间")
        return self


class Finding(StrictModel):
    dimension: str
    severity: Literal["critical", "warning", "suggestion"]
    paragraph: int | None = Field(
        default=None,
        ge=1,
        description="引用正文的段落编号，优先填写，由程序取得该段原文",
    )
    quote: str = Field(
        default="", description="可留空并填写paragraph；若填写必须为该段逐字连续原文"
    )
    evidence: str = ""
    source_chapter_id: str | None = None
    suggestion: str


class ReviewReport(StrictModel):
    scores: dict[str, float] = Field(
        description="四个 key 必须精确为：" + "、".join(WEIGHTS),
        json_schema_extra={
            "properties": {
                k: {"type": "number", "minimum": 0, "maximum": 10} for k in WEIGHTS
            },
            "required": list(WEIGHTS),
            "additionalProperties": False,
        },
    )
    findings: list[Finding] = Field(default_factory=list)
    summary: str = ""

    @model_validator(mode="after")
    def validate_scores(self):
        if set(self.scores) != set(WEIGHTS):
            raise ValueError(
                "必须给出四个维度的全部分数，key 精确为：" + "、".join(WEIGHTS)
            )
        if any(not 0 <= x <= 10 for x in self.scores.values()):
            raise ValueError("得分必须在 0–10 之间")
        return self

    @computed_field
    @property
    def total(self) -> float:
        return round(sum(self.scores[k] * w for k, w in WEIGHTS.items()), 2)

    @computed_field
    @property
    def passed(self) -> bool:
        return self.total >= 7 and not any(
            f.severity == "critical" for f in self.findings
        )


class MemoryPatch(StrictModel):
    name: str = Field(
        description="只可使用这些精确名称：" + "、".join(MEMORY_NAMES),
        json_schema_extra={"enum": MEMORY_NAMES},
    )
    content: str
    reason: str
    quotes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def valid_name(self):
        if self.name not in MEMORY_NAMES:
            raise ValueError("未知记忆文档")
        return self


class StoryFact(StrictModel):
    subject: str
    predicate: str
    value: str
    kind: Literal[
        "character",
        "item",
        "realm",
        "event",
        "hook",
        "relationship",
        "knowledge",
        "rule",
    ] = "event"
    status: str = "active"
    paragraph: int | None = Field(
        default=None, ge=1, description="支持该事实的正文段落编号，由程序取得原文"
    )
    quote: str = Field(
        default="", description="可留空并填写paragraph；若填写必须是该段逐字连续原文"
    )
    related: list[str] = Field(default_factory=list)


class StateChangeBase(StrictModel):
    """A proposal, never an authorized/source-bound event from model output."""

    model_config = ConfigDict(extra="forbid", strict=True)
    paragraph: int = Field(ge=1, description="本章正文支持该变化的段落编号")
    quote: str = Field(default="", description="逐字引文，可留空由应用按paragraph取得")
    labels: dict[EntityID, NonBlank] = Field(
        default_factory=dict,
        description="稳定ASCII实体ID到中文显示名的映射，沿用已知ID，不以显示名作为身份",
    )


class EstablishChange(StateChangeBase):
    operation: Literal["resource.establish"]
    holder_id: EntityID
    resource_id: EntityID
    quantity: NonNegativeInt


class TransferChange(StateChangeBase):
    operation: Literal["resource.transfer"]
    from_holder_id: EntityID
    to_holder_id: EntityID
    resource_id: EntityID
    quantity: PositiveInt


class ConsumeChange(StateChangeBase):
    operation: Literal["resource.consume"]
    holder_id: EntityID
    resource_id: EntityID
    quantity: PositiveInt


class LearnChange(StateChangeBase):
    operation: Literal["knowledge.learn"]
    actor_id: EntityID
    fact_id: EntityID
    fact: NonBlank


class PlantChange(StateChangeBase):
    operation: Literal["hook.plant"]
    hook_id: EntityID
    description: NonBlank
    payoff_condition: NonBlank
    window_start: Position
    window_end: Position


class ResolveChange(StateChangeBase):
    operation: Literal["hook.resolve"]
    hook_id: EntityID
    explanation: NonBlank


StateChange = Annotated[
    EstablishChange
    | TransferChange
    | ConsumeChange
    | LearnChange
    | PlantChange
    | ResolveChange,
    Field(discriminator="operation"),
]
STATE_CHANGE_ADAPTER = TypeAdapter(StateChange)


def ground_state_changes(changes, body: str):
    """Fill omitted quotes only; supplied wrong quotes must fail, never be repaired."""
    paragraphs = numbered_paragraphs(body)
    for change in changes:
        if change.paragraph > len(paragraphs):
            raise ValueError("状态提案引用段落超出正文")
        source = paragraphs[change.paragraph - 1]["text"]
        if change.quote and change.quote not in source:
            raise ValueError("状态提案引文与指定正文段落不一致")
        if not change.quote:
            change.quote = source


class MemoryProposal(StrictModel):
    patches: list[MemoryPatch] = Field(default_factory=list)
    facts: list[StoryFact] = Field(default_factory=list)
    state_changes: list[StateChange] = Field(
        default_factory=list,
        max_length=12,
        description="可选的已发生状态变化提案；不是正式事件，需作者逐项明确接受，不得把未知数量补0或将计划/否定变成发生",
    )
    chapter_summary: str
    next_plan: ChapterPlan

    @model_validator(mode="before")
    @classmethod
    def ignore_application_owned_documents(cls, value):
        # These documents are rebuilt from verified summaries/plans by the
        # application. Model output has no write authority over them.
        if isinstance(value, dict) and isinstance(value.get("patches"), list):
            return value | {
                "patches": [
                    patch
                    for patch in value["patches"]
                    if not isinstance(patch, dict)
                    or patch.get("name") not in ("目前剧情", "当前章节设计规划")
                ]
            }
        return value

    @model_validator(mode="after")
    def unique_memory_documents(self):
        names = [patch.name for patch in self.patches]
        if len(names) != len(set(names)):
            raise ValueError("同一记忆文档不能重复提案，请合并为一份完整内容")
        return self


class Blueprint(StrictModel):
    logline: str
    alternatives: list[str]
    outline: str
    memory: dict[str, str] = Field(
        description="十份文档的完整内容，key 必须精确为：" + "、".join(MEMORY_NAMES),
        json_schema_extra={
            "propertyNames": {"enum": MEMORY_NAMES},
            "minProperties": 10,
            "maxProperties": 10,
        },
    )
    chapters: list[ChapterPlan]

    @model_validator(mode="after")
    def valid_memory(self):
        if set(self.memory) != set(MEMORY_NAMES):
            raise ValueError("必须包含十份核心记忆")
        return self


class ContextPack(StrictModel):
    project_id: str
    project_revision: int
    memory: dict[str, str]
    previous_chapter: str
    recent_summaries: list[dict]
    evidence: list[dict]
    facts: list[dict]
    retrieval_mode: str
    story_state: dict = Field(default_factory=dict)


class RunCreate(StrictModel):
    project_id: str
    kind: Literal["blueprint", "chapter", "review", "rewrite", "volume"] = "chapter"
    chapter_id: str | None = None
    instruction: str = Field(default="", max_length=12000)
    selection: str = Field(default="", max_length=24000)
    ablation: Literal["full", "baseline", "no_memory", "no_revision"] = "full"
    auto_revise: bool = True
    context_budget_bytes: int = Field(
        default=60000,
        ge=12000,
        le=480000,
        description="仅给模型的context子包的UTF-8字节上限，不含正文、规划或schema；核心资料超限时停止并提示整理",
    )


class DraftSave(StrictModel):
    content: str = Field(max_length=100000)
    title: str = Field(max_length=150)
    base_revision: int


class Approve(StrictModel):
    base_revision: int
    project_revision: int
    accepted_patches: list[str] = Field(default_factory=list)
    overrides: dict[str, str] = Field(default_factory=dict)
    note: str = ""
    accepted_facts: list[int] | None = None
    accepted_state_changes: list[Annotated[int, Field(ge=0, strict=True)]] = Field(
        default_factory=list,
        max_length=12,
        description="作者明确勾选的state_changes下标；默认不接受任何状态事件，旧客户端不会自动启用",
    )
    summary_override: str | None = Field(default=None, min_length=1, max_length=3000)


class StateProposalUpdate(StrictModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    base_revision: int = Field(ge=0)
    project_revision: int = Field(ge=0)
    state_changes: list[StateChange] = Field(max_length=12)


class SettingsUpdate(StrictModel):
    api_key: str | None = None
    budget: float = Field(default=300, gt=0, le=300)
    vector_enabled: bool = False
    retrieval_policy: Literal["keyword", "semantic", "hybrid", "reranked"] = "keyword"


class BlindVote(StrictModel):
    preference: Literal["a", "b", "tie"]
    reason: str = Field(default="", max_length=3000)
