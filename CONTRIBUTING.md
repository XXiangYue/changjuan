# 参与长卷开发

欢迎修正文档、报告问题、改进工作台和补充可复现的测试。涉及较大的功能调整时，可以先在 [Issues](https://github.com/XXiangYue/changjuan/issues) 描述创作场景、预期行为和现有困难。

## 本地开发

按 [README](README.md#start) 安装依赖。后端在一个终端运行：

```bash
.venv/bin/novel-agent serve
```

前端在另一个终端运行：

```bash
pnpm --dir web dev
```

使用 Vite 显示的地址打开开发界面；`/api` 由开发服务器转发到 `127.0.0.1:8765`。修改 Python 后重启后端，修改前端时由 Vite 更新页面。服务会使用当前项目中的本地数据；实验或数据模型改动应在独立克隆中进行。

## 提交前检查

macOS / 已安装 zsh 的环境可运行：

```bash
./scripts/check.sh
```

也可以分别执行：

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src/novel_agent/studio src/novel_agent/cli.py tests/test_studio.py --select F
pnpm --dir web build
```

测试临时目录位于项目 `.cache/`。日常测试不需要真实模型密钥；`eval` 命令中的生成实验会调用模型，执行前请阅读 [评估方法](docs/评估方法.md)。

文档或图片修改需检查相对链接、图片路径、GitHub 渲染及窄屏可读性。不要把静态徽章写成未经验证的构建或覆盖率成绩。

## 修改落在哪里

| 内容 | 入口 |
| --- | --- |
| 工作台交互 | `web/src/` |
| HTTP / SSE 接口 | `src/novel_agent/studio/api.py` |
| 创作工作流与恢复 | `src/novel_agent/studio/engine.py` |
| 事务、版本、导入导出 | `src/novel_agent/studio/repository.py` |
| 检索和上下文预算 | `src/novel_agent/studio/retrieval.py`、`context_budget.py` |
| 状态账本与 MCP | `src/novel_agent/studio/story_state.py`、`story_mcp.py` |
| 创作规范 | `src/novel_agent/craft/skills/` |

`src/novel_agent/` 中的早期终端实现仍作为兼容与回归代码保留。当前产品入口是 `cli.py` 和 `studio/`；涉及旧模块的改动应核对现有测试和依赖。

## 问题与 Pull Request

报告问题时，请提供操作步骤、预期结果、实际结果，以及操作系统和依赖版本。可以附经过脱敏的错误信息或界面截图。

提交改动时说明解决的问题、行为变化、验证方式及尚未解决的限制。与正文保存、历史改稿或记忆状态有关的改动，重点核对版本冲突、作者确认和中断恢复。

请勿提交 `.env`、`.local/`、`data/`、模型缓存、个人稿件或原始运行记录。本地 Agent 指令文件也不进入源码分发。公开示例使用虚构内容；项目代码遵循 [MIT 许可证](LICENSE)。
