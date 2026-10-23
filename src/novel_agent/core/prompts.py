"""AI 生成提示词管理模块

按工作流组织：
- /start  ->  InitPrompts
- /new_volume  ->  NewVolumePrompts
- /chapter  ->  ChapterPrompts
"""

from typing import Dict, List, Tuple, Optional


# =============================================================================
# /start 初始化工作流提示词
# =============================================================================

class InitPrompts:
    """/start 初始化工作流 - 文档填写提示词

    工作流节点：
      1~8. 填写 8 个记忆文档（按 DOCS_CONFIG 顺序逐个生成）
    """

    # 文档配置：按创作顺序排列 (名称, 路径)
    DOCS_CONFIG = [
        ("世界观", "workspace/世界观.md"),
        ("主角形象状态", "workspace/主角形象状态.md"),
        ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
        ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
        ("伏笔", "workspace/剧情/伏笔.md"),
        ("有关角色", "workspace/剧情/有关角色.md"),
        ("事件", "workspace/剧情/事件.md"),
        ("当前章节设计规划", "workspace/剧情/当前章节设计规划.md"),
    ]

    @classmethod
    def get_doc_prompt(cls, doc_name: str) -> str:
        """获取指定文档的填写指导提示词"""
        prompts = {
            "世界观": cls._worldview_prompt(),
            "主角形象状态": cls._character_prompt(),
            "本卷核心剧情": cls._plot_prompt(),
            "下一卷剧情": cls._next_plot_prompt(),
            "伏笔": cls._foreshadow_prompt(),
            "有关角色": cls._characters_prompt(),
            "事件": cls._events_prompt(),
            "当前章节设计规划": cls._chapter_plan_prompt(),
        }
        return prompts.get(doc_name, "")

    @classmethod
    def build_init_node_context(
        cls,
        work_dir: str,
        doc_name: str,
        doc_path: str,
        user_prompt: str,
        generated_docs: List[Dict[str, str]],
        template_content: str,
    ) -> str:
        """构建 /start 初始化工作流节点的完整上下文

        Args:
            work_dir: 工作目录绝对路径
            doc_name: 当前要填写的文档名称
            doc_path: 文档保存路径（相对于 work_dir）
            user_prompt: 用户输入的小说总提示词
            generated_docs: 已完成填写的文档列表 [{"name": xxx, "content": xxx}, ...]
            template_content: 模板文件的完整内容（包含格式框架）

        Returns:
            完整的提示词上下文
        """
        doc_guide = cls.get_doc_prompt(doc_name)

        # 已生成文档上下文
        if generated_docs:
            generated_context = "\n\n".join(
                f"[已填写文档 {i+1}: {d['name']}]\n{d['content']}"
                for i, d in enumerate(generated_docs)
            )
        else:
            generated_context = "（暂无已填写的文档，这是第一个文档）"

        # ========== 构建完整上下文 ==========
        return f"""小说创作初始化 - 填写文档：{doc_name}

{'='*60}
一、小说总提示词（用户输入的小说设定）
{'='*60}
{user_prompt}

{'='*60}
二、当前要填写的文档
{'='*60}
  文档名称：{doc_name}
  保存路径：{doc_path}

{'='*60}
三、文档填写指导
{'='*60}
{doc_guide}

{'='*60}
四、模板文档格式框架（填写时必须保留此格式框架）
{'='*60}
{template_content}

{'='*60}
五、已完成的文档内容（供参考，保持一致性）
{'='*60}
{generated_context}

{'='*60}
六、重要要求
{'='*60}
1. 严格按照上面的模板格式填写内容，不要改变格式框架
2. 文档头部的元数据（文档类型、内容说明、更新逻辑、更新要求）必须原样保留
3. 保持 markdown 格式和标题层级
4. 通过 file_write 工具将内容写入保存路径，不要在对话中输出文档全文

请开始填写文档内容："""

    @classmethod
    def build_init_doc_prompt(
        cls,
        doc_index: int,
        total_docs: int,
        doc_name: str,
        doc_path: str,
        doc_prompt: str,
        template_content: str,
        user_prompt: str,
        generated_docs: List[Dict],
        work_dir: str = "",
    ) -> str:
        """兼容旧接口：构造初始化文档提示词（委托给 build_init_node_context）"""
        return cls.build_init_node_context(
            work_dir=work_dir,
            doc_name=doc_name,
            doc_path=doc_path,
            user_prompt=user_prompt,
            generated_docs=generated_docs,
            template_content=template_content,
        )

    # ---- 各文档的填写指导 ----

    @staticmethod
    def _worldview_prompt() -> str:
        """世界观填写指导"""
        return """【世界观填写指导】

这是一个从零开始初始化的小说世界观设定。请根据小说总提示词，填写以下内容：

## 核心设定
（请详细描述小说的核心世界观设定，包括：
- 世界的本质是什么（仙界/凡界/异世界/都市/科幻等）
- 世界的核心法则或规律
- 世界的主要特点

示例：这是一个修仙世界，天地间存在灵气，修士可以吸收灵气修炼突破境界）

## 社会结构
（请描述社会阶层、组织、势力分布）

示例：
- 宗门：正道七大宗门、魔道三宗
- 家族：千年世家、寒门小派
- 王朝：世俗皇权、统辖凡人
- 其他：散修联盟、民间帮派

## 世界规则
（请描述世界的运行规则）

示例：
- 修炼体系：炼气→筑基→金丹→元婴→化神，共五大境界
- 灵气分布：灵脉聚集之地灵气浓郁，荒芜之地灵气稀薄
- 资源分布：灵石、灵药、法宝的获取方式

## 地理环境
（请描述主要地理环境）

示例：
- 州域：中州为核心区域，东州西州为边缘
- 宗门驻地：青云山（正道）、魔域深渊（魔道）
- 禁地：葬神渊、虚空裂缝

## 势力划分
（请描述主要势力和阵营）

示例：
- 正道：青云宗、天机阁、玄天宫
- 魔道：血煞宗、万魔窟
- 中立：万宝楼、天机楼

## 扩展设定
（请描述其他扩展性设定）

示例：
- 种族：人族、妖族、灵族
- 文化：修真文化、世俗文化
- 禁忌：不可屠杀凡人、不可使用禁忌之术"""

    @staticmethod
    def _character_prompt() -> str:
        """主角形象状态填写指导"""
        return """【主角形象状态填写指导】

这是一个从零开始初始化的主角设定。请根据小说总提示词，填写以下内容：

## 基础设定

### 身份信息
- 姓名：（主角姓名）
- 年龄：（主角年龄）
- 身份/职业：（主角身份，如：宗门弟子、散修、王朝皇子等）

### 外貌特征
（请详细描述主角的外貌：面容、身材、服饰、标志性特征）

示例：
- 面容清秀，眉目间有一股倔强之气
- 身穿青色长袍，腰挂长剑
- 左手腕有一道疤痕（幼时受伤留下）

## 性格设定

### 核心性格
（主角的核心性格特点）

示例：沉稳内敛、重情重义、心思缜密

### 性格弱点
（主角的性格弱点）

示例：太过善良、容易心软、优柔寡断

### 行为习惯
（主角的行为习惯）

示例：思考时喜欢抚摸剑柄、紧张时眉头微皱

## 能力设定

### 核心能力
（主角的核心能力）

示例：剑法（青云剑诀）、炼丹（基础）、阵法（入门）

### 能力限制
（主角能力的限制或代价）

示例：剑法威力强大但消耗灵气大，战斗中难以持久

## 当前状态

### 心理状态
（当前的心理状态，初始化时可以为"平静"或"初出茅庐的忐忑"）

### 处境状态
（当前的处境，初始化时可以为"刚刚踏入修炼之路"）

### 目标/动机
（当前的目标或动机，初始化时为初始目标）

示例：变强、寻找身世真相、复仇"""

    @staticmethod
    def _plot_prompt() -> str:
        """本卷核心剧情填写指导"""
        return """【本卷核心剧情填写指导】

这是一个从零开始初始化的本卷剧情设计。请根据小说总提示词和已完成的设定，填写以下内容：

## 第X卷（根据实际情况填写卷号）

### 已完成剧情
（初始化时：本卷刚开始，无已完成剧情，保留表格框架但留空或填写"无"）

| 章节 | 核心事件 | 结果 |
|-----|---------|-----|
|     |         |     |

### 进行中剧情
（当前正在发生的剧情线索）

示例：
| 章节 | 当前进展 | 悬念 |
|-----|---------|-----|
| 第1章 | 主角通过宗门入门考核，正式成为外门弟子 | 入门考核中被长老发现天赋，后续会有什么际遇？ |

### 未完成剧情设计
（计划中但尚未开始的剧情）

示例：
| 设计点 | 剧情走向 | 可调整空间 |
|-------|---------|----------|
| 宗门秘境 | 主角在秘境中获得传承 | 可根据实际创作调整 |
| 身世之谜 | 主角身世逐渐揭开 | 发现更多线索 |"""

    @staticmethod
    def _next_plot_prompt() -> str:
        """下一卷剧情填写指导"""
        return """【下一卷剧情填写指导】

这是一个从零开始初始化的下一卷剧情设计。请根据小说总提示词和本卷剧情，预先设计下一卷：

## 第X卷 预先设计（X为下一卷的卷号）

### 卷名/主题
（下一卷的名称和主题）

示例：破境之路 - 主角突破到筑基境界

### 核心冲突
（下一卷的核心矛盾和冲突）

示例：
- 外部冲突：正魔大战爆发，主角被卷入
- 内部冲突：主角面临心魔考验

### 主要目标
（下一卷的主要目标）

示例：
1. 主角突破到筑基境界
2. 揭示主角身世的部分真相
3. 获得关键道具：玄天剑

### 剧情走向设计
（下一卷的剧情走向概述，包括开端、发展、高潮、结局的概括）

示例：
- 开端：主角回到宗门，开始闭关修炼
- 发展：修炼中遇到瓶颈，意外获得丹药辅助
- 高潮：正魔大战爆发，主角被迫参战
- 结局：大战结束，主角突破成功但也身受重伤"""

    @staticmethod
    def _foreshadow_prompt() -> str:
        """伏笔填写指导"""
        return """【伏笔填写指导】

这是一个从零开始初始化的伏笔记录。请根据小说总提示词和剧情设计，预先埋下伏笔：

## 待回收伏笔
（计划在后续章节中回收的伏笔，初始化时可以填写计划中的伏笔）

示例：
| 伏笔内容 | 埋下章节 | 预期回收章节 | 回收方式 | 状态 |
|---------|---------|------------|---------|-----|
| 主角身世之谜 | 第1章 | 第10章 | 神秘人出现揭示 | 待回收 |
| 神秘玉佩 | 第3章 | 第8章 | 玉佩发光引出秘境 | 待回收 |

## 计划伏笔
（计划在后续章节中埋入的伏笔）

示例：
| 计划埋入位置 | 伏笔内容 | 预期作用 |
|-------------|---------|---------|
| 第5章 | 主角遇到神秘老者 | 为后续传承埋下伏笔 |
| 第7章 | 发现古老遗迹 | 引入新的修炼体系 |

注意：如果暂无伏笔设计，表格可以保留框架但留空。"""

    @staticmethod
    def _characters_prompt() -> str:
        """有关角色填写指导"""
        return """【有关角色填写指导】

这是一个从零开始初始化的角色记录。请根据小说总提示词，填写与主角相关的重要角色：

## 重要角色

### 角色001
- 身份：（角色身份，如：宗门师兄、神秘强者、敌人等）
- 与主角关系：（与主角的关系，如：导师、竞争对手、挚友等）
- 核心性格：（角色的核心性格）
- 在剧情中的作用：（这个角色在剧情中的作用）
- 当前状态：（角色当前的状态）

示例：
- 身份：青云宗内门师兄
- 与主角关系：同门师兄，后期成为挚友
- 核心性格：温和谦逊、重情重义
- 在剧情中的作用：引导主角成长，在危机时刻帮助主角
- 当前状态：在宗门中修炼，修为筑基中期

### 角色002（如有）
- 身份：
- 与主角关系：
- 核心性格：
- 在剧情中的作用：
- 当前状态：

注意：如果暂无其他重要角色，可以只填写角色001或留空框架。"""

    @staticmethod
    def _events_prompt() -> str:
        """事件填写指导"""
        return """【事件填写指导】

这是一个从零开始初始化的事件记录。请根据小说总提示词和剧情设计，记录重要事件：

## 主线事件
（记录本卷中的主线事件，这些事件推动剧情发展）

示例：
| 事件 | 开始章节 | 当前状态 | 影响 |
|-----|---------|---------|-----|
| 宗门入门考核 | 第1章 | 已完成 | 主角正式成为宗门弟子 |
| 秘境历练 | 第3章 | 进行中 | 主角获得传承，实力提升 |

## 并行事件
（记录与主线并行的其他重要事件）

示例：
| 事件 | 描述 | 对主线影响 |
|-----|------|----------|
| 正魔关系紧张 | 正道与魔道摩擦频繁，大战一触即发 | 为主角后续卷入大战埋下伏笔 |

注意：如果暂无事件设计，表格可以保留框架但留空。"""

    @staticmethod
    def _chapter_plan_prompt() -> str:
        """当前章节设计规划填写指导"""
        return """【当前章节设计规划填写指导】

这是一个从零开始初始化的章节规划。请根据小说总提示词和剧情设计，规划第一个章节：

## 当前章节
- 章节编号:（第一个章节为第1章）
- 章节名:（章节名称）

## 本章目标
（本章要达成的目标）

示例：让主角正式踏入修炼世界，通过入门考核展现主角天赋

## 剧情承接
- 承接上文:（由于是第一章，承接上文可以填写"开篇设定"或"无"）
- 本章发展:（本章剧情如何发展）
- 引出下文:（本章如何为下一章铺垫）

## 本章核心事件

| 事件 | 设计内容 | 预期效果 |
|-----|---------|---------|
| （事件名） | （事件详细设计） | （预期达成的效果） |

示例：
| 事件 | 设计内容 | 预期效果 |
|-----|---------|---------|
| 入门考核 | 主角参加宗门入门考核，展现出过人天赋 | 引起长老注意，为后续际遇埋下伏笔 |

## 角色安排

| 角色 | 本章作用 |
|-----|---------|
| 主角 | 主要视角，展现性格和能力 |
| 宗门长老 | 考核主持者，发现主角天赋 |
| 其他弟子 | 衬托主角，参与考核 |

## 伏笔安排
（本章要埋下的伏笔）

示例：本章埋下"神秘玉佩发出微光"的伏笔

## 氛围设计
（本章的氛围）

示例：紧张中带着期待，考核的紧张氛围与主角的自信形成对比

## 字数目标
（本章的字数目标）

示例：3000字"""


# =============================================================================
# /new_volume 新卷创作工作流提示词
# =============================================================================

class NewVolumePrompts:
    """/new_volume 新卷创作工作流 - 卷文档更新提示词

    工作流节点：
      1. 前卷剧情概述（如有前卷）
      2. 本卷核心剧情
      3. 下一卷剧情
    """

    # 文档列表配置
    VOLUME_DOCS_WITH_PREV = [
        ("前卷剧情概述", "workspace/剧情/前卷剧情概述.md"),
        ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
        ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
    ]

    VOLUME_DOCS_WITHOUT_PREV = [
        ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
        ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
    ]

    @staticmethod
    def build_memory_context(all_docs: Dict[str, str]) -> str:
        """构建记忆文档上下文"""
        return f"""小说创作 - 新卷创作工作流

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

    @staticmethod
    def get_doc_prompt(doc_name: str) -> str:
        """获取指定文档的更新规则"""
        rules = {
            "前卷剧情概述": """【前卷剧情概述更新规则】
操作：根据前卷的"本卷核心剧情.md"内容，提取核心事件、关键转折点、角色发展，编写前卷剧情概述。

重要：前卷剧情概述是对前一个卷的剧情概括，不是本卷的内容。

## 核心事件提取
（从"特殊参考"中的前卷核心剧情，提取核心事件）

### 本卷核心事件
（请列出前卷的核心事件，用序号列表）
示例：
1. 主角入门拜师，通过宗门入门考核
2. 主角获得神秘传承，修为突飞猛进
3. 主角卷入正魔争斗，遭遇危机

### 关键转折点
（请列出前卷的关键转折点）
示例：
1. 第3章：主角被发现拥有特殊体质
2. 第5章：主角与师兄产生矛盾
3. 第8章：魔道入侵，主角被迫逃亡

### 主要角色发展
（请列出前卷中主要角色的发展变化）
示例：
- 主角：从炼气期突破到筑基期
- 师兄：修为巩固，成为内门弟子
- 神秘老者：逐渐显现更多线索

注意：如果这是第一卷或前一卷没有剧情概述可参考，则此文档可以留空框架或填写"无前卷剧情"。
""",
            "本卷核心剧情": """【本卷核心剧情更新规则】
操作：根据下一卷的剧情设计，规划本卷的详细剧情走向。

重要：本卷尚未开始创作，所以"已完成剧情"部分应为空或填写"无"，重点放在"进行中剧情"和"未完成剧情设计"。

## 第X卷（当前要创作的卷）

### 已完成剧情
（初始化时本卷尚未开始，此部分应为空或填写"无"）

### 进行中剧情
（这是本卷的核心剧情线，是小说开篇的主要内容）
（请详细设计本卷开头部分的剧情，包括：
- 开篇事件（如：主角入门、获得机缘、遭遇危机等）
- 主要矛盾（如：修炼瓶颈、敌人追杀、身世之谜等）
- 预期章节走向（如：前5章主要讲述主角入门经历）

示例：
| 章节 | 当前进展 | 悬念 |
|-----|---------|-----|
| 第1章 | 主角参加宗门入门考核，展现出过人天赋 | 主角被哪位长老收为弟子？ |
| 第2章 | 主角拜入宗门，成为外门弟子 | 主角能否在修炼上取得突破？ |
| 第3章 | 主角获得神秘玉佩，发现其中蕴含功法 | 玉佩的来历是什么？ |

### 未完成剧情设计
（这是本卷计划但尚未开始的剧情，为后续章节预留空间）
（请设计本卷后半部分的剧情走向）

示例：
| 设计点 | 剧情走向 | 可调整空间 |
|-------|---------|----------|
| 宗门秘境 | 主角参加秘境历练，获得传承 | 可根据实际情况调整 |
| 身世线索 | 主角发现身世线索，引出更大秘密 | 可挖掘更多背景故事 |

注意：本卷核心剧情是指导本卷创作的重要文档，设计要有连贯性和可发展性。
""",
            "下一卷剧情": """【下一卷剧情设计规则】
操作：综合所有记忆文档，设计下一卷的剧情。

重要：这是为尚未创作的新卷预先设计剧情，要有前瞻性和引导性。

## 第X卷 预先设计（X为下一卷的卷号）

### 卷名/主题
（请设计下一卷的名称和主题）

示例：破境之路 - 主角冲击筑基境界

### 核心冲突
（请描述下一卷的核心矛盾和冲突）

示例：
- 外部冲突：正魔大战全面爆发，主角被迫卷入
- 内部冲突：主角面临心魔考验，险些走火入魔
- 人际冲突：师门出现叛徒，主角遭受陷害

### 主要目标
（请列出下一卷的主要目标）

示例：
1. 主角突破到筑基境界
2. 揭示主角身世的部分真相
3. 获得关键道具或传承
4. 化解师门危机

### 剧情走向设计
（请详细描述下一卷的剧情走向，包括：开端、发展、高潮、结局的概括）

示例：
- 开端：大战爆发，主角随宗门出战，展现成长
- 发展：战斗中主角发现魔道阴谋，陷入危机
- 高潮：主角突破成功，但身受重伤
- 结局：大战结束，主角声名鹊起，但发现更大危机即将来临

注意：下一卷剧情设计要有连贯性，与本卷剧情自然衔接。
""",
        }
        return rules.get(doc_name, "")

    @classmethod
    def build_doc_prompt(cls, doc_name: str, all_docs: Dict[str, str]) -> str:
        """构建新卷文档提示词（兼容旧接口）"""
        context = cls.build_memory_context(all_docs)
        doc_rule = cls.get_doc_prompt(doc_name)
        return context + doc_rule if doc_rule else context

    @classmethod
    def build_new_volume_node_context(
        cls,
        doc_name: str,
        doc_path: str,
        volume_num: int,
        all_docs: Dict[str, str],
        template_content: str,
        next_plot_content: str = "",
        prev_plot_content: str = "",
    ) -> str:
        """构建 /new_volume 工作流节点的完整上下文

        Args:
            doc_name: 当前要更新的文档名称
            doc_path: 文档路径
            volume_num: 当前卷编号
            all_docs: 所有记忆文档内容
            template_content: 模板文件的完整内容
            next_plot_content: 下一卷剧情内容（用于本卷核心剧情的参考）
            prev_plot_content: 前一卷核心剧情内容（用于前卷剧情概述的参考）
        """
        doc_guide = cls.get_doc_prompt(doc_name)
        memory_context = cls.build_memory_context(all_docs)

        # 特定文档的特殊参考内容
        special_ref = ""
        if doc_name == "前卷剧情概述":
            special_ref = f"""
{'='*60}
特殊参考：前卷核心剧情内容（用于编写前卷剧情概述）
{'='*60}
{prev_plot_content if prev_plot_content else '（无前卷核心剧情内容）'}
"""
        elif doc_name == "本卷核心剧情":
            special_ref = f"""
{'='*60}
特殊参考：下一卷剧情设计（用于设计本卷剧情）
{'='*60}
{next_plot_content if next_plot_content else '（无下一卷剧情内容）'}
"""

        # ========== 构建完整上下文 ==========
        return f"""小说创作 - 新卷创作
当前任务：更新第 {volume_num} 卷相关文档

{'='*60}
一、所有记忆文档内容
{'='*60}
{memory_context}

{'='*60}
二、当前要更新的文档
{'='*60}
  文档名称：{doc_name}
  保存路径：{doc_path}

{'='*60}
三、文档更新规则
{'='*60}
{doc_guide}
{special_ref}

{'='*60}
四、模板文档格式框架（填写时必须保留此格式框架）
{'='*60}
{template_content}

{'='*60}
五、重要要求
{'='*60}
1. 严格按照模板格式填写内容，不要改变格式框架
2. 文档头部的元数据（文档类型、内容说明、更新逻辑、更新要求）必须原样保留
3. 保持 markdown 格式和标题层级
4. 通过 file_write 工具将内容写入对应保存路径，不要在对话中输出文档全文
5. 不要添加额外说明

请开始更新文档内容："""


# =============================================================================
# /chapter 章节创作工作流提示词
# =============================================================================

class ChapterPrompts:
    """/chapter 章节创作工作流 - 章节生成与记忆文档更新提示词

    工作流节点：
      1. 生成章节内容
      2~9. 逐个更新 8 个记忆文档
    """

    # 记忆文档更新顺序
    MEMORY_DOCS_ORDER = [
        ("世界观", "workspace/世界观.md"),
        ("主角形象状态", "workspace/主角形象状态.md"),
        ("本卷核心剧情", "workspace/剧情/本卷核心剧情.md"),
        ("下一卷剧情", "workspace/剧情/下一卷剧情.md"),
        ("伏笔", "workspace/剧情/伏笔.md"),
        ("有关角色", "workspace/剧情/有关角色.md"),
        ("事件", "workspace/剧情/事件.md"),
        ("当前章节设计规划", "workspace/剧情/当前章节设计规划.md"),
    ]

    @staticmethod
    def build_chapter_prompt(
        volume_num: int,
        chapter_num: int,
        chapter_plan: str,
        recent_chapters_context: str,
        all_docs: Dict[str, str],
        save_dir: str = "",
    ) -> str:
        """构建章节生成提示词

        Args:
            volume_num: 当前卷号
            chapter_num: 当前章节号
            chapter_plan: 当前章节规划内容
            recent_chapters_context: 近3章的自动读取内容（由工作流自动构建）
            all_docs: 所有记忆文档内容
            save_dir: 章节文件保存目录的绝对路径
        """
        plan_text = chapter_plan if chapter_plan else '(暂无章节规划)'

        save_path_hint = f"{save_dir}/第{chapter_num}章 - 章节名称.md" if save_dir else ""

        # ========== 构建完整上下文 ==========
        return f"""小说创作 - 章节创作
当前任务：创作第 {volume_num} 卷 第 {chapter_num} 章

{'='*60}
一、当前章节规划
{'='*60}
{plan_text}

{'='*60}
二、近3章创作窗口
{'='*60}
{recent_chapters_context}

{'='*60}
三、所有记忆文档内容
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

{'='*60}

四、工作目录：
{save_dir}
五、其他要求
不用更新其他的相关记忆文档
六、创作要求
{'='*60}

1. 根据章节规划和记忆文档，创作本章内容
2. 注意与近3章剧情的衔接，保持连贯性
3. 保持世界观，角色设定，剧情的一致性
4. 可以选择性的埋下伏笔
5. 使用tool_use类型，通过 file_write 工具将章节内容写入指定路径，{save_path_hint}
6. 文件名格式：第{chapter_num}章 - 章节名称.md

请开始创作第 {volume_num} 卷 第 {chapter_num} 章："""

    @staticmethod
    def build_memory_doc_prompt(
        doc_name: str,
        doc_path: str,
        chapter_content: str,
        all_docs: Dict[str, str],
    ) -> str:
        """构建记忆文档更新提示词

        Args:
            doc_name: 当前要更新的文档名称
            doc_path: 文档路径
            chapter_content: 刚创作的章节内容
            all_docs: 所有记忆文档内容（包含当前文档）
        """
        chapter_text = chapter_content if chapter_content else '(无)'

        # 构建排除当前文档的所有记忆文档上下文
        other_docs_context = ""
        for key, value in all_docs.items():
            if key != doc_path:
                doc_title = key.split('/')[-1]
                other_docs_context += f"[{doc_title}]\n{value}\n\n"

        # ========== 构建完整上下文 ==========
        return f"""小说创作 - 记忆文档更新
当前任务：更新 "{doc_name}"
保存路径：{doc_path}

{'='*60}
一、刚创作的章节内容（这是更新记忆文档的主要依据）
{'='*60}
{chapter_text}

{'='*60}
二、其他记忆文档（供参考，确保更新后的一致性）
{'='*60}
{other_docs_context if other_docs_context else '（无其他记忆文档）'}

{'='*60}
三、重要要求
{'='*60}
1. 首先使用 file_read 工具读取要更新的原文档（{doc_path}），了解当前内容
2. 以"刚创作的章节内容"为主要依据，提取其中信息来更新记忆文档
3. 对照"其他记忆文档"，确保更新后各文档之间逻辑剧情保持一致性
4. 保持原文档的格式结构不变
5. 要求文档内容简洁，逻辑结构清晰。保留对于未来剧情发展、保证剧情一致性有关作用的内容，以及小说固定的设定
5. 使用tool_use类型，通过 file_write 工具，将更新后的完整内容写入文档，{doc_path}
6. 如果章节内容没有涉及需要更新的内容，则使用 file_write 写入原内容不变
7. 更新完指定文档之后直接结束
请开始操作："""

    # ---- 审核节点 ----
    # 审核维度定义
    REVIEW_DIMENSIONS = [
        ("剧情一致性", 0.30, "审核内容是否与记忆文档中的剧情设计、世界观设定保持一致"),
        ("文笔流畅度", 0.25, "审核文字表达是否流畅自然，有无语法错误、表达生硬等问题"),
        ("人设吻合度", 0.25, "审核角色行为、对话是否符合已设定的人物性格和身份"),
        ("原创性", 0.20, "审核内容是否存在抄袭、过度模仿等问题"),
    ]

    MAX_REVISION_ROUNDS = 3

    # 审核提示词模板
    REVIEW_TEMPLATE = """你是一位资深小说编辑，负责审核小说章节内容。

重要：直接输出审核结果文本，不要输出思考分析过程。

## 审核内容
{chapter_content}

## 记忆文档上下文
{memory_context}

## 审核维度及权重
{dimensions}

## 输出格式

## 审核结果

### 剧情一致性
- 得分: X/10
- 问题（如有）: [具体问题描述]
- 建议: [改进建议]

### 文笔流畅度
- 得分: X/10
- 问题（如有）: [具体问题描述]
- 建议: [改进建议]

### 人设吻合度
- 得分: X/10
- 问题（如有）: [具体问题描述]
- 建议: [改进建议]

### 原创性
- 得分: X/10
- 问题（如有）: [具体问题描述]
- 建议: [改进建议]

## 综合评分
综合得分: X/10

## 是否通过
- 通过: 是/否
- 返工原因: [如果未通过，说明主要原因]

## 改进方向
[如果未通过，给出具体的改进方向]

注意：请直接输出审核结果，不要在内部思考中分析。评分要客观公正。"""

    @classmethod
    def build_review_context(cls, chapter_content: str, memory_context: str) -> str:
        """构建审核节点的完整上下文

        Args:
            chapter_content: 要审核的章节内容
            memory_context: 记忆文档上下文

        Returns:
            完整的审核提示词
        """
        dimensions_text = "\n".join(
            f"- {d[0]}（权重 {d[1]}）: {d[2]}"
            for d in cls.REVIEW_DIMENSIONS
        )

        return cls.REVIEW_TEMPLATE.format(
            chapter_content=chapter_content,
            memory_context=memory_context,
            dimensions=dimensions_text,
        )

    REVISION_TEMPLATE = """你是一位小说作者，请根据审核反馈修改章节内容。

## 审核反馈
{review_result}

## 记忆文档上下文
{memory_context}

## 操作步骤

1. 使用 file_read 工具读取原章节文件：{chapter_path}
2. 根据审核反馈，使用 file_write 工具将修改后的内容写入原文件路径（{chapter_path}）
3. 不要使用文本类型输出修改后的章节全文（用 file_write 写入章节内容到对应目录下的文档）

请开始操作："""

    @classmethod
    def build_revision_prompt(
        cls,
        chapter_path: str,
        review_result: dict,
        all_docs: dict,
    ) -> str:
        """构建修订节点的完整上下文

        Args:
            chapter_path: 章节文件的绝对路径
            review_result: 审核结果字典
            all_docs: 所有记忆文档内容

        Returns:
            完整的修订提示词
        """
        # 格式化审核结果
        scores = review_result.get("scores", {})
        passed = review_result.get("passed", False)
        reason = review_result.get("reason", "")
        improvement = review_result.get("improvement", "")
        raw = review_result.get("raw_result", "")

        review_lines = [f"综合得分: {scores.get('综合评分', '?')}/10"]
        review_lines.append(f"通过: {'是' if passed else '否'}")
        if reason:
            review_lines.append(f"返工原因: {reason}")
        review_lines.append("")
        for dim_name, _, _ in cls.REVIEW_DIMENSIONS:
            if dim_name in scores:
                review_lines.append(f"  {dim_name}: {scores[dim_name]}/10")
        if improvement:
            review_lines.append(f"\n改进方向:\n{improvement}")
        review_lines.append(f"\n审核详情:\n{raw[:3000]}")

        review_text = "\n".join(review_lines)

        # 构建记忆文档上下文摘要
        doc_lines = []
        for key, value in all_docs.items():
            label = key.split("/")[-1]
            doc_lines.append(f"[{label}]\n{value if value else '(空)'}")
        memory_text = "\n\n".join(doc_lines)

        return cls.REVISION_TEMPLATE.format(
            chapter_path=chapter_path,
            review_result=review_text,
            memory_context=memory_text,
        )


# =============================================================================
# 导出
# =============================================================================

__all__ = [
    "InitPrompts",
    "NewVolumePrompts",
    "ChapterPrompts",
]
