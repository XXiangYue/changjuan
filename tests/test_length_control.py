"""Explicit author range gates the real graph, separately from editor scores."""

import pytest
from fastapi.testclient import TestClient

from novel_agent.studio.api import create_app
from novel_agent.studio.models import ChapterPlan
from tests.test_studio import FixtureModel, create, setup, draft, wait


@pytest.mark.parametrize(
    "values",
    [
        {"min_characters": 450},
        {"max_characters": 550},
        {"min_characters": 550, "max_characters": 450},
        {"min_characters": 510, "max_characters": 600},
    ],
)
def test_invalid_author_ranges_cannot_be_saved(values):
    with pytest.raises(ValueError):
        ChapterPlan(title="测试", goal="测试", target_words=500, **values)


def test_strict_range_bounds_repairs_and_requires_explicit_override(tmp_path):
    class LongFixture(FixtureModel):
        writes = 0

        async def text(self, rid, role, system, prompt, **kwargs):
            self.writes += 1
            assert "450—550个Unicode字符" in prompt
            if self.writes > 1:
                assert "至少删除80字符" in prompt
            prefix = "沈砚将唯一的温肺丹交给洛蘅。"
            return prefix + "甲" * (580 - len(prefix))

    model = LongFixture()
    with TestClient(create_app(tmp_path, model)) as client:
        p = setup(client, create(client))
        for ch in p["chapters"]:
            plan = ch["plan"] | {"min_characters": 450, "max_characters": 550}
            response = client.put(
                f"/api/chapters/{ch['id']}/plan",
                json={"base_revision": ch["draft_revision"], "plan": plan},
            )
            assert response.status_code == 200
        p = client.get(f"/api/projects/{p['id']}").json()
        rid, ch = draft(client, p, p["chapters"][0]["id"])
        assert model.writes == 4  # initial + at most three repairs
        assert model.review_count == 2  # cheap length failures precede the editors
        assert ch["review"]["passed"] is True  # editor score is independent
        assert ch["review"]["length_check"] == {
            "characters": 580,
            "target": 500,
            "minimum": 450,
            "maximum": 550,
            "unit": "Unicode characters including punctuation and newlines",
            "within_range": False,
        }
        payload = {
            "base_revision": ch["draft_revision"],
            "project_revision": p["revision"],
            "accepted_patches": [x["name"] for x in ch["proposal"]["patches"]],
        }
        response = client.post(f"/api/runs/{rid}/approve", json=payload)
        assert response.status_code == 409
        assert client.get(f"/api/runs/{rid}").json()["status"] == "awaiting_approval"
        assert client.get(f"/api/chapters/{ch['id']}").json()["canonical_revision"] == 0
        payload["note"] = "离线验收：确认保留超长稿，不把高编辑分当字数达标。"
        response = client.post(f"/api/runs/{rid}/approve", json=payload)
        assert response.status_code == 202, response.text
        wait(client, rid, "completed")
        next_ch = client.get(f"/api/chapters/{p['chapters'][1]['id']}").json()
        assert next_ch["plan"]["min_characters"] == 450
        assert next_ch["plan"]["max_characters"] == 550
