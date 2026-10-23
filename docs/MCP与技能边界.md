# MCP 与创作技能

长卷允许外部客户端查询当前作品的资料、读取故事状态，并提交由作者审阅的变化提案。MCP 服务复用本地工作台的数据与权限检查，正式定稿仍在工作台完成。

## 启动 MCP 服务

先启动工作台，再在支持 stdio 的 MCP 客户端中配置命令。下例需要把项目绝对路径和作品 ID 替换为自己的值：

```bash
/absolute/path/changjuan/.venv/bin/novel-agent mcp \
  --project-id YOUR_PROJECT_ID \
  --before-position 100004 \
  --port 8765
```

作品 ID 可从本地 `GET /api/projects` 响应中读取。`before-position` 是严格的时间上界，按“卷号 × 100000 + 章号”编码；例如 `100004` 表示只读取第一卷第四章之前的有效历史。代码入口见 [story_mcp.py](../src/novel_agent/studio/story_mcp.py)。

服务启动时固定根目录、作品、章节时间上界与本机端口。模型不能通过工具参数切换目标作品、地址或文件路径。

## 三个工具

| 工具 | 行为 | 权限 |
| --- | --- | --- |
| `search_story` | 搜索有效历史正文和来源 | 只读；按作品、时间及版本过滤 |
| `get_story_state` | 读取已确认的故事状态 | 只读；遵循同一时间边界 |
| `propose_state_changes` | 为当前草稿提出结构化变化 | 只创建待审提案，不写入正式故事 |

提案检查作品归属、章节位置、草稿与作品版本、操作结构、原文引文及状态前置条件。无法回放的变化可留下明确的无效待审标记，由作者核对。服务没有正式提交、任意文件访问或终端执行工具。

HTTP 客户端只连接本机工作台，禁用环境代理和重定向，并设置超时。返回量受到限制；证据被省略或请求失败时会明确标记。这是应用权限控制，不代表模型对提示注入免疫。

## Tool Calling、MCP 与 Skill

| 机制 | 在项目中的作用 |
| --- | --- |
| Tool Calling | 内部规划器通过绑定作品的 `search_story` 工具查询资料；应用负责验证和执行 |
| MCP | 通过官方 Python SDK 的 stdio 服务向外部客户端提供上述三个工具 |
| Skill | 规划器读取 [玄幻创作规范](../src/novel_agent/craft/skills/xuanhuan/SKILL.md)，组织能力边界、成长代价、伏笔和人物知情规则 |

内部规划器的直接工具调用和外部 MCP 是不同路径。创作规范是应用资源，与本地开发助手的 `agents.md` 指令文件无关。

当前项目只有一份玄幻创作规范，并明确要求规划器读取它。注册成功、实际读到文件、遵守规则和提升小说质量需要分别验证；现有实现不代表已完成多技能自主路由或技能开关的质量对照。

## 验证与扩展

```bash
.venv/bin/pytest -q tests/test_story_mcp.py tests/test_mcp_live_eval.py
```

上述测试使用夹具，检查工具发现、参数、跨作品访问、未来或旧版本过滤、引文、状态回放与输出边界。真实模型任务另由 [run_mcp_live_eval.py](../scripts/run_mcp_live_eval.py) 执行，需要模型配置并产生费用。协议测试次数不能当作真实模型的工具选择成功率。

参考协议：[MCP 工具规范](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)。新增工具时，需要在参数校验、数据查询和业务提交层维持相同的作品、版本与时间边界。
