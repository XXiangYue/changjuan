"""事件类型定义"""


class AgentEvents:
    """Agent 事件类型"""
    CONTENT = "content"           # 内容输出
    THINKING = "thinking"         # 思考过程
    TOOL_CALL = "tool_call"       # 工具调用
    TOOL_RESULT = "tool_result"  # 工具结果
    ERROR = "error"               # 错误
    WARNING = "warning"           # 警告
    INFO = "info"                 # 信息
