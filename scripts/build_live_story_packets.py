"""Export immutable blind reading packets; never edit a manuscript or call a model.

Primary packets require an explicit completion flag and canonical revision 1 for
both three-chapter trajectories. The local case deterministically binds the last
writer output before the first semantic review to the first writer output after
that review. It is one selected repair case, not an error-rate experiment.
Reviewer files contain only anonymous IDs, chapter numbers, verbatim bodies and
body hashes. Source configuration, review comments and mappings stay private.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import shutil
import tempfile
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "artifacts/resume/live-story-v1"
FULL_PID = "262040e834944e08a3b3a622cb598544"
BASELINE_PID = "76c6e879beac493ab8a3cd8db9a3cb40"
LOCAL_RUN = "6f56b25359bc4192ae3f1b45c71d5488"
CHECKS = ("P1.block", "P1.stop", "P1.release", "P1.return", "P1.witness")


def sha(value):
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def checked_path(path):
    path = Path(path)
    require(path.is_absolute(), "必须使用绝对路径")
    path = path.resolve()
    require(path.is_relative_to(ROOT), "只允许当前工作目录内的路径")
    return path


def api(path, port):
    with urlopen(f"http://127.0.0.1:{port}/api/{path}", timeout=30) as response:
        return json.load(response)


def anonymous_id():
    return "entry_" + secrets.token_hex(6)


def frozen_protocol():
    initial = json.loads((BASE / "baseline/execution-addendum.json").read_text())
    protocol_path = ROOT / "evaluations/live-story-v1/protocol.json"
    brief_path = protocol_path.with_name("brief.json")
    require(
        sha(protocol_path.read_bytes()) == initial["protocol_sha256"],
        "冻结protocol已变，拒绝默默更换评审规则",
    )
    require(sha(brief_path.read_bytes()) == initial["brief_sha256"], "冻结brief已变")
    return json.loads(protocol_path.read_text())


def packet(entry_id, chapters):
    return {
        "entry_id": entry_id,
        "chapters": [
            {"number": number, "body": body, "sha256": sha(body)}
            for number, body in sorted(chapters.items())
        ],
    }


def canonical_chapters(project):
    rows = project["chapters"]
    require(len(rows) == 3, "primary必须是三章；不得把未完成轨迹伪装成完整卷")
    require({row["number"] for row in rows} == {1, 2, 3}, "章号必须为1/2/3")
    for row in rows:
        require(
            row["canonical_revision"] == 1,
            "primary只允许首次定稿；历史改稿v2须另立验收",
        )
        require(not row["stale"], "primary包含待复核旧稿，拒绝封存")
        require(row["canonical"].strip(), "primary正文不能为空")
        require(row["canonical"] == row["content"], "primary当前草稿不等于已确认正文")
    return {row["number"]: row["canonical"] for row in rows}


def select_repair(events):
    require(bool(events), "没有事件，无法绑定真实修订")
    sequences = [event["seq"] for event in events]
    require(sequences == sorted(set(sequences)), "事件必须按唯一序号排序")
    require(len({event["run_id"] for event in events}) == 1, "事件来自多个run")
    outputs = [
        event
        for event in events
        if event["type"] == "model.output" and event["data"].get("role") == "作者"
    ]
    reviews = [event for event in events if event["type"] == "review"]
    require(bool(reviews), "尚无非篇幅编辑审核")
    first = reviews[0]
    before = [event for event in outputs if event["seq"] < first["seq"]]
    after = [event for event in outputs if event["seq"] > first["seq"]]
    require(bool(before) and bool(after), "首次语义审核前后没有成对作者输出")
    pair = (before[-1], after[0])
    for event in outputs:
        text = event["data"].get("text")
        require(isinstance(text, str) and text.strip(), "作者输出为空")
        next_writer = min(
            (other["seq"] for other in outputs if other["seq"] > event["seq"]),
            default=float("inf"),
        )
        saves = [
            other
            for other in events
            if other["type"] == "draft.saved"
            and event["seq"] < other["seq"] < next_writer
        ]
        require(len(saves) == 1, "作者输出未唯一绑定draft.saved")
        require(saves[0]["data"]["words"] == len(text), "输出长度与保存草稿不符")
    return outputs, first, pair


def source_receipt():
    initial = json.loads((BASE / "baseline/execution-addendum.json").read_text())
    generation = json.loads((BASE / "generation-source/receipt.json").read_text())
    comparisons = []
    for filename, expected in generation["source_sha256"].items():
        copied = BASE / "generation-source" / filename
        actual = sha(copied.read_bytes())
        key = "src/novel_agent/studio/" + filename
        original = initial["source_sha256"].get(key)
        require(actual == expected, "generation-source副本与receipt不符：" + filename)
        comparisons.append(
            {
                "file": filename,
                "copied_sha256": actual,
                "baseline_initial_sha256": original,
                "matches_baseline_initial": actual == original if original else None,
            }
        )
    return {
        "comparisons": comparisons,
        "captured_stage": generation["copied_at_stage"],
        "not_before_first_call": generation["not_pretend_before_first_call"],
        "runtime_record": "操作者root报告生产服务为session8990启动时加载的代码，期间未重启/reload；工作稿修改尚未加载。",
        "proof_limit": "副本与调用前指纹相同支持代码一致性；文件哈希本身不能独立证明进程没有reload。",
    }


def seal_bundle(output, documents):
    output = checked_path(output)
    require(not output.exists(), "输出已存在，primary/case一经封存不得覆盖")
    temporary_root = ROOT / ".cache/packet-build"
    temporary_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(dir=temporary_root))
    try:
        for relative, value in documents.items():
            relative_path = Path(relative)
            require(
                not relative_path.is_absolute() and ".." not in relative_path.parts,
                "输出路径越界",
            )
            target = staging / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(dump(value), encoding="utf-8")
            if relative_path.parts[0] == "operator-only":
                target.chmod(0o600)
        manifest = {
            "scope": "文件哈希保证导出后未变，不代表语义评审正确。只将public内文件交评审者。",
            "files": {
                str(path.relative_to(staging)): sha(path.read_bytes())
                for path in sorted(staging.rglob("*"))
                if path.is_file()
            },
        }
        (staging / "seal.json").write_text(dump(manifest), encoding="utf-8")
        output.parent.mkdir(parents=True, exist_ok=True)
        staging.rename(output)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def build_primary(port, output, confirmed):
    require(confirmed, "等操作者明确通知primary完成，再加--confirm-primary-ready封存")
    frozen_protocol()
    projects = {
        "full": api("projects/" + FULL_PID, port),
        "baseline": api("projects/" + BASELINE_PID, port),
    }
    contents = {name: canonical_chapters(project) for name, project in projects.items()}
    documents = {}
    mapping = {}
    for name, chapters in contents.items():
        entry_id = anonymous_id()
        require(entry_id not in mapping, "随机ID碰撞，请重试未封存导出")
        documents["public/" + entry_id + ".json"] = packet(entry_id, chapters)
        mapping[entry_id] = {
            "configuration": name,
            "project_id": projects[name]["id"],
            "chapter_sources": [
                {key: row[key] for key in ("id", "number", "canonical_revision")}
                | {"body_sha256": sha(row["canonical"])}
                for row in projects[name]["chapters"]
            ],
        }
    documents["public/index.json"] = {
        "entry_ids": sorted(mapping),
        "instructions": "只读列出的匿名packet及给定冻结brief/protocol，勿读取operator-only、数据库或项目正文；分别逐章及全卷评价，先封存判定再解盲。",
        "limits": "两条三章轨迹；AI判断不等于自然人阅读偏好或总体质量结论。",
    }
    documents["operator-only/mapping.json"] = mapping
    documents["operator-only/primary-snapshots.json"] = projects
    documents["operator-only/source-comparison.json"] = source_receipt()
    documents["operator-only/protocol-proof.json"] = {
        "protocol_sha256": sha(
            (ROOT / "evaluations/live-story-v1/protocol.json").read_bytes()
        ),
        "brief_sha256": sha(
            (ROOT / "evaluations/live-story-v1/brief.json").read_bytes()
        ),
        "policy": "primary只保存首次定稿version1；历史修订必须输出独立v2包，不能覆盖本目录。",
    }
    seal_bundle(output, documents)
    return {"public_directory": str(output / "public"), "entries": sorted(mapping)}


def build_local(port, output):
    protocol = frozen_protocol()
    run = api("runs/" + LOCAL_RUN, port)
    events = api("runs/" + LOCAL_RUN + "/events?once=true", port)
    outputs, first_review, pair = select_repair(events)
    # Use the original event, not current project state after a history edit.
    writer_input = next(
        event
        for event in events
        if event["type"] == "model.input" and event["data"].get("role") == "作者"
    )
    initial_prompt, _ = json.JSONDecoder().raw_decode(writer_input["data"]["prompt"])
    first_body = initial_prompt["context"]["previous_chapter"]
    require(
        isinstance(first_body, str) and bool(first_body.strip()),
        "缺少本次run固定的共同上一章快照",
    )
    documents = {}
    mapping = {}
    for source in pair:
        entry_id = anonymous_id()
        require(entry_id not in mapping, "随机ID碰撞")
        body = source["data"]["text"]
        documents["public/" + entry_id + ".json"] = packet(
            entry_id, {1: first_body, 2: body}
        )
        mapping[entry_id] = {
            "run_id": LOCAL_RUN,
            "writer_event_seq": source["seq"],
            "body_sha256": sha(body),
            "relative_to_first_semantic_review": "before"
            if source["seq"] < first_review["seq"]
            else "after",
            "characters": len(body),
        }
    payoff = next(row for row in protocol["payoffs"] if row["id"] == "P1")
    documents["public/scope.json"] = {
        "entry_ids": sorted(mapping),
        "task": "分别阅读两个匿名第2章；第1章是两者相同的上下文。只判指定五项，不评整卷、文笔、配置或错误率，不猜前后顺序。",
        "checks": [row for row in payoff["checks"] if row["id"] in CHECKS],
        "evidence_rules": protocol["evidence_rules"],
        "allowed_evidence_chapters": [2],
        "warning": "角色说众人看见，不自动证明动作实际发生；仔细区分计划、角色声称与已叙述行为。不能据引文存在自动判语义正确。",
        "assessment_schema": {
            "entry_id": "匿名ID",
            "checks": [
                {
                    "id": "五项之一",
                    "satisfied": True,
                    "reason": "具体阅读判断",
                    "evidence": [
                        {
                            "chapter": 2,
                            "quote": "连续6–220字符原文",
                            "source_kind": "narrated_action|character_statement|written_record",
                        }
                    ],
                }
            ],
        },
        "limits": "单个局部修订案例，属于刻意选择的真实过程，不可当自然错误率或一般改写收益。",
    }
    documents["operator-only/mapping.json"] = mapping
    documents["operator-only/raw-events.json"] = events
    documents["operator-only/source-run.json"] = run
    documents["operator-only/writer-outputs.json"] = [
        {
            "event": source,
            "body_sha256": sha(source["data"]["text"]),
            "characters": len(source["data"]["text"]),
        }
        for source in outputs
    ]
    documents["operator-only/first-semantic-review.json"] = {
        "review_event": first_review,
        "reviewed_writer_event_seq": pair[0]["seq"],
        "reviewed_body_sha256": sha(pair[0]["data"]["text"]),
        "next_writer_event_seq": pair[1]["seq"],
        "next_body_sha256": sha(pair[1]["data"]["text"]),
        "binding": "事件序号顺序绑定；first review前最近作者输出和其后首个作者输出，未按结果挑稿。",
        "shared_chapter1_sha256": sha(first_body),
        "shared_source": "本run首个作者model.input.context.previous_chapter原样读取；不取未来可能修订的当前章节。",
    }
    seal_bundle(output, documents)
    return {"public_directory": str(output / "public"), "entries": sorted(mapping)}


def validate_local(packet_data, assessment):
    require(set(assessment) == {"entry_id", "checks"}, "评审只允许entry_id/checks")
    require(assessment["entry_id"] == packet_data["entry_id"], "评审ID与匿名包不符")
    chapters = {row["number"]: row["body"] for row in packet_data["chapters"]}
    for row in packet_data["chapters"]:
        require(sha(row["body"]) == row["sha256"], "匿名包正文哈希不符")
    rows = assessment["checks"]
    require(
        len(rows) == len(CHECKS) and {row.get("id") for row in rows} == set(CHECKS),
        "必须恰好评指定五项",
    )
    for row in rows:
        require(set(row) == {"id", "satisfied", "reason", "evidence"}, "判断字段不符")
        require(type(row["satisfied"]) is bool, "satisfied必须是布尔")
        require(
            isinstance(row["reason"], str) and len(row["reason"].strip()) >= 6,
            "须有具体理由",
        )
        require(isinstance(row["evidence"], list), "evidence必须为列表")
        require(not row["satisfied"] or bool(row["evidence"]), "成立项必须有正文引文")
        for quote in row["evidence"]:
            require(set(quote) == {"chapter", "quote", "source_kind"}, "引文字段不符")
            require(
                type(quote["chapter"]) is int and quote["chapter"] == 2,
                "本地案例只接受第2章动作证据",
            )
            require(
                quote["source_kind"]
                in {"narrated_action", "character_statement", "written_record"},
                "来源类型不符",
            )
            require(
                isinstance(quote["quote"], str) and 6 <= len(quote["quote"]) <= 220,
                "引文长度须6–220字符",
            )
            require(quote["quote"] in chapters[2], "引文不是连续逐字原文")
    return {
        "entry_id": assessment["entry_id"],
        "schema_and_quotes_valid": True,
        "satisfied_count": sum(row["satisfied"] for row in rows),
        "maximum": 5,
        "boundary": "计数基于评审语义标签；程序不自动判兑现或评审无偏。",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("primary", "local-case", "validate-local"))
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--confirm-primary-ready", action="store_true")
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--assessment", type=Path)
    args = parser.parse_args()
    if args.mode == "validate-local":
        require(
            args.packet is not None and args.assessment is not None,
            "需--packet和--assessment",
        )
        result = validate_local(
            json.loads(checked_path(args.packet).read_text()),
            json.loads(checked_path(args.assessment).read_text()),
        )
    else:
        require(args.out is not None, "需绝对--out目录")
        output = checked_path(args.out)
        result = (
            build_primary(args.port, output, args.confirm_primary_ready)
            if args.mode == "primary"
            else build_local(args.port, output)
        )
    print(dump(result), end="")


if __name__ == "__main__":
    main()
