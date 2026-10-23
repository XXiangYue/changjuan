"""Build an offline product/evidence handout from actual experiment files."""

from pathlib import Path
import base64
import html
import json
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "artifacts"
esc = lambda s: html.escape(str(s))
guide = OUT / "guide"
guide.mkdir(exist_ok=True)
raw = json.loads((OUT / "evaluation/results.json").read_text())
experiments = {e["name"]: e for e in raw["experiments"]}
full = experiments.get("完整样书", {}).get("result", {})
base = experiments.get("旧流程策略对照", {}).get("result", {})
probe = experiments.get("固定场景消融 v1", {}).get("result", {})
integration = experiments.get("真实执行图续写验收 v1", {}).get("result", {})
agent_result = experiments.get("Codex子代理匿名片段评审 v1", {}).get("result", {})
agent_reading = agent_result.get("agent_reading", {})
reading_names = {
    "full": "完整工作流",
    "baseline": "旧策略",
    "tie": "接近",
    "mixed": "无多数",
}
agent_rows = "".join(
    "<tr>"
    + "".join(
        "<td>" + esc(value) + "</td>"
        for value in (
            sample["sample_id"],
            sample["chapter"],
            sample["full"],
            sample["baseline"],
            sample["tie"],
            reading_names[sample["majority"]],
        )
    )
    + "</tr>"
    for sample in agent_reading.get("by_sample", [])
)
usage = raw["usage"]


def image(name, caption):
    path = OUT / "screenshots" / name
    if not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode()
    return (
        '<figure><img alt="'
        + esc(caption)
        + '" src="data:image/png;base64,'
        + encoded
        + '"><figcaption>'
        + esc(caption)
        + "</figcaption></figure>"
    )


def metric_table(result):
    return (
        "<table><tbody>"
        + "".join(
            "<tr><th>"
            + esc(k)
            + "</th><td>"
            + esc(v if v is not None else "尚无数据")
            + "</td></tr>"
            for k, v in result.get("metrics", {}).items()
        )
        + "</tbody></table>"
    )


test_count = "待运行"
xml = OUT / "testing/pytest.xml"
if xml.exists():
    suites = ET.parse(xml).getroot()
    test_count = sum(int(s.attrib.get("tests", 0)) for s in suites.iter("testsuite"))
full_count = full.get("metrics", {}).get("定稿章数", 0)
full_chars = full.get("metrics", {}).get("正文字符数", 0)
full_han = full.get("metrics", {}).get("汉字数（基本区）", 0)
votes = raw.get("human_votes", [])
voted = sum(bool(v.get("preference")) for v in votes)
probe_rows = "".join(
    "<tr><td>"
    + esc(
        {
            "baseline": "旧策略",
            "full": "完整机制",
            "no_memory": "无历史上下文",
            "no_revision": "不自动修订",
        }.get(k, k)
    )
    + "</td>"
    + "".join(
        "<td>" + esc(v[key]) + "</td>"
        for key in (
            "successful_trials",
            "violations_model_judged",
            "preserved",
            "not_tested",
            "readability",
            "momentum",
        )
    )
    + "</tr>"
    for k, v in probe.get("by_mode", {}).items()
)
mode_names = {
    "baseline": "旧策略",
    "full": "完整流程",
    "no_memory": "无历史检索及状态",
    "no_revision": "不自动修订",
}
integration_rows = "".join(
    "<tr><td>"
    + esc(mode_names.get(mode, mode))
    + "</td>"
    + "".join(
        "<td>" + esc(values.get(key)) + "</td>"
        for key in (
            "successful_trials",
            "violations_model_judged",
            "preserved",
            "not_tested",
            "archive_retrieved",
            "structured_state_present",
            "mean_extra_drafts",
        )
    )
    + "</tr>"
    for mode, values in integration.get("by_mode", {}).items()
)
integration_summary = "；".join(
    f"{mode_names.get(mode, mode)}{values['successful_trials']}次："
    f"{values['violations_model_judged']}项违例、{values['preserved']}项保持、{values['not_tested']}项未触及"
    for mode, values in integration.get("by_mode", {}).items()
)
integration_metrics = integration.get("metrics", {})
features = [
    (
        "把方向握在自己手里",
        "全书方向、分卷、章节与场景；修改前看到关联的计划和十份记忆。",
        "分层规划 · DeepAgents 创作技能",
    ),
    (
        "先问故事此刻是什么",
        "上一章全文、近十章摘要、带出处的状态和历史片段共同进入上下文。",
        "时序状态 · FTS5 / BGE / Qdrant",
    ),
    (
        "检查意见能回到原文",
        "两位编辑独立读稿，先区分事实矛盾、计划变化和表达偏好。引文由段落编号取回。",
        "角色推理预算 · 结构化审阅",
    ),
    (
        "确认后才进入正式故事",
        "正文、摘要、事实与记忆一起审阅。拒绝的下一章提案不会悄悄覆盖原计划。",
        "HITL · 原子提交 · 乐观版本",
    ),
    (
        "中断后还能接着写",
        "网页只接收流式事件；任务与检查点在本地。历史重写会让后文进入复核。",
        "LangGraph 持久化 · SSE 续接",
    ),
    (
        "把改进交给证据",
        "保留样书、对照、失败、费用与匿名阅读选择，区分工程可靠性和文学偏好。",
        "可复跑评估 · 费用预留与结算",
    ),
]
feature_html = "".join(
    '<article class="card"><span class="num">0'
    + str(i + 1)
    + "</span><h3>"
    + title
    + "</h3><p>"
    + body
    + "</p><small>"
    + tech
    + "</small></article>"
    for i, (title, body, tech) in enumerate(features)
)
steps = "".join(
    "<span>" + step + "</span>"
    for step in (
        "作者目标",
        "查阅上下文",
        "写场景正文",
        "双编辑审阅",
        "作者确认",
        "版本与记忆",
        "继续下一章",
    )
)
page = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>长卷 · 产品与实测导览</title><style>
:root{--ink:#253a30;--green:#45644e;--muted:#69766b;--line:#dfe4d8;--paper:#fffef8}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#f3f4ec;color:var(--ink);font:16px/1.85 'PingFang SC','Microsoft YaHei',sans-serif}a{color:var(--green);text-underline-offset:4px}main{max-width:1120px;margin:auto;padding:38px 42px 70px}header{display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid var(--line);padding-bottom:24px}.brand{font:600 26px 'Songti SC',serif;letter-spacing:4px}.tag{font-size:12px;letter-spacing:2px;color:var(--muted)}.hero{padding:66px 0 38px}.eyebrow{font-size:12px;letter-spacing:3px;color:#74836f}h1{font:500 clamp(32px,4.4vw,54px)/1.35 'Songti SC','SimSun',serif;letter-spacing:2px;margin:18px 0 23px;max-width:850px}h2{font:500 29px 'Songti SC',serif;margin:0 0 20px}h3{font-size:17px;margin:10px 0}p{margin:0 0 15px;color:#526353}.lead{max-width:800px;font-size:18px}.cta{display:inline-block;padding:11px 22px;border-radius:7px;background:var(--green);color:#fff;text-decoration:none;margin:15px 12px 0 0}.secondary{background:#e2e9da;color:var(--ink)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:28px 0}.stats>div,.card,.surface{background:var(--paper);border:1px solid var(--line);border-radius:12px;padding:24px}.stats strong{display:block;font:32px 'Songti SC',serif;margin:10px 0}.stats small,.card small{font-size:12px;color:var(--muted)}section{margin:55px 0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.card p{font-size:14px}.num{font:23px Georgia,serif;color:#98aa88}.flow{display:flex;flex-wrap:wrap;gap:9px;margin:25px 0}.flow span{padding:10px 13px;background:#e3ebdb;border-radius:5px;font-size:13px}.flow span:not(:last-child):after{content:' →';margin-left:8px;color:#8e9f86}figure{margin:25px 0 40px}img{width:100%;height:auto;border:1px solid var(--line);border-radius:12px;display:block;box-shadow:0 12px 35px #273d3010}figcaption{font-size:13px;color:var(--muted);margin-top:10px}.two{display:grid;grid-template-columns:1fr 1fr;gap:22px}table{width:100%;border-collapse:collapse;font-size:13px}th,td{padding:11px 9px;text-align:left;border-bottom:1px solid var(--line)}th{font-weight:500;color:var(--muted)}thead{background:#edf1e6}.note{border-left:3px solid #9caf8d;padding:14px 19px;background:#eaf0e2;font-size:14px;margin:20px 0}.table-wrap{overflow:auto}details{border-bottom:1px solid var(--line);padding:16px 0}summary{cursor:pointer;font-size:17px}details p{margin-top:15px;font-size:14px}.links{display:flex;gap:13px;flex-wrap:wrap;margin-top:20px}.links a{padding:8px 12px;background:#e4ebdc;border-radius:5px;font-size:14px}footer{border-top:1px solid var(--line);padding-top:25px;font-size:12px;color:var(--muted)}@media(max-width:760px){main{padding:24px 20px}.hero{padding-top:40px}.grid,.two{grid-template-columns:1fr}.stats{grid-template-columns:1fr 1fr}.tag{display:none}.stats>div{padding:18px}}@media print{body{background:white}main{padding:10px}.cta{display:none}section,figure,.card{break-inside:avoid}.grid{grid-template-columns:1fr 1fr}details{display:block}details>*{display:block}}
</style></head><body><main><header><span class="brand">卷 · 长卷</span><span class="tag">CHANGJUAN / LOCAL NOVEL WORKSPACE</span></header><div class="hero"><span class="eyebrow">灵感由你掌舵，细节一起照顾。</span><h1>把故事写下去，<br>也把每次改变记清楚。</h1><p class="lead">面向长篇作者的本地创作工作台。从章节计划、流式写作，到事实、摘要与记忆审阅，让下一章有来处，让定稿有依据。</p><a class="cta" href="http://127.0.0.1:8765/">打开本地工作台 →</a><a class="cta secondary" href="evaluation/full-novel.md">阅读实际样书</a></div>"""
page += (
    '<div class="stats"><div>完整样书<strong>'
    + esc(full_count)
    + " 章</strong><small>"
    + esc(full_chars)
    + " 字符 / "
    + esc(full_han)
    + " 汉字</small></div><div>真实流程续写验收<strong>"
    + esc(integration_metrics.get("成功试次", 0))
    + " / 96</strong><small>另有96次机制探针 · 分别报告</small></div><div>工程验证<strong>"
    + esc(test_count)
    + "</strong><small>回归用例，不冒充文学评分</small></div><div>官方 API 费用估算<strong>¥"
    + format(usage.get("spent", 0) + usage.get("reserved", 0), ".2f")
    + "</strong><small>保守口径 · 预算上限 ¥300</small></div></div>"
)
page += image("overview-final.png", "实际产品界面与样书进度。数据来自本地存储。")
page += (
    '<section><span class="eyebrow">FROM PROBLEM TO WORKFLOW</span><h2>长篇的难点，落到每一章里解决。</h2><div class="grid">'
    + feature_html
    + "</div></section>"
)
page += (
    '<section><h2>你认可的变化，才成为故事。</h2><p>机器先完成有边界的工作，再把正文和记忆一起交给作者。超出修订上限时保留草稿；模型的判断可以被作者有理由地否决。</p><div class="flow">'
    + steps
    + "</div>"
    + image("writing-final.png", "真实写作工作台：目录、正文与规划/审阅/记忆并列。")
    + "</section>"
)
page += (
    '<section><h2>版本与方向，都能回看。</h2><div class="two">'
    + image("version-comparison.png", "独立验收作品的版本比较：修改前后的原文都保留。")
    + image("direction-impact.png", "独立验收作品的方向预览：先看关联计划与十份记忆。")
    + "</div><p>恢复历史版本先形成草稿。历史重写后，后文进入复核；即使正文不变，也能按新上下文重新确认。导入稿保留原稿版本，支持重审与继续追加章节。</p></section>"
)
page += (
    '<section><span class="eyebrow">EVIDENCE AND LIMITS</span><h2>把工程可靠性和读者偏好分开回答。</h2><div class="two"><article class="surface"><h3>完整工作流样书</h3>'
    + metric_table(full)
    + '</article><article class="surface"><h3>旧策略适配对照</h3>'
    + metric_table(base)
    + '</article></div><div class="note">完整卷包含 Codex AI 作为测试操作者的界面修订，开发期间也调整过审阅提示、推理配置和篇幅检查。它是探索性产品验收，不能把整卷差异纯归因于 DeepSeek Agent 技术，也不能将模型编辑分当作真实读者好评。</div><h3>固定场景机制探针</h3><p>12 场景 × 4 配置 × 2 重复；同一 DeepSeek 模型判断。证据注入与返工探针不等同整套产品的端到端消融。</p><div class="table-wrap"><table><thead><tr><th>配置</th><th>有效试次</th><th>判定违例</th><th>保持约束</th><th>未触及</th><th>可读性</th><th>推进分</th></tr></thead><tbody>'
    + probe_rows
    + '</tbody></table></div><p class="note">没有触及约束不算正确。“无历史上下文”组最终也为零违例：其修订环节会收到编辑提供的事实，且未触及约束更多。此结果不能单独证明检索的因果贡献。每种配置只有24试次，同模型生成与评审存在相关偏差，不能由此推出普遍文学质量优势。原始失败均保留。</p></section>'
)
if integration:
    page += (
        "<section><h2>再把同样的任务放进真实执行图。</h2><p>"
        + "12个任务 × 4配置 × 2重复，调用实际的上下文组装、FTS5/BGE/Qdrant、LangGraph、双编辑、记忆提案与版本提交。自动测试操作者统一确认，无外部AI改正文；独立评测不接收配置标签。</p>"
        + "<p>有效试次 "
        + esc(integration_metrics.get("成功试次", 0))
        + " / 96，另保留 "
        + esc(integration_metrics.get("失败试次", 0))
        + ' 次失败尝试。</p><div class="table-wrap"><table><thead><tr><th>配置</th><th>有效试次</th><th>判定违例</th><th>保持约束</th><th>未触及</th><th>档案命中</th><th>状态存在</th><th>额外修订</th></tr></thead><tbody>'
        + integration_rows
        + '</tbody></table></div><p>本轮关闭自动修订组的模型阅读分与推进分略高，完整流程仅少1项违例，不能据此证明返工的独立收益。作者现在可以关闭“自动修订”，保留编辑意见后自行改稿；默认仍开启，与已完成实验一致。</p><p class="note">这是人为构造的历史信息缺口压力输入：第1段存历史事实，中间十段和摘要刻意不含它；四组共享基础规则。它能检查真实历史渠道是否起作用，不能证明自然长篇的遗忘率或文学优势。任务复用前述12场景，不是另一批独立样本，不合并成192个独立案例。两次重复不能支持普遍性结论。</p></section>'
    )
audits = [
    (name, experiments.get(name + " · 整卷定点审阅", {}).get("result", {}))
    for name in ("完整样书", "旧流程策略对照")
]
if any(result.get("metrics") for _, result in audits):
    page += '<section><h2>整卷的承诺，有没有兑现？</h2><p>同一模型按五项约束与三条伏笔审阅两卷，原文引文由程序核验；语义结论仍可能误判。规则在创作过程中补充，不能当作预注册研究。</p><div class="two">'
    for name, result in audits:
        page += (
            '<article class="surface"><h3>'
            + esc(name)
            + "</h3>"
            + metric_table(result)
            + "</article>"
        )
    page += "</div></section>"
if agent_reading:
    page += (
        "<section><h2>三位AI子代理，独立评阅六组片段。</h2><p>"
        + esc(agent_reading["interpretation"])
        + '</p><div class="table-wrap"><table><thead><tr><th>片段</th><th>章节</th><th>完整工作流</th><th>旧策略</th><th>接近</th><th>多数选择</th></tr></thead><tbody>'
        + agent_rows
        + '</tbody></table></div><p class="note">本轮依用户选择由子代理完成；三位同类AI使用独立上下文，18个选择嵌套于六组片段，不是18个独立样本或真人评价。先核对原文引文并封存结果哈希，再揭晓版本身份。</p><p>'
        + "</p><p>".join(esc(x) for x in agent_reading["observations"])
        + '</p><a href="evaluation/AI代理片段评审.md">查看全部理由、分歧与原文依据 →</a>'
        + image(
            "agent-reading-final.png",
            "本轮AI子代理评审在实际实验页展示，与真人阅读选择分别保存。",
        )
        + "</section>"
    )
page += (
    '<section><h2>可选的自然人阅读评价</h2><p>六组等长匿名片段固定取自两卷第1、3、5、7、9、12章开头。入口保留在工作台的“实验与洞察”，未开展的评价不补写；已看解盲结果的人再评同组片段，不作为严格盲评。</p><div class="surface"><h3>已提交 '
    + str(voted)
    + " / "
    + str(len(votes))
    + " 组</h3><p>本轮使用AI子代理评阅。真人选择单独保存，不是尚待完成的交付要求。</p></div>"
    + image("research-final.png", "实验页显示真实记录、限制及匿名片段入口。")
    + "</section>"
)
page += (
    '<section><h2>交付与复现</h2><details><summary>怎样开始自己的作品？</summary><p>双击项目内的“启动长卷.command”，设置官方 DeepSeek 密钥，创建作品并确认蓝图。之后一章一章写作、审阅和定稿；已有稿件可导入ZIP后从第一章复核。</p></details><details><summary>怎样复跑实验？</summary><p>先启动服务。novel-agent eval --mode report 只生成报告；sample、baseline、ablations、integration、audit 会调用官方模型并受本地费用预算约束。冻结输入、失败与配置变化均保留。</p></details><details><summary>目前能证明什么？</summary><p>可以核对一次真实短卷创作闭环、既定工程用例、固定机制场景与真实执行图的历史缺口压力验收。不能证明零幻觉、保证原创、百万字稳定、全面胜过旧策略或所有读者都喜欢。引文存在也不保证语义解释正确，作者审阅仍然必要。</p></details><div class="links"><a href="../README.md">启动说明</a><a href="../docs/使用手册.md">作者手册</a><a href="../docs/架构与接口.md">架构与接口</a><a href="evaluation/实测报告.md">完整实测报告</a><a href="evaluation/results.json">原始结果</a><a href="evaluation/protocol-changes.json">配置与操作者记录</a><a href="../docs/演示路线.md">八分钟演示路线</a></div></section><footer>长卷 · 本地创作工作台。报告快照：'
    + esc(raw["generated_at"])
    + "<br>费用为官方高峰单价的保守估算，未知计费保留预算；以官方账单为准。不含Codex订阅、人员时间与本机计算成本。</footer></main></body></html>"
)
fm, bm = full.get("metrics", {}), base.get("metrics", {})
comparison = "\n".join(
    f"| {key} | {fm.get(key, '待完成')} | {bm.get(key, '待完成')} |"
    for key in (
        "定稿章数",
        "正文字符数",
        "汉字数（基本区）",
        "篇幅达标章数",
        "平均编辑分",
        "费用估算",
        "每万字符费用",
        "点击至首正文P50秒",
        "整章待审P50秒（含中断）",
        "整章待审P95秒（含中断）",
    )
)
audit_comparison = "\n".join(
    "| "
    + title
    + " | "
    + " | ".join(
        str(result.get("metrics", {}).get(k, "待完成"))
        for k in (
            "约束违例（模型）",
            "伏笔兑现（模型）",
            "部分兑现",
            "剧情推进（模型）",
            "重复风险（模型，越低越好）",
        )
    )
    + " |"
    for title, result in audits
)
agent_section = ""
if agent_reading:
    agent_section = (
        "## AI子代理片段评阅：偏好接近，分歧保留\n\n"
        + agent_reading["interpretation"]
        + "\n\n"
        + "\n".join("- " + x for x in agent_reading["observations"])
        + "\n\n用户选择用子代理完成本轮片段比较。三个同类AI各在独立上下文中阅读六组等长片段，不接收版本身份和其他评审结果。原文引文逐字验证，结果哈希先封存、后揭晓身份。18个选择来自同六组片段，不能视为18个独立样本、真人评价或整卷质量结论。完整卷还包含Codex编辑，因此不能将偏好归因于纯DeepSeek生成能力。\n\n"
        + "[全部评审理由、引文与范围](../artifacts/evaluation/AI代理片段评审.md)，原始片段包和封存回执位于 artifacts/evaluation/agent-reading/agent-reading-20261018-v1。\n\n"
    )
conclusion = f"""# 长卷 · 交付与实验结论

快照时间：{raw["generated_at"]}。本文件由实际结果生成，更新命令见 README。

## 已交付的产品

长卷是作者主导的单人本地创作工作台。灵感、全书方向、分卷和章节目标进入规划；模型写草稿并提出审阅意见，作者对正文、摘要、事实与十份记忆逐项作决定。确认后才成为正式故事。

当前作品《余烬照山河》已完成 {fm.get("定稿章数", 0)} 章，{fm.get("汉字数（基本区）", 0):,} 个基本区汉字、{fm.get("正文字符数", 0):,} 个含标点换行的字符。样书完成修炉与小镇危机的局部故事弧，山外失踪线保留为后续引子。两份稿件、三种导出格式、原始调用与失败均已保存。它是实写验收样书，需要继续由读者判断文学效果。

## 难点、关键方法与可核对的证据

| 难点 | 本次方法 | 证据与边界 |
|---|---|---|
| 长篇中人物会知道未来、药会重复出现 | 全文核心记忆、上章正文、近十章摘要、时序状态、关键词与语义检索 | retrieval-proof.json 的24条正式来源检查；96场景机制探针。字面来源有效不保证语义正确 |
| 规划只是梗概，胜利没有行动和代价 | 全书—卷—章—场景规划；记录目标、冲突、代价与变化 | 12章正文及规划。第12章仍由Codex补足备料的实际兑现，不能宣传为模型自动解决了一切 |
| 草稿和提取错误污染设定 | 正文、摘要、事实和记忆联合确认；乐观版本与单事务提交 | 实际拒绝错误主体、资源推断和重复文档；提交失败回归 |
| 返工只改措辞，编辑也会误判 | 独立一致性/叙事上下文、原句证据、最多三轮修订、允许有理由的作者取舍 | 第7章半枚丹误报、第10章角色违规与剧情矛盾的区分；原低分和意见保留 |
| 刷新、中断或重启会丢稿 | LangGraph检查点、后台任务、持久化SSE、幂等提交 | 第4章暂停重启恢复；第11章格式失败后只重试记忆，正文哈希和版本保持 |
| 技术介绍无法证明功能真的使用 | 技能哈希与工具轨迹、费用预留、完整失败记录 | DeepAgents真实read_file/search_story轨迹；本地BGE与Qdrant启用证明；{test_count}项工程测试 |

这里与科研Agent相似的是目标、证据、工具、检验和结果保存。区别在于小说没有唯一正确解：一致性可检查，人物选择、张力和读者期待需要作者与真实读者裁决。

## 同设定整卷对照：控制改善，代价上升

两卷共用模型别名、初始设定、12章蓝图和每章约2500字要求。旧策略使用全量核心记忆、上章全文、单编辑与不自动返工；完整系统加入历史状态、混合检索、双编辑与返工。基线是共用新基础设施的策略适配，不是原代码逐字复刻。

| 指标 | 完整系统 | 旧策略适配 |
|---|---:|---:|
{comparison}

篇幅达标按程序的含符号字符口径、目标上下20%计算。完整系统仍有章节超出目标并由测试操作者写明原因接受。平均编辑分没有更高，而且审阅配置不同，不能把两组自评差异当作独立质量结论。

完整流程的费用与整章等待都更高。双编辑、额外上下文、返工、人工界面修订和开发恢复都在这次样本里；每万字符费用包含失败、规划、改写和整卷审阅，不是纯写作推理单价。首字延迟与整章待审时间分别报告，后者包含中断与开发修复等待，不能当作受控性能基准。

## 机制探针：发现帮助，也保留反例

12个固定场景×4配置×2重复，共96个有效试次，另保留4个失败试次。每种配置评价48项约束。旧策略为11项违例、9项保持、28项未触及；完整机制为0、38、10；关闭历史上下文为0、29、19；关闭自动修订为1、38、9。

不能把未触及约束算作正确。无历史组最终也为零违例，因为修订环节会收到编辑查证的事实；其约束覆盖更低。这个结果支持在这些场景中提供证据和允许修订有用，不能单独证明本地向量检索的因果收益。生成与评测使用同一模型，24试次来自相同12场景的重复，不具备充分独立性与统计功效。

## 真实执行图：检验历史渠道与修订的实际作用

补充验收不直接给作者注入档案，而是将相同12任务置于真实服务中。四种配置各重复两次，调用生产执行图的实际开关，完整经过上下文、FTS5/BGE/Qdrant、结构化状态、写章、编辑、记忆提案和定稿。测试操作者统一自动确认最终产出，没有Codex外部正文修订。独立评测只见任务、已知事实与续写，不见配置名称。

当前有效试次 {integration_metrics.get("成功试次", 0)}/96，另保留 {integration_metrics.get("失败试次", 0)} 次失败尝试。{integration_summary}。

输入的前十一段是冻结档案，不是模型写成的前十一章：第1段存远处事实，中间十段是同一时刻的中性静物，核心动态文档和近十章摘要刻意未记该事实。所有配置共享相同基础能力规则。带来源的结构化档案由输入预置，不测自动提取质量。它验证历史渠道对指定信息缺口的补救，不估计普通创作发生遗忘的概率。

首轮87有效、9次连接超时，随后改为本地工作台HTTP直连，只补失败项：3个定稿和4个完整草稿核对哈希复用，2个没有完整稿才重新写。原冻结控制器、首轮结果与传输修复另存，五份生成/审阅/检索/事务核心源码保持原值；细节见transport-recovery.json。从结果看，历史渠道在这些压力输入下帮助保持约束；不能拆分关键词、语义检索和预置状态各自的贡献。关闭自动修订组的模型可读性与推进分反而略高，完整流程仅少1项违例，两个重复不足以证明返工的独立收益。工作台因此提供“自动修订”开关，由作者决定，默认仍开启，未改变本次实验输入或正文。历史原文命中、结构化状态存在、修订次数、全部调用成本和失败见实测报告与results.json。额外正文轮次包含篇幅返工，不都是一致性纠错。这12个任务复用了前述探针，两个实验不可合并为192个独立案例。生成和评审仍是同一模型；两次重复、短场景压力输入不支持一般文学质量或自然长篇收益的结论。状态与检索一起开关，也不能单独分离向量、关键词和结构化状态各自的贡献。

## 整卷审阅：减少违例不等于更会讲故事

下表均为同一模型的判断。原文引文经程序逐字校验，解释仍可能有误。

| 作品 | 约束违例 | 伏笔完整兑现 | 部分兑现 | 推进分 | 重复风险（低好） |
|---|---:|---:|---:|---:|---:|
{audit_comparison}

完整系统五项约束未被判定违例，但三条伏笔都仅获部分兑现；旧策略有一项资源违例，账册线被判完整兑现。两组推进分与重复风险相同，本次没有取得整卷叙事收束或重复控制优势。模型认为中段翻账、公堂、听息和咳血反复出现，拖慢节奏。这是当前文学编辑能力的明确限制。

本次审阅把幕后来源、账册底页和失踪案结案也纳入兑现标准，比局部修炉弧线更严格。下一轮应在写作前更清楚地定义局部回收与全案结案，不能在看完结果后把partial改成resolved。本报告保留原判与原始引文。

{agent_section}## 谁做了编辑，谁做了评价

测试操作者是Codex AI助手，不是自然人作者。完整卷包含通过界面的外部AI修稿、摘要校正、事实拒绝和定稿决定。开发时也调整过思考配置、篇幅检查、结构输出和记忆处理。这是探索性产品验收，不能把质量差异纯归因于DeepSeek或某一项Agent技术。编辑干预与配置变化详见 protocol-changes.json。

真实用户评价当前已提交 {voted}/{len(votes)} 组。依用户选择，本轮片段比较由子代理完成，真人入口为可选功能，未提交的偏好不作推断。只从两卷第1、3、5、7、9、12章开头各取等长500字符；已读解盲报告的用户再评同组片段，不作为严格盲评。AI评审结果与真人投票分别保存。

## 费用、运行与复现

全部真实开发和实验调用的费用保守估算为 ¥{usage.get("spent", 0) + usage.get("reserved", 0):.2f}，预算上限¥300。按官方高峰未缓存输入2元、输出8元/百万token估算；未知费用保守保留，不扣缓存和闲时优惠。以官方账单为准，不含Codex订阅、人员时间与本机计算。

调用采用DeepSeek官方deepseek-flash。2026-10-18核对的[官方更新](https://api-docs.deepseek.com/zh-cn/news/news261017/)称该别名已升级至V4.1-Flash-1017；实验只报告响应实际返回的标识和日期，不能独立认证内部快照。

运行检查、导出、格式恢复和版本变更都有原始记录；源码、依赖和测试结果见 implementation-manifest.json。软件验证包含真实使用和离线注入故障，离线夹具不计入模型效果。

新实验应复制项目并保留冻结输入、锁文件、全部失败与新的模型日期。现有证据仅覆盖本机、本模型、一部玄幻短卷及12个固定场景；百万字、跨题材、多作者、Windows与云部署未作生产验收。

## 可使用的产品表述

“长卷把创作Agent放进作者能控制的工作台：每章有计划、有来源、可审阅、能恢复。我们真实完成一部约3万汉字的小说，保留失败、费用和不利结果，再让读者判断是否想继续读。”

不使用“零幻觉”“自动爆款”“全面超越旧系统”等现有数据不支持的结论。现代Agent技术在这里的价值，首先是可控过程与可复核证据；文学质量仍需要更强的承诺管理、编辑判断和真实作者使用验证。

详细入口：[使用手册](使用手册.md)、[架构与接口](架构与接口.md)、[实测报告](../artifacts/evaluation/实测报告.md)、[原始结果](../artifacts/evaluation/results.json)、[配置与编辑记录](../artifacts/evaluation/protocol-changes.json)、[实际修复](实写中发现的问题与修复.md)。
"""
(ROOT / "docs/交付与实验结论.md").write_text(conclusion)
for source in [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]:
    content = source.read_text().replace("(../artifacts/", "(../")
    if source.name == "README.md":
        content = content.replace("(docs/", "(").replace("(artifacts/", "(../")
    (guide / source.name).write_text(content)
page = page.replace("../README.md", "guide/README.md").replace("../docs/", "guide/")
page = page.replace(
    '<a href="guide/架构与接口.md">架构与接口</a>',
    '<a href="guide/架构与接口.md">架构与接口</a><a href="guide/交付与实验结论.md">技术与实验结论</a>',
)
(OUT / "产品与实测导览.html").write_text(page)
print(OUT / "产品与实测导览.html")
