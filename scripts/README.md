# 开发与评估脚本

常规使用从 [项目首页](../README.md) 开始。这些脚本服务于开发、固定场景评估与本地证据整理，不是第一次启动的必需步骤。

## 日常开发

| 脚本 | 用途与前提 |
| --- | --- |
| `dev.sh` | 安装依赖、构建前端并启动工作台；需要 zsh、uv、pnpm |
| `check.sh` | pytest、限定范围的 Ruff 检查与前端构建；需要已安装开发依赖 |
| `build_manifest.py` | 记录源码和依赖指纹；先运行 `check.sh` 生成测试 XML |
| `verify_delivery.py` | 检查已有本地数据库与可交付文件；`--backup` 还会备份数据库，要求没有活跃或待确认任务 |

## 检索与上下文

| 脚本 | 用途与前提 |
| --- | --- |
| `build_retrieval_cases.py` | 构建固定检索评估输入 |
| `build_novel_retrieval_cases.py` | 构建小说场景检索输入 |
| `revise_novel_retrieval_cases_v11.py` | 生成 v1.1 检索输入修订 |
| `replay_context_budget.py` | 对已保存的本地输入重放上下文预算；依赖 `artifacts/resume/` 中的历史样本 |

仓库已包含冻结后的 `evaluations/` 输入，日常回归无需重新运行构建脚本。修改输入后需记录版本，不能把已见的验收集当作新的独立样本。

## 工作流与真实模型评估

| 脚本 | 用途与前提 |
| --- | --- |
| `run_recovery_probe.py` | 在项目内的独立夹具目录检查进程中断与恢复；使用模型夹具 |
| `run_mcp_live_eval.py` | 运行固定 MCP 真实模型任务；需要密钥、模型权限并产生费用 |
| `run_live_story_baseline.py` | 运行真实写作基线；需要本地作品与模型配置 |
| `setup_live_rewrite_case.py` | 准备历史改稿验收场景 |
| `build_live_story_packets.py` | 从已有运行结果生成匿名评阅材料 |
| `score_live_story.py` | 按冻结协议整理实写评分 |
| `summarize_live_story_v1.py` | 汇总已有实写结果 |
| `import_agent_reading.py` | 校验并导入已完成的 AI 片段评阅记录 |

部分固定场景工具依赖已完成的本地作品或未随源码发布的运行档案。执行前阅读脚本中的输入路径；缺失输入时，不代表全新安装失败。通用评估入口、费用及方法边界见 [评估方法](../docs/评估方法.md)。
