"""章节内容审核器"""

from typing import Callable, Optional

from ..core import (
    AgentEvents,
    ChapterPrompts,
    parse_review_result,
)


class ChapterReviewer:
    """章节内容审核器"""

    def __init__(self, agent, output_callback: Callable[[str], None]):
        self.agent = agent
        self.output_callback = output_callback
        self.revision_count = 0

    def output(self, text: str):
        """输出文本"""
        self.output_callback(text)

    def review(self, chapter_content: str, memory_context: str) -> dict:
        """
        审核章节内容

        Args:
            chapter_content: 要审核的章节内容
            memory_context: 记忆文档上下文

        Returns:
            审核结果字典，包含 passed、scores、reason、improvement
        """
        if not chapter_content.strip():
            return {"passed": False, "scores": {}, "reason": "空章节不能通过审核", "improvement": "先写入正文", "raw_result": ""}
        self.revision_count += 1

        prompt = ChapterPrompts.build_review_context(chapter_content, memory_context)

        dims_display = "\n".join(
            f"  - {d[0]}（{int(d[1]*100)}%）"
            for d in ChapterPrompts.REVIEW_DIMENSIONS
        )
        self.output(f"""
============================================================
  内容审核
============================================================

审核维度：
{dims_display}

[正在进行审核...]
""")

        # 流式获取审核结果
        model_output = []
        has_content = False
        for event in self.agent.stream_response(prompt):
            if event.get("type") in (AgentEvents.CONTENT, AgentEvents.THINKING):
                text = event.get("content", "")
                if text.strip():
                    has_content = True
                model_output.append(text)
                self.output(text)
            elif event.get("type") == AgentEvents.TOOL_CALL:
                name = event.get("name", "unknown")
                inp = event.get("input", {})
                if name == "file_read":
                    path = inp.get("path", inp.get("file_path", ""))
                    self.output(f"\n[调用工具: {name}  {path}]")
                else:
                    self.output(f"\n[调用工具: {name}]")
            elif event.get("type") == AgentEvents.TOOL_RESULT:
                name = event.get("name", "unknown")
                result = str(event.get("content", ""))
                snippet = result[:200].replace("\n", " ") if result else ""
                self.output(f"\n[{name} 完成]: {snippet}")

        result_text = "".join(model_output)

        # 模型没有输出时提示
        if not has_content and not result_text.strip():
            self.output("\n  [模型未生成审核内容，审核失败]")

        review_result = parse_review_result(result_text)

        # 显示审核结果
        self._print_review_result(review_result)

        return review_result

    def _print_review_result(self, result: dict):
        """打印审核结果"""
        scores = result.get("scores", {})
        passed = result.get("passed", False)
        raw_text = result.get("raw_result", "")

        self.output("""
============================================================
  审核结果
============================================================
""")

        # 显示各维度分数
        has_scores = False
        for dim_name, _, _ in ChapterPrompts.REVIEW_DIMENSIONS:
            if dim_name in scores:
                self.output(f"  {dim_name}: {scores[dim_name]}/10")
                has_scores = True

        total = scores.get("综合评分", scores.get("总得分", 0))

        # 如果解析失败（分数为0或空），显示模型原始输出
        if not has_scores or total == 0:
            if raw_text.strip():
                self.output(f"\n[模型原始输出]")
                self.output(f"\n{raw_text.strip()[:2000]}\n")
            else:
                self.output(f"\n  [模型未输出审核内容]\n")
            total = 0
            passed = False
        self.output(f"""
  综合评分: {total}/10
  通过状态: {'✓ 通过' if passed else '✗ 未通过'}
""")

        if not passed:
            if result.get("reason"):
                self.output(f"  返工原因: {result['reason']}")
            self.output(f"""
============================================================
  当前返工轮次: {self.revision_count}/{ChapterPrompts.MAX_REVISION_ROUNDS}
============================================================
""")

            if self.revision_count >= ChapterPrompts.MAX_REVISION_ROUNDS:
                self.output("""
  [警告] 已达到最大返工轮次限制
  将继续保存当前版本

""")
        else:
            self.output("")

    def reset(self):
        """重置审核器"""
        self.revision_count = 0

    @property
    def should_continue_revision(self) -> bool:
        """是否应该继续返工"""
        return self.revision_count < ChapterPrompts.MAX_REVISION_ROUNDS
