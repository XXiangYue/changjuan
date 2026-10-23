"""小说创作 Agent 系统"""

# 核心模块
from .agent import NovelAgent, create_agent, AgentEvents
from .risk import create_confirm_handler
from .utils.config import Config, load_config

# 工作流模块
from .workflows import BaseWorkflow, InitWorkflow, NewVolumeWorkflow, ChapterWorkflow

# UI 模块
from .ui import TerminalInterface

# 工具模块
from .skills import SkillLoader, create_skill_loader, RISKY_TOOLS

__version__ = "1.0.0"

__all__ = [
    # 核心
    "NovelAgent",
    "create_agent",
    "AgentEvents",
    "create_confirm_handler",
    "Config",
    "load_config",
    # 工作流
    "BaseWorkflow",
    "InitWorkflow",
    "NewVolumeWorkflow",
    "ChapterWorkflow",
    # UI
    "TerminalInterface",
    # 工具
    "SkillLoader",
    "create_skill_loader",
    "RISKY_TOOLS",
]
