"""Validate a blind AI assessment against frozen story rules and exact evidence.

This does not call a model, infer plot events from keywords, or replace semantic
reading. It calculates the frozen acceptance predicate over an AI's labels.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = ROOT / "evaluations" / "live-story-v1" / "protocol.json"
BRIEF = PROTOCOL.with_name("brief.json")
PROTOCOL_SHA256 = "4c6e29bce61fb0e3fa32dc860e7a8fb74837d8a21148ca45407a83840885242a"
KINDS = {"narrated_action", "character_statement", "written_record"}
OUTCOMES = {"preserved", "violation", "not_tested"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    path = Path(path)
    require(path.is_absolute(), "请使用绝对路径")
    require(path.resolve().is_relative_to(ROOT), "文件必须在当前工作目录内")
    return json.loads(path.read_text())


def protocol():
    data = PROTOCOL.read_bytes()
    require(sha(data) == PROTOCOL_SHA256, "冻结protocol的SHA不符，不能静默改变规则")
    rules = json.loads(data)
    require(sha(BRIEF.read_bytes()) == rules["brief_sha256"], "冻结brief的SHA不符")
    return rules


def packet_chapters(packet):
    require(
        set(packet) <= {"entry_id", "chapters", "created_at"},
        "匿名包含额外元数据；去除配置、费用、轨迹等",
    )
    require(
        isinstance(packet.get("entry_id"), str) and packet["entry_id"], "缺entry_id"
    )
    rows = packet.get("chapters", [])
    require(
        isinstance(rows, list) and len(rows) == 3, "应有三章；未完成轨迹另记运行失败"
    )
    require({row.get("number") for row in rows} == {1, 2, 3}, "章号须为1、2、3且不重复")
    result = {}
    for row in rows:
        require(
            set(row) <= {"number", "title", "body", "sha256"},
            "章节含额外配置或评分信息",
        )
        body = row.get("body")
        require(isinstance(body, str) and body.strip(), "正文不能为空")
        if "sha256" in row:
            require(sha(body.encode()) == row["sha256"], "正文与匿名包SHA不符")
        result[row["number"]] = body
    return result


def reason(item):
    text = item.get("reason")
    require(isinstance(text, str) and len(text.strip()) >= 6, "判断须有具体理由")
    require(not any(x in text for x in ("TODO", "待独立阅读后填写")), "仍有未填写模板")


def quotes(item, chapters, *, allowed, required):
    rows = item.get("evidence")
    require(isinstance(rows, list), "evidence须为列表")
    require(not required or bool(rows), "成立/违例/叙事评分必须有原文证据")
    for row in rows:
        require(
            set(row) == {"chapter", "quote", "source_kind"},
            "引文只含chapter/quote/source_kind",
        )
        chapter, quote = row["chapter"], row["quote"]
        require(
            type(chapter) is int and chapter in allowed, "引文章节越界或用后文修补前章"
        )
        require(row["source_kind"] in KINDS, "引文来源类型无效")
        require(
            isinstance(quote, str) and 6 <= len(quote) <= 220, "引文须连续6–220字符"
        )
        require(quote in chapters[chapter], f"第{chapter}章不存在该连续原句")


def indexed(rows, expected, label):
    require(isinstance(rows, list), label + "须为列表")
    require(len(rows) == len(expected), label + "缺项或有重复")
    require(all(isinstance(r, dict) for r in rows), label + "项目须为对象")
    result = {r.get("id"): r for r in rows}
    require(set(result) == set(expected), label + "ID不完整或越界")
    return result


def constraints(rows, rules, chapters, allowed):
    items = indexed(rows, [r["id"] for r in rules["constraints"]], "约束")
    for item in items.values():
        require(item.get("outcome") in OUTCOMES, "约束outcome无效")
        reason(item)
        quotes(
            item, chapters, allowed=allowed, required=item["outcome"] != "not_tested"
        )
    return items


def payoff_level(checks):
    values = [c["satisfied"] for c in checks]
    if all(values):
        return 3
    if any(values[1:]):
        return 2
    return 1 if values[0] else 0


def evaluate(packet, assessment, rules):
    chapters = packet_chapters(packet)
    require(assessment.get("entry_id") == packet["entry_id"], "评审与匿名包ID不符")
    per_chapter = assessment.get("chapter_assessments", [])
    require(isinstance(per_chapter, list) and len(per_chapter) == 3, "须逐章判断三章")
    require({r.get("chapter") for r in per_chapter} == {1, 2, 3}, "逐章判断编号错误")
    for row in per_chapter:
        n = row["chapter"]
        constraints(row.get("constraints"), rules, chapters, set(range(1, n + 1)))
    whole = constraints(assessment.get("whole_constraints"), rules, chapters, {1, 2, 3})
    payoffs = indexed(
        assessment.get("payoffs"), [r["id"] for r in rules["payoffs"]], "payoff"
    )
    payoff_scores = {}
    for rule in rules["payoffs"]:
        item = payoffs[rule["id"]]
        checks = indexed(
            item.get("checks"), [c["id"] for c in rule["checks"]], "payoff检查"
        )
        ordered = []
        for check_rule in rule["checks"]:
            check = checks[check_rule["id"]]
            require(
                type(check.get("satisfied")) is bool,
                "satisfied须为布尔值，不接受缺失或字符串",
            )
            reason(check)
            quotes(
                check,
                chapters,
                allowed=set(check_rule["chapters"]),
                required=check["satisfied"],
            )
            ordered.append(check)
        payoff_scores[rule["id"]] = {
            "level": payoff_level(ordered),
            "completed_checks": sum(c["satisfied"] for c in ordered),
            "required_checks": len(ordered),
            "missing_check_ids": [c["id"] for c in ordered if not c["satisfied"]],
        }
    narrative = indexed(
        assessment.get("narrative"),
        [r["id"] for r in rules["narrative_dimensions"]],
        "叙事",
    )
    for item in narrative.values():
        require(
            type(item.get("score")) is int and 0 <= item["score"] <= 4,
            "叙事分须为0–4整数",
        )
        reason(item)
        quotes(item, chapters, allowed={1, 2, 3}, required=True)
    require(
        isinstance(assessment.get("overall_comment"), str)
        and assessment["overall_comment"].strip(),
        "缺全卷意见",
    )
    require(
        isinstance(assessment.get("limitations"), list) and assessment["limitations"],
        "须说明AI评审边界",
    )
    length = {
        str(n): {
            "characters": len(body),
            "basic_han_characters": sum("\u4e00" <= c <= "\u9fff" for c in body),
            "within_1200_1600": 1200 <= len(body) <= 1600,
            "body_sha256": sha(body.encode()),
        }
        for n, body in sorted(chapters.items())
    }
    preserved = all(r["outcome"] == "preserved" for r in whole.values())
    fully_paid = all(r["level"] == 3 for r in payoff_scores.values())
    narrative_pass = (
        all(r["score"] >= 2 for r in narrative.values())
        and narrative["N1"]["score"] >= 3
    )
    length_pass = all(r["within_1200_1600"] for r in length.values())
    return {
        "entry_id": packet["entry_id"],
        "reviewer_type": "ai_independent_reading",
        "semantic_verdicts_are_ai_judgments": True,
        "program_verifies": "完整schema、引文字面与允许章节、固定评分运算；不自动判语义真假",
        "protocol_sha256": PROTOCOL_SHA256,
        "brief_sha256": rules["brief_sha256"],
        "chapter_length": length,
        "whole_constraint_counts": dict(Counter(r["outcome"] for r in whole.values())),
        "payoffs": payoff_scores,
        "payoff_total": sum(r["level"] for r in payoff_scores.values()),
        "payoff_total_maximum": 9,
        "narrative": {key: item["score"] for key, item in narrative.items()},
        "gates": {
            "length": length_pass,
            "six_constraints": preserved,
            "three_complete_payoffs": fully_paid,
            "narrative": narrative_pass,
        },
        "meets_this_protocol_according_to_ai": length_pass
        and preserved
        and fully_paid
        and narrative_pass,
        "assessment": assessment,
    }


def template(packet, rules):
    def constraint(rule):
        return {
            "id": rule["id"],
            "outcome": "not_tested",
            "reason": "待独立阅读后填写",
            "evidence": [],
        }

    return {
        "entry_id": packet["entry_id"],
        "chapter_assessments": [
            {"chapter": n, "constraints": [constraint(r) for r in rules["constraints"]]}
            for n in (1, 2, 3)
        ],
        "whole_constraints": [constraint(r) for r in rules["constraints"]],
        "payoffs": [
            {
                "id": p["id"],
                "checks": [
                    {
                        "id": c["id"],
                        "satisfied": False,
                        "reason": "待独立阅读后填写",
                        "evidence": [],
                    }
                    for c in p["checks"]
                ],
            }
            for p in rules["payoffs"]
        ],
        "narrative": [
            {"id": n["id"], "score": None, "reason": "待独立阅读后填写", "evidence": []}
            for n in rules["narrative_dimensions"]
        ],
        "overall_comment": "",
        "limitations": ["AI评审，不是自然人阅读评价；引文逐字正确不保证语义判断无偏。"],
    }


def self_test():
    body = "他把铜管口封住，铃声停了。"
    item = {
        "evidence": [
            {"chapter": 1, "quote": "他把铜管口封住", "source_kind": "narrated_action"}
        ]
    }
    quotes(item, {1: body, 2: body, 3: body}, allowed={1}, required=True)
    for check in (
        lambda: quotes(
            item,
            {1: "不含该句的离线夹具", 2: body, 3: body},
            allowed={1},
            required=True,
        ),
        lambda: quotes(item, {1: body, 2: body, 3: body}, allowed={2}, required=True),
        lambda: quotes({"evidence": []}, {1: body}, allowed={1}, required=True),
    ):
        try:
            check()
        except ValueError:
            pass
        else:
            raise AssertionError("无效引文或跨章引用未被拒绝")
    require(
        [
            payoff_level([{"satisfied": x} for x in xs])
            for xs in (
                (False, False, False),
                (True, False, False),
                (True, True, False),
                (True, True, True),
            )
        ]
        == [0, 1, 2, 3],
        "兑现等级运算错误",
    )
    print(
        "离线自检通过：逐字引文、章节边界、必填证据和完整/部分兑现等级；不计模型效果。"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--packet")
    parser.add_argument("--assessment")
    parser.add_argument("--output")
    parser.add_argument("--template", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    rules = protocol()
    if args.self_test:
        self_test()
        return
    require(args.packet, "需要--packet绝对路径")
    packet = read_json(args.packet)
    packet_chapters(packet)
    if args.template:
        print(dump(template(packet, rules)))
        return
    require(args.assessment, "需要独立AI填写的--assessment")
    result = evaluate(packet, read_json(args.assessment), rules)
    result["packet_sha256"] = sha(Path(args.packet).read_bytes())
    result["raw_assessment_sha256"] = sha(Path(args.assessment).read_bytes())
    result["validator_sha256"] = sha(Path(__file__).read_bytes())
    content = dump(result)
    if args.output:
        destination = Path(args.output)
        require(
            destination.is_absolute() and destination.resolve().is_relative_to(ROOT),
            "输出须为工作目录内绝对路径",
        )
        if destination.exists():
            require(
                destination.read_text() == content,
                "拒绝覆盖不同的既有评分，请另存新的评审版本",
            )
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content)
        print(
            dump(
                {
                    "entry_id": packet["entry_id"],
                    "output": str(destination),
                    "sha256": sha(content.encode()),
                    "payoff_total": result["payoff_total"],
                    "meets_this_protocol_according_to_ai": result[
                        "meets_this_protocol_according_to_ai"
                    ],
                }
            )
        )
    else:
        print(content)


if __name__ == "__main__":
    main()
