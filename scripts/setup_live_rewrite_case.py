"""Freeze and initialize one unpaid chapter-rewrite acceptance fixture via HTTP.

This command never starts a run, reviews or approves a chapter. It imports the
primary first chapter verbatim as an unreviewed draft, preserving primary data.
Only the human/AI test operator in the UI may subsequently request DeepSeek's
rewrite; that later intervention must be reported separately from primary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
CASE = ROOT / "artifacts/resume/live-rewrite-v1"
RULES = ROOT / "evaluations/live-rewrite-v1"
PRIMARY = ROOT / "artifacts/resume/live-story-v1/review-packets/primary"
SHARED = ROOT / "artifacts/resume/live-story-v1/shared-blueprint.json"
SOURCE_HASH = "c4b08f2c94c08df311719b4e719e11e5021005b7fd33dd258d40485b4f39f74e"
INSTRUCTION = (
    "将选中的第一章全文改写为1200至1600个Unicode字符（含标点换行、不含章节标题），"
    "目标1400字符；压缩重复表达，保留完整场景、人物动作与对话，不用梗概代替。"
    "修正三处日期：真实领条的落款改为今晨寅时，阮禾说的许岱取药时间也改为今晨寅时；"
    "王婶要赶的是第三日早市，不是明日早市，第三天天边发白前亲送还钳的约定保持。"
    "保留实际听到两短一长、读明‘许岱，领清灼散一包’的真实签名领条、"
    "王婶实际交借唯一合口炉钳及林砚答应期限；旧木楔垫药架、麻绳试拉架脚仍有实际动作。"
    "林砚和阮禾只能知道许岱签名领了一包，仍不知道药给了谁或用途，不提前揭示阿田服药。"
    "母亲服下现存一包且只镇痛；触温必须掌贴铜、只辨眼前热振并付出掌痛发麻，"
    "不改写成读心、回看历史或无代价突破。只返回改写后的完整正文，不加标题、解释或核对清单。"
)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(value):
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(encoded(value), encoding="utf-8")


def freeze():
    require(not (RULES / "protocol.json").exists(), "规则已冻结，不能覆盖")
    seal = json.loads((PRIMARY / "seal.json").read_text())
    for name, expected in seal["files"].items():
        require(
            sha((PRIMARY / name).read_bytes()) == expected,
            "primary封存文件已变：" + name,
        )
    mapping = json.loads((PRIMARY / "operator-only/mapping.json").read_text())
    entry_id = next(
        key for key, row in mapping.items() if row["configuration"] == "full"
    )
    packet_path = PRIMARY / "public" / (entry_id + ".json")
    source_packet = json.loads(packet_path.read_text())
    source = next(row for row in source_packet["chapters"] if row["number"] == 1)
    require(sha(source["body"]) == source["sha256"] == SOURCE_HASH, "原稿来源SHA不符")
    require(len(source["body"]) == 1770, "原稿应为1770字符，不接受后来历史改稿")
    shared = json.loads(SHARED.read_text())
    plan = shared["chapters"][0]["plan"] | {
        "target_words": 1400,
        "min_characters": 1200,
        "max_characters": 1600,
    }
    snapshot = {
        "project": {
            "title": "改稿验收 · 借来的铁",
            "genre": shared["genre"],
            "premise": shared["premise"],
            "style": shared["style"],
            "target_chapters": 1,
            "chapter_words": 1400,
        },
        "memory": {row["name"]: row["content"] for row in shared["memory"]},
        "outline": shared["outline"],
        "plan": plan,
        "draft": {"title": plan["title"], "content": source["body"]},
    }
    write(CASE / "input-snapshot.json", snapshot)
    brief = {
        "version": "live-rewrite-v1",
        "title": snapshot["project"]["title"],
        "scope": "单章独立修订验收；原三章大纲/初始记忆仅作为上下文，不额外生成第二、三章。",
        "source": {
            "primary_packet": str(packet_path.relative_to(ROOT)),
            "primary_packet_sha256": sha(packet_path.read_bytes()),
            "source_body_sha256": SOURCE_HASH,
            "source_characters": 1770,
            "shared_initial_snapshot": str(SHARED.relative_to(ROOT)),
            "shared_initial_snapshot_sha256": sha(SHARED.read_bytes()),
        },
        "input_snapshot_sha256": sha((CASE / "input-snapshot.json").read_bytes()),
        "instruction": INSTRUCTION,
        "selection": "原章全文，逐字等于source_body_sha256；由操作者在产品UI选中。",
        "length": {
            "target": 1400,
            "min_characters": 1200,
            "max_characters": 1600,
            "unit": "正文Unicode字符，含标点换行、不含标题",
        },
        "date_corrections": [
            {
                "location": "签名领条落款",
                "before": "落款昨日近寅时",
                "after_meaning": "今晨寅时",
            },
            {
                "location": "阮禾关于许岱取药的说法",
                "before": "上午许镇吏取走一格药包",
                "after_meaning": "今晨寅时取走药包",
            },
            {
                "location": "王婶需用炉钳的早市",
                "before": "我明日早市补灶口",
                "after_meaning": "第三日早市用钳；第三天天边发白前实际亲送归还的约定不变",
            },
        ],
    }
    write(RULES / "brief.json", brief)
    protocol = {
        "version": "live-rewrite-v1",
        "frozen_at": datetime.now(UTC).isoformat(),
        "brief_sha256": sha((RULES / "brief.json").read_bytes()),
        "unit": "同一1770字符原稿的一次产品UI全文选段rewrite轨迹；不是新增三章v2、不是独立小说样本。",
        "operator": "Codex AI测试操作者；初始化只用应用API，后续UI明确指定改写，不代表自然人使用评价。",
        "intervention": "操作者主动指出超长及三个日期问题；属于额外操作干预，不能并入primary full/baseline胜负，也不能声称系统自主发现全部问题。",
        "setup_contract": [
            {"id": "P1.setup", "rule": "实际听见井铃两短一长，保留具体声响问题。"},
            {
                "id": "P2.setup",
                "rule": "真实领条明确写许岱、清灼散、一包，林砚实际读明签名与数量。",
            },
            {
                "id": "P3.setup",
                "rule": "王婶实际交借唯一合口炉钳，林砚答应第三天天边发白前亲送归还；不能提前写成已经归还。",
            },
            {
                "id": "P3.prepare",
                "rule": "保留原章计划中旧木楔垫稳药架、麻绳试拉架脚的实际动作；普通旧物保留可用。",
            },
        ],
        "other_checks": [
            {
                "id": "D1",
                "rule": "领条取药落款是第一日今晨寅时，不是昨日；可保留昨日付药钱，不能全局删除昨日来伪造日期一致。",
            },
            {
                "id": "D2",
                "rule": "阮禾说许岱取药是今晨寅时，与领条一致；不是第一日上午另一次取药。",
            },
            {
                "id": "D3",
                "rule": "王婶需用炉钳赶第三日早市，非明日早市；归还期限不延后。",
            },
            {
                "id": "K1",
                "rule": "林砚和阮禾仍不知道失药用途及服用者；正文不提前揭露阿田服药，不把作者初始真相当角色已知。",
            },
            {
                "id": "C1",
                "rule": "触温须掌贴铜，只得当下冷热振动且有掌痛发麻代价；不读心、读历史、隔空或突破万能解题。",
            },
            {
                "id": "C2",
                "rule": "只服下现存一包、只镇痛，不能增加药量或治好母病/伤掌。",
            },
        ],
        "acceptance": "实际正文为1200至1600 Unicode字符，且setup_contract及other_checks全部有明确文本支持；缺项即失败，不用编辑高分抵消。工程闭环完成与上述内容验收分开报告。",
        "evidence": "每个成立项给6–220字符连续原文与解释；角色声称、书面记录、叙述动作分开标注。程序只校验字数/引文字面，不代替语义判断。",
        "execution": {
            "initialization": "通过POST project与PUT memory/outline/plan/draft初始化，无run、无review、无proposal、无canon版本，不调用模型。",
            "rewrite": "操作者在UI选择原稿全文，将brief.instruction原样作为改写要求；正文只能由DeepSeek rewrite产生。应用正常审核/结构化重试保留完整日志。",
            "failures": "不因长度、评分或内容不理想另开任务重抽；失败、异常、取消、重试、全部稿与费用保留；额外尝试须另建验收版本而非覆盖本轨迹。",
            "approval": "是否定稿另由操作者明确确认；未经确认状态不得当canon或正式事实。",
            "cost": "沿用现有Meter调用前预留/调用后结算与用户全局300元上限；初始化阶段无LLM调用。",
        },
        "limits": [
            "单章局部纠错不证明整卷质量提升。",
            "初始上下文仍含共享三章作者计划，角色不可自动知道未来真相。",
            "AI操作者和AI语义检查不能声称真人阅读偏好。",
            "primary已封存，不能用本稿替换primary原稿再报告胜负。",
        ],
    }
    write(RULES / "protocol.json", protocol)
    write(
        CASE / "freeze-receipt.json",
        {
            "frozen_at": protocol["frozen_at"],
            "protocol_sha256": sha((RULES / "protocol.json").read_bytes()),
            "brief_sha256": sha((RULES / "brief.json").read_bytes()),
            "input_snapshot_sha256": brief["input_snapshot_sha256"],
            "source_body_sha256": SOURCE_HASH,
            "setup_script_sha256": sha(Path(__file__).read_bytes()),
            "before_model_call": True,
            "model_calls_started_by_setup": 0,
        },
    )
    print(
        encoded(
            {
                "frozen": True,
                "protocol": str(RULES / "protocol.json"),
                "source_sha256": SOURCE_HASH,
            }
        ),
        end="",
    )


class Setup:
    def __init__(self, port):
        self.http = httpx.Client(
            base_url=f"http://127.0.0.1:{port}", timeout=30, trust_env=False
        )

    def call(self, method, path, payload=None):
        # The fixture tool has no run/approve capability, even accidentally.
        require(
            method == "GET"
            or (method == "POST" and path == "/api/projects")
            or (
                method == "PUT"
                and any(
                    marker in path
                    for marker in ("/memory/", "/outline", "/plan", "/draft")
                )
            ),
            "初始化工具不允许该写接口",
        )
        response = self.http.request(method, path, json=payload)
        response_data = response.json()
        if method != "GET":
            with (CASE / "http-mutations.jsonl").open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        {
                            "method": method,
                            "path": path,
                            "request": payload,
                            "status": response.status_code,
                            "response": response_data,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        response.raise_for_status()
        return response_data

    def initialize(self, ready):
        require(ready, "需操作者确认新服务已重启后使用--new-service-ready")
        receipt = json.loads((CASE / "freeze-receipt.json").read_text())
        for path, key in (
            (RULES / "protocol.json", "protocol_sha256"),
            (RULES / "brief.json", "brief_sha256"),
            (CASE / "input-snapshot.json", "input_snapshot_sha256"),
        ):
            require(
                sha(path.read_bytes()) == receipt[key], "冻结文件已变：" + str(path)
            )
        source = json.loads((CASE / "input-snapshot.json").read_text())
        runtime_path = CASE / "runtime.json"
        if runtime_path.exists():
            runtime = json.loads(runtime_path.read_text())
            project = self.call("GET", "/api/projects/" + runtime["project_id"])
        else:
            project = self.call("POST", "/api/projects", source["project"])
            runtime = {
                "project_id": project["id"],
                "source_body_sha256": SOURCE_HASH,
                "initialization_only": True,
            }
            write(runtime_path, runtime)
        require(not project["runs"], "已有运行；不能重置已开始的验收")
        require(
            len(project["chapters"]) == 1
            and not project["chapters"][0]["canonical_revision"],
            "必须为一章未定稿项目",
        )
        for name, content in source["memory"].items():
            current = next(
                row["content"] for row in project["memory"] if row["name"] == name
            )
            if current != content:
                project = self.call(
                    "PUT",
                    f"/api/projects/{project['id']}/memory/{name}",
                    {"content": content, "project_revision": project["revision"]},
                )
        if project["outline"] != source["outline"]:
            project = self.call(
                "PUT",
                f"/api/projects/{project['id']}/outline",
                {"outline": source["outline"], "project_revision": project["revision"]},
            )
        chapter = project["chapters"][0]
        if chapter["plan"] != source["plan"]:
            chapter = self.call(
                "PUT",
                f"/api/chapters/{chapter['id']}/plan",
                {"base_revision": chapter["draft_revision"], "plan": source["plan"]},
            )
        require(
            not chapter["content"] or chapter["content"] == source["draft"]["content"],
            "已有不同正文，拒绝外部覆盖",
        )
        if chapter["content"] != source["draft"]["content"]:
            chapter = self.call(
                "PUT",
                f"/api/chapters/{chapter['id']}/draft",
                source["draft"] | {"base_revision": chapter["draft_revision"]},
            )
        project = self.call("GET", "/api/projects/" + project["id"])
        chapter = project["chapters"][0]
        memory_equal = {
            row["name"]: row["content"] for row in project["memory"]
        } == source["memory"]
        checks = {
            "memory_equal": memory_equal,
            "outline_equal": project["outline"] == source["outline"],
            "plan_equal_with_explicit_bounds": chapter["plan"] == source["plan"],
            "draft_sha256_equal": sha(chapter["content"]) == SOURCE_HASH,
            "no_canonical_version": not chapter["canonical_revision"]
            and not chapter["canonical"],
            "unreviewed": not chapter["review"]
            and not chapter["proposal"]
            and not chapter["reviewed_hash"],
            "no_runs_or_model_calls": not project["runs"],
        }
        write(CASE / "initial-project.json", project)
        write(
            CASE / "initialization-proof.json",
            {
                "checks": checks,
                "source_body_sha256": SOURCE_HASH,
                "characters": len(chapter["content"]),
                "state": self.call("GET", f"/api/projects/{project['id']}/story-state"),
                "scope": "只初始化为未审核draft，不作为成功改写或审核通过。",
            },
        )
        require(all(checks.values()), "初始化验证未全部通过，保留日志并停止")
        runtime |= {
            "chapter_id": chapter["id"],
            "draft_revision": chapter["draft_revision"],
            "project_revision": project["revision"],
            "ready_for_ui_rewrite": True,
        }
        write(runtime_path, runtime)
        print(encoded(runtime), end="")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("freeze", "init"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--new-service-ready", action="store_true")
    args = parser.parse_args()
    if args.mode == "freeze":
        freeze()
    else:
        setup = Setup(args.port)
        try:
            setup.initialize(args.new_service_ready)
        finally:
            setup.http.close()


if __name__ == "__main__":
    main()
