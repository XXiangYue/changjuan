"""API/graph/SQLite integration with an offline deterministic model fixture.

No legacy manuscript is changed. All projects/data live under pytest tmp_path;
fault-injected proposal JSON tests transaction rollback, not model accuracy.
"""

import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from novel_agent.studio.api import create_app
from novel_agent.studio.db import encode
from novel_agent.studio.models import Approve, ChapterPlan, MemoryProposal
from novel_agent.studio.repository import Conflict
from tests.test_studio import FixtureModel, create, draft, setup, wait

FIRST_PARAGRAPHS = [
    "沈砚清点药匣，里面只有两枚温肺丹。",
    "洛蘅摊开空药袋，温肺丹一枚也没有。",
    "沈砚把一枚温肺丹交给洛蘅。",
    "洛蘅看见炉底密印，得知旧炉由闻家铸造。",
    "石门浮出半枚铜纹，没人知道另半枚在哪里。",
]
SECOND_PARAGRAPHS = [
    "洛蘅吞下一枚温肺丹，药袋里再无余丹。",
    "沈砚听洛蘅亲口说明，旧炉由闻家铸造。",
    "石门另一半铜纹印在钥匙背面，谜底完整了。",
]
LABELS = {
    "actor:shen": "沈砚",
    "actor:luo": "洛蘅",
    "item:pill": "温肺丹",
    "fact:furnace-maker": "旧炉铸造者",
    "hook:copper-mark": "半枚铜纹",
}


def body(paragraphs):
    result = "\n\n".join(paragraphs)
    while len(result) < 450:
        result += "\n炉火渐渐暗下去，二人收好手边的器具，等夜风从窗外吹过。"
    return result


def first_changes(quantity=2):
    def proposal(operation, paragraph, **fields):
        return {
            "operation": operation,
            "paragraph": paragraph,
            "labels": LABELS,
            **fields,
        }

    return [
        proposal(
            "resource.establish",
            1,
            holder_id="actor:shen",
            resource_id="item:pill",
            quantity=quantity,
        ),
        proposal(
            "resource.establish",
            2,
            holder_id="actor:luo",
            resource_id="item:pill",
            quantity=0,
        ),
        proposal(
            "resource.transfer",
            3,
            from_holder_id="actor:shen",
            to_holder_id="actor:luo",
            resource_id="item:pill",
            quantity=1,
        ),
        proposal(
            "knowledge.learn",
            4,
            actor_id="actor:luo",
            fact_id="fact:furnace-maker",
            fact="旧炉由闻家铸造。",
        ),
        proposal(
            "hook.plant",
            5,
            hook_id="hook:copper-mark",
            description="石门出现半枚铜纹。",
            payoff_condition="找到另一半铜纹并说明用途。",
            window_start=100002,
            window_end=100003,
        ),
    ]


def second_changes():
    return [
        {
            "operation": "resource.consume",
            "paragraph": 1,
            "holder_id": "actor:luo",
            "resource_id": "item:pill",
            "quantity": 1,
            "labels": LABELS,
        },
        {
            "operation": "knowledge.learn",
            "paragraph": 2,
            "actor_id": "actor:shen",
            "fact_id": "fact:furnace-maker",
            "fact": "旧炉由闻家铸造。",
            "labels": LABELS,
        },
        {
            "operation": "hook.resolve",
            "paragraph": 3,
            "hook_id": "hook:copper-mark",
            "explanation": "作者确认钥匙背面的另一半铜纹回应此前线索。",
            "labels": LABELS,
        },
    ]


class StateFixtureModel(FixtureModel):
    def __init__(self):
        super().__init__()
        self.initial_quantity = 2
        self.override_changes = None

    async def text(self, rid, role, system, prompt, **kwargs):
        chapter = json.JSONDecoder().raw_decode(prompt)[0]["chapter"]
        return body(FIRST_PARAGRAPHS if chapter == 1 else SECOND_PARAGRAPHS)

    async def structured(self, rid, role, system, prompt, schema, **kwargs):
        if schema is not MemoryProposal:
            return await super().structured(rid, role, system, prompt, schema, **kwargs)
        payload = json.loads(prompt)
        is_first = "清点药匣" in payload["chapter"][0]["text"]
        changes = (
            self.override_changes
            if self.override_changes is not None
            else first_changes(self.initial_quantity)
            if is_first
            else second_changes()
        )
        proposal = MemoryProposal(
            patches=[
                {
                    "name": "主角形象状态",
                    "content": "状态由正文重新确认。",
                    "reason": "离线事务夹具",
                }
            ],
            facts=[
                {
                    "subject": "本章资源",
                    "predicate": "记载",
                    "value": "以正文为准",
                    "kind": "item",
                    "paragraph": 1,
                }
            ],
            state_changes=copy.deepcopy(changes),
            chapter_summary="已发生的行动，待作者确认。",
            next_plan=ChapterPlan(title="下一章", goal="查证铜纹", target_words=500),
        )
        kwargs["validator"](proposal)
        return proposal


@pytest.fixture
def state_client(tmp_path):
    model = StateFixtureModel()
    with TestClient(create_app(tmp_path, model)) as client:
        yield client, model


def prepare(client):
    return setup(client, create(client))


def accept(client, project, run_id, chapter, selected=None, *, expect="completed"):
    payload = {
        "base_revision": chapter["draft_revision"],
        "project_revision": project["revision"],
        "accepted_patches": [item["name"] for item in chapter["proposal"]["patches"]],
    }
    if selected is not None:
        payload["accepted_state_changes"] = selected
    response = client.post(f"/api/runs/{run_id}/approve", json=payload)
    assert response.status_code == 202, response.text
    if expect == "failed":
        import time

        for _ in range(300):
            current = client.get(f"/api/runs/{run_id}").json()
            if current["status"] == "failed":
                return current
            time.sleep(0.01)
        pytest.fail("expected failed semantic commit")
    wait(client, run_id, "completed")
    return client.get(f"/api/projects/{project['id']}").json()


def test_real_api_preview_then_explicit_subset_commits_bound_events_only(state_client):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    preview = client.get(
        f"/api/chapters/{chapter['id']}/state-preview?selected=0,1,3"
    ).json()
    assert preview["status"] == "ready" and preview["author_confirmation_required"]
    assert (
        preview["snapshot"]["inventories"]["actor:shen"]["item:pill"]["quantity"] == 2
    )
    assert preview["entity_labels"]["actor:luo"] == "洛蘅"
    assert "洛蘅" in preview["changes"][3]["summary"]
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM story_events")["n"] == 0
    project = accept(client, project, run_id, chapter, [0, 1, 3])
    report = client.get(f"/api/projects/{project['id']}/story-state").json()
    snapshot = report["snapshot"]
    assert report["status"] == "ready" and not report["empty"]
    assert snapshot["inventories"]["actor:shen"]["item:pill"]["quantity"] == 2
    assert snapshot["inventories"]["actor:luo"]["item:pill"]["quantity"] == 0
    assert set(snapshot["knowledge"]) == {"actor:luo"} and snapshot["hooks"] == {}
    rows = client.app.state.db.all("SELECT * FROM story_events ORDER BY sequence")
    assert [row["sequence"] for row in rows] == [1, 2, 4]
    for row in rows:
        bound = json.loads(row["event"])
        assert bound["source"]["chapter_id"] == chapter["id"]
        assert bound["source"]["version"] == row["version"] == 1
        assert (
            bound["source"]["quote"] in chapter["content"]
            and bound["status"] == "confirmed"
        )
        assert bound["id"] == row["id"]


def test_old_client_without_explicit_checks_does_not_write_any_state_event(
    state_client,
):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    assert len(chapter["proposal"]["state_changes"]) == 5
    project = accept(
        client, project, run_id, chapter
    )  # no accepted_state_changes field
    assert project["chapters"][0]["canonical_revision"] == 1
    report = client.get(f"/api/projects/{project['id']}/story-state").json()
    assert report["empty"] and report["snapshot"]["inventories"] == {}
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM story_events")["n"] == 0


def test_legacy_proposal_without_state_changes_still_finalizes_and_is_not_inferred(
    state_client,
):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    del chapter["proposal"]["state_changes"]
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?",
        (encode(chapter["proposal"]), chapter["id"]),
    )
    preview = client.get(f"/api/chapters/{chapter['id']}/state-preview").json()
    assert preview["status"] == "ready" and preview["changes"] == []
    accept(client, project, run_id, chapter)
    assert client.get(f"/api/projects/{project['id']}/story-state").json()["empty"]


def test_unknown_resource_blocks_preview_but_knowledge_and_hook_can_be_selected(
    state_client,
):
    client, model = state_client
    model.override_changes = first_changes()[
        2:
    ]  # no explicit initial resource proposals
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    invalid = client.get(f"/api/chapters/{chapter['id']}/state-preview").json()
    assert invalid["status"] == "invalid" and invalid["snapshot"] is None
    assert invalid["errors"][0]["code"] == "unknown_inventory"
    empty = client.get(f"/api/chapters/{chapter['id']}/state-preview?selected=").json()
    assert empty["status"] == "ready" and empty["selected"] == []
    selected = client.get(
        f"/api/chapters/{chapter['id']}/state-preview?selected=1,2"
    ).json()
    assert selected["status"] == "ready"
    project = accept(client, project, run_id, chapter, [1, 2])
    snapshot = client.get(f"/api/projects/{project['id']}/story-state").json()[
        "snapshot"
    ]
    assert snapshot["inventories"] == {}
    assert set(snapshot["knowledge"]["actor:luo"]) == {"fact:furnace-maker"}
    assert snapshot["hooks"]["hook:copper-mark"]["status"] == "open"


def test_rejecting_a_malformed_unselected_change_preserves_valid_knowledge_choice(
    state_client,
):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    damaged = copy.deepcopy(chapter["proposal"])
    damaged["state_changes"][2]["quantity"] = -1
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?",
        (encode(damaged), chapter["id"]),
    )
    preview = client.get(
        f"/api/chapters/{chapter['id']}/state-preview?selected=3"
    ).json()
    assert preview["status"] == "ready" and preview["errors"] == []
    assert not preview["changes"][2]["schema_valid"]
    project = accept(client, project, run_id, chapter, [3])
    snapshot = client.get(f"/api/projects/{project['id']}/story-state").json()[
        "snapshot"
    ]
    assert snapshot["inventories"] == {}
    assert set(snapshot["knowledge"]["actor:luo"]) == {"fact:furnace-maker"}
    assert len(snapshot["applied_event_ids"]) == 1


@pytest.mark.parametrize(
    "fault", ["wrong_quote", "illegal_quantity", "unknown_precondition"]
)
def test_invalid_selected_event_rolls_back_body_memory_facts_stale_flags_and_version(
    state_client, fault
):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    before_memory = copy.deepcopy(project["memory"])
    proposal = copy.deepcopy(chapter["proposal"])
    if fault == "wrong_quote":
        proposal["state_changes"][0]["quote"] = "这句引文并不存在。"
        selected = [0]
    elif fault == "illegal_quantity":
        proposal["state_changes"][0]["quantity"] = -1
        selected = [0]
    else:
        selected = [2]  # transfer selected without its initial inventory conditions
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?", (encode(proposal), chapter["id"])
    )
    preview = client.get(
        f"/api/chapters/{chapter['id']}/state-preview?selected={selected[0]}"
    ).json()
    assert preview["status"] == "invalid" and preview["errors"]
    failed = accept(client, project, run_id, chapter, selected, expect="failed")
    assert "事件" in failed["error"]
    after = client.get(f"/api/projects/{project['id']}").json()
    assert after["revision"] == project["revision"] and after["memory"] == before_memory
    assert after["chapters"][0]["canonical_revision"] == 0
    assert after["chapters"][0]["canonical"] == "" and after["facts"] == []
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM versions")["n"] == 0
    assert client.app.state.db.one("SELECT COUNT(*) AS n FROM story_events")["n"] == 0


def test_history_replacement_invalidates_event_suffix_and_replays_strict_before_state(
    state_client,
):
    client, model = state_client
    project = prepare(client)
    first_id, second_id = [chapter["id"] for chapter in project["chapters"]]
    run_id, first = draft(client, project, first_id)
    project = accept(client, project, run_id, first, [0, 1, 2, 3, 4])
    run_id, second = draft(client, project, second_id)
    assert (
        second["context"]["story_state"]["inventories"]["actor:luo"]["item:pill"][
            "quantity"
        ]
        == 1
    )
    project = accept(client, project, run_id, second, [0, 1, 2])
    all_state = client.get(f"/api/projects/{project['id']}/story-state").json()[
        "snapshot"
    ]
    assert all_state["inventories"]["actor:luo"]["item:pill"]["quantity"] == 0
    assert all_state["hooks"]["hook:copper-mark"]["status"] == "resolved"
    prior = client.get(
        f"/api/projects/{project['id']}/story-state?before=100002"
    ).json()["snapshot"]
    assert prior["inventories"]["actor:luo"]["item:pill"]["quantity"] == 1
    assert (
        "actor:shen" not in prior["knowledge"]
        and prior["hooks"]["hook:copper-mark"]["status"] == "open"
    )
    assert client.get(
        f"/api/projects/{project['id']}/story-state?before=100001"
    ).json()["empty"]

    first = client.get(f"/api/chapters/{first_id}").json()
    response = client.put(
        f"/api/chapters/{first_id}/draft",
        json={
            "content": first["content"].replace("两枚", "三枚"),
            "title": first["title"],
            "base_revision": first["draft_revision"],
        },
    )
    assert response.status_code == 200
    model.initial_quantity = 3
    response = client.post(
        "/api/runs",
        json={"project_id": project["id"], "chapter_id": first_id, "kind": "review"},
    )
    run_id = response.json()["id"]
    wait(client, run_id)
    first = client.get(f"/api/chapters/{first_id}").json()
    assert first["context"]["story_state"]["inventories"] == {}
    project = accept(client, project, run_id, first, [0, 1, 2, 3, 4])
    assert project["chapters"][1]["stale"] == 1
    replayed = client.get(f"/api/projects/{project['id']}/story-state").json()[
        "snapshot"
    ]
    assert replayed["inventories"]["actor:shen"]["item:pill"]["quantity"] == 2
    assert replayed["inventories"]["actor:luo"]["item:pill"]["quantity"] == 1
    assert replayed["hooks"]["hook:copper-mark"]["status"] == "open"
    rows = client.app.state.db.all("SELECT chapter_id,version,stale FROM story_events")
    assert len(rows) == 13 and sum(row["stale"] for row in rows) == 8
    assert all(row["version"] == 2 for row in rows if row["stale"] == 0)

    response = client.post(
        "/api/runs",
        json={"project_id": project["id"], "chapter_id": second_id, "kind": "review"},
    )
    run_id = response.json()["id"]
    wait(client, run_id)
    second = client.get(f"/api/chapters/{second_id}").json()
    project = accept(client, project, run_id, second, [0, 1, 2])
    final = client.get(f"/api/projects/{project['id']}/story-state").json()["snapshot"]
    assert final["inventories"]["actor:luo"]["item:pill"]["quantity"] == 0
    assert final["resource_totals"]["item:pill"] == {
        "established": 3,
        "remaining": 2,
        "consumed": 1,
    }
    assert final["hooks"]["hook:copper-mark"]["status"] == "resolved"
    assert client.post(f"/api/runs/{run_id}/approve", json={}).status_code == 409


def test_proposal_cannot_forge_event_authority_or_accept_invalid_index_types(
    state_client,
):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    malicious = copy.deepcopy(chapter["proposal"])
    malicious["state_changes"][0]["status"] = "confirmed"
    client.app.state.db.execute(
        "UPDATE chapters SET proposal=? WHERE id=?", (encode(malicious), chapter["id"])
    )
    preview = client.get(
        f"/api/chapters/{chapter['id']}/state-preview?selected=0"
    ).json()
    assert (
        preview["status"] == "invalid"
        and preview["errors"][0]["code"] == "invalid_state_proposal"
    )
    assert (
        client.get(
            f"/api/chapters/{chapter['id']}/state-preview?selected=0,0"
        ).status_code
        == 400
    )
    assert (
        client.get(
            f"/api/chapters/{chapter['id']}/state-preview?selected=-1"
        ).status_code
        == 400
    )
    assert (
        client.get(f"/api/projects/{project['id']}/story-state?before=-1").status_code
        == 422
    )
    rejected = client.post(
        f"/api/runs/{run_id}/approve",
        json={
            "base_revision": chapter["draft_revision"],
            "project_revision": project["revision"],
            "accepted_state_changes": [True],
        },
    )
    assert rejected.status_code == 400
    assert client.get(f"/api/runs/{run_id}").json()["status"] == "awaiting_approval"
    # Valid index types still cannot turn forged proposal authority into canon.
    failed = accept(client, project, run_id, chapter, [0], expect="failed")
    assert failed["status"] == "failed"


def test_tool_proposal_update_requires_fresh_cas_and_new_author_selection(state_client):
    client, _ = state_client
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    proposed = [first_changes()[3]]
    request = {
        "base_revision": chapter["draft_revision"],
        "project_revision": project["revision"],
        "state_changes": proposed,
    }
    response = client.put(f"/api/chapters/{chapter['id']}/state-proposal", json=request)
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["draft_revision"] == chapter["draft_revision"] + 1
    assert (
        updated["content"] == chapter["content"]
        and updated["reviewed_hash"] == chapter["reviewed_hash"]
    )
    assert updated["state_preview"]["draft_revision"] == updated["draft_revision"]
    assert updated["proposal"]["accepted_state_changes"] == []
    assert updated["state_preview"]["status"] == "ready"
    assert (
        client.put(
            f"/api/chapters/{chapter['id']}/state-proposal", json=request
        ).status_code
        == 409
    )
    with pytest.raises(Conflict):
        client.app.state.repo.commit_chapter(
            chapter["id"],
            Approve(
                base_revision=chapter["draft_revision"],
                project_revision=project["revision"],
                accepted_state_changes=[0],
            ),
        )
    state = client.get(f"/api/projects/{project['id']}/story-state").json()
    assert state["project_revision"] == project["revision"] and state["empty"]
    project = accept(client, project, run_id, updated, [0])
    final = client.get(f"/api/projects/{project['id']}/story-state").json()["snapshot"]
    assert len(final["applied_event_ids"]) == 1 and final["inventories"] == {}
    assert final["knowledge"]["actor:luo"]


def test_tool_proposal_unknown_precondition_stays_pending_and_is_audited(state_client):
    client, _ = state_client
    project = prepare(client)
    _, chapter = draft(client, project, project["chapters"][0]["id"])
    response = client.put(
        f"/api/chapters/{chapter['id']}/state-proposal",
        json={
            "base_revision": chapter["draft_revision"],
            "project_revision": project["revision"],
            "state_changes": [first_changes()[2]],
        },
    )
    assert response.status_code == 200
    updated = response.json()
    assert (
        updated["state_preview"]["status"] == "invalid"
        and updated["state_preview"]["errors"][0]["code"] == "unknown_inventory"
    )
    assert client.get(f"/api/projects/{project['id']}/story-state").json()["empty"]
    audit = client.app.state.db.one(
        "SELECT detail FROM audit WHERE action='state.proposal.edited'"
    )
    detail = json.loads(audit["detail"])
    assert (
        detail["precheck_status"] == "invalid"
        and detail["accepted_state_changes"] == []
    )
    assert detail["errors"][0]["code"] == "unknown_inventory"


def test_tool_proposal_bad_source_or_forged_authority_does_not_mutate_pending_data(
    state_client,
):
    client, _ = state_client
    project = prepare(client)
    _, chapter = draft(client, project, project["chapters"][0]["id"])
    change = first_changes()[3]
    request = {
        "base_revision": chapter["draft_revision"],
        "project_revision": project["revision"],
        "state_changes": [change],
    }
    request["state_changes"][0]["quote"] = "不在正文里的引用。"
    assert (
        client.put(
            f"/api/chapters/{chapter['id']}/state-proposal", json=request
        ).status_code
        == 400
    )
    request["state_changes"][0]["quote"] = ""
    request["state_changes"][0]["source"] = {
        "chapter_id": "other-book",
        "version": 1,
        "quote": "伪造",
    }
    assert (
        client.put(
            f"/api/chapters/{chapter['id']}/state-proposal", json=request
        ).status_code
        == 422
    )
    after = client.get(f"/api/chapters/{chapter['id']}").json()
    assert after == chapter
    assert (
        client.app.state.db.one(
            "SELECT COUNT(*) n FROM audit WHERE action='state.proposal.edited'"
        )["n"]
        == 0
    )


def test_search_api_passes_history_boundary_and_limit_to_retrieval_source(
    state_client, monkeypatch
):
    client, _ = state_client
    project = prepare(client)
    calls = []

    def search(pid, query, before, limit):
        calls.append((pid, query, before, limit))
        return []

    monkeypatch.setattr(client.app.state.retrieval, "search", search)
    response = client.get(
        f"/api/projects/{project['id']}/search?q=铜铃&before=100002&limit=3"
    )
    assert response.status_code == 200 and calls == [(project["id"], "铜铃", 100002, 3)]
    assert (
        client.get(f"/api/projects/{project['id']}/search?before=-1").status_code == 422
    )
    assert (
        client.get(f"/api/projects/{project['id']}/search?limit=9").status_code == 422
    )


def export_one_confirmed_chapter(client):
    project = prepare(client)
    run_id, chapter = draft(client, project, project["chapters"][0]["id"])
    project = accept(client, project, run_id, chapter, [0, 1, 2, 3, 4])
    return project, client.get(f"/api/projects/{project['id']}/export").content


def test_zip_import_preserves_provenance_as_pending_not_confirmed_and_survives_reexport(
    state_client,
):
    client, _ = state_client
    original, raw = export_one_confirmed_chapter(client)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        bundle = json.loads(archive.read("story-events.json"))
    assert len(bundle["events"]) == 5 and bundle["pending_imports"] == []
    assert bundle["chapters"][0]["version"] == 1
    response = client.post("/api/projects/import", content=raw)
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["id"] != original["id"]
    chapter = imported["chapters"][0]
    assert chapter["id"] != original["chapters"][0]["id"] and chapter["stale"] == 1
    assert len(chapter["proposal"]["state_changes"]) == 5
    state = client.get(f"/api/projects/{imported['id']}/story-state").json()
    assert state["empty"] and state["snapshot"]["inventories"] == {}
    preview = client.get(f"/api/chapters/{chapter['id']}/state-preview").json()
    assert preview["status"] == "ready" and preview["author_confirmation_required"]
    assert preview["snapshot"]["inventories"]["actor:luo"]["item:pill"]["quantity"] == 1
    assert len(preview["imported_provenance"]) == 5
    for provenance in preview["imported_provenance"]:
        assert provenance["status"] == "pending"
        assert provenance["source_project_id"] == original["id"]
        assert (
            provenance["original_event"]["source"]["chapter_id"]
            == original["chapters"][0]["id"]
        )
    repeated_raw = client.get(f"/api/projects/{imported['id']}/export").content
    with zipfile.ZipFile(io.BytesIO(repeated_raw)) as archive:
        repeated_bundle = json.loads(archive.read("story-events.json"))
    assert (
        repeated_bundle["events"] == [] and len(repeated_bundle["pending_imports"]) == 5
    )
    again = client.post("/api/projects/import", content=repeated_raw)
    assert again.status_code == 201, again.text
    again = again.json()
    assert client.get(f"/api/projects/{again['id']}/story-state").json()["empty"]
    provenance = client.get(
        f"/api/chapters/{again['chapters'][0]['id']}/state-preview"
    ).json()["imported_provenance"]
    assert {entry["source_project_id"] for entry in provenance} == {original["id"]}


def test_imported_pending_events_survive_model_review_then_require_explicit_author_subset(
    state_client,
):
    client, _ = state_client
    original, raw = export_one_confirmed_chapter(client)
    imported = client.post("/api/projects/import", content=raw).json()
    chapter_id = imported["chapters"][0]["id"]
    response = client.post(
        "/api/runs",
        json={"project_id": imported["id"], "chapter_id": chapter_id, "kind": "review"},
    )
    run_id = response.json()["id"]
    wait(client, run_id)
    chapter = client.get(f"/api/chapters/{chapter_id}").json()
    assert len(chapter["proposal"]["state_changes"]) == 5
    assert "alternative_state_changes" in chapter["proposal"]
    assert client.get(f"/api/projects/{imported['id']}/story-state").json()["empty"]
    imported = accept(client, imported, run_id, chapter, [3, 4])
    snapshot = client.get(f"/api/projects/{imported['id']}/story-state").json()[
        "snapshot"
    ]
    assert snapshot["inventories"] == {} and snapshot["knowledge"]["actor:luo"]
    assert snapshot["hooks"]["hook:copper-mark"]["status"] == "open"
    assert len(snapshot["applied_event_ids"]) == 2
    for source in snapshot["event_sources"].values():
        assert source["chapter_id"] == chapter_id and source["version"] == 2
    provenance = client.get(f"/api/chapters/{chapter_id}/state-preview").json()[
        "imported_provenance"
    ]
    assert sum(item["status"] == "accepted" for item in provenance) == 2
    assert sum(item["status"] == "rejected" for item in provenance) == 3
    assert {item["original_event"]["source"]["chapter_id"] for item in provenance} == {
        original["chapters"][0]["id"]
    }


@pytest.mark.parametrize("fault", ["version", "chapter", "quote", "body_hash"])
def test_tampered_event_zip_is_rejected_before_creating_an_imported_project(
    state_client, fault
):
    client, _ = state_client
    _, raw = export_one_confirmed_chapter(client)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    bundle = json.loads(members["story-events.json"])
    if fault == "version":
        bundle["events"][0]["event"]["source"]["version"] = 99
    elif fault == "chapter":
        bundle["events"][0]["event"]["source"]["chapter_id"] = "different-chapter"
    elif fault == "quote":
        bundle["events"][0]["event"]["source"]["quote"] = "并不存在的引文。"
    else:
        bundle["chapters"][0]["body_sha256"] = "0" * 64
    members["story-events.json"] = encode(bundle).encode()
    altered = io.BytesIO()
    with zipfile.ZipFile(altered, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    before = client.app.state.db.one("SELECT COUNT(*) n FROM projects")["n"]
    response = client.post("/api/projects/import", content=altered.getvalue())
    assert response.status_code == 400, response.text
    assert client.app.state.db.one("SELECT COUNT(*) n FROM projects")["n"] == before
