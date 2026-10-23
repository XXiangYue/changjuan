"""Freeze retrieval questions from 24 delivered canonical chapters, without retrieval.

Questions were selected by chapter reading. Source offsets and all overlapping
gold chunks are derived from the quoted canonical text and validated exactly.
"""

from __future__ import annotations

import hashlib
import json
import random
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evaluations" / "retrieval-novel-v1"
PROJECTS = {
    "full": "9730fab365eb44d3985896a64253fd7f",
    "baseline": "815825904710442c9e549e83f140c725",
}
FACTS = []


def f(book, chapter, label, direct, paraphrase, quote, answer, basis):
    FACTS.append(
        {
            "book": book,
            "chapter": chapter,
            "label": label,
            "direct": direct,
            "paraphrase": paraphrase,
            "quote": quote,
            "answer": answer,
            "basis": basis,
        }
    )


f(
    "full",
    1,
    "订单条件",
    "镇西杂货铺的铜扣订单约定几日交货，价格比平时高多少？",
    "沈砚接的那批扣门铜活，交期和加价幅度各是多少？",
    "镇西杂货铺缺一批扣门的铜扣，三日交货，价比平时多一成半。",
    "三日交货，价格高一成半。",
    "原句直接记述已经接下的订单条件；不把约定交期视作交货已完成。",
)
f(
    "full",
    1,
    "初次封灰",
    "沈砚第一次将红灰带回铺子后，用什么容器封存，压在何处？",
    "刚从炉房取回的那撮异色粉末，最初被藏在哪个器物下，装在什么里面？",
    "他把灰收进小陶罐，蜡封罐口，压进旧砧底下",
    "装在蜡封小陶罐，压在旧砧底下。",
    "这是首章已完成的收存动作；查询时点截止首章，不引用次章迁藏位置。",
)
f(
    "full",
    2,
    "赎押期限",
    "楚衡立押砧字据时，三日宽限是否计今日，钱到牌赎截止何时？",
    "最初押下谋生砧时，镇守给的宽限从哪天起算，最后等到哪一刻？",
    "三日宽限，今日不算，第三日日落前钱到牌赎。",
    "今日不算，第三日日落前。",
    "询问已经明确写出的初次条款；不把后文缩期带回此章。",
)
f(
    "full",
    2,
    "陶罐迁藏",
    "抬砧前沈砚把蜡封陶罐和拓号纸从哪里取出，又塞到哪里？",
    "旧砧将被押走之前，那份封着的灰样及拓纸改藏在铺子什么位置？",
    "砖缝里抠出那只蜡封陶罐和拓号纸，一并塞进灶膛后头松动的砖里",
    "从砖缝取出，改藏灶膛后头松砖内。",
    "原句含取出与重新存放两个已完成动作，不需推断持有人。",
)
f(
    "full",
    3,
    "铜铃锁箱",
    "辨听铜铃后，洛蘅如何包好铜铃并收在什么地方？",
    "沈砚右手发麻后，那件断舌铜器由同伴怎样妥善保存？",
    "洛蘅点头，拿布巾把铜铃裹了，收进铁箱内层，按紧搭扣。",
    "洛蘅用布巾包裹，放铁箱内层并扣紧。",
    "动作直接完成，不仅是口头约定；段落中的主语与物品明确。",
)
f(
    "full",
    3,
    "封条骑缝日",
    "沈砚亲自挑开旧窑封条卷边后，看见骑缝上写的哪一天？",
    "到镇北窑口查封纸时，歪红戳旁清楚露出的日期是多少？",
    "骑缝处红戳盖得歪，“初九”两字清清楚楚。",
    "初九。",
    "来自实地看见的封条字样，不依赖先前茶摊转述。",
)
f(
    "full",
    4,
    "同门自号",
    "洛蘅认出窑壁刻字后，说同门给自己起的号是什么？",
    "修炉女子辨认墙上的崔字后，将那位失踪同门的自称说成什么？",
    "过了两息才道：“崔炉。他给自己起的号。”",
    "崔炉。",
    "承接洛蘅辨认刻字的叙述，直接引出自号；不从刀痕推论铸铃者。",
)
f(
    "full",
    4,
    "断管收存",
    "楚衡指定由哪个机构留存旧窑挖出的断铁管？",
    "镇丁将窑底金属残管裹好后，镇守说这件证物归哪里保管？",
    "楚衡看他两息，把铁管交给镇丁裹进麻布：“衙署留着。”",
    "衙署。",
    "引文同时包括下令者、断管与明确收存机构；不将携带者等同所有者。",
)
f(
    "full",
    5,
    "三指失感",
    "沈砚清晨试手时，哪三根右手手指重捏后才有闷压感？",
    "铺主测试伤手后，感觉迟钝的中间三个指头具体是哪几个？",
    "食指、中指、无名指捏重了才有闷闷的压感",
    "右手食指、中指、无名指。",
    "清晨测试的叙述逐一列出手指；不推断永久残疾或医学病因。",
)
f(
    "full",
    5,
    "砧钉铭字",
    "沈砚拿去当铺的四枚砧钉，钉帽上的字合起来是什么？",
    "那四颗被拿去换钱的旧钉，合读四个帽面刻字得到哪句话？",
    "钉帽上各刻一字，合起来是“沈记老作”。",
    "沈记老作。",
    "这是当铺交易前直接可见的铭刻，不由印边相似推论来历。",
)
f(
    "full",
    6,
    "购丹拆款",
    "沈砚准备付温肺丹价时，解开合用的是原先哪两份钱？",
    "为了给同伴买那枚护肺药，铺主挪用了本来分给哪两项支出的款？",
    "他把赎牌那份连同预留料钱解开，合起来够付丹价",
    "赎牌的钱和预留材料钱。",
    "正文明确拆解两份资金，随后排钱付款；不从购买反推原始丹价。",
)
f(
    "full",
    6,
    "整丹随人",
    "买下的一整枚温肺丹在本章末由谁带走？",
    "离开铺子时，刚购得的护肺丸药整颗实际随哪位人物保管？",
    "温肺丹整枚由洛蘅带走，半枚今夜归她，半枚明早带上炉。",
    "洛蘅带走整枚。",
    "区分已经完成的带走与随后半枚用途安排；不声称本章已服药。",
)
f(
    "full",
    7,
    "提前服丹",
    "洛蘅因锉灰咳得紧，提前服了多少温肺丹？",
    "原来留到夜里吃的护肺药，修炉女子在初审前已经用了多大一份？",
    "说好夜里服，刚才锉灰咳得紧，我提前服了半枚。",
    "半枚。",
    "洛蘅回铺后亲口说明已服的数量，后文瓶中余量相合。",
)
f(
    "full",
    7,
    "灰样实际称分",
    "宋文书验封后，全部红灰实际怎样称分和封存？",
    "作为换取推荐格的一步，那罐样品开封后被分成几份、重量如何处理？",
    "楚衡才开罐，将全部红灰称成等重两份，各自封好",
    "称成等重两份，各自封好。",
    "取已经执行的称分句，不将沈砚先前提出的交易条件当执行证据。",
)
f(
    "full",
    8,
    "誊本增行",
    "沈记誊本比副本多出的两行，分别写了什么？",
    "灯下核对那份将上户主会的抄账时，额外添上的两个条目是什么？",
    "一行“另，崔炉一件，待勘”，墨色发淡，是另一只手添的；再一行压在页角，险些被裁口切掉，“温肺丹，借”。",
    "另，崔炉一件，待勘；温肺丹，借。",
    "完整引文直接列出两个增行，不由笔迹推断添加者姓名。",
)
f(
    "full",
    8,
    "清点四件",
    "小满带回的已画圈清单，实际清点了哪四件东西？",
    "会前到封铺看件数之后，跑腿少年报回的器具和封罐有哪些、各几件？",
    "清完了，四样，火钳、两块砧垫铁、一只蜡封罐。",
    "火钳一件、砧垫铁两块、蜡封罐一只。",
    "清点结果直接列件数；蜡封没有拆，不能据此推断罐内数量。",
)
f(
    "full",
    9,
    "公堂还页",
    "户主会上楚衡当众启封的两张纸，分别是哪两类账页？",
    "当夜核对暂扣纸张时，镇守摊开的那一对来源不同的页面是什么？",
    "一张旧账本抽出页，一张誊本页，各按着沈砚的手印",
    "旧账本抽出页与誊本页。",
    "启封还页的现场已发生，两个页面类型有明确并列原句。",
)
f(
    "full",
    9,
    "摸账逃向",
    "小满说散会摸走抄账纸的人往哪条巷子跑？",
    "趁户主会混乱偷页的人，按追出去少年的回报，逃向镇里哪个方向的巷道？",
    "砚哥，那人往北巷跑了，我认不清脸，只看见袖口露一截纸边，是抄账的那种格眼纸。",
    "北巷。",
    "只问小满已回报的逃跑方向；原句明确脸没认清，不推定犯人身份。",
)
f(
    "full",
    10,
    "铜楔刮臂",
    "洛蘅从矿道退出来时，什么东西刮伤了她的小臂？",
    "修炉女子从塌顶处退出借力时，手臂的伤是被哪件工具的哪部分划出的？",
    "退时掰那根铜楔借力，楔头刮了小臂。",
    "借力铜楔的楔头。",
    "洛蘅叙述已发生的受伤动作，不把后续落石肩伤混入此问。",
)
f(
    "full",
    10,
    "听铃新代价",
    "临时借铃听息后，沈砚左手哪里新出现了多大范围的发木？",
    "窑前那次比对火路结束时，除旧有右手伤外，另一只手新增的感觉损失落在哪一块？",
    "手离铃时左掌根也木了半圈，右手三指仍旧无知觉",
    "左掌根木了半圈。",
    "将新增左掌根发木与原有右手三指伤区分；不推论伤势不可逆。",
)
f(
    "full",
    11,
    "提前开铃扣俸",
    "宋文书因楚衡提前开铃，定下扣多久俸银、抵什么亏空？",
    "擅动封存铜器的责任清算中，镇守被要求拿哪一段俸禄补哪笔欠缺？",
    "主事者以职俸作保，扣一年俸银，抵灶户火租亏空。",
    "扣一年俸银，抵灶户火租亏空。",
    "问当堂宣布且楚衡认领的处理条款，不虚构之后实际结算账目。",
)
f(
    "full",
    11,
    "试火耳伤",
    "两回试火后，沈砚耳朵哪里出现了出血迹象？",
    "修炉前夜新增的听觉痛楚之后，棉絮上那点血来自哪个部位？",
    "右耳忽然像有人拿细铜丝从耳道往外扯，一丝一丝地疼。抬手按，指腹蹭到耳垂底下湿了一线，就灯下一照，棉絮上洇出针尖大一点红。",
    "右耳耳垂底下出现血迹，棉絮上有针尖大红点。",
    "痛楚、耳垂湿线和红点连续发生；仅描述文本可见代价，不作医学诊断。",
)
f(
    "full",
    12,
    "半丹用尽",
    "旧账本末页记下剩半枚温肺丹由谁、何时服用，是否还有余药？",
    "修炉撑不住后，那份最后的护肺药实际落到谁口中，余量又记成多少？",
    "半枚，今日清晨，沈砚服，没剩。",
    "沈砚今日清晨服了半枚，没剩。",
    "服药动作已经发生并当场记账，不把此前留存安排当作服药证据。",
)
f(
    "full",
    12,
    "碎铃权属",
    "宋文书记下铜铃两瓣残片仍归谁，衙署暂存哪一份？",
    "引火毁掉铃后，公堂记录如何区分碎片的所有者与临时保管去向？",
    "差役拾残片，两瓣权属仍归沈记。衙署暂存一瓣对字据，核毕送回，另一瓣还铺。",
    "两瓣权属仍归沈记；衙署暂存一瓣核对字据，另一瓣还铺。",
    "问已经宣布并由差役拾取的分配安排；不声称暂存一瓣已经核毕归还。",
)
f(
    "baseline",
    1,
    "食指初失感",
    "首回在镇脉炉听息后，沈砚测试右手哪一根手指，摸起来像隔着什么？",
    "炉房那次咳血后，最早被他检查出触觉消失的是哪个指头，叙述用了何种隔层比喻？",
    "再去摸右手食指——指头肚贴上去，什么也觉不出来了，像隔了一层湿棉。",
    "右手食指，像隔了一层湿棉。",
    "源章明确只有此次被测试的食指；不把后续三指或整臂的代价提前。",
)
f(
    "baseline",
    1,
    "灰罐底角",
    "沈砚首回从炉房取回红灰后，用小罐收起，放在旧砧哪里？",
    "第一次把炉里异色粉末带回家时，盛灰的小容器被摆在哪一处？",
    "他拿小罐把灰收起来，放在旧砧的底角。",
    "旧砧的底角。",
    "已经完成的放置动作有独立原句；不套用完整工作流稿的灶后藏法。",
)
f(
    "baseline",
    2,
    "开刃预付",
    "楚衡给沈砚接三十把铁锨头开刃活，实际预付了多少铜板？",
    "镇守让铺主为锨头磨刃时，从布袋数出的首笔工钱是多少？",
    "楚衡拉开抽屉摸出个布袋子，数了三十个铜板推过来",
    "三十个铜板。",
    "付款已发生；不将旧欠六十铜板与本次预付款混为一笔。",
)
f(
    "baseline",
    2,
    "给小满添鞋",
    "沈砚分给小满多少铜板作添鞋钱，余款打算用来买什么？",
    "回铺分配新工钱时，跑腿孩子拿到的鞋钱是多少，师兄留下的那份用途是什么？",
    "这十五个先拿着，替你添双鞋。剩下的我留着买炭。",
    "十五个铜板给小满添鞋，余款留作买炭。",
    "已经数钱递给小满；鞋和炭的用途是当时明确安排，不声称已购得。",
)
f(
    "baseline",
    3,
    "耐火泥补炉",
    "洛蘅实际用什么材料修补了沈记炭炉的什么部位？",
    "来到铺子后，修炉女子从油布包取出的东西，被抹到小炉哪一块？",
    "里头是调好的耐火泥，就着炭炉的余温把炉壁薄处抹了一层。",
    "调好的耐火泥，抹在炉壁薄处。",
    "引文记录实际抹泥，不只引用她先前提出的修补建议。",
)
f(
    "baseline",
    3,
    "第十七锨崩刃",
    "引火做到第几把锨头时，沈砚指头麻木使锨头磕出什么损伤？",
    "那批开刃活进行到哪一件时，因为握钳失准而把刃口撞坏？",
    "引到第十七把，右边指头麻得整根发木，夹钳的时候有一瞬间找不准位置，锨头在砧上磕了一下，刃口崩出个豁。",
    "第十七把，刃口崩出豁口。",
    "动作序号和实物损伤在同一连续原句中，不能只凭麻木推断失败。",
)
f(
    "baseline",
    4,
    "铜片七痕",
    "洛蘅数了两遍后，铜片上能看清几道记号，她说每道对应什么？",
    "管架废渣里取出的薄铜上，修炉女子辨出的线痕数量和她给出的计数含义是什么？",
    "能看清的七道。”洛蘅数了两遍，“每道对应一趟活。七趟。",
    "七道，每道对应一趟活。",
    "问洛蘅在场数出的痕数和解释，不把七趟自动当成七个不同工人。",
)
f(
    "baseline",
    4,
    "门板红绳签",
    "从管架回到铺子时，门板上别着什么，签头缠的绳是什么颜色？",
    "二人返家发现门上多出的那件小东西是什么，它上面绕着怎样的绳线？",
    "门板上别着一根竹签，签头上缠了一小圈红绳，红得发暗。",
    "一根竹签，签头缠暗红色红绳。",
    "已看见的物证外形，不推定是谁留下或要传何种暗号。",
)
f(
    "baseline",
    5,
    "半丸油纸转手",
    "沈砚提出把余半粒丸药转交后，装药的油纸包最终由谁伸手拿了？",
    "铺主把包药纸放在门闩上劝同伴收下，那份药实际被哪位人物接走？",
    "洛蘅看了他一眼，伸手把油纸包拿了。",
    "洛蘅。",
    "采用实际拿包的完成句，而非沈砚提出转交时的意向句。",
)
f(
    "baseline",
    5,
    "竹签最终插处",
    "沈砚把竹签先收进陶罐又拿出，最终插在什么地方？",
    "为让守门的小满看得到，红绳小签最后被固定在门后哪件物品的哪个部位？",
    "又拿出来，插在门后的簸箕把手上。",
    "门后的簸箕把手上。",
    "问当章结束前的最后放置，不将被撤回的陶罐存法当现状。",
)
f(
    "baseline",
    6,
    "签押三点水",
    "沈砚看结案字据时，纸上沈字的三点水是连笔还是分开写？",
    "镇守展示的旧案签押中，那个姓氏左旁三点的具体写法是什么？",
    "这张纸上的“沈”字，三点水分开写，撇是撇，捺是捺，板板正正，像临帖临出来的。",
    "三点水分开写。",
    "只问纸上可见笔画特征；不把不像师父的判断升级为已查明伪造者。",
)
f(
    "baseline",
    6,
    "油纸埋铜片",
    "回铺后，沈砚怎样包装铜片，又埋在什么位置？",
    "为次日不带证物下管架，他把那枚半山字铜料怎样遮藏在灶边？",
    "搬开灶后的半截坯砖，在墙根底下挖了个浅坑，把铜片拿油纸裹了，埋进去，砖头压严实。",
    "油纸包裹，埋灶后墙根浅坑，以坯砖压实。",
    "连续叙述了包装、挖坑与覆盖动作，不套用另一卷的灰罐藏处。",
)
f(
    "baseline",
    7,
    "镇丁父业",
    "支持封样画押的年轻镇丁，说自己的父亲做什么？",
    "那名表示懂得新旧砖泥的在场卫役，用家中长辈从事哪种手艺解释自己的经验？",
    "我爹是烧窑的。”年轻镇丁把肩膀缩了缩，声音没降",
    "父亲是烧窑的。",
    "问镇丁当场说明的职业背景，不据此认定他的父亲就是某个周姓人物。",
)
f(
    "baseline",
    7,
    "样袋定位",
    "洛蘅取泥样和废渣后，叫陈四在两只样袋上写下哪两类信息？",
    "为记录此次现场取样，那位只负责写字的人在袋面标注了哪些时间与地点要素？",
    "叫陈四在袋上写了时辰和位置。",
    "时辰和位置。",
    "封袋标记已实际写下；不声称吴司事因此签了封样条。",
)
f(
    "baseline",
    8,
    "黄册担保数",
    "洛蘅看到黄皮账沈记垫数栏，担保人和私接火数分别记成什么？",
    "料院混入后翻到的那本黄色总目，在铺子担保项下列了谁的名字及多少勾？",
    "担保人写的是老沈匠，私接火数记了两千四百勾",
    "老沈匠，两千四百勾。",
    "仅描述洛蘅看到的特定账栏，不将账面数字当作真实借款或实际付款。",
)
f(
    "baseline",
    8,
    "夹页朱圈原文",
    "黑皮册子夹页最底下，朱笔圈出的沈记记录原文是什么？",
    "洛蘅后来带回的那张撕下账纸，红圈标出的关于铺子垫款的那句话怎么写？",
    "沈记垫数，查无实付。",
    "沈记垫数，查无实付。",
    "直接问可见文字，不试图消解本章其他财务解释中的张力或矛盾。",
)
f(
    "baseline",
    9,
    "矿管挂名",
    "洛蘅回铺后说，矿上如今挂名的管事姓什么、叫什么？",
    "户主会后修炉女子报告的现任矿区名义负责人，其完整姓名是什么？",
    "矿上如今挂名的管事姓鲁，单名一个礼字。",
    "鲁礼。",
    "原句直接说明姓名；不由挂名管事推断其已被证实参与失踪案。",
)
f(
    "baseline",
    9,
    "砸牌砖块",
    "沈记招牌被砸后，砸牌物是什么，落在门口哪里？",
    "赶回巷口看见旧木匾新裂口时，台阶底下还留着怎样的致损物？",
    "砸的是一块半截青砖，还滚在门阶底下。",
    "半截青砖，滚在门阶底下。",
    "问现场物证，不根据陈婶的神态认定投砖者身份。",
)
f(
    "baseline",
    10,
    "灰样试烧色",
    "沈砚把小满带回的红灰撒进灶火试烧，火苗腾起什么颜色？",
    "矿道口取来的那撮粉末实际入火检验时，冒出的色光是什么？",
    "他把灰撒进灶膛火里试，火苗舔过，腾起一小片蓝绿。",
    "蓝绿色。",
    "真实发生的撒灰试烧与颜色反应；不要求推导化学成分。",
)
f(
    "baseline",
    10,
    "新抄本入坑",
    "夜里回铺后，沈砚怎样包楚衡给的抄本，塞在何处？",
    "镇守从总目摘出的那卷纸被带回家后，铺主把它包好藏到了哪个已有的隐匿点？",
    "把抄本拿油纸裹了，塞进灶后墙根底下那个新挖深的坑里",
    "油纸包裹，塞入灶后墙根新挖深的坑。",
    "采用已完成的藏纸动作，不用楚衡禁止外传的约定替代实际去向。",
)
f(
    "baseline",
    11,
    "卖砧留赵铺",
    "沈砚卖掉旧铁砧后，铁砧留在谁的铺子门口？",
    "为筹灯油和炭钱让出的那件老家当，交易后最终摆在哪位铁匠店前？",
    "铁砧留在老赵铺子门口",
    "老赵的铺子门口。",
    "实物已交易且留在买方门口；不把之前‘要卖’的意向作为完成证据。",
)
f(
    "baseline",
    11,
    "两丸蜡纸药",
    "洛蘅离开前留下几丸清肺丸，用什么纸包着？",
    "同伴走后铺主打开药包，实际看到多少颗清肺药，外层包材是什么？",
    "两丸清肺丸用蜡纸包着，旁边还有一小截她拓印剩下的墨。",
    "两丸清肺丸，用蜡纸包着。",
    "打开药包的实际清点；与另一卷一整枚温肺丹的数量和药名分别保留。",
)
f(
    "baseline",
    12,
    "旧账分年",
    "傍晚镇上传下核账结果，旧账分几年还，加罚数怎样处理？",
    "巡使核账后公布的还款安排，要求欠款分摊多长时间，额外罚款留下多少？",
    "旧账按户分摊，分三年还，加罚数抹了大半，楚衡的存记抵作保。",
    "按户分摊，分三年还，加罚数抹了大半。",
    "问已经传下的核账条款，不把分三年还解释为债已实际还清。",
)
f(
    "baseline",
    12,
    "来信钎子位置",
    "洛蘅来信说，同门的一枚钎子在矿道什么位置找到？",
    "结尾收到的寻人信中，实际发现那件修炉工具的位置，按废巷段数和步数怎么描述？",
    "在第三段废巷往里第七步处，找到同门钎子一枚",
    "第三段废巷往里第七步处。",
    "只问来信已报告的物证位置，不由工具发现断定同门已获救。",
)


def sha(content):
    return hashlib.sha256(content).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def sentence_span(body, anchor):
    """Expand a unique selected anchor to complete adjoining sentence boundaries.

    Paragraphs and 。！？ are source punctuation, not generated evidence. Keep
    trailing quotation marks when present so displayed evidence is readable.
    """
    assert body.count(anchor) == 1, ("anchor must be unique", anchor)
    a = body.index(anchor)
    b = a + len(anchor)
    boundaries = ".。！？!?\n"
    start = max((body.rfind(char, 0, a) for char in boundaries), default=-1) + 1
    while start < a and body[start] in "”’ \t":
        start += 1
    if body[b - 1] not in boundaries:
        ends = [body.find(char, b) for char in boundaries]
        ends = [x for x in ends if x >= 0]
        b = min(ends) + 1 if ends else len(body)
    while b < len(body) and body[b] in "”’":
        b += 1
    return start, b


def load_sources():
    # Connection-level readonly mode: no new tables, settings reads, or writes.
    db = ROOT / "data" / "studio.sqlite"
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    sources = []
    try:
        for book, pid in PROJECTS.items():
            rows = connection.execute(
                "SELECT id,project_id,volume,number,title,canonical,canonical_revision,stale FROM chapters WHERE project_id=? AND canonical_revision>0 ORDER BY volume,number",
                (pid,),
            ).fetchall()
            assert len(rows) == 12 and [r["number"] for r in rows] == list(range(1, 13))
            for row in rows:
                assert not row["stale"] and row["canonical"].strip()
                sources.append(dict(row) | {"book": book})
    finally:
        connection.close()
    return sources


def build():
    assert len(FACTS) == 48
    assert Counter((x["book"], x["chapter"]) for x in FACTS) == {
        (book, n): 2 for book in PROJECTS for n in range(1, 13)
    }
    sources = load_sources()
    rng = random.Random(2026102102)
    development = {book: sorted(rng.sample(list(range(1, 13)), 4)) for book in PROJECTS}
    docs, scenarios, queries, manifests = [], [], [], []
    for source in sources:
        book, number = source["book"], source["number"]
        body = source["canonical"]
        digest = sha(body.encode())
        split = "dev" if number in development[book] else "heldout"
        pid, cid = source["project_id"], source["id"]
        revision = source["canonical_revision"]
        position = source["volume"] * 100000 + number
        chapter_key = f"{pid}:{source['volume']}:{number}"
        chunks = []
        for index, start in enumerate(range(0, len(body), 380)):
            end = min(start + 450, len(body))
            doc = {
                "id": str(uuid5(NAMESPACE_URL, f"{cid}/{revision}/{index}")),
                "project": pid,
                "book": book,
                "scenario_id": f"{book}-ch{number:02d}",
                "source_chapter_id": cid,
                "source_chapter_key": chapter_key,
                "source_sha256": digest,
                "canonical_revision": revision,
                "source_revision": revision,
                "volume": source["volume"],
                "chapter": number,
                "position": position,
                "title": source["title"],
                "stale": False,
                "split": split,
                "chunk_index": index,
                "start": start,
                "end": end,
                "content": body[start:end],
                "role": "canonical_chapter_chunk",
            }
            docs.append(doc)
            chunks.append(doc)
        manifests.append(
            {
                "book": book,
                "project": pid,
                "source_chapter_id": cid,
                "source_chapter_key": chapter_key,
                "title": source["title"],
                "volume": source["volume"],
                "chapter": number,
                "canonical_revision": revision,
                "source_sha256": digest,
                "characters": len(body),
                "split": split,
                "chunk_ids": [d["id"] for d in chunks],
            }
        )
        facts = [x for x in FACTS if x["book"] == book and x["chapter"] == number]
        for fact_index, fact in enumerate(facts, 1):
            sid = f"{book}-ch{number:02d}-fact{fact_index}"
            start, end = sentence_span(body, fact["quote"])
            quote = body[start:end]
            gold = [d["id"] for d in chunks if d["start"] <= start and d["end"] >= end]
            assert gold, ("complete sentence crosses all chunks", sid, quote)
            evidence = {
                "project": pid,
                "source_chapter_id": cid,
                "source_revision": revision,
                "source_sha256": digest,
                "start": start,
                "end": end,
                "quote": quote,
                "selected_anchor": fact["quote"],
                "expected_doc_ids": gold,
            }
            scenario = {
                "id": sid,
                "project": pid,
                "book": book,
                "source_chapter_id": cid,
                "source_chapter_key": chapter_key,
                "volume": source["volume"],
                "chapter": number,
                "source_revision": revision,
                "source_sha256": digest,
                "split": split,
                "category": fact["label"],
                "reference_answer": fact["answer"],
                "judgment_basis": fact["basis"],
                "evidence_spans": [evidence],
                "gold_semantics": "any_of",
                "expected_doc_ids": gold,
            }
            scenarios.append(scenario)
            for qid, subset in (("direct", "direct"), ("paraphrase", "paraphrase")):
                queries.append(
                    {
                        "id": f"{sid}-{qid}",
                        "scenario_id": sid,
                        "split": split,
                        "project": pid,
                        "book": book,
                        "query": fact[qid],
                        "before": position + 1,
                        "expected_doc_ids": gold,
                        "gold_semantics": "any_of",
                        "category": fact["label"],
                        "query_subset": subset,
                        "answerability": "answerable",
                        "source_chapter_id": cid,
                        "source_chapter_key": chapter_key,
                        "source_revision": revision,
                        "source_sha256": digest,
                        "reference_answer": fact["answer"],
                        "judgment_basis": fact["basis"],
                        "evidence_spans": [evidence],
                    }
                )
    corpus = {
        "version": "novel-v1",
        "source_kind": "delivered_canonical_novels",
        "sources": manifests,
        "scenarios": scenarios,
        "documents": docs,
    }
    query_file = {"version": "novel-v1", "queries": queries}
    validate(corpus, query_file, sources)
    corpus_text, query_text = dump(corpus), dump(query_file)
    protocol = {
        "version": "novel-v1",
        "frozen_date": "2026-10-21",
        "seed": 2026102102,
        "source": {
            "authority": "data/studio.sqlite以mode=ro读取两作品的24章当前canonical；源正文与原评测均未改写",
            "project_ids": PROJECTS,
            "export_sha256": {
                name: sha((ROOT / "artifacts" / "evaluation" / name).read_bytes())
                for name in ("full-novel.md", "baseline-novel.md")
            },
            "chapter_revisions_and_body_sha256": "corpus.sources逐章保存；不以可变数据库文件整体哈希代替正文快照",
        },
        "counts": {
            "books": 2,
            "source_chapters": 24,
            "scenarios": 48,
            "queries": 96,
            "documents": len(docs),
            "dev_chapters": 8,
            "heldout_chapters": 16,
            "dev_scenarios": 16,
            "heldout_scenarios": 32,
            "dev_queries": 32,
            "heldout_queries": 64,
            "quotes": 48,
            "scenarios_with_overlapping_gold_alternatives": sum(
                len(s["expected_doc_ids"]) > 1 for s in scenarios
            ),
        },
        "split": {
            "unit": "source_chapter_id",
            "development_chapter_numbers": development,
            "procedure": "Random(2026102102)按完整稿、基线稿顺序各抽4/12章作dev，其余8/12章heldout；两事实和其两种问法跟随来源章，不拆到两边",
            "caveat": "24章来自同一初始设定的两卷，章间相关，48情境或96问法都不是独立作者/独立小说样本；推断应按来源章节聚合，题干改述不增加独立样本量。",
        },
        "annotation": {
            "method": "Codex AI逐章阅读全文，每章选两个具体事实与两种问法，人工式AI判断明确引文及答案，不使用检索排名或被测系统输出来出题。",
            "scope": "动作、归属、数量、代价、条款与可见物证；叙述/人物发言/账面文字分别在题干与依据注明；不从账面推定真实债务，不把承诺当完成。",
            "not_human_gold": "这不是自然人专家标注；单AI仍可能遗漏更早章节同事实表达或误解故事，参考答案用于审阅，指标只检验预先指定的证据来源。",
            "quote_span": "字符索引按Python Unicode字符串[start:end)，不按UTF-8字节；原始anchor向相邻完整句边界扩展，quote与源章逐字相等，所有gold块完整包含该quote。",
        },
        "chunking": {
            "characters": 450,
            "stride": 380,
            "overlap": 70,
            "id": "uuid5(NAMESPACE_URL, f'{chapter_id}/{canonical_revision}/{chunk_index}')；与已交付生产切片键规则一致",
            "gold_semantics": "any_of：同一quote可能落入两个重叠块，命中其中任一块即该情境证据命中；不得要求同时找回重叠副本，也不得用召回副本比例稀释得分。",
        },
        "eligibility": {
            "project": "仅目标project；另一卷具有同名人物与相似事件，是自然跨作品干扰",
            "time": "before=源章volume*100000+chapter+1；仅position<before有效，源章可用，后文真实章节作为未来干扰",
            "version": "只有当前canonical，stale=false；此套件不人工添加旧版、近重复或32份未来压力副本",
        },
        "recommended_metrics": [
            "any-gold Hit/Recall@k（二元）与首个合法gold的MRR；每情境两问先平均，再按来源章聚合",
            "未来/跨作品/失效来源泄漏与合法候选返回数量分开报告",
            "直接问法与改述子集分报；改述仅为词面差异，不宣称只有向量才能解答",
            "如计算区间或配对重采样，至少按来源章节聚类；两卷同设定的相关性另披露",
            "冷/热时延与实际启用渠道另列；不据检索命中推定文学质量或生成事实正确率",
        ],
        "freeze_policy": "此数据独立于v2构造和排序实现；只读小说来源，不看v2结果逐项或新排序实现；冻结后不因heldout输赢改题、gold或split。生成器不调用检索、收费模型或heldout评估。",
        "limitations": [
            "新题来自已经用于开发和交付的小说文本，不是新作者/新故事外部泛化集；仅问句与gold新冻结",
            "只有两卷共用设定的玄幻短卷；完整稿含Codex外部编辑，不能归因于纯DeepSeek创作能力",
            "这套件检验真实小说正文上的指定来源检索，不检验自然长篇遗忘率、事实自动提取或百万字承载",
            "48条引文与96问法相关；两问法不是96个独立事实，24来源章也不是24部独立小说",
            "没有无答案题，不可从本套件得出拒答率、幻觉率或答案faithfulness结论",
            "gold只列源章完整包含指定引文的块；更早章可能有可替代的相关表述，不能把gold未命中直接称内容错误",
        ],
        "sha256": {
            "corpus.json": sha(corpus_text.encode()),
            "queries.json": sha(query_text.encode()),
        },
        "validation": {
            "status": "passed",
            "checks": [
                "24章每章2情境、每情境2唯一问法；48情境/96查询",
                "逐卷4/8来源章分dev/heldout；32/64查询，无同章跨split",
                "正文SHA、revision及所有切片与只读canonical逐字一致",
                "48条quote原文span逐字匹配，每一quote有至少一个单块完整包含",
                "gold=源章所有完整包含同一quote的切片，重叠采用any_of",
                "所有gold项目相同、stale=false、position<before；future为原有后文，没有人工压力重复",
            ],
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    payloads = (
        ("corpus.json", corpus_text),
        ("queries.json", query_text),
        ("protocol.json", dump(protocol)),
    )
    # Check every preexisting frozen file before writing any file.
    for name, content in payloads:
        target = OUT / name
        if target.exists() and target.read_text() != content:
            raise RuntimeError(f"拒绝覆盖不一致的冻结文件：{target}")
    for name, content in payloads:
        (OUT / name).write_text(content)
    print(
        dump(
            {
                "files": {
                    str(OUT / n): sha((OUT / n).read_bytes()) for n, _ in payloads
                },
                "counts": protocol["counts"],
                "development": development,
                "retrieval_executed": False,
            }
        )
    )


def validate(corpus, query_file, sources):
    queries, docs, scenarios = (
        query_file["queries"],
        corpus["documents"],
        corpus["scenarios"],
    )
    source_map = {s["id"]: s for s in sources}
    lookup = {d["id"]: d for d in docs}
    assert len(lookup) == len(docs)
    assert (
        len(queries)
        == len({q["id"] for q in queries})
        == len({q["query"] for q in queries})
        == 96
    )
    assert len(scenarios) == 48
    assert Counter(q["split"] for q in queries) == {"dev": 32, "heldout": 64}
    assert Counter(s["split"] for s in scenarios) == {"dev": 16, "heldout": 32}
    assert Counter(q["query_subset"] for q in queries) == {
        "direct": 48,
        "paraphrase": 48,
    }
    chapter_splits = defaultdict(set)
    scenario_queries = defaultdict(list)
    for d in docs:
        source = source_map[d["source_chapter_id"]]
        body = source["canonical"]
        assert d["content"] == body[d["start"] : d["end"]]
        assert d["canonical_revision"] == source["canonical_revision"]
        assert d["source_sha256"] == sha(body.encode())
        assert d["position"] == d["volume"] * 100000 + d["chapter"]
        assert d["end"] - d["start"] <= 450 and d["start"] == d["chunk_index"] * 380
        assert d["project"] == source["project_id"] and not d["stale"]
        chapter_splits[d["source_chapter_id"]].add(d["split"])
    for q in queries:
        scenario_queries[q["scenario_id"]].append(q)
        chapter_splits[q["source_chapter_id"]].add(q["split"])
        source = source_map[q["source_chapter_id"]]
        assert q["before"] == source["volume"] * 100000 + source["number"] + 1
        assert q["gold_semantics"] == "any_of" and q["expected_doc_ids"]
        for span in q["evidence_spans"]:
            body = source["canonical"]
            assert body[span["start"] : span["end"]] == span["quote"]
            assert span["selected_anchor"] in span["quote"]
            actual = [
                d["id"]
                for d in docs
                if d["source_chapter_id"] == q["source_chapter_id"]
                and d["start"] <= span["start"]
                and d["end"] >= span["end"]
            ]
            assert actual == q["expected_doc_ids"] == span["expected_doc_ids"]
        for gid in q["expected_doc_ids"]:
            d = lookup[gid]
            assert (
                d["project"] == q["project"]
                and d["position"] < q["before"]
                and not d["stale"]
            )
            assert d["source_revision"] == q["source_revision"]
    assert len(chapter_splits) == 24 and all(
        len(s) == 1 for s in chapter_splits.values()
    )
    assert Counter(next(iter(s)) for s in chapter_splits.values()) == {
        "dev": 8,
        "heldout": 16,
    }
    assert len(scenario_queries) == 48
    for pair in scenario_queries.values():
        assert (
            len(pair) == 2
            and pair[0]["expected_doc_ids"] == pair[1]["expected_doc_ids"]
        )
        assert pair[0]["source_chapter_id"] == pair[1]["source_chapter_id"]
    assert Counter((s["book"], s["chapter"]) for s in scenarios) == {
        (b, n): 2 for b in PROJECTS for n in range(1, 13)
    }


if __name__ == "__main__":
    build()
