import { useEffect, useState } from "react";
import {
  ArrowRight,
  Check,
  Eye,
  FlaskConical,
  RefreshCw,
  ShieldCheck,
  CheckCircle2,
} from "lucide-react";
import { api, post, words, labels, type Research, type Pair } from "./api";
import { Button, Empty, Modal } from "./ui";
type RevealedPair = {
  id: string;
  labels: string;
  preference: "a" | "b" | "tie";
  reason: string;
};
function ResultDetails({ value }: { value: unknown }) {
  const [open, setOpen] = useState(false);
  return (
    <details onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>查看完整结果与限制</summary>
      {open && <pre>{JSON.stringify(value, null, 2)}</pre>}
    </details>
  );
}
const readingNames: Record<string, string> = {
  full: "完整工作流",
  baseline: "旧策略",
  tie: "接近",
  mixed: "无多数",
};
type AgentReading = {
  interpretation: string;
  observations: string[];
  by_sample: {
    sample_id: string;
    chapter: number;
    full: number;
    baseline: number;
    tie: number;
    majority: string;
  }[];
  reviews: {
    sample_id: string;
    reviewer_id: string;
    selected_version: string;
    reason: string;
    presented_labels: Record<string, string>;
    evidence: { side: string; quote: string }[];
  }[];
};
function AgentReadingResults({ value }: { value: AgentReading }) {
  return (
    <div className="agent-reading-results">
      <p className="quiet-note">{value.interpretation}</p>
      <div className="probe-results">
        <table>
          <thead>
            <tr>
              <th>片段</th>
              <th>来源章节</th>
              <th>完整工作流</th>
              <th>旧策略</th>
              <th>接近</th>
              <th>多数选择</th>
            </tr>
          </thead>
          <tbody>
            {value.by_sample.map((s) => (
              <tr key={s.sample_id}>
                <td>{s.sample_id}</td>
                <td>第 {s.chapter} 章</td>
                <td>{s.full}</td>
                <td>{s.baseline}</td>
                <td>{s.tie}</td>
                <td>{readingNames[s.majority]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details>
        <summary>阅读三位代理的理由与原文依据</summary>
        {value.by_sample.map((s) => (
          <div className="agent-reading-sample" key={s.sample_id}>
            <h4>
              {s.sample_id} · 第 {s.chapter} 章
            </h4>
            {value.reviews
              .filter((r) => r.sample_id === s.sample_id)
              .map((r) => (
                <div className="agent-reading-review" key={r.reviewer_id}>
                  <strong>
                    {r.reviewer_id} · {readingNames[r.selected_version]}
                  </strong>
                  <p>{r.reason}</p>
                  {r.evidence.map((e) => (
                    <blockquote key={e.side}>
                      <small>
                        原 {e.side.toUpperCase()} ·{" "}
                        {readingNames[r.presented_labels[e.side]]}
                      </small>
                      {e.quote}
                    </blockquote>
                  ))}
                </div>
              ))}
          </div>
        ))}
      </details>
      <p className="quiet-note">
        AI子代理评审，独立上下文、同一模型配置；18个选择来自同六组片段，不能视为18个独立样本或真人评价。
      </p>
    </div>
  );
}
function RetrievalResults({
  value,
}: {
  value: {
    by_mode: Record<
      string,
      {
        answerable_queries: number;
        query_recall_at_5: number;
        mrr_at_5: number;
        leaked_results: number;
        no_answer_queries: number;
        no_answer_queries_with_any_evidence: number;
        p95_search_ms: number;
      }
    >;
  };
}) {
  return (
    <div className="probe-results">
      <table>
        <thead>
          <tr>
            <th>检索配置</th>
            <th>可回答查询</th>
            <th>Recall@5</th>
            <th>MRR@5</th>
            <th>非法来源</th>
            <th>无答案仍返回证据</th>
            <th>P95毫秒</th>
          </tr>
        </thead>
        <tbody>
          {Object.entries(value.by_mode).map(([key, row]) => (
            <tr key={key}>
              <td>
                {{
                  legacy: "原策略",
                  keyword: "关键词",
                  semantic: "BGE语义",
                  hybrid: "时序融合",
                  reranked: "本地深度重排",
                }[key] || key}
              </td>
              <td>{row.answerable_queries}</td>
              <td>{(100 * row.query_recall_at_5).toFixed(1)}%</td>
              <td>{row.mrr_at_5.toFixed(3)}</td>
              <td>{row.leaked_results}</td>
              <td>
                {row.no_answer_queries_with_any_evidence} /{" "}
                {row.no_answer_queries}
              </td>
              <td>{row.p95_search_ms.toFixed(1)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="quiet-note">
        输入、划分和原始结果均独立保存；短档案与真实正文切块分别报告。查询改写共享事实，不能作为独立样本。无答案返回片段不等于模型编造答案。延迟含热模型查询与检索，不含初始化及正文生成。
      </p>
    </div>
  );
}
export function ResearchPage({
  data,
  onRefresh,
  notify,
}: {
  data: Research | null;
  onRefresh: () => Promise<void>;
  notify: (s: string) => void;
}) {
  const [pair, setPair] = useState<Pair | null>(null),
    [choice, setChoice] = useState(""),
    [reason, setReason] = useState(""),
    [reveal, setReveal] = useState<RevealedPair[] | null>(null);
  const hasAgentReading = data?.experiments.some(
    (e) => e.result?.agent_reading,
  );
  useEffect(() => {
    onRefresh().catch(() => {});
  }, []);
  return (
    <div className="page research-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">EVIDENCE, NOT JUST PROMISES</span>
          <h1>让每一次改进，有迹可循。</h1>
          <p>从能否稳定运行，到故事是否更值得读，分别寻找答案。</p>
        </div>
        <div className="research-actions">
          <Button
            disabled={!data?.pairs.length}
            onClick={() =>
              document.getElementById("blind-reading")?.scrollIntoView({
                behavior: "smooth",
                block: "start",
              })
            }
          >
            <Eye size={15} /> 参与匿名阅读
          </Button>
          <Button onClick={() => onRefresh()}>
            <RefreshCw size={15} /> 刷新结果
          </Button>
        </div>
      </div>
      <div className="research-stats">
        {[
          ["DeepSeek模型调用", data?.usage.calls || 0, "次"],
          ["输入上下文", words(data?.usage.input_tokens || 0), "tokens"],
          [
            "模型输出（含思考）",
            words(data?.usage.output_tokens || 0),
            "tokens",
          ],
          [
            "费用保守估算",
            "¥" + (data?.usage.spent || 0).toFixed(3),
            "上限 ¥300",
          ],
        ].map(([t, v, s]) => (
          <div className="surface" key={t}>
            <span>{t}</span>
            <strong>{v}</strong>
            <small>{s}</small>
          </div>
        ))}
      </div>
      <section className="surface">
        <div className="section-title">
          <h2>
            <FlaskConical size={17} /> 对照实验
          </h2>
          <span className="quiet-tag">真实运行与AI评审 · 分别记录</span>
        </div>
        {data?.experiments.length ? (
          <div className="experiment-list">
            {[...data.experiments]
              .sort(
                (a, b) =>
                  Number(Boolean(b.result?.retrieval_evaluation)) -
                    Number(Boolean(a.result?.retrieval_evaluation)) ||
                  Number(Boolean(b.result?.agent_reading)) -
                    Number(Boolean(a.result?.agent_reading)),
              )
              .map((e) => (
                <article key={e.id}>
                  <header>
                    <h3>{e.name}</h3>
                    <span className="pill">{labels[e.status] || e.status}</span>
                  </header>
                  <p>
                    {e.result?.summary ||
                      e.result?.interpretation ||
                      e.config?.description ||
                      "实验记录已保存。"}
                  </p>
                  <div className="metrics-inline">
                    {Object.entries(e.result?.metrics || {}).map(([k, v]) => (
                      <span key={k}>
                        {k}
                        <strong>{String(v)}</strong>
                      </span>
                    ))}
                  </div>
                  {e.result?.by_mode && (
                    <div className="probe-results">
                      <table>
                        <thead>
                          <tr>
                            <th>执行配置</th>
                            <th>有效试次</th>
                            <th>违例</th>
                            <th>保持</th>
                            <th>未触及</th>
                            <th>推进分</th>
                            {e.result.metrics?.["目标有效试次"] && (
                              <>
                                <th>历史命中 / 状态</th>
                                <th>平均额外修订</th>
                              </>
                            )}
                          </tr>
                        </thead>
                        <tbody>
                          {Object.entries(e.result.by_mode).map(
                            ([key, v]: [string, any]) => (
                              <tr key={key}>
                                <td>
                                  {{
                                    baseline: "旧策略",
                                    full: "完整机制",
                                    no_memory: "无历史上下文",
                                    no_revision: "不自动修订",
                                  }[key] || key}
                                </td>
                                <td>{v.successful_trials}</td>
                                <td>{v.violations_model_judged}</td>
                                <td>{v.preserved}</td>
                                <td>{v.not_tested}</td>
                                <td>{v.momentum}</td>
                                {e.result.metrics?.["目标有效试次"] && (
                                  <>
                                    <td>
                                      {v.archive_retrieved} /{" "}
                                      {v.structured_state_present}
                                    </td>
                                    <td>{v.mean_extra_drafts}</td>
                                  </>
                                )}
                              </tr>
                            ),
                          )}
                        </tbody>
                      </table>
                      <p className="quiet-note">
                        {e.result.metrics?.["目标有效试次"]
                          ? "真实执行图的历史信息缺口压力验收；历史档案为合成输入，四组共享基础规则。使用与机制探针相同的12场景，不能合并为独立样本。同模型判定，未触及约束不算正确。"
                          : "同模型判定；未触及的约束不算正确。此表为证据注入与返工的机制探针，不代表整套产品的端到端消融。"}
                      </p>
                    </div>
                  )}
                  {e.result?.agent_reading && (
                    <AgentReadingResults value={e.result.agent_reading} />
                  )}
                  {e.result?.retrieval_evaluation && (
                    <RetrievalResults value={e.result.retrieval_evaluation} />
                  )}
                  <ResultDetails value={e.result} />
                </article>
              ))}
          </div>
        ) : (
          <Empty icon={FlaskConical} title="实测结果，会在这里说话">
            运行 novel-agent eval
            后，完整样书、旧策略对照与消融结果会显示在这里。
          </Empty>
        )}
      </section>
      <section className="surface" id="blind-reading">
        <div className="section-title">
          <h2>
            <Eye size={17} />{" "}
            {hasAgentReading ? "可选：真人匿名阅读" : "匿名阅读对比"}
          </h2>
          <span className="muted">
            {data?.pairs.filter((p) => p.preference).length || 0} /{" "}
            {data?.pairs.length || 0} 已评价
          </span>
        </div>
        <p className="section-intro">
          {hasAgentReading
            ? "本轮代理评阅已完成；此处保留可选的自然人评价。若已看解盲结果，后续选择不作严格盲评。"
            : "先读文字，再看版本。你更愿意继续阅读哪一个故事？"}
        </p>
        <div className="blind-list">
          {data?.pairs.map((p, i) => (
            <button
              key={p.id}
              onClick={() => {
                setPair(p);
                setChoice(p.preference || "");
                setReason(p.reason || "");
              }}
            >
              <span>{String(i + 1).padStart(2, "0")}</span>
              <div>
                <strong>匿名片段 {i + 1}</strong>
                <small>
                  {p.preference
                    ? "已记录你的阅读选择"
                    : "两段等长文字 · 约 2 分钟"}
                </small>
              </div>
              {p.preference ? (
                <CheckCircle2 size={18} />
              ) : (
                <ArrowRight size={18} />
              )}
            </button>
          ))}
        </div>
        {!data?.pairs.length && (
          <p className="quiet-note">
            对照创作完成后，将按固定位置生成六组匿名片段。
          </p>
        )}
        {!!data?.pairs.length && data.pairs.every((p) => p.preference) && (
          <Button
            onClick={async () => {
              try {
                setReveal(await api<RevealedPair[]>("/blind/reveal"));
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            揭晓版本与阅读选择
          </Button>
        )}
        {reveal && (
          <div className="blind-reveal">
            <h3>你的六次阅读选择</h3>
            <p className="quiet-note">
              这是你的片段偏好，不代表所有读者或整部小说的质量。
            </p>
            {reveal.map((item, i) => {
              const identity = JSON.parse(item.labels);
              const name = (value: string) =>
                value === "full" ? "完整工作流" : "旧策略对照";
              return (
                <article key={item.id}>
                  <strong>
                    匿名片段 {i + 1} · 第 {identity.chapter} 章
                  </strong>
                  <span>
                    A：{name(identity.a)} · B：{name(identity.b)}
                  </span>
                  <p>
                    {item.preference === "tie"
                      ? "你认为两版接近"
                      : `你更想继续读 ${item.preference.toUpperCase()} · ${name(identity[item.preference])}`}
                  </p>
                  {item.reason && <blockquote>{item.reason}</blockquote>}
                </article>
              );
            })}
          </div>
        )}
      </section>
      <div className="methodology">
        <ShieldCheck size={20} />
        <div>
          <h3>如何理解这些数字</h3>
          <p>
            规则检查衡量已定义的约束；编辑评分来自模型判断；AI片段选择与真人投票分别记录，不能互相替代。相同模型生成与评审可能存在相关偏差，少量样本不代表所有读者。费用按官方高峰单价保守估算，实际以账单为准。
          </p>
        </div>
      </div>
      {pair && (
        <Modal
          title="哪一个故事，让你更想往下读？"
          subtitle="两版顺序已随机排列。暂不显示模型配置与评分。"
          onClose={() => setPair(null)}
          wide
        >
          <div className="compare-grid blind-reading">
            <article>
              <h4>片段 A</h4>
              <div className="pre-wrap">{pair.a}</div>
            </article>
            <article>
              <h4>片段 B</h4>
              <div className="pre-wrap">{pair.b}</div>
            </article>
          </div>
          <div className="choice-row">
            {[
              ["a", "更想读 A"],
              ["b", "更想读 B"],
              ["tie", "接近 / 难以选择"],
            ].map(([k, t]) => (
              <button
                className={choice === k ? "active" : ""}
                key={k}
                onClick={() => setChoice(k)}
              >
                {choice === k && <Check size={14} />}
                {t}
              </button>
            ))}
          </div>
          <label className="vote-reason">
            你为什么这样选？
            <textarea
              rows={2}
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：人物的选择更有说服力，或更想知道接下来会发生什么。"
            />
          </label>
          <Button
            variant="primary"
            disabled={!choice}
            onClick={async () => {
              try {
                await post("/blind/" + pair.id + "/vote", {
                  preference: choice,
                  reason,
                });
                await onRefresh();
                setPair(null);
                notify("你的真实阅读感受已记录");
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            记录我的选择
            <Check size={14} />
          </Button>
        </Modal>
      )}
    </div>
  );
}
