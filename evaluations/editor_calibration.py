"""Development calibration, separate from frozen acceptance probes."""
import asyncio,json
from pathlib import Path
from novel_agent.studio.evaluation import infrastructure
from novel_agent.studio.models import ProjectCreate,ReviewReport,numbered_paragraphs,ground_citations
from novel_agent.studio.llm import ModelClient
from novel_agent.studio.db import uid,encode,now
from langchain_core.messages import SystemMessage,HumanMessage

CASES=[
 {'name':'计划与履行及半枚守恒','canon':'上一章上午，沈砚把唯一一枚丹交给洛蘅。洛蘅说今晚出门前服自己的半枚，把另半枚留给沈砚。此时仍在当日午前，今晚尚未到来。','body':'午饭前洛蘅回来，打开瓶子，只留下一片半月形的丹。“刚才咳得紧，我把自己的半枚提前服了。你这半枚没动。”她把瓶口重新抹上蜡，收进箱里。','violation':False},
 {'name':'唯一药物已经耗尽','canon':'上一章唯一一枚温肺丹已交给洛蘅并整枚服完。沈砚没有药，也没有购入或获赠其他药。','body':'沈砚从原来空着的衣袋里取出另一枚温肺丹，当即服下。此间无人赠药，他也没去过药铺。肺伤立刻痊愈。','violation':True},
 {'name':'先悬念后解释不等于知情泄漏','canon':'沈砚昨日在药铺借丹，药师把这事记入私簿。沈砚目前不知道官吏昨夜查过私簿。','body':'沈砚看见官册上多了一行“温肺丹，借”，愣住了。他不知道这句话怎么进了官册。\n\n过了一会儿，楚衡说：“昨夜差役翻了药铺私簿，照抄了这行。”沈砚这才明白，药师的账也被查了。','violation':False},
]

async def main():
 s,db,repo,retrieval=infrastructure();p=repo.create(ProjectCreate(title='编辑校准 · 非样书',premise='仅用于开发中校准误报和漏报，不计入冻结验收样本。',target_chapters=1,chapter_words=600));db.execute('UPDATE projects SET archived=1 WHERE id=?',(p['id'],));eid=uid();cfg={'stage':'开发校准，不能作为独立验收','model':'deepseek-flash','protocol':'同一JSON模式、同一案例与评分schema，仅切换思考模式；每格一次','cases':CASES};db.execute('INSERT INTO experiments VALUES(?,?,?,?,?,?)',(eid,'编辑推理校准 v1',encode(cfg),'{}','running',now()));client=ModelClient(db,s,retrieval);results=[];sem=asyncio.Semaphore(3)
 async def run(case,thinking):
  async with sem:
   rid=uid();db.execute('INSERT INTO runs(id,project_id,kind,config,status,created,updated) VALUES(?,?,?,?,?,?,?)',(rid,p['id'],'evaluation',encode({'case':case['name'],'thinking':thinking}),'evaluating',now(),now()));system='审核中文小说，区分已经发生的事实、角色尚未履行的计划和当前章稍后才解释的悬念。未知不等于矛盾，不能把风格建议当严重错误。仅在与给定事实真正冲突时列critical。可以没有问题。四个维度均给0–10分，分数key精确匹配schema。只返回JSON，不输出思考。每个finding填写正文paragraph编号，quote可空。schema:'+encode(ReviewReport.model_json_schema());prompt=encode({'canon':case['canon'],'chapter':numbered_paragraphs(case['body'])});db.event(rid,'model.input',{'system':system,'prompt':prompt,'thinking':thinking});model,meter=client.model(rid,'编辑校准·'+('思考'if thinking else'直接'),max_tokens=10000,json_mode=True);model=model.bind(extra_body={'thinking':{'type':'enabled' if thinking else'disabled'}},reasoning_effort='high' if thinking else'none');item={'case':case['name'],'thinking':thinking,'run_id':rid,'expected_violation':case['violation']}
   try:
    raw=await model.ainvoke([SystemMessage(system),HumanMessage(prompt)]);v=ReviewReport.model_validate_json(raw.content);ground_citations(v.findings,case['body']);detected=any(f.severity=='critical' for f in v.findings);item.update(report=v.model_dump(),detected=detected,correct=detected==case['violation']);db.event(rid,'model.output',{'text':raw.content,'role':'编辑校准'});db.execute("UPDATE runs SET status='completed',result=?,updated=? WHERE id=?",(encode(item),now(),rid))
   except Exception as e:
    item['error']=type(e).__name__+': '+str(e)[:400];db.execute("UPDATE runs SET status='failed',error=?,updated=? WHERE id=?",(item['error'],now(),rid))
   results.append(item);print(case['name'],thinking,item.get('correct',item.get('error')),flush=True)
 await asyncio.gather(*(run(case,thinking) for case in CASES for thinking in (False,True)))
 result={'summary':'三组有确定逻辑答案的开发校准，只有每格一次；不并入96个固定机制试次。','metrics':{'非思考判对':sum(x.get('correct',False) for x in results if not x['thinking']),'思考判对':sum(x.get('correct',False) for x in results if x['thinking']),'每配置用例':3},'trials':results,'limitations':['开发校准样本，不是独立验收或统计优势证据','只反映一次API采样；两个模式均可能误报或漏报']};db.execute('UPDATE experiments SET result=?,status=? WHERE id=?',(encode(result),'completed',eid));Path('artifacts/evaluation/editor-calibration.json').write_text(json.dumps({'config':cfg,'result':result},ensure_ascii=False,indent=2))

asyncio.run(main())
