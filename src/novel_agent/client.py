"""API 客户端模块"""

from langchain_anthropic import ChatAnthropic

from .utils.config import Config


class APIClient:
    """API 客户端封装"""

    def __init__(self, config: Config):
        self.config = config
        self._client: ChatAnthropic = None

    @property
    def client(self) -> ChatAnthropic:
        """懒加载获取客户端"""
        if self._client is None:
            self._client = ChatAnthropic(
                model=self.config.model_name,
                anthropic_api_key=self.config.api_key,
                base_url=self.config.base_url,
                max_tokens=self.config.max_tokens,
            )
        return self._client

    def bind_tools(self, tools: list) -> ChatAnthropic:
        """绑定工具并返回新的客户端实例"""
        return self.client.bind_tools(tools)


def create_client(config: Config) -> APIClient:
    """创建 API 客户端"""
    return APIClient(config)
