"""Author-visible character contract; never truncate a generated manuscript."""

import math


def bounds(plan):
    target = plan["target_words"]
    minimum = plan.get("min_characters")
    maximum = plan.get("max_characters")
    return (
        math.ceil(target * 0.8) if minimum is None else minimum,
        math.floor(target * 1.2) if maximum is None else maximum,
    )


def check_length(body, plan):
    minimum, maximum = bounds(plan)
    return {
        "characters": len(body),
        "target": plan["target_words"],
        "minimum": minimum,
        "maximum": maximum,
        "unit": "Unicode characters including punctuation and newlines",
        "within_range": minimum <= len(body) <= maximum,
    }


def writing_contract(plan, current_body=""):
    minimum, maximum = bounds(plan)
    target = plan["target_words"]
    scenes = max(1, len(plan.get("scenes", [])))
    per_scene = max(1, (target - 100) // scenes)
    contract = f"\n篇幅合同：正文{minimum}—{maximum}个Unicode字符，包含标点、空格和换行，不含标题。目标{target}；约{scenes}个场景，每场约{per_scene}字符，转场合计约100字符。用动作完成因果和选择，删重复解释、同种痛感和无变化的对白；不要用梗概代替场景，也不要输出字数说明。"
    if current_body and len(current_body) > maximum:
        contract += f"\n原稿实测{len(current_body)}字符，需要实质压缩到约{target}字符，至少删除{len(current_body) - target}字符，不能只换词。先合并重复对话和感受，保留本章必要动作、见证和人物选择。不得为省字把实际解除/恢复等关键动作改成计划或总结。"
    elif current_body and len(current_body) < minimum:
        contract += f"\n原稿实测{len(current_body)}字符，需补足约{target - len(current_body)}字符；只扩写改变人物处境的动作和对话。"
    return contract
