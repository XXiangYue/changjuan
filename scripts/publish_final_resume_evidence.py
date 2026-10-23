"""Expose final frozen outcomes in the local product, without changing evaluations."""

from hashlib import sha256
import json
from pathlib import Path

from novel_agent.studio.db import Database, encode, now

ROOT = Path(__file__).resolve().parents[1]


def publish():
    db = Database(ROOT / "data/studio.sqlite")
    records = [
        (
            "resume-primary-story-v1-final",
            "三章实际创作 · 两组均未通过冻结验收",
            "artifacts/resume/live-story-v1/result-final.json",
            "相同初始设定与计划，各一条三章轨迹；匿名AI逐章与全卷核验后解盲。两组均超长，完整兑现同为1/3；没有完整系统整体优胜证据。操作者纠正蓝图、审阅提案与摘要的干预另有记录；不是真人盲评。",
            {
                "基线篇幅通过": "0/3",
                "完整系统篇幅通过": "0/3",
                "两组完整兑现": "均1/3",
                "整体优势": "未证实",
            },
        ),
        (
            "resume-local-repair-v1-final",
            "真实返工坏例 · 解除与复响补齐，篇幅变差",
            "artifacts/resume/live-story-v1/repair-review/review.json",
            "按原始事件顺序绑定首次语义审核前后稿，另一位AI只读匿名包核验。一个有意选择的坏例：五项局部检查由3/5到5/5；字符1627到1990，超限恶化。不是五个独立案例或一般修订收益。",
            {"同一坏例检查": "3/5 → 5/5", "字符": "1627 → 1990", "副作用": "篇幅恶化"},
        ),
        (
            "resume-ui-rewrite-v1-final",
            "实际界面改稿 · 篇幅合同与逐项确认",
            "artifacts/resume/live-rewrite-v1/result.json",
            "额外单章定向改稿，经界面选段、审阅正文及记忆/状态、确认定稿和导出。三处日期由AI操作者阅读核对；未知余额转移提案被界面阻止，取消该项后提交。显式干预样例，未替换三章主实验。",
            {
                "原稿字符": 1770,
                "改后字符": 1571,
                "约定范围": "1200–1600",
                "模型调用": 4,
                "费用估算": "¥0.273460",
            },
        ),
        (
            "resume-memory-correction-v1-final",
            "末轮复核勘误 · 正文与记忆仍需分别核对",
            "artifacts/resume/live-rewrite-v1/memory-correction-addendum.json",
            "改稿正文已是两次触温、第三次被挡下，但AI操作者接受的事件摘要仍误记三次。最后UI复核发现后，在记忆编辑器修正为版本3，正文及正式版本1未变，旧审核失效。原封存记录保留，追加新快照与导出；不把人工修正写成自动审核成功。",
            {
                "遗漏": "事件摘要次数",
                "记忆版本": "2 → 3",
                "正文版本": "1，未变",
                "额外模型调用": 0,
            },
        ),
        (
            "resume-context-replay-v1-final",
            "上下文预算 · 冻结包离线复算",
            "artifacts/resume/context-replay/2ffea12a08174682be510eb40b5655ec/verification-offline.json",
            "24个保存上下文×3角色，独立冻结包禁数据库和网络重算一致。资料子包总UTF8字节减少27.14%，各视图不超过60000字节；不含历史作者指令，不等于整个请求token或实际账单节省。",
            {
                "重算一致": "72/72",
                "原始子包字节": 4060713,
                "选后字节": 2958621,
                "子包字节减少": "27.14%",
            },
        ),
        (
            "resume-sigkill-v2-final",
            "进程强杀 · 正式版本、事件与索引恢复",
            "artifacts/resume/recovery/6cc76947e809412c9831fa0c4e8a12b7/results.json",
            "四个真实SIGKILL边界使用当前冻结的11份源码验证；每案恢复正文、十份记忆、事件、FTS和镜像，重复确认不新增版本。离线模型夹具，本地SQLite与关键词范围；不证明向量恢复、供应商扣费对账或通用SLA。",
            {
                "边界通过": "4/4",
                "源码哈希一致": 11,
                "重复提交": "未增版本/事件",
                "检索范围": "SQLite FTS",
            },
        ),
    ]
    for identifier, title, relative, summary, metrics in records:
        path = ROOT / relative
        json.loads(path.read_text())
        value = {
            "summary": summary,
            "metrics": metrics,
            "raw": {"path": relative, "sha256": sha256(path.read_bytes()).hexdigest()},
            "source": "frozen_artifact_import",
        }
        previous = db.one(
            "SELECT result FROM experiments WHERE id=?", (identifier,), required=False
        )
        if previous:
            assert json.loads(previous["result"]) == value, (
                "Existing evidence differs; use a new ID"
            )
            continue
        db.execute(
            "INSERT INTO experiments(id,name,config,result,status,created) VALUES(?,?,?,?,?,?)",
            (
                identifier,
                title,
                encode(
                    {
                        "source": "frozen_artifact_import",
                        "no_model_call_during_import": True,
                    }
                ),
                encode(value),
                "completed",
                now(),
            ),
        )
    print(json.dumps({"published_final_records": len(records), "model_calls": 0}))


if __name__ == "__main__":
    publish()
