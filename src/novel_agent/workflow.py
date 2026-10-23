"""工作流模块 - 固定工作流程"""

from pathlib import Path
from typing import Optional, Callable, List
from enum import Enum

from .agent import AgentEvents


class WorkflowState(Enum):
    """工作流状态"""
    IDLE = "idle"
    INIT_GET_DIR = "init_get_dir"
    INIT_GET_PROMPT = "init_get_prompt"
    INIT_GENERATING = "init_generating"
    INIT_CONFIRM = "init_confirm"
    READY = "ready"
    NEW_VOLUME_GET_NUM = "new_volume_get_num"
    NEW_VOLUME_GENERATING = "new_volume_generating"
    NEW_VOLUME_CONFIRM = "new_volume_confirm"
    CHAPTER_GET_VOLUME = "chapter_get_volume"
    CHAPTER_GET_NUM = "chapter_get_num"
    CHAPTER_GENERATING = "chapter_generating"
    CHAPTER_CONFIRM = "chapter_confirm"
    CHAPTER_UPDATE_DOC = "chapter_update_doc"
    CHAPTER_UPDATE_CONFIRM = "chapter_update_confirm"


class InitDoc:
    """初始化文档"""
    def __init__(self, name: str, path: str, prompt: str, status: str = "待生成"):
        self.name = name          # 文档名称
        self.path = path          # 文档路径（相对于 workspace）
        self.prompt = prompt      # 填写指导提示词
        self.status = status      # 状态
        self.content = ""         # 生成的内容


class WorkflowManager:
    """工作流管理器"""

    def __init__(self, agent, output_callback: Callable):
        self.agent = agent
        self.output_callback = output_callback
        self.state = WorkflowState.IDLE
        self.work_dir: Optional[Path] = None
        self.novel_dir: Optional[Path] = None
        self.workspace_dir: Optional[Path] = None
        self.user_prompt: str = ""
        self.docs: List[InitDoc] = []
        self.current_doc_index = 0
        self.docs_written = []
        self.generated_content = ""  # 当前生成的内容

    def output(self, text: str):
        """输出文本"""
        self.output_callback(text)

    def handle_command(self, cmd: str) -> bool:
        """处理命令"""
        if cmd in ("quit", "exit", "q"):
            self.output("再见!")
            return False

        if self.state == WorkflowState.IDLE:
            if cmd == "/start":
                self._start_init()
            elif cmd.startswith("/"):
                self.output(f"未知命令: {cmd}")
            else:
                self.output("输入 /start 开始初始化新小说")
            return True

        elif self.state == WorkflowState.INIT_GET_DIR:
            self._handle_init_dir(cmd)

        elif self.state == WorkflowState.INIT_GET_PROMPT:
            self._handle_init_prompt(cmd)

        elif self.state == WorkflowState.INIT_GENERATING:
            # 生成中不处理命令
            return True

        elif self.state == WorkflowState.INIT_CONFIRM:
            self._handle_confirm(cmd)

        elif self.state == WorkflowState.READY:
            if cmd == "/start":
                self.output("初始化已在进行中，使用 /status 查看进度")
            elif cmd == "/new_volume":
                self._start_new_volume()
            elif cmd == "/chapter":
                self._start_chapter()
            elif cmd.startswith("/"):
                self.output("未知命令")
            else:
                return True
            return True

        elif self.state == WorkflowState.NEW_VOLUME_GET_NUM:
            self._handle_new_volume_num(cmd)

        elif self.state == WorkflowState.NEW_VOLUME_GENERATING:
            # 生成中不处理命令
            return True

        elif self.state == WorkflowState.NEW_VOLUME_CONFIRM:
            self._handle_new_volume_confirm(cmd)

        elif self.state == WorkflowState.CHAPTER_GET_VOLUME:
            self._handle_chapter_volume(cmd)

        elif self.state == WorkflowState.CHAPTER_GET_NUM:
            self._handle_chapter_num(cmd)

        elif self.state == WorkflowState.CHAPTER_GENERATING:
            # 生成中不处理命令
            return True

        elif self.state == WorkflowState.CHAPTER_CONFIRM:
            self._handle_chapter_confirm(cmd)

        elif self.state == WorkflowState.CHAPTER_UPDATE_DOC:
            self._handle_chapter_update_doc(cmd)

        elif self.state == WorkflowState.CHAPTER_UPDATE_CONFIRM:
            self._handle_chapter_update_confirm(cmd)

        return True

    def _start_init(self):
        """开始初始化"""
        self.state = WorkflowState.INIT_GET_DIR
        self.output("""
============================================================
  初始化小说项目
============================================================

请输入工作目录路径（该目录下需包含小说本体文件）

示例: /Users/xxx/novel_project

输入 q 取消
""")

    def _handle_init_dir(self, cmd: str):
        """处理目录输入"""
        if cmd.lower() == "q":
            self.output("已取消。")
            self.state = WorkflowState.IDLE
            return

        work_dir = Path(cmd).resolve()

        if not work_dir.exists():
            self.output(f"目录不存在: {work_dir}\n请重新输入：")
            return

        self.work_dir = work_dir

        # 创建 workspace 目录
        self.workspace_dir = work_dir / "workspace"
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

        # 小说本体文件夹
        self.novel_dir = work_dir / "小说本体"
        self.novel_dir.mkdir(parents=True, exist_ok=True)

        # 创建剧情目录
        self._create_dirs()

        self.output(f"""
============================================================
  工作目录: {self.work_dir}
  小说本体: {self.novel_dir.name}
  记忆文档: {self.workspace_dir}
============================================================

小说本体文件夹结构：
  小说本体/
    └── 第X卷/
          └── 第X章.md

下一步：输入小说总提示词
""")
        self.state = WorkflowState.INIT_GET_PROMPT

    def _handle_init_prompt(self, cmd: str):
        """处理提示词输入"""
        if cmd.lower() == "q":
            self.output("已取消。")
            self.state = WorkflowState.IDLE
            return

        self.user_prompt = cmd

        # 初始化文档列表
        self.docs = [
            InitDoc(
                name="世界观",
                path="workspace/世界观.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

小说提示词：
{user_prompt}

请先阅读模板文档 `workspace/世界观.md` 的结构，然后按照模板中的各章节（核心设定、社会结构、世界规则、地理环境、势力划分）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="主角形象状态",
                path="workspace/主角形象状态.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

小说提示词：
{user_prompt}

请先阅读模板文档 `workspace/主角形象状态.md` 的结构，然后按照模板中的各章节（身份信息、外貌特征、核心性格、性格弱点、行为习惯、能力设定、当前状态）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="本卷核心剧情",
                path="workspace/剧情/本卷核心剧情.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

小说提示词：
{user_prompt}

请先阅读模板文档 `workspace/剧情/本卷核心剧情.md` 的结构，然后按照模板中的表格和章节（已完成剧情、进行中剧情、未完成剧情设计）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="下一卷剧情",
                path="workspace/剧情/下一卷剧情.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

请先阅读模板文档 `workspace/剧情/下一卷剧情.md` 的结构，然后按照模板中的章节（卷名/主题、核心冲突、主要目标）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="伏笔",
                path="workspace/剧情/伏笔.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

请先阅读模板文档 `workspace/剧情/伏笔.md` 的结构，然后按照模板中的表格（待回收伏笔、计划伏笔）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="有关角色",
                path="workspace/剧情/有关角色.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

请先阅读模板文档 `workspace/剧情/有关角色.md` 的结构，然后按照模板中的角色模板（角色001等）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="事件",
                path="workspace/剧情/事件.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

请先阅读模板文档 `workspace/剧情/事件.md` 的结构，然后按照模板中的表格（主线事件、并行事件、世界动态）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
            InitDoc(
                name="当前章节设计规划",
                path="workspace/剧情/当前章节设计规划.md",
                prompt="""阅读模板文档的结构和填写要求，然后按照模板结构填写内容。

请先阅读模板文档 `workspace/剧情/当前章节设计规划.md` 的结构，然后按照模板中的各章节（当前章节、本章目标、剧情承接、本章核心事件、角色安排、伏笔安排、氛围设计、字数目标）填写对应内容。

保持模板的 markdown 结构，只填写内容，不改变模板格式。"""
            ),
        ]

        self.current_doc_index = 0
        self.docs_written = []

        self.output("""
============================================================
  开始初始化记忆文档
============================================================

按顺序填写以下文档：
""")
        for i, doc in enumerate(self.docs):
            self.output(f"  {i+1}. {doc.name}")

        self.output("""

============================================================

""")

        # 开始生成第一个文档
        self._generate_current_doc()

    def _generate_current_doc(self):
        """生成当前文档"""
        doc = self.docs[self.current_doc_index]

        # 构建包含完整上下文的提示词
        full_prompt = self._build_doc_prompt(doc)

        self.output(f"""
************************************************************
文档 {self.current_doc_index + 1}/{len(self.docs)}: {doc.name}
************************************************************

[正在生成内容，请稍候...]
[上下文: 小说提示词 + {self.current_doc_index} 个已生成文档]

""")

        self.state = WorkflowState.INIT_GENERATING
        self.generated_content = ""

        # 使用流式方式生成内容
        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                self.output(f"\n[{name} 完成]")

        self.generated_content = "".join(content_parts)
        doc.content = self.generated_content
        doc.status = "已生成"

        # 等待用户确认
        self._show_confirm_prompt()

    def _build_doc_prompt(self, doc: InitDoc) -> str:
        """构建文档生成提示词，包含完整初始化上下文"""
        # 读取模板文档内容
        template_content = self._read_doc(doc.path)

        # 构建完整的初始化上下文
        context_header = f"""小说创作初始化 - 第 {self.current_doc_index + 1}/{len(self.docs)} 个文档

小说总提示词：
{self.user_prompt}

工作目录：{self.work_dir}

{'='*60}
当前要填写的文档模板：{doc.name}
{'='*60}
{template_content if template_content else '(模板文件不存在)'}

{'='*60}
以下是目前已生成的文档内容（按生成顺序）：
{'='*60}
"""

        # 收集所有已生成的文档内容
        generated_docs = []
        for i, d in enumerate(self.docs[:self.current_doc_index]):
            if d.content:
                generated_docs.append(f"\n[文档{i+1}: {d.name}]\n{d.content}")

        if generated_docs:
            context_header += "\n".join(generated_docs)
        else:
            context_header += "\n（暂无已生成的文档）"

        context_header += f"""
{'='*60}
填写要求：
{'='*60}
"""

        # 组合完整提示词
        full_prompt = context_header + doc.prompt

        return full_prompt

    def _get_doc_content(self, doc_name: str) -> str:
        """获取已生成的文档内容"""
        for i in range(self.current_doc_index):
            doc = self.docs[i]
            if doc.name == doc_name and doc.content:
                return doc.content
        return ""

    def _show_confirm_prompt(self):
        """显示确认提示"""
        doc = self.docs[self.current_doc_index]
        self.output(f"""
============================================================
[文档已生成] {doc.name}
============================================================

下一步操作：
  - 输入任意内容：确认并保存此文档，继续下一个
  - 输入 skip：跳过此文档，不保存
  - 输入 redo：重新生成此文档
  - 输入 q：退出初始化

""")
        self.state = WorkflowState.INIT_CONFIRM

    def _handle_confirm(self, cmd: str):
        """处理确认"""
        cmd_lower = cmd.lower()
        doc = self.docs[self.current_doc_index]

        if cmd_lower == "q":
            # 退出初始化
            self._finish_init()
            return

        elif cmd_lower == "redo":
            # 重新生成
            self.output("[重新生成中...]")
            self._generate_current_doc()
            return

        elif cmd_lower == "skip":
            # 跳过
            self.output("[已跳过]")
            doc.status = "已跳过"
            self._move_to_next_doc()
            return

        # 确认保存
        if doc.content:
            self._write_doc(doc)
            self.docs_written.append(doc.name)
            self.output(f"[已保存] {doc.name}")

        self._move_to_next_doc()

    def _move_to_next_doc(self):
        """移动到下一个文档"""
        self.current_doc_index += 1

        if self.current_doc_index >= len(self.docs):
            self._finish_init()
        else:
            self._generate_current_doc()

    def _write_doc(self, doc: InitDoc):
        """写入文档"""
        if not doc.content:
            return

        fpath = self.work_dir / doc.path
        fpath.parent.mkdir(parents=True, exist_ok=True)
        fpath.write_text(doc.content.strip(), encoding="utf-8")

    def _finish_init(self):
        """完成初始化"""
        self.state = WorkflowState.READY

        written = [name for name in self.docs_written]
        skipped = [doc.name for doc in self.docs if doc.status == "已跳过"]

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

开始创作前，请先阅读记忆文档，了解：
  - 世界观设定
  - 主角设定
  - 剧情走向

下一步：
  输入 /new_volume 开启新一卷创作

""")

    def _create_dirs(self):
        """创建目录结构"""
        dirs = [
            "workspace/剧情",
        ]
        for d in dirs:
            (self.work_dir / d).mkdir(parents=True, exist_ok=True)

        # 创建记忆文档框架
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
        }

        for path, content in templates.items():
            fpath = self.work_dir / path
            if not fpath.exists():
                fpath.parent.mkdir(parents=True, exist_ok=True)
                fpath.write_text(content, encoding="utf-8")

    def is_idle(self) -> bool:
        """是否处于空闲状态"""
        return self.state == WorkflowState.IDLE

    def is_init_mode(self) -> bool:
        """是否处于初始化模式"""
        return self.state != WorkflowState.READY

    # ==================== 新卷创作工作流 ====================

    def _start_new_volume(self):
        """开始新卷创作"""
        self.state = WorkflowState.NEW_VOLUME_GET_NUM
        self.output("""
============================================================
  开启新一卷创作
============================================================

请输入新卷编号（数字，如：1、2、3...）

输入 q 取消
""")

    def _handle_new_volume_num(self, cmd: str):
        """处理卷编号输入"""
        if cmd.lower() == "q":
            self.output("已取消。")
            self.state = WorkflowState.READY
            return

        try:
            volume_num = int(cmd.strip())
        except ValueError:
            self.output("请输入有效的数字，如：1、2、3...")
            return

        self.volume_num = volume_num

        # 创建卷文件夹
        volume_dir = self.novel_dir / f"第{volume_num}卷"
        volume_dir.mkdir(parents=True, exist_ok=True)
        self.volume_dir = volume_dir

        # 检查是否是第一次创建卷（检查是否有其他卷包含章节）
        has_previous_volumes = False
        for d in self.novel_dir.iterdir():
            if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷") and d != volume_dir:
                chapters = [f for f in d.iterdir() if f.suffix == ".md"]
                if chapters:
                    has_previous_volumes = True
                    break

        # 读取需要更新的文档
        self.prev_volume_summary = self._read_doc("workspace/剧情/前卷剧情概述.md")
        self.current_plot = self._read_doc("workspace/剧情/本卷核心剧情.md")
        self.next_plot = self._read_doc("workspace/剧情/下一卷剧情.md")

        # 根据是否有前卷来决定更新的文档
        if has_previous_volumes:
            # 有前卷，需要更新前卷剧情概述
            self.volume_docs = [
                ("前卷剧情概述", "workspace/剧情/前卷剧情概述.md"),
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]
            docs_list = """将依次更新以下文档：
  1. 前卷剧情概述 - 参考当前本卷核心剧情
  2. 本卷核心剧情 - 参考当前下一卷剧情
  3. 下一卷剧情 - 综合设计"""
        else:
            # 第一次创建卷，没有前卷，不需要更新前卷剧情概述
            self.volume_docs = [
                ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
                ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
            ]
            docs_list = """将依次更新以下文档：
  1. 本卷核心剧情 - 参考当前下一卷剧情
  2. 下一卷剧情 - 综合设计"""

        self.current_volume_doc_index = 0

        self.output(f"""
============================================================
  新卷创作 - 第 {volume_num} 卷
============================================================

卷文件夹: {volume_dir.name}

{docs_list}

""")

        # 开始第一个文档的生成
        self._generate_volume_doc()

    def _read_doc(self, path: str) -> str:
        """读取文档内容"""
        fpath = self.work_dir / path
        if fpath.exists():
            return fpath.read_text(encoding="utf-8")
        return ""

    def _generate_volume_doc(self):
        """生成卷相关文档"""
        doc_name, doc_path = self.volume_docs[self.current_volume_doc_index]

        # 构建提示词
        full_prompt = self._build_volume_doc_prompt(doc_name, doc_path)

        self.output(f"""
************************************************************
文档 {self.current_volume_doc_index + 1}/{len(self.volume_docs)}: {doc_name}
************************************************************

[正在生成内容，请稍候...]
[上下文: 所有记忆文档]

""")

        self.state = WorkflowState.NEW_VOLUME_GENERATING
        self.generated_content = ""

        # 使用流式方式生成内容
        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                self.output(f"\n[{name} 完成]")

        self.generated_content = "".join(content_parts)

        # 等待用户确认
        self._show_volume_confirm_prompt()

    def _build_volume_doc_prompt(self, doc_name: str, doc_path: str) -> str:
        """构建新卷文档生成提示词，包含完整上下文"""
        # 读取所有记忆文档
        all_docs_content = {}
        doc_files = [
            "workspace/世界观.md",
            "workspace/主角形象状态.md",
            "workspace/剧情/本卷核心剧情.md",
            "workspace/剧情/下一卷剧情.md",
            "workspace/剧情/前卷剧情概述.md",
            "workspace/剧情/伏笔.md",
            "workspace/剧情/有关角色.md",
            "workspace/剧情/事件.md",
            "workspace/剧情/当前章节设计规划.md",
        ]
        for f in doc_files:
            all_docs_content[f] = self._read_doc(f)

        # 构建上下文头
        context_header = f"""小说创作 - 新卷创作工作流
当前操作: 更新 "{doc_name}"

{'='*60}
所有记忆文档内容：
{'='*60}

[世界观.md]
{all_docs_content.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs_content.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md] (当前卷)
{all_docs_content.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md] (当前设计的下一卷)
{all_docs_content.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md] (历史卷概述)
{all_docs_content.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs_content.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs_content.get('workspace/剧情/有关角色.md', '(无)')}

[事件.md]
{all_docs_content.get('workspace/剧情/事件.md', '(无)')}

[当前章节设计规划.md]
{all_docs_content.get('workspace/剧情/当前章节设计规划.md', '(无)')}

{'='*60}
当前任务：
{'='*60}
"""

        # 根据不同文档生成不同的提示词
        if doc_name == "前卷剧情概述":
            return context_header + """【前卷剧情概述更新规则】
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
- [角色名]：[发展描述]
...

请直接输出完整的"前卷剧情概述.md"内容。
"""
        elif doc_name == "本卷核心剧情":
            return context_header + """【本卷核心剧情更新规则】
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

请直接输出完整的"本卷核心剧情.md"内容。
"""
        elif doc_name == "下一卷剧情":
            return context_header + """【下一卷剧情设计规则】
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

请直接输出完整的"下一卷剧情.md"内容。
"""

        return context_header

    def _show_volume_confirm_prompt(self):
        """显示卷文档确认提示"""
        doc_name, _ = self.volume_docs[self.current_volume_doc_index]
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
        self.state = WorkflowState.NEW_VOLUME_CONFIRM

    def _handle_new_volume_confirm(self, cmd: str):
        """处理新卷文档确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.volume_docs[self.current_volume_doc_index]

        if cmd_lower == "q":
            # 退出新卷创作
            self._finish_new_volume()
            return

        elif cmd_lower == "redo":
            # 重新生成
            self.output("[重新生成中...]")
            self._generate_volume_doc()
            return

        elif cmd_lower == "skip":
            # 跳过
            self.output("[已跳过]")
            self._move_to_next_volume_doc()
            return

        # 确认保存
        if self.generated_content:
            fpath = self.work_dir / doc_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(self.generated_content.strip(), encoding="utf-8")
            self.output(f"[已保存] {doc_name}")

        self._move_to_next_volume_doc()

    def _move_to_next_volume_doc(self):
        """移动到下一个卷文档"""
        self.current_volume_doc_index += 1

        if self.current_volume_doc_index >= len(self.volume_docs):
            self._finish_new_volume()
        else:
            self._generate_volume_doc()

    def _finish_new_volume(self):
        """完成新卷创作"""
        self.state = WorkflowState.READY

        # 检查是否更新了前卷剧情概述
        has_prev = any(name == "前卷剧情概述" for name, _ in self.volume_docs)

        if has_prev:
            docs_msg = """已更新文档：
  1. 前卷剧情概述
  2. 本卷核心剧情
  3. 下一卷剧情"""
        else:
            docs_msg = """已更新文档：
  1. 本卷核心剧情
  2. 下一卷剧情"""

        self.output(f"""
============================================================
  新卷创作完成 - 第 {self.volume_num} 卷
============================================================

{docs_msg}

卷文件夹已创建：
  {self.volume_dir.name}/

下一步：
  输入 /chapter 开始创作章节
""")

    # ==================== 章节创作工作流 ====================

    def _start_chapter(self):
        """开始章节创作"""
        # 检查是否已有卷文件夹
        if not self.novel_dir or not self.novel_dir.exists():
            self.output("""
============================================================
  错误：小说本体文件夹不存在
============================================================

请先使用 /new_volume 创建新卷
""")
            self.state = WorkflowState.READY
            return

        # 列出已有卷
        volumes = [d for d in self.novel_dir.iterdir() if d.is_dir() and d.name.startswith("第") and d.name.endswith("卷")]
        if not volumes:
            self.output("""
============================================================
  错误：暂无卷文件夹
============================================================

请先使用 /new_volume 创建新卷
""")
            self.state = WorkflowState.READY
            return

        volumes.sort(key=lambda x: x.name)
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
        self.state = WorkflowState.CHAPTER_GET_VOLUME

    def _handle_chapter_volume(self, cmd: str):
        """处理卷编号输入"""
        if cmd.lower() == "q":
            self.state = WorkflowState.READY
            self.output("已取消。")
            return

        try:
            volume_num = int(cmd.strip())
        except ValueError:
            self.output("请输入有效的数字，如：1、2、3...")
            return

        volume_dir = self.novel_dir / f"第{volume_num}卷"
        if not volume_dir.exists():
            self.output(f"卷文件夹不存在：第{volume_num}卷\n请先使用 /new_volume 创建")
            self.state = WorkflowState.READY
            return

        self.volume_num = volume_num
        self.volume_dir = volume_dir

        # 统计已有章节数
        existing_chapters = [f for f in volume_dir.iterdir() if f.suffix == ".md"]
        next_chapter_num = len(existing_chapters) + 1

        self.output(f"""
============================================================
  第 {volume_num} 卷
============================================================

已有章节数：{len(existing_chapters)}
下一个章节编号：{next_chapter_num}

请输入章节编号（直接回车使用 {next_chapter_num}，或输入其他数字）：
""")
        self.state = WorkflowState.CHAPTER_GET_NUM
        self.next_chapter_num = next_chapter_num

    def _handle_chapter_num(self, cmd: str):
        """处理章节编号输入"""
        if cmd.lower() == "q":
            self.state = WorkflowState.READY
            self.output("已取消。")
            return

        # 如果直接回车，使用建议的章节编号
        if cmd.strip() == "":
            chapter_num = self.next_chapter_num
        else:
            try:
                chapter_num = int(cmd.strip())
            except ValueError:
                self.output("请输入有效的数字")
                return

        self.chapter_num = chapter_num

        # 创建章节文件
        chapter_file = self.volume_dir / f"第{chapter_num}章.md"
        if not chapter_file.exists():
            chapter_file.write_text("", encoding="utf-8")

        self.chapter_file = chapter_file

        self.output(f"""
============================================================
  准备创作：第 {self.volume_num} 卷 第 {chapter_num} 章
============================================================

章节文件：{chapter_file.name}

正在读取记忆文档...
""")

        # 开始生成章节内容
        self._generate_chapter_content()

    def _generate_chapter_content(self):
        """生成章节内容"""
        # 读取所有记忆文档
        all_docs_content = {}
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
        for f in doc_files:
            all_docs_content[f] = self._read_doc(f)

        # 读取当前章节规划
        chapter_plan = all_docs_content.get("workspace/剧情/当前章节设计规划.md", "")

        # 读取上一章内容（如果存在）
        prev_chapter_content = ""
        if self.chapter_num > 1:
            prev_file = self.volume_dir / f"第{self.chapter_num - 1}章.md"
            if prev_file.exists():
                prev_chapter_content = prev_file.read_text(encoding="utf-8")

        # 构建提示词
        full_prompt = f"""小说创作 - 章节创作
当前任务：创作第 {self.volume_num} 卷 第 {self.chapter_num} 章

{'='*60}
当前章节规划：
{'='*60}
{chapter_plan if chapter_plan else '(暂无章节规划)'}

{'='*60}
上一章内容（用于衔接）：
{'='*60}
{prev_chapter_content if prev_chapter_content else '(无上一章内容)'}

{'='*60}
所有记忆文档内容：
{'='*60}

[世界观.md]
{all_docs_content.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs_content.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md]
{all_docs_content.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md]
{all_docs_content.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md]
{all_docs_content.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs_content.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs_content.get('workspace/剧情/有关角色.md', '(无)')}

[目前剧情.md] (近10章内容)
{all_docs_content.get('workspace/剧情/目前剧情.md', '(无)')}

[事件.md]
{all_docs_content.get('workspace/剧情/事件.md', '(无)')}

{'='*60}
创作要求：
{'='*60}
1. 根据章节规划和记忆文档，创作本章内容
2. 注意与上一章的衔接
3. 保持世界观和角色设定的一致性
4. 埋下必要的伏笔
5. 直接输出章节内容，不需要其他说明

请开始创作第 {self.volume_num} 卷 第 {self.chapter_num} 章：
"""

        self.output(f"""
************************************************************
  第 {self.volume_num} 卷 第 {self.chapter_num} 章
************************************************************

[正在生成章节内容，请稍候...]

""")

        self.state = WorkflowState.CHAPTER_GENERATING
        self.generated_content = ""

        # 使用流式方式生成内容
        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                self.output(f"\n[{name} 完成]")

        self.generated_content = "".join(content_parts)

        # 等待用户确认
        self._show_chapter_confirm_prompt()

    def _show_chapter_confirm_prompt(self):
        """显示章节确认提示"""
        self.output(f"""
============================================================
[章节内容已生成] 第 {self.volume_num} 卷 第 {self.chapter_num} 章
============================================================

下一步操作：
  - 输入任意内容：确认并保存章节，继续更新记忆文档
  - 输入 skip：跳过保存，直接更新记忆文档
  - 输入 redo：重新生成章节
  - 输入 q：退出章节创作

""")
        self.state = WorkflowState.CHAPTER_CONFIRM

    def _handle_chapter_confirm(self, cmd: str):
        """处理章节确认"""
        cmd_lower = cmd.lower()

        if cmd_lower == "q":
            self.state = WorkflowState.READY
            self.output("已取消章节创作。")
            return

        elif cmd_lower == "redo":
            self.output("[重新生成中...]")
            self._generate_chapter_content()
            return

        elif cmd_lower == "skip":
            self.output("[已跳过保存]")
        else:
            # 保存章节内容
            if self.generated_content:
                self.chapter_file.write_text(self.generated_content.strip(), encoding="utf-8")
                self.output(f"[已保存章节] {self.chapter_file.name}")

        # 开始更新记忆文档
        self._start_update_memory_docs()

    def _start_update_memory_docs(self):
        """开始更新记忆文档"""
        # 记忆文档更新顺序（按用户要求）
        self.memory_docs = [
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
        self.current_memory_doc_index = 0

        self.output("""
============================================================
  更新记忆文档
============================================================

按顺序更新以下文档：
  1. 世界观（模糊更新 - 引入新设定）
  2. 主角形象状态（状态动态更新）
  3. 本卷核心剧情（更新已完成/进行中/未完成）
  4. 下一卷剧情（可调整优化）
  5. 伏笔（按需更新）
  6. 有关角色（添加新角色/删除无用角色）
  7. 目前剧情（更新近10章内容）
  8. 事件（主线/并行事件）
  9. 当前章节设计规划（更新为下一章规划）

""")

        self._generate_memory_doc()

    def _generate_memory_doc(self):
        """生成记忆文档更新内容"""
        doc_name, doc_path = self.memory_docs[self.current_memory_doc_index]

        # 读取当前文档内容和章节内容
        current_doc_content = self._read_doc(doc_path)
        chapter_content = self.chapter_file.read_text(encoding="utf-8") if self.chapter_file.exists() else ""

        # 读取所有记忆文档
        all_docs_content = {}
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
        for f in doc_files:
            all_docs_content[f] = self._read_doc(f)

        # 构建提示词
        full_prompt = f"""小说创作 - 记忆文档更新
当前任务：更新 "{doc_name}"

请先阅读：
1. 当前文档内容：`{doc_path}`
2. 刚创作的章节内容

然后根据章节内容，合理更新文档。

{'='*60}
当前文档内容：
{'='*60}
{current_doc_content if current_doc_content else '(文档为空)'}

{'='*60}
刚创作的章节内容：
{'='*60}
{chapter_content if chapter_content else '(无)'}

{'='*60}
所有记忆文档：
{'='*60}
[世界观.md]
{all_docs_content.get('workspace/世界观.md', '(无)')}

[主角形象状态.md]
{all_docs_content.get('workspace/主角形象状态.md', '(无)')}

[本卷核心剧情.md]
{all_docs_content.get('workspace/剧情/本卷核心剧情.md', '(无)')}

[下一卷剧情.md]
{all_docs_content.get('workspace/剧情/下一卷剧情.md', '(无)')}

[前卷剧情概述.md]
{all_docs_content.get('workspace/剧情/前卷剧情概述.md', '(无)')}

[伏笔.md]
{all_docs_content.get('workspace/剧情/伏笔.md', '(无)')}

[有关角色.md]
{all_docs_content.get('workspace/剧情/有关角色.md', '(无)')}

[目前剧情.md]
{all_docs_content.get('workspace/剧情/目前剧情.md', '(无)')}

[事件.md]
{all_docs_content.get('workspace/剧情/事件.md', '(无)')}

[当前章节设计规划.md]
{all_docs_content.get('workspace/剧情/当前章节设计规划.md', '(无)')}

{'='*60}
更新要求：
{'='*60}
"""

        if doc_name == "世界观":
            full_prompt += """【世界观更新规则】
操作：从本章内容中提取新引入的世界设定，按格式追加到原文档末尾。
- 新地区：在"地理环境"章节末尾添加 "## 新地区名称\\n[描述]"
- 新规则/法则：在"世界规则"章节末尾添加 "## 新规则名称\\n[描述]"
- 新势力：在"势力划分"章节末尾添加 "## 新势力名称\\n[描述]"
- 无新增设定时：输出原文档内容不变

请输出完整的更新后文档内容：
"""
        elif doc_name == "主角形象状态":
            full_prompt += """【主角形象状态更新规则】
操作：检查本章内容中主角的状态变化，按以下规则更新：
- 心理状态变化：在"心理状态"下用新内容替换原内容
- 处境状态变化：在"处境状态"下用新内容替换原内容
- 目标/动机变化：在"目标/动机"下用新内容替换原内容
- 形象/性格：不做修改
- 无状态变化时：输出原文档内容不变

请输出完整的更新后文档内容：
"""
        elif doc_name == "本卷核心剧情":
            full_prompt += """【本卷核心剧情更新规则】
操作：根据本章内容，按以下规则更新三部分：
1. 已完成剧情：如果本章有核心事件完成，在表格末尾添加一行 [第X章|事件名称|已完成]
2. 进行中剧情：如果有进行中的剧情，更新"进行中剧情"表格的当前进展列
3. 未完成剧情设计：如需调整，修改"未完成剧情设计"表格内容

格式要求：
| 章节 | 核心事件/进展 | 结果/状态 |
|-----|-------------|---------|
| 第X章 | 事件描述 | 进行中/已完成 |

请输出完整的更新后文档内容：
"""
        elif doc_name == "下一卷剧情":
            full_prompt += """【下一卷剧情更新规则】
操作：检查本章剧情发展，如果影响了下卷设计，按以下规则调整：
- 卷名/主题变化：替换"卷名/主题"下的内容
- 核心冲突变化：替换"核心冲突"下的内容
- 主要目标变化：替换"主要目标"下的内容
- 剧情走向变化：替换"剧情走向设计"下的内容
- 无需调整时：输出原文档内容不变

请输出完整的更新后文档内容：
"""
        elif doc_name == "伏笔":
            full_prompt += """【伏笔更新规则】
操作：根据本章内容，按以下规则更新：
1. 回收伏笔：在"待回收伏笔"表格中找到对应行，将"状态"列改为"已回收"，添加"回收章节"列
2. 新埋伏笔：在"计划伏笔"表格末尾添加新行 [计划埋入位置|伏笔内容|预期作用]

表格格式：
| 伏笔内容 | 埋下章节 | 预期回收章节 | 回收方式 | 状态 |
|---------|---------|------------|---------|-----|
| 伏笔描述 | 第X章 | 第X章 | 方式 | 进行中/已回收 |

请输出完整的更新后文档内容：
"""
        elif doc_name == "有关角色":
            full_prompt += """【有关角色更新规则】
操作：根据本章内容，按以下规则更新：
1. 新角色出场：在角色列表末尾添加新角色模板
2. 角色退场：将该角色的"当前状态"改为"已退场"
3. 角色状态变化：更新对应角色的"当前状态"内容

新增角色模板：
### 角色XXX
- 身份:
- 与主角关系:
- 核心性格:
- 在剧情中的作用:
- 当前状态:

请输出完整的更新后文档内容：
"""
        elif doc_name == "目前剧情":
            full_prompt += """【目前剧情更新规则】
操作：管理近10章的剧情记录。
1. 在文档开头添加本章内容，格式：
## 第X章
[本章完整内容]
2. 如果文档中超过10章，删除最后的章节
3. 保持最新章节在前面

请输出完整的更新后文档内容：
"""
        elif doc_name == "事件":
            full_prompt += """【事件更新规则】
操作：根据本章内容，按以下规则更新：
1. 主线事件完成：将"主线事件"表格中对应行的"当前状态"改为"已完成"
2. 新主线事件：在"主线事件"表格末尾添加新行
3. 新并行事件：在"并行事件"表格末尾添加新行

表格格式：
主线事件：
| 事件 | 开始章节 | 当前状态 | 影响 |
|-----|---------|---------|-----|
| 事件名 | 第X章 | 进行中/已完成 | 影响描述 |

并行事件：
| 事件 | 描述 | 对主线影响 |
|-----|------|----------|
| 事件名 | 描述 | 影响 |

请输出完整的更新后文档内容：
"""
        elif doc_name == "当前章节设计规划":
            full_prompt += """【当前章节设计规划更新规则】
操作：分两步执行。
第一步：将本章标记为完成，在完成记录表格末尾添加：
| 完成日期 | 完成字数 | 备注 |
|---------|---------|-----|
| [日期] | [预估字数] | 第X章完成 |

第二步：设计下一章，替换以下各项目内容：
- 章节编号：第X+1章
- 章节名：[章节名]
- 本章目标：[目标描述]
- 剧情承接-承接上文：[承接内容]
- 剧情承接-本章发展：[本章发展]
- 剧情承接-引出下文：[引出内容]
- 本章核心事件：[事件描述]
- 角色安排：[角色及作用]
- 伏笔安排：[伏笔设计]
- 氛围设计：[氛围描述]
- 字数目标：[X-X字]

请输出完整的更新后文档内容：
"""
        elif doc_name == "当前章节设计规划":
            full_prompt += """更新"当前章节设计规划"文档：
- 将本章设为"已完成"，添加到完成记录
- 设计下一章的章节规划：
  1. 章节编号和名称
  2. 本章目标
  3. 剧情承接（上文、本章发展、引出下文）
  4. 本章核心事件
  5. 角色安排
  6. 伏笔安排
  7. 氛围设计
  8. 字数目标
- 保持模板的 markdown 结构，只更新内容

请直接输出更新后的文档内容。
"""

        self.output(f"""
************************************************************
  更新文档 {self.current_memory_doc_index + 1}/{len(self.memory_docs)}: {doc_name}
************************************************************

[正在生成更新内容，请稍候...]

""")

        self.state = WorkflowState.CHAPTER_UPDATE_DOC
        self.generated_content = ""

        # 使用流式方式生成内容
        content_parts = []
        for event in self.agent.stream_response(full_prompt):
            if event.get("type") == AgentEvents.CONTENT:
                text = event.get("content", "")
                content_parts.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                self.output(f"\n[{name} 完成]")

        self.generated_content = "".join(content_parts)

        # 等待用户确认
        self._show_memory_doc_confirm_prompt()

    def _show_memory_doc_confirm_prompt(self):
        """显示记忆文档确认提示"""
        doc_name, _ = self.memory_docs[self.current_memory_doc_index]
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
        self.state = WorkflowState.CHAPTER_UPDATE_CONFIRM

    def _handle_chapter_update_confirm(self, cmd: str):
        """处理记忆文档确认"""
        cmd_lower = cmd.lower()
        doc_name, doc_path = self.memory_docs[self.current_memory_doc_index]

        if cmd_lower == "q":
            self._finish_chapter()
            return

        elif cmd_lower == "redo":
            self.output("[重新生成中...]")
            self._generate_memory_doc()
            return

        elif cmd_lower == "skip":
            self.output("[已跳过]")
            self._move_to_next_memory_doc()
            return

        # 保存文档
        if self.generated_content:
            fpath = self.work_dir / doc_path
            fpath.parent.mkdir(parents=True, exist_ok=True)
            fpath.write_text(self.generated_content.strip(), encoding="utf-8")
            self.output(f"[已保存] {doc_name}")

        self._move_to_next_memory_doc()

    def _move_to_next_memory_doc(self):
        """移动到下一个记忆文档"""
        self.current_memory_doc_index += 1

        if self.current_memory_doc_index >= len(self.memory_docs):
            self._finish_chapter()
        else:
            self._generate_memory_doc()

    def _finish_chapter(self):
        """完成章节创作"""
        self.state = WorkflowState.READY

        self.output(f"""
============================================================
  章节创作完成
============================================================

已完成：
  - 章节创作：第 {self.volume_num} 卷 第 {self.chapter_num} 章
  - 记忆文档更新（9个文档）

章节文件：{self.chapter_file.name}

下一步：
  输入 /chapter 继续创作下一章
  输入 /new_volume 创建新卷
""")


class ChapterWorkflow:
    """章节创作工作流（待实现）"""

    def __init__(self, agent, output_callback: Callable):
        self.agent = agent
        self.output_callback = output_callback
        self.state = WorkflowState.READY

    def output(self, text: str):
        self.output_callback(text)

    def handle_command(self, cmd: str) -> bool:
        """处理章节创作命令"""
        if cmd.lower() == "q":
            return False

        if cmd == "/back":
            self.output("返回主菜单")
            return False

        return True
