"""Agent 模块测试"""

import pytest
from novel_agent import NovelAgent, AgentEvents, Config, create_agent, RISKY_TOOLS


class TestConfig:
    """配置测试"""

    def test_config_creation(self):
        """测试配置创建"""
        config = Config(
            api_key="test-key",
            base_url="https://test.com",
            model_name="TestModel",
            working_dir="/tmp",
        )
        assert config.api_key == "test-key"
        assert config.model_name == "TestModel"


class TestAgentEvents:
    """Agent 事件类型测试"""

    def test_event_types(self):
        """测试事件类型常量"""
        assert AgentEvents.THINKING == "thinking"
        assert AgentEvents.CONTENT == "content"
        assert AgentEvents.TOOL_CALL == "tool_call"
        assert AgentEvents.TOOL_RESULT == "tool_result"


class TestRiskyTools:
    """危险工具测试"""

    def test_risky_tools(self):
        assert "file_write" in RISKY_TOOLS
        assert "file_read" not in RISKY_TOOLS
