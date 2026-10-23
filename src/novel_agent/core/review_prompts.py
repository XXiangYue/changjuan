"""旧文本审核格式的兼容解析；分数始终由程序按四维权重计算。"""
import re
WEIGHTS={"剧情一致性":.30,"文笔流畅度":.25,"人设吻合度":.25,"原创性":.20}
def parse_review_result(result:str)->dict:
    normalized=result.replace("：",":")
    scores={}
    for name in WEIGHTS:
        match=re.search(r"###\s*"+re.escape(name)+r"\s*\n(.*?)(?=\n##|\Z)",normalized,re.S)
        if match:
            value=re.search(r"得分:\s*([0-9]+(?:\.[0-9]+)?)\s*/\s*10",match.group(1))
            if value and 0<=float(value.group(1))<=10:scores[name]=float(value.group(1))
    valid=set(scores)==set(WEIGHTS)
    total=round(sum(scores.get(k,0)*v for k,v in WEIGHTS.items()),2)
    scores["综合评分"]=total if valid else 0
    reason=re.search(r"返工原因:\s*([^\n]+)",normalized)
    improvement=re.search(r"##\s*改进方向\s*\n(.*)",normalized,re.S)
    return {"scores":scores,"passed":valid and total>=7,"reason":reason.group(1) if reason else "" if valid else "四维评分缺失或超出范围","improvement":improvement.group(1).strip() if improvement else "","raw_result":result}
