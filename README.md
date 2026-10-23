# 长卷 · 让故事生长

开源版本采用 [MIT 许可证](LICENSE)。仓库包含源码、测试、评估输入及文档；个人小说、密钥、数据库和 `artifacts/` 下的本地实验产物不随源码发布。下文涉及 `artifacts/` 的报告与样书链接仅适用于生成了对应产物的本地工作区，历史实验描述不代表仓库附带原始证据。

面向长篇作者的本地小说创作工作台。你决定故事的方向，长卷协助规划、续写、检查设定和整理记忆。每章的正文、事实与记忆变更都能审阅，确认后才进入正式故事。

**2026-10-21补强**：加入作者勾选的事件账本、带时序及版本资格的检索、60KB资料子包预算、三个受限MCP工具与强杀恢复验证。默认检索依据开发集选择关键词；深度重排可选，实测更慢。新三章实写保留超长和兑现不足，单个真实返工坏例得到补齐，未宣称整体文学质量提升。见[简历与证据](docs/项目简历与面试材料.md)、[开发闭环](docs/简历蓝图与开发闭环.md)及[历史改稿机制](docs/历史改稿机制.md)。

**已接通 DeepSeek 官方 API，完成原创样书、对照实验和三位AI子代理片段评审。** 原始结果见 [实测报告](artifacts/evaluation/实测报告.md)。代理的18个选择为完整工作流8、旧策略7、接近3，未显示明确的整体追读优势；[逐位理由与引文](artifacts/evaluation/AI代理片段评审.md)已保留。真人评价为可选功能，未提交的偏好不会被补写。

## 启动

本机直接双击 **启动长卷.command**，或在项目目录运行：

~~~bash
./scripts/dev.sh
~~~

打开 http://127.0.0.1:8765 。启动脚本使用项目内的 Python 环境与已构建前端。首次安装需要 Python 3.12、uv、Node.js 22+ 和 pnpm 10+：

~~~bash
UV_CACHE_DIR="$PWD/.cache/uv" uv sync --frozen --extra dev
pnpm --dir web install --frozen-lockfile --store-dir "$PWD/.cache/pnpm"
pnpm --dir web build
.venv/bin/novel-agent serve --open
~~~

在“模型与设置”中配置官方 DeepSeek 密钥并测试连接；也支持项目 .env 中的 DEEPSEEK_API_KEY。密钥只由后端使用，不进入前端响应、导出或实验报告。默认模型固定为 deepseek-flash，实际响应模型标识和调用日期保存在用量记录。

## 从灵感到定稿

1. 新建作品，填写方向、文风、章数与每章字数。
2. 在“故事规划”中生成蓝图，比较冲突方案，确认全书方向、章节安排与十份核心记忆。
3. 进入“写作工作台”，亲自写作或让长卷创作。右侧可查看场景目标、必须遵守的设定与历史依据。
4. 查看两位编辑的意见。可关闭“自动修订”后自己改稿；开启时最多三轮，未解决的问题保留供作者决定。
5. 逐项选择事实和记忆变化；可编辑记忆内容或拒绝提案。确认后保存正文版本与故事状态。
6. 继续下一章；可以选段改写、恢复历史版本、搜索来源，最后导出 Markdown、TXT 或原目录结构 ZIP。

修改正文会使旧审核和提案失效。重写历史章节会标记后续章节待复核；请从最早的待复核章开始。任务暂停、断线或重启后，从运行提示条恢复即可。

## 可以使用的能力

| 作者的需要 | 对应操作 |
|---|---|
| 先控制剧情，再写正文 | 故事规划、冲突候选、章节场景与约束编辑 |
| 查清角色此时知道什么 | 资料库的时序事实、原文引用、章节来源 |
| 长篇写到后面仍能衔接 | 核心记忆、上一章全文、近十章摘要、关键词与语义检索 |
| 改一段，不丢整章 | 选段改写、自动保存、版本比较与恢复草稿 |
| 不让猜测污染设定 | 草稿与正式稿分离、正文和记忆一起审阅 |
| 刷新后继续工作 | 后台任务、LangGraph 检查点、持久 SSE 事件 |
| 看清效果和费用 | 运行记录、预算预留、真实实验、匿名片段对比 |

语义检索使用本地 BGE 中文嵌入与 Qdrant，无需启动数据库服务。首次启用会下载模型到本项目缓存。不可用时界面显示降级，关键词和时序状态仍可使用。

“故事资料库→状态账本”可查看已确认的持有、知情和伏笔来源；“记忆”审阅页中的状态变化默认不勾选。资源以整数最小单位计量，未知数量不当成零。程序核对来源、版本和前置条件，作者仍需判断含义是否正确。在“调整规划”中可填写篇幅上下限，不填沿用目标±20%；生成会按场景分配篇幅并实测正文字符，仍超限时保留草稿供作者处理。

## 数据与运行

- SQLite 是权威数据源：data/studio.sqlite；检查点：data/checkpoints.sqlite。
- Markdown 阅读镜像：data/projects/作品ID/workspace 与 小说本体/第X卷/第X章.md。
- 私有设置：.local/settings.json；模型缓存：.cache；实验：artifacts/evaluation。
- 一份作品同一时间只有一个创作或待确认任务；不同作品可同时工作。
- 服务仅监听本机回环地址。本版为单人本地产品，不提供账号、云同步或付费服务。
- 导出格式卡片将文件保存在 data/projects/作品ID/exports，并显示路径；也可主动点击浏览器下载。
- ZIP 导出包含当前已定稿正文与记忆；完整历史、运行记录和检查点请在停止服务后备份 data 目录。ZIP 不含密钥。
- 费用是按官方高峰单价和实际返回 token 数的保守估算，并非官方账单；未知计费用量保守保留。预算最高 300 元，不足下一次调用时保存并暂停。

## 验证与实验

~~~bash
./scripts/check.sh
.venv/bin/novel-agent eval --mode report
.venv/bin/novel-agent eval --mode ablations
.venv/bin/novel-agent eval --mode integration --parallel 8
.venv/bin/novel-agent eval --mode audit
.venv/bin/novel-agent eval --mode baseline
.venv/bin/novel-agent eval --mode sample
.venv/bin/novel-agent eval --mode retrieval --dataset retrieval-novel-v1.1 --split heldout --rerank
.venv/bin/novel-agent mcp --project-id 作品ID --before-position 100004 --port 8765
~~~

retrieval不调用收费聊天API；需已缓存的本地模型，`--rerank`不会自动下载，CPU测试可能约10分钟。验收集已公开，后续重跑只作回归，不作为新的未见样本。MCP命令通过stdio连接运行中的本地服务，启动时固定作品与时间上界，只有查询、状态读取及待确认提案工具，没有正式定稿权限；详见[MCP与技能边界](docs/MCP与技能边界.md)。

sample、baseline、ablations、integration、audit 会真实调用模型并产生费用，需要服务已启动。默认整卷命令使用《余烬照山河》已确认蓝图，可从进度继续；测试操作者的自动确认会明确写入审计，不能代替读者评价。固定探针只重试尚未成功的试次，原始失败不删除。integration 另外运行96次真实章节执行图的历史缺口压力验收：历史输入是冻结档案，不是真实生成的前十一章；共享稳定规则，按实际开关启用/关闭历史检索与结构化状态。已完成正文的评测失败优先复用原稿，不为补分重新生成。本地工作台HTTP直连，不经过系统代理；首轮传输故障与恢复规则变更另存transport-recovery.json。客户端最多8个在途试验，服务同时执行2个创作任务，编辑节点可并行。

冻结输入见 [evaluations/frozen-cases.json](evaluations/frozen-cases.json)，历史源码快照见 [artifacts/baseline/legacy-source.zip](artifacts/baseline/legacy-source.zip)。原终端 /start、/new_volume、/chapter 入口通过 novel-agent terminal 共用新服务；旧实现保留用于审计与兼容阅读。

## 阅读材料

- [产品与实测导览（含实际界面）](artifacts/产品与实测导览.html)
- [作者使用手册](docs/使用手册.md)
- [架构与接口](docs/架构与接口.md)
- [产品与技术叙事](docs/产品与技术叙事.md)
- [交付与实验结论：包括未取得优势的指标](docs/交付与实验结论.md)
- [评估方法与边界](docs/评估方法.md)
- [演示路线](docs/演示路线.md)
- [实写中发现的问题与修复](docs/实写中发现的问题与修复.md)
- [实时生成的实测报告](artifacts/evaluation/实测报告.md)
- [完整样书](artifacts/evaluation/full-novel.md) · [旧策略对照](artifacts/evaluation/baseline-novel.md)

报告与导览是带时间戳的快照。追加真实盲评后，运行 `novel-agent eval --mode report`，再运行 `.venv/bin/python scripts/build_delivery.py` 更新材料；这两个操作不调用模型。

所有任务结束后，可运行 `.venv/bin/python scripts/verify_delivery.py --backup` 更新SQLite交付备份与完整性记录。它也检查当前配置密钥是否出现在可交付文件或ZIP中，不打印密钥；有活跃任务时拒绝备份。`.venv/bin/python scripts/build_manifest.py` 记录源码、依赖与测试快照。

长卷为作者提供可靠的创作过程，不保证每一稿都成为好小说。角色采用独立上下文，但目前使用同一基础模型；引文存在不等于事实解释一定正确，作者审阅仍是关键。
