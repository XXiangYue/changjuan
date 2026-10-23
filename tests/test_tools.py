"""Skills 模块测试"""

import pytest
from pathlib import Path

from novel_agent.skills import SkillLoader, create_skill_loader, RISKY_TOOLS


class TestSkillLoader:
    """SkillLoader 测试"""

    def test_load_skills(self):
        """测试加载 skills"""
        loader = create_skill_loader(Path(__file__).parent.parent / "src" / "novel_agent" / "skills")
        tools = loader.get_tools()
        tool_names = [t.name for t in tools]

        assert "file_read" in tool_names
        assert "file_write" in tool_names
        assert "list_directory" in tool_names
        assert "get_working_directory" in tool_names


class TestRiskyTools:
    """危险工具测试"""

    def test_risky_tools(self):
        """测试危险工具识别"""
        assert "file_write" in RISKY_TOOLS

    def test_safe_tools(self):
        """测试安全工具"""
        assert "file_read" not in RISKY_TOOLS
        assert "list_directory" not in RISKY_TOOLS
        assert "get_working_directory" not in RISKY_TOOLS
