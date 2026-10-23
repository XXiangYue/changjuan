"""LangGraph 工作流实现"""

from pathlib import Path
from typing import Optional, Callable, List, Dict, Any
from enum import Enum
from dataclasses import dataclass, field

from .agent import AgentEvents


class WorkflowType(Enum):
    """工作流类型"""
    IDLE = "idle"
    INIT = "init"
    NEW_VOLUME = "new_volume"
    CHAPTER = "chapter"


class WorkflowState(Enum):
    """工作流状态"""
    IDLE = "idle"
    # 初始化工作流状态
    INIT_GET_DIR = "init_get_dir"
    INIT_GET_PROMPT = "init_get_prompt"
    INIT_GENERATING = "init_generating"
    INIT_CONFIRM = "init_confirm"
    # 新卷工作流状态
    NEW_VOLUME_GET_NUM = "new_volume_get_num"
    NEW_VOLUME_GENERATING = "new_volume_generating"
    NEW_VOLUME_CONFIRM = "new_volume_confirm"
    # 章节工作流状态
    CHAPTER_GET_VOLUME = "chapter_get_volume"
    CHAPTER_GET_NUM = "chapter_get_num"
    CHAPTER_GENERATING = "chapter_generating"
    CHAPTER_CONFIRM = "chapter_confirm"
    CHAPTER_UPDATE_DOC = "chapter_update_doc"
    CHAPTER_UPDATE_CONFIRM = "chapter_update_confirm"
    # 完成状态
    READY = "ready"
    FINISHED = "finished"


@dataclass
class InitDoc:
    """初始化文档"""
    name: str
    path: str
    prompt: str
    status: str = "待生成"
    content: str = ""


@dataclass
class WorkflowContext:
    """工作流上下文"""
    workflow_type: WorkflowType = WorkflowType.IDLE
    state: WorkflowState = WorkflowState.IDLE

    # 通用数据
    work_dir: Optional[Path] = None
    novel_dir: Optional[Path] = None
    workspace_dir: Optional[Path] = None
    user_prompt: str = ""

    # 初始化相关
    init_docs: List[InitDoc] = field(default_factory=list)
    current_init_doc_index: int = 0
    init_docs_written: List[str] = field(default_factory=list)

    # 新卷相关
    volume_num: int = 0
    volume_dir: Optional[Path] = None
    volume_docs: List[tuple] = field(default_factory=list)  # [(name, path), ...]
    current_volume_doc_index: int = 0
    has_previous_volumes: bool = False

    # 章节相关
    chapter_num: int = 0
    chapter_file: Optional[Path] = None
    memory_docs: List[tuple] = field(default_factory=list)  # [(name, path), ...]
    current_memory_doc_index: int = 0
    next_chapter_num: int = 1

    # 通用生成结果
    generated_content: str = ""
    user_input: str = ""  # 当前用户输入


class LangGraphWorkflow:
    """基于 LangGraph 的工作流"""

    def __init__(self, agent, output_callback: Callable):
        self.agent = agent
        self.output_callback = output_callback
        self.context = WorkflowContext()

    def output(self, text: str):
        """输出文本"""
        self.output_callback(text)

    # ==================== 核心路由 ====================

    def route_command(self, cmd: str) -> bool:
        """路由命令"""
        if cmd in ("quit", "exit", "q"):
            self.output("再见!")
            return False

        # IDLE 状态
        if self.context.state == WorkflowState.IDLE:
            if cmd == "/start":
                return self.start_init_workflow()
            elif cmd.startswith("/"):
                self.output(f"未知命令: {cmd}")
            else:
                self.output("输入 /start 开始初始化新小说")
            return True

        # READY 状态
        if self.context.state == WorkflowState.READY:
            if cmd == "/start":
                self.output("初始化已在进行中")
            elif cmd == "/new_volume":
                return self.start_new_volume_workflow()
            elif cmd == "/chapter":
                return self.start_chapter_workflow()
            elif cmd.startswith("/"):
                self.output("未知命令")
            else:
                return True
            return True

        # 其他状态 - 交给当前工作流处理
        return self.handle_current_workflow(cmd)

    def handle_current_workflow(self, cmd: str) -> bool:
        """处理当前工作流"""
        workflow_type = self.context.workflow_type

        if workflow_type == WorkflowType.INIT:
            return self.handle_init_workflow(cmd)
        elif workflow_type == WorkflowType.NEW_VOLUME:
            return self.handle_new_volume_workflow(cmd)
        elif workflow_type == WorkflowType.CHAPTER:
            return self.handle_chapter_workflow(cmd)

        return True

    # ==================== 初始化工作流 ====================

    def start_init_workflow(self) -> bool:
        """启动初始化工作流"""
        self.context.workflow_type = WorkflowType.INIT
        self.context.state = WorkflowState.INIT_GET_DIR
        self.output("""
============================================================
  初始化小说项目
============================================================

请输入工作目录路径（该目录下需包含小说本体文件）

示例: /Users/xxx/novel_project

输入 q 取消
""")
        return True

    def handle_init_workflow(self, cmd: str) -> bool:
        """处理初始化工作流"""
        state = self.context.state

        if state == WorkflowState.INIT_GET_DIR:
            return self._init_handle_dir(cmd)
        elif state == WorkflowState.INIT_GET_PROMPT:
            return self._init_handle_prompt(cmd)
        elif state == WorkflowState.INIT_GENERATING:
            return True  # 生成中不处理
        elif state == WorkflowState.INIT_CONFIRM:
            return self._init_handle_confirm(cmd)

        return True

    def _init_handle_dir(self, cmd: str) -> bool:
        """处理目录输入"""
        if cmd.lower() == "q":
            self._reset_to_ready()
            self.output("已取消。")
            return True

        work_dir = Path(cmd).resolve()
        if not work_dir.exists():
            self.output(f"目录不存在: {work_dir}\n请重新输入：")
            return True

        self.context.work_dir = work_dir
        self.context.workspace_dir = work_dir / "workspace"
        self.context.workspace_dir.mkdir(parents=True, exist_ok=True)
        self.context.novel_dir = work_dir / "小说本体"
        self.context.novel_dir.mkdir(parents=True, exist_ok=True)

        # 创建目录结构
        self._create_dirs()

        self.output(f"""
============================================================
  工作目录: {self.context.work_dir}
  小说本体: {self.context.novel_dir.name}
  记忆文档: {self.context.workspace_dir}
============================================================

下一步：输入小说总提示词
""")
        self.context.state = WorkflowState.INIT_GET_PROMPT
        return True

    def _init_handle_prompt(self, cmd: str) -> bool:
        """处理提示词输入"""
        if cmd.lower() == "q":
            self._reset_to_ready()
            self.output("已取消。")
            return True

        self.context.user_prompt = cmd

        # 初始化文档列表
        self.context.init_docs = [
            InitDoc(
                name="世界观",
                path="workspace/世界观.md",
                prompt=self._get_init_doc_prompt("世界观"),
            ),
            InitDoc(
                name="主角形象状态",
                path="workspace/主角形象状态.md",
                prompt=self._get_init_doc_prompt("主角形象状态"),
            ),
            InitDoc(
                name="本卷核心剧情",
                path="workspace/剧情/本卷核心剧情.md",
                prompt=self._get_init_doc_prompt("本卷核心剧情"),
            ),
            InitDoc(
                name="下一卷剧情",
                path="workspace/剧情/下一卷剧情.md",
                prompt=self._get_init_doc_prompt("下一卷剧情"),
            ),
            InitDoc(
                name="伏笔",
                path="workspace/剧情/伏笔.md",
                prompt=self._get_init_doc_prompt("伏笔"),
            ),
            InitDoc(
                name="有关角色",
                path="workspace/剧情/有关角色.md",
                prompt=self._get_init_doc_prompt("有关角色"),
            ),
            InitDoc(
                name="事件",
                path="workspace/剧情/事件.md",
                prompt=self._get_init_doc_prompt("事件"),
            ),
            InitDoc(
                name="当前章节设计规划",
                path="workspace/剧情/当前章节设计规划.md",
                prompt=self._get_init_doc_prompt("当前章节设计规划"),
            ),
        ]
        self.context.current_init_doc_index = 0
        self.context.init_docs_written = []

        self.output("""
============================================================
  开始初始化记忆文档
============================================================

按顺序填写以下文档：
""")
        for i, doc in enumerate(self.context.init_docs):
            self.output(f"  {i+1}. {doc.name}")

        self.output("\n" + "=" * 55 + "\n")

        # 开始生成第一个文档
        return self._init_generate_current()

    def _init_generate_current(self) -> bool:
        """生成当前初始化文档"""
        doc = self.context.init_docs[self.context.current_init_doc_index]
        full_prompt = self._build_init_doc_prompt(doc)

        self.output(f"""
************************************************************
文档 {self.context.current_init_doc_index + 1}/{len(self.context.init_docs)}: {doc.name}
************************************************************

[正在生成内容，请稍候...]

""")

        self.context.state = WorkflowState.INIT_GENERATING
        self.context.generated_content = ""

        # 流式生成
        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                self.output(f"\n[调用工具: {event.get('name', 'unknown')}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                self.output(f"\n[{event.get('name', 'unknown')} 完成]")

        self.context.generated_content = "".join(content_parts)
        doc.content = self.context.generated_content
        doc.status = "已生成"

        self._show_init_confirm_prompt()
        return True

    def _show_init_confirm_prompt(self):
        """显示初始化确认提示"""
        doc = self.context.init_docs[self.context.current_init_doc_index]
        self.output(f"""
-----------------------------------------------
[文档已保存] {doc.name}
-----------------------------------------------

下一步操作：
  - 输入任意内容：继续填写下一个文档
  - 输入 skip：跳过此文档
  - 输入 redo：重新生成此文档
  - 输入 q：退出初始化

""")
        self.context.state = WorkflowState.INIT_CONFIRM

    def _init_handle_confirm(self, cmd: str) -> bool:
        """处理初始化确认"""
        cmd_lower = cmd.lower()
        doc = self.context.init_docs[self.context.current_init_doc_index]

        if cmd_lower == "q":
            self._init_finish()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._init_generate_current()

        if cmd_lower == "skip":
            self.output("[已跳过]")
        elif doc.content:
            self._write_init_doc(doc)
            self.context.init_docs_written.append(doc.name)
            self.output(f"[已保存] {doc.name}")

        # 下一个文档
        self.context.current_init_doc_index += 1
        if self.context.current_init_doc_index >= len(self.context.init_docs):
            self._init_finish()
        else:
            return self._init_generate_current()

        return True

    def _init_finish(self):
        """完成初始化"""
        self._reset_to_ready()

        written = self.context.init_docs_written
        skipped = [doc.name for doc in self.context.init_docs if doc.status == "已跳过"]

        self.output("""
============================================================
  初始化完成
============================================================

已保存文档：""")
        for name in written:
            self.output(f"  ✓ {name}")

        if skipped:
            self.output("\n已跳过文档：")
            for name in skipped:
                self.output(f"  - {name}")

        self.output("""
-----------------------------------------------

记忆文档已写入 workspace/ 目录。

下一步：
  输入 /new_volume 开启新一卷创作

""")

    def _build_init_doc_prompt(self, doc: InitDoc) -> str:
        """构建初始化文档提示词"""
        template_content = self._read_doc(doc.path)

        context = f"""小说创作初始化 - 第 {self.context.current_init_doc_index + 1}/{len(self.context.init_docs)} 个文档

小说总提示词：
{self.context.user_prompt}

{'='*60}
当前要填写的文档模板：{doc.name}
{'='*60}
{template_content if template_content else '(模板文件不存在)'}

{'='*60}
以下是目前已生成的文档内容（按生成顺序）：
{'='*60}
"""

        # 收集已生成文档
        generated = []
        for i, d in enumerate(self.context.init_docs[:self.context.current_init_doc_index]):
            if d.content:
                generated.append(f"\n[文档{i+1}: {d.name}]\n{d.content}")

        if generated:
            context += "\n".join(generated)
        else:
            context += "\n（暂无已生成的文档）"

        context += f"""

{'='*60}
填写要求：
{'='*60}
{doc.prompt}
"""
        return context

    def _get_init_doc_prompt(self, doc_name: str) -> str:
        """获取初始化文档的填写提示"""
        prompts = {
            "世界观": """阅读模板文档的结构，然后按照模板中的各章节（核心设定、社会结构、世界规则、地理环境、势力划分）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "主角形象状态": """阅读模板文档的结构，然后按照模板中的各章节（身份信息、外貌特征、核心性格、性格弱点、行为习惯、能力设定、当前状态）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "本卷核心剧情": """阅读模板文档的结构，然后按照模板中的表格和章节（已完成剧情、进行中剧情、未完成剧情设计）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "下一卷剧情": """阅读模板文档的结构，然后按照模板中的章节（卷名/主题、核心冲突、主要目标）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "伏笔": """阅读模板文档的结构，然后按照模板中的表格（待回收伏笔、计划伏笔）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "有关角色": """阅读模板文档的结构，然后按照模板中的角色模板（角色001等）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "事件": """阅读模板文档的结构，然后按照模板中的表格（主线事件、并行事件、世界动态）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
            "当前章节设计规划": """阅读模板文档的结构，然后按照模板中的各章节（当前章节、本章目标、剧情承接、本章核心事件、角色安排、伏笔安排、氛围设计、字数目标）填写对应内容。保持模板的 markdown 结构，只填写内容，不改变模板格式。""",
        }
        return prompts.get(doc_name, "")

    # ==================== 新卷工作流 ====================

    def start_new_volume_workflow(self) -> bool:
        """启动新卷工作流"""
        if not self.context.novel_dir or not self.context.novel_dir.exists():
            self.output("""
============================================================
  错误：小说本体文件夹不存在
============================================================

请先使用 /start 初始化项目
""")
            return True

        volumes = [d for d in self.context.novel_dir.iterdir()
                   if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷")]

        self.output("""
============================================================
  开启新一卷创作
============================================================
""")
        if volumes:
            volumes.sort()
            self.output("已有卷：")
            for v in volumes:
                chapters = [c for c in v.iterdir() if c.suffix == ".md"]
                self.output(f"  {v.name} - {len(chapters)} 章")

        self.output("\n请输入新卷编号（数字，如：1、2、3...）\n\n输入 q 取消\n")

        self.context.workflow_type = WorkflowType.NEW_VOLUME
        self.context.state = WorkflowState.NEW_VOLUME_GET_NUM
        return True

    def handle_new_volume_workflow(self, cmd: str) -> bool:
        """处理新卷工作流"""
        state = self.context.state

        if state == WorkflowState.NEW_VOLUME_GET_NUM:
            return self._new_volume_handle_num(cmd)
        elif state == WorkflowState.NEW_VOLUME_GENERATING:
            return True
        elif state == WorkflowState.NEW_VOLUME_CONFIRM:
            return self._new_volume_handle_confirm(cmd)

        return True

    def _new_volume_handle_num(self, cmd: str) -> bool:
        """处理卷编号"""
        if cmd.lower() == "q":
            self._reset_to_ready()
            self.output("已取消。")
            return True

        try:
            volume_num = int(cmd.strip())
        except ValueError:
            self.output("请输入有效的数字，如：1、2、3...")
            return True

        self.context.volume_num = volume_num
        self.context.volume_dir = self.context.novel_dir / f"第{volume_num}卷"
        self.context.volume_dir.mkdir(parents=True, exist_ok=True)

        # 检查是否有前卷
        has_prev = False
        for d in self.context.novel_dir.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷") and d != self.context.volume_dir:
                chapters = [f for f in d.iterdir() if f.suffix == ".md"]
                if chapters:
                    has_prev = True
                    break

        self.context.has_previous_volumes = has_prev

        # 设置要更新的文档
        if has_prev:
            self.context.volume_docs = [
                ("前卷剧情概述", "workspace/剧情/前卷剧情概述.md"),
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]
        else:
            self.context.volume_docs = [
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]

        self.context.current_volume_doc_index = 0

        self.output(f"""
============================================================
  新卷创作 - 第 {volume_num} 卷
============================================================

卷文件夹: {self.context.volume_dir.name}

将依次更新以下文档：""")
        for i, (name, _) in enumerate(self.context.volume_docs):
            self.output(f"  {i+1}. {name}")

        self.output("\n" + "=" * 55 + "\n")

        return self._new_volume_generate_current()

    def _new_volume_generate_current(self) -> bool:
        """生成当前新卷文档"""
        doc_name, doc_path = self.context.volume_docs[self.context.current_volume_doc_index]
        full_prompt = self._build_new_volume_doc_prompt(doc_name, doc_path)

        self.output(f"""
************************************************************
文档 {self.context.current_volume_doc_index + 1}/{len(self.context.volume_docs)}: {doc_name}
************************************************************

[正在生成内容，请稍候...]

""")

        self.context.state = WorkflowState.NEW_VOLUME_GENERATING
        self.context.generated_content = ""

        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                self.output(f"\n[调用工具: {event.get('name', 'unknown')}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                self.output(f"\n[{event.get('name', 'unknown')} 完成]")

        self.context.generated_content = "".join(content_parts)

        self._show_new_volume_confirm_prompt()
        return True

    def _show_new_volume_confirm_prompt(self):
        """显示新卷确认提示"""
        doc_name, _ = self.context.volume_docs[self.context.current_volume_doc_index]
        self.output(f"""
============================================================
[文档已生成] {doc_name}
============================================================

下一步操作：
  - 输入任意内容：确认并保存此文档，继续下一个
  - 输入 skip：跳过此文档，不保存
  - 输入 redo：重新生成此文档
  - 输入 q：退出新卷创作

""")
        self.context.state = WorkflowState.NEW_VOLUME_CONFIRM

    def _new_volume_handle_confirm(self, cmd: str) -> bool:
        """处理新卷确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.context.volume_docs[self.context.current_volume_doc_index]

        if cmd_lower == "q":
            self._reset_to_ready()
            self._finish_new_volume()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._new_volume_generate_current()

        if cmd_lower == "skip":
            self.output("[已跳过]")
        elif self.context.generated_content:
            self._write_doc(doc_path, self.context.generated_content)
            self.output(f"[已保存] {doc_name}")

        # 下一个文档
        self.context.current_volume_doc_index += 1
        if self.context.current_volume_doc_index >= len(self.context.volume_docs):
            self._reset_to_ready()
            self._finish_new_volume()
        else:
            return self._new_volume_generate_current()

        return True

    def _build_new_volume_doc_prompt(self, doc_name: str, doc_path: str) -> str:
        """构建新卷文档提示词"""
        all_docs = self._read_all_memory_docs()

        context = f"""小说创作 - 新卷创作工作流
当前操作: 更新 "{doc_name}"

{'='*60}
所有记忆文档内容：
{'='*60}

[世界观.md]
{all_docs.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md]
{all_docs.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md]
{all_docs.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md]
{all_docs.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs.get('workspace/剧情/有关角色.md', '(无)')}

[事件.md]
{all_docs.get('workspace/剧情/事件.md', '(无)')}

[当前章节设计规划.md]
{all_docs.get('workspace/剧情/当前章节设计规划.md', '(无)')}

{'='*60}
"""

        if doc_name == "前卷剧情概述":
            return context + """【前卷剧情概述更新规则】
操作：将"本卷核心剧情.md"的内容转化为卷剧情概述。
格式：
# 前卷剧情概述

## 第X卷 剧情概述

### 本卷核心事件
1. [事件1]
2. [事件2]
...

### 关键转折点
1. [转折1]
2. [转折2]
...

### 主要角色发展
- [角色名]：[发展描述]
...

请直接输出完整内容。
"""
        elif doc_name == "本卷核心剧情":
            return context + """【本卷核心剧情更新规则】
操作：将"下一卷剧情.md"的设计转化为本卷的详细剧情。
格式：
# 本卷核心剧情

## 第X卷

### 已完成剧情
| 章节 | 核心事件 | 结果 |
|-----|---------|-----|
|     |         |     |

### 进行中剧情
| 章节 | 当前进展 | 悬念 |
|-----|---------|-----|
|     |         |     |

### 未完成剧情设计
| 设计点 | 剧情走向 | 可调整空间 |
|-------|---------|----------|
|       |         |           |

请直接输出完整内容。
"""
        elif doc_name == "下一卷剧情":
            return context + """【下一卷剧情设计规则】
操作：综合所有记忆文档，设计下一卷剧情。
格式：
# 下一卷剧情

## 第X卷 预先设计

### 卷名/主题
[卷名] - [主题描述]

### 核心冲突
[冲突描述]

### 主要目标
1. [目标1]
2. [目标2]
...

### 剧情走向设计
[详细剧情走向]

请直接输出完整内容。
"""

        return context

    def _finish_new_volume(self):
        """完成新卷"""
        has_prev = self.context.has_previous_volumes

        docs_msg = "已更新文档："
        if has_prev:
            docs_msg += "\n  1. 前卷剧情概述\n  2. 本卷核心剧情\n  3. 下一卷剧情"
        else:
            docs_msg += "\n  1. 本卷核心剧情\n  2. 下一卷剧情"

        self.output(f"""
============================================================
  新卷创作完成 - 第 {self.context.volume_num} 卷
============================================================

{docs_msg}

卷文件夹已创建：
  {self.context.volume_dir.name}/

下一步：
  输入 /chapter 开始创作章节
""")

    # ==================== 章节工作流 ====================

    def start_chapter_workflow(self) -> bool:
        """启动章节工作流"""
        if not self.context.novel_dir or not self.context.novel_dir.exists():
            self.output("""
============================================================
  错误：小说本体文件夹不存在
============================================================

请先使用 /new_volume 创建新卷
""")
            return True

        volumes = [d for d in self.context.novel_dir.iterdir()
                   if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷")]

        if not volumes:
            self.output("""
============================================================
  错误：暂无卷文件夹
============================================================

请先使用 /new_volume 创建新卷
""")
            return True

        volumes.sort()
        self.output("""
============================================================
  开始章节创作
============================================================

已有卷：
""")
        for v in volumes:
            chapters = [c for c in v.iterdir() if c.suffix == ".md"]
            self.output(f"  {v.name} - {len(chapters)} 章")

        self.output("\n请输入要创作章节的卷编号（如：1）：")

        self.context.workflow_type = WorkflowType.CHAPTER
        self.context.state = WorkflowState.CHAPTER_GET_VOLUME
        return True

    def handle_chapter_workflow(self, cmd: str) -> bool:
        """处理章节工作流"""
        state = self.context.state

        if state == WorkflowState.CHAPTER_GET_VOLUME:
            return self._chapter_handle_volume(cmd)
        elif state == WorkflowState.CHAPTER_GET_NUM:
            return self._chapter_handle_num(cmd)
        elif state == WorkflowState.CHAPTER_GENERATING:
            return True
        elif state == WorkflowState.CHAPTER_CONFIRM:
            return self._chapter_handle_confirm(cmd)
        elif state == WorkflowState.CHAPTER_UPDATE_DOC:
            return True
        elif state == WorkflowState.CHAPTER_UPDATE_CONFIRM:
            return self._chapter_handle_update_confirm(cmd)

        return True

    def _chapter_handle_volume(self, cmd: str) -> bool:
        """处理卷编号"""
        if cmd.lower() == "q":
            self._reset_to_ready()
            self.output("已取消。")
            return True

        try:
            volume_num = int(cmd.strip())
        except ValueError:
            self.output("请输入有效的数字，如：1、2、3...")
            return True

        volume_dir = self.context.novel_dir / f"第{volume_num}卷"
        if not volume_dir.exists():
            self.output(f"卷文件夹不存在：第{volume_num}卷\n请先使用 /new_volume 创建")
            self._reset_to_ready()
            return True

        self.context.volume_num = volume_num
        self.context.volume_dir = volume_dir

        # 计算下一章编号
        existing_chapters = [f for f in volume_dir.iterdir() if f.suffix == ".md"]
        self.context.next_chapter_num = len(existing_chapters) + 1

        self.output(f"""
============================================================
  第 {volume_num} 卷
============================================================

已有章节数：{len(existing_chapters)}
下一个章节编号：{self.context.next_chapter_num}

请输入章节编号（直接回车使用 {self.context.next_chapter_num}，或输入其他数字）：
""")
        self.context.state = WorkflowState.CHAPTER_GET_NUM
        return True

    def _chapter_handle_num(self, cmd: str) -> bool:
        """处理章节编号"""
        if cmd.lower() == "q":
            self._reset_to_ready()
            self.output("已取消。")
            return True

        if cmd.strip() == "":
            chapter_num = self.context.next_chapter_num
        else:
            try:
                chapter_num = int(cmd.strip())
            except ValueError:
                self.output("请输入有效的数字")
                return True

        self.context.chapter_num = chapter_num

        # 创建章节文件
        chapter_file = self.context.volume_dir / f"第{chapter_num}章.md"
        if not chapter_file.exists():
            chapter_file.write_text("", encoding="utf-8")
        self.context.chapter_file = chapter_file

        self.output(f"""
============================================================
  准备创作：第 {self.context.volume_num} 卷 第 {chapter_num} 章
============================================================

章节文件：{chapter_file.name}

正在读取记忆文档...
""")

        # 开始生成章节内容
        return self._chapter_generate_content()

    def _chapter_generate_content(self) -> bool:
        """生成章节内容"""
        all_docs = self._read_all_memory_docs()
        chapter_plan = all_docs.get("workspace/剧情/当前章节设计规划.md", "")

        # 读取上一章
        prev_content = ""
        if self.context.chapter_num > 1:
            prev_file = self.context.volume_dir / f"第{self.context.chapter_num - 1}章.md"
            if prev_file.exists():
                prev_content = prev_file.read_text(encoding="utf-8")

        full_prompt = f"""小说创作 - 章节创作
当前任务：创作第 {self.context.volume_num} 卷 第 {self.context.chapter_num} 章

{'='*60}
当前章节规划：
{'='*60}
{chapter_plan if chapter_plan else '(暂无章节规划)'}

{'='*60}
上一章内容（用于衔接）：
{'='*60}
{prev_content if prev_content else '(无上一章内容)'}

{'='*60}
所有记忆文档内容：
{'='*60}

[世界观.md]
{all_docs.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md]
{all_docs.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md]
{all_docs.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md]
{all_docs.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs.get('workspace/剧情/有关角色.md', '(无)')}

[目前剧情.md]
{all_docs.get('workspace/剧情/目前剧情.md', '(无)')}

[事件.md]
{all_docs.get('workspace/剧情/事件.md', '(无)')}

{'='*60}
创作要求：
{'='*60}
1. 根据章节规划和记忆文档，创作本章内容
2. 注意与上一章的衔接
3. 保持世界观和角色设定的一致性
4. 埋下必要的伏笔
5. 直接输出章节内容，不需要其他说明

请开始创作第 {self.context.volume_num} 卷 第 {self.context.chapter_num} 章：
"""

        self.output(f"""
************************************************************
  第 {self.context.volume_num} 卷 第 {self.context.chapter_num} 章
************************************************************

[正在生成章节内容，请稍候...]

""")

        self.context.state = WorkflowState.CHAPTER_GENERATING
        self.context.generated_content = ""

        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                self.output(f"\n[调用工具: {event.get('name', 'unknown')}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                self.output(f"\n[{event.get('name', 'unknown')} 完成]")

        self.context.generated_content = "".join(content_parts)

        self._show_chapter_confirm_prompt()
        return True

    def _show_chapter_confirm_prompt(self):
        """显示章节确认提示"""
        self.output(f"""
============================================================
[章节内容已生成] 第 {self.context.volume_num} 卷 第 {self.context.chapter_num} 章
============================================================

下一步操作：
  - 输入任意内容：确认并保存章节，继续更新记忆文档
  - 输入 skip：跳过保存，直接更新记忆文档
  - 输入 redo：重新生成章节
  - 输入 q：退出章节创作

""")
        self.context.state = WorkflowState.CHAPTER_CONFIRM

    def _chapter_handle_confirm(self, cmd: str) -> bool:
        """处理章节确认"""
        cmd_lower = cmd.lower()

        if cmd_lower == "q":
            self._reset_to_ready()
            self.output("已取消章节创作。")
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._chapter_generate_content()

        if cmd_lower == "skip":
            self.output("[已跳过保存]")
        elif self.context.generated_content:
            self.context.chapter_file.write_text(self.context.generated_content.strip(), encoding="utf-8")
            self.output(f"[已保存章节] {self.context.chapter_file.name}")

        # 开始更新记忆文档
        return self._start_update_memory_docs()

    def _start_update_memory_docs(self) -> bool:
        """开始更新记忆文档"""
        self.context.memory_docs = [
            ("世界观", "workspace/世界观.md"),
            ("主角形象状态", "workspace/主角形象状态.md"),
            ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
            ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ("伏笔", "workspace/剧情/伏笔.md"),
            ("有关角色", "workspace/剧情/有关角色.md"),
            ("目前剧情", "workspace/剧情/目前剧情.md"),
            ("事件", "workspace/剧情/事件.md"),
            ("当前章节设计规划", "workspace/剧情/当前章节设计规划.md"),
        ]
        self.context.current_memory_doc_index = 0

        self.output("""
============================================================
  更新记忆文档
============================================================

按顺序更新以下文档：
  1. 世界观（模糊更新）
  2. 主角形象状态（状态动态更新）
  3. 本卷核心剧情（更新已完成/进行中/未完成）
  4. 下一卷剧情（可调整优化）
  5. 伏笔（按需更新）
  6. 有关角色（添加/删除/更新）
  7. 目前剧情（近10章）
  8. 事件（主线/并行）
  9. 当前章节设计规划（更新为下一章）

""")

        return self._chapter_generate_memory_doc()

    def _chapter_generate_memory_doc(self) -> bool:
        """生成记忆文档更新"""
        doc_name, doc_path = self.context.memory_docs[self.context.current_memory_doc_index]
        full_prompt = self._build_memory_doc_prompt(doc_name, doc_path)

        self.output(f"""
************************************************************
  更新文档 {self.context.current_memory_doc_index + 1}/{len(self.context.memory_docs)}: {doc_name}
************************************************************

[正在生成更新内容，请稍候...]

""")

        self.context.state = WorkflowState.CHAPTER_UPDATE_DOC
        self.context.generated_content = ""

        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                self.output(f"\n[调用工具: {event.get('name', 'unknown')}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                self.output(f"\n[{event.get('name', 'unknown')} 完成]")

        self.context.generated_content = "".join(content_parts)

        self._show_memory_doc_confirm_prompt()
        return True

    def _show_memory_doc_confirm_prompt(self):
        """显示记忆文档确认提示"""
        doc_name, _ = self.context.memory_docs[self.context.current_memory_doc_index]
        self.output(f"""
============================================================
[文档已生成] {doc_name}
============================================================

下一步操作：
  - 输入任意内容：确认并保存此文档，继续下一个
  - 输入 skip：跳过此文档，不保存
  - 输入 redo：重新生成此文档
  - 输入 q：退出更新

""")
        self.context.state = WorkflowState.CHAPTER_UPDATE_CONFIRM

    def _chapter_handle_update_confirm(self, cmd: str) -> bool:
        """处理记忆文档确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.context.memory_docs[self.context.current_memory_doc_index]

        if cmd_lower == "q":
            self._reset_to_ready()
            self._finish_chapter()
            return True

        if cmd_lower == "redo":
            self.output("[重新生成中...]")
            return self._chapter_generate_memory_doc()

        if cmd_lower == "skip":
            self.output("[已跳过]")
        elif self.context.generated_content:
            self._write_doc(doc_path, self.context.generated_content)
            self.output(f"[已保存] {doc_name}")

        # 下一个文档
        self.context.current_memory_doc_index += 1
        if self.context.current_memory_doc_index >= len(self.context.memory_docs):
            self._reset_to_ready()
            self._finish_chapter()
        else:
            return self._chapter_generate_memory_doc()

        return True

    def _build_memory_doc_prompt(self, doc_name: str, doc_path: str) -> str:
        """构建记忆文档更新提示词"""
        current_doc = self._read_doc(doc_path)
        chapter_content = self.context.chapter_file.read_text(encoding="utf-8") if self.context.chapter_file.exists() else ""
        all_docs = self._read_all_memory_docs()

        context = f"""小说创作 - 记忆文档更新
当前任务：更新 "{doc_name}"

{'='*60}
当前文档内容：
{'='*60}
{current_doc if current_doc else '(文档为空)'}

{'='*60}
刚创作的章节内容：
{'='*60}
{chapter_content if chapter_content else '(无)'}

{'='*60}
所有记忆文档：
{'='*60}
[世界观.md]
{all_docs.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md]
{all_docs.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md]
{all_docs.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md]
{all_docs.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs.get('workspace/剧情/有关角色.md', '(无)')}

[目前剧情.md]
{all_docs.get('workspace/剧情/目前剧情.md', '(无)')}

[事件.md]
{all_docs.get('workspace/剧情/事件.md', '(无)')}

[当前章节设计规划.md]
{all_docs.get('workspace/剧情/当前章节设计规划.md', '(无)')}

{'='*60}
"""

        prompts = {
            "世界观": """【世界观更新规则】
操作：从本章内容中提取新引入的世界设定，按格式追加到原文档末尾。
- 新地区：在"地理环境"章节末尾添加
- 新规则/法则：在"世界规则"章节末尾添加
- 新势力：在"势力划分"章节末尾添加
- 无新增设定时：输出原文档内容不变

请输出完整的更新后文档内容：
""",
            "主角形象状态": """【主角形象状态更新规则】
操作：检查本章内容中主角的状态变化，按以下规则更新：
- 心理状态变化：在"心理状态"下用新内容替换原内容
- 处境状态变化：在"处境状态"下用新内容替换原内容
- 目标/动机变化：在"目标/动机"下用新内容替换原内容
- 形象/性格：不做修改
- 无状态变化时：输出原文档内容不变

请输出完整的更新后文档内容：
""",
            "本卷核心剧情": """【本卷核心剧情更新规则】
操作：根据本章内容，按以下规则更新三部分：
1. 已完成剧情：如果本章有核心事件完成，在表格末尾添加一行
2. 进行中剧情：如果有进行中的剧情，更新"进行中剧情"表格的当前进展列
3. 未完成剧情设计：如需调整，修改"未完成剧情设计"表格内容

请输出完整的更新后文档内容：
""",
            "下一卷剧情": """【下一卷剧情更新规则】
操作：检查本章剧情发展，如果影响了下卷设计，进行相应调整：
- 卷名/主题变化：替换对应内容
- 核心冲突变化：替换对应内容
- 主要目标变化：替换对应内容
- 剧情走向变化：替换对应内容
- 无需调整时：输出原文档内容不变

请输出完整的更新后文档内容：
""",
            "伏笔": """【伏笔更新规则】
操作：根据本章内容，按以下规则更新：
1. 回收伏笔：将"待回收伏笔"表格中对应行的状态改为"已回收"
2. 新埋伏笔：在"计划伏笔"表格末尾添加新行

请输出完整的更新后文档内容：
""",
            "有关角色": """【有关角色更新规则】
操作：根据本章内容，按以下规则更新：
1. 新角色出场：在角色列表末尾添加新角色模板
2. 角色退场：将该角色的"当前状态"改为"已退场"
3. 角色状态变化：更新对应角色的"当前状态"内容

请输出完整的更新后文档内容：
""",
            "目前剧情": """【目前剧情更新规则】
操作：管理近10章的剧情记录。
1. 在文档开头添加本章内容
2. 如果文档中超过10章，删除最后的章节
3. 保持最新章节在前面

请输出完整的更新后文档内容：
""",
            "事件": """【事件更新规则】
操作：根据本章内容，按以下规则更新：
1. 主线事件完成：将对应行的"当前状态"改为"已完成"
2. 新主线事件：在"主线事件"表格末尾添加新行
3. 新并行事件：在"并行事件"表格末尾添加新行

请输出完整的更新后文档内容：
""",
            "当前章节设计规划": """【当前章节设计规划更新规则】
操作：分两步执行。
第一步：将本章标记为完成
第二步：设计下一章，替换各项目内容（章节编号、本章目标、剧情承接、本章核心事件、角色安排、伏笔安排、氛围设计、字数目标）

请输出完整的更新后文档内容：
""",
        }

        return context + prompts.get(doc_name, "")

    def _finish_chapter(self):
        """完成章节创作"""
        self.output(f"""
============================================================
  章节创作完成
============================================================

已完成：
  - 章节创作：第 {self.context.volume_num} 卷 第 {self.context.chapter_num} 章
  - 记忆文档更新（9个文档）

章节文件：{self.context.chapter_file.name}

下一步：
  输入 /chapter 继续创作下一章
  输入 /new_volume 创建新卷
""")

    # ==================== 辅助方法 ====================

    def _reset_to_ready(self):
        """重置为就绪状态"""
        self.context.workflow_type = WorkflowType.IDLE
        self.context.state = WorkflowState.READY

    def _read_doc(self, path: str) -> str:
        """读取文档"""
        if not self.context.work_dir:
            return ""
        fpath = self.context.work_dir / path
        if fpath.exists():
            return fpath.read_text(encoding="utf-8")
        return ""

    def _write_doc(self, path: str, content: str):
        """写入文档"""
        if not self.context.work_dir:
            return
        fpath = self.context.work_dir / path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(content.strip(), encoding="utf-8")

    def _write_init_doc(self, doc: InitDoc):
        """写入初始化文档"""
        self._write_doc(doc.path, doc.content)

    def _read_all_memory_docs(self) -> Dict[str, str]:
        """读取所有记忆文档"""
        doc_files = [
            "workspace/世界观.md",
            "workspace/主角形象状态.md",
            "workspace/剧情/本卷核心剧情.md",
            "workspace/剧情/下一卷剧情.md",
            "workspace/剧情/前卷剧情概述.md",
            "workspace/剧情/伏笔.md",
            "workspace/剧情/有关角色.md",
            "workspace/剧情/目前剧情.md",
            "workspace/剧情/事件.md",
            "workspace/剧情/当前章节设计规划.md",
        ]
        return {f: self._read_doc(f) for f in doc_files}

    def _create_dirs(self):
        """创建目录结构"""
        dirs = ["workspace/剧情"]
        for d in dirs:
            (self.context.work_dir / d).mkdir(parents=True, exist_ok=True)

        templates = {
            "workspace/世界观.md": """# 世界观

```
================================================================================
文档类型: 世界观设定
内容说明: 作为小说的核心逻辑设定
更新逻辑: 模糊更新原则，随着剧情发展可引入新设定
更新要求: 章节创作完成后由模型决定是否引入新设定
================================================================================
```

## 核心设定



## 社会结构



## 世界规则



## 地理环境



## 势力划分



## 扩展设定

""",
            "workspace/主角形象状态.md": """# 主角形象状态

```
================================================================================
文档类型: 主角设定
内容说明: 记录主角设定、形象、行为指导
更新逻辑: 形象性格不更新，状态随剧情动态更新
更新要求: 章节创作后根据剧情进展动态更新
================================================================================
```

## 基础设定

### 身份信息
- 姓名:
- 年龄:
- 身份/职业:

### 外貌特征



## 性格设定

### 核心性格



### 性格弱点



### 行为习惯



## 能力设定

### 核心能力



### 能力限制



## 当前状态

### 心理状态



### 处境状态



### 目标/动机

""",
            "workspace/剧情/前卷剧情概述.md": """# 前卷剧情概述

```
================================================================================
文档类型: 剧情概述
内容说明: 记录本卷之前的所有卷剧情概括
更新逻辑: 只包含本卷之前的卷剧情概括
更新要求: 开启新一卷创作时更新
================================================================================
```

""",
            "workspace/剧情/本卷核心剧情.md": """# 本卷核心剧情

```
================================================================================
文档类型: 剧情设计
内容说明: 本卷剧情走向设计，分为已完成、进行中、未完成三部分
更新逻辑: 每次创作后根据章节内容更新
更新要求: 章节完成后分别对三部分进行更新
================================================================================
```

## 第X卷

### 已完成剧情

| 章节 | 核心事件 | 结果 |
|-----|---------|-----|
|     |         |     |


### 进行中剧情

| 章节 | 当前进展 | 悬念 |
|-----|---------|-----|
|     |         |     |


### 未完成剧情设计

| 设计点 | 剧情走向 | 可调整空间 |
|-------|---------|----------|
|       |         |           |

""",
            "workspace/剧情/下一卷剧情.md": """# 下一卷剧情

```
================================================================================
文档类型: 下一卷剧情设计
内容说明: 对下一卷剧情的预先设计
更新逻辑: 可根据剧情进展调整
更新要求: 如有更好的设计方案可进行合理修改
================================================================================
```

## 第X卷 预先设计

### 卷名/主题



### 核心冲突



### 主要目标

""",
            "workspace/剧情/伏笔.md": """# 伏笔

```
================================================================================
文档类型: 伏笔记录
内容说明: 记录埋下的待回收伏笔、计划伏笔
更新逻辑: 根据章节创作内容按需更新
更新要求: 按需更新
================================================================================
```

## 待回收伏笔

| 伏笔内容 | 埋下章节 | 预期回收章节 | 回收方式 | 状态 |
|---------|---------|------------|---------|-----|
|         |         |              |         |      |


## 计划伏笔

| 计划埋入位置 | 伏笔内容 | 预期作用 |
|-------------|---------|---------|
|             |         |         |

""",
            "workspace/剧情/有关角色.md": """# 有关角色

```
================================================================================
文档类型: 角色记录
内容说明: 记录与主角剧情相关的角色设定与状态
更新逻辑: 根据章节创作内容按需更新
更新要求: 章节创作完成后按需更新
================================================================================
```

## 重要角色

### 角色001

- 身份:
- 与主角关系:
- 核心性格:
- 在剧情中的作用:
- 当前状态:

""",
            "workspace/剧情/目前剧情.md": """# 目前剧情

```
================================================================================
文档类型: 近期剧情记录
内容说明: 记录近10章创作的章节完整内容
更新逻辑: 每完成一章创作后更新，补充最新章节，移除最早的章节
更新要求: 章节完成后更新
================================================================================
```

## 近10章剧情

""",
            "workspace/剧情/事件.md": """# 事件

```
================================================================================
文档类型: 事件记录
内容说明: 记录当前正在发生的事件
更新逻辑: 章节创作完成后根据剧情按需更新
更新要求: 按需更新
================================================================================
```

## 主线事件

| 事件 | 开始章节 | 当前状态 | 影响 |
|-----|---------|---------|-----|
|     |         |         |      |


## 并行事件

| 事件 | 描述 | 对主线影响 |
|-----|------|----------|
|     |      |           |

""",
            "workspace/剧情/当前章节设计规划.md": """# 当前章节设计规划

```
================================================================================
文档类型: 章节规划
内容说明: 对当前要创作章节的设计与规划
更新逻辑: 完成章节创作后更新为下一章规划
更新要求: 完成章节创作并确定后更新
================================================================================
```

## 当前章节

- 章节编号:
- 章节名:


## 本章目标



## 剧情承接

- 承接上文:
- 本章发展:
- 引出下文:


## 本章核心事件

| 事件 | 设计内容 | 预期效果 |
|-----|---------|---------|
|     |         |         |


## 角色安排

| 角色 | 本章作用 |
|-----|---------|
|     |          |


## 伏笔安排



## 氛围设计



## 字数目标

""",
        }

        for path, content in templates.items():
            fpath = self.context.work_dir / path
            if not fpath.exists():
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")

    def is_ready(self) -> bool:
        """是否处于就绪状态"""
        return self.context.state == WorkflowState.READY

    def is_idle(self) -> bool:
        """是否处于空闲状态"""
        return self.context.state == WorkflowState.IDLE
