import { useEffect, useState } from "react";
import {
  ArrowRight,
  ArrowUpRight,
  CheckCheck,
  Eye,
  GitBranch,
  PenLine,
  Sparkles,
} from "lucide-react";
import { put, labels, type Project, type Chapter, type Run } from "./api";
import { Button, Empty, Modal } from "./ui";
export { MemoryPage } from "./memory";
export { ResearchPage } from "./research";

export function Outline({
  project: p,
  run,
  busy,
  start,
  approve,
  refresh,
  notify,
  openChapter,
  onAppend,
}: {
  project: Project;
  run?: Run;
  busy: boolean;
  start: (k: string, i?: string) => void;
  approve: () => void;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  openChapter: (id: string) => void;
  onAppend: (volume: number) => void;
}) {
  const [instruction, setInstruction] = useState(""),
    [edit, setEdit] = useState(false),
    [text, setText] = useState(p.outline),
    [planEdit, setPlanEdit] = useState<Chapter | null>(null),
    [draftPlan, setDraftPlan] = useState<any>(null),
    [direction, setDirection] = useState<{
      premise: string;
      style: string;
    } | null>(null);
  const bp =
    run &&
    ["blueprint", "volume"].includes(run.kind) &&
    run.status === "awaiting_approval"
      ? run.result
      : null;
  useEffect(() => {
    if (!edit) setText(p.outline);
  }, [p.outline]);
  async function save() {
    try {
      await put("/projects/" + p.id + "/outline", {
        outline: text,
        project_revision: p.revision,
      });
      setEdit(false);
      await refresh();
      notify("卷纲已保存，相关审核需要重新确认");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  return (
    <div className="page outline-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">THE STORY BENEATH THE WORDS</span>
          <h1>先看见故事的远方。</h1>
          <p>让每一次相遇、选择与告别，都有来处，也有回响。</p>
        </div>
        <Button
          onClick={() =>
            start(p.revision ? "volume" : "blueprint", instruction)
          }
          variant="primary"
          disabled={busy || !!bp}
        >
          <Sparkles size={16} />
          {p.revision ? "筹备下一卷" : "生成故事蓝图"}
        </Button>
      </div>
      <section className="direction-card">
        <div>
          <span className="eyebrow">YOUR DIRECTION</span>
          <h3>故事由你掌舵</h3>
          <p>{p.premise}</p>
          <Button
            variant="small ghost"
            disabled={
              !!run &&
              [
                "queued",
                "running",
                "awaiting_approval",
                "interrupted",
              ].includes(run.status)
            }
            onClick={() => setDirection({ premise: p.premise, style: p.style })}
          >
            <PenLine size={13} />
            调整故事方向
          </Button>
        </div>
        <textarea
          aria-label="规划补充"
          value={instruction}
          onChange={(e) => setInstruction(e.target.value)}
          placeholder="给规划员的补充：放慢成长节奏、增加对手的行动线、让主角为胜利付出更具体的代价…"
        />
        <small>生成蓝图或新卷时，会一起参考这些要求。</small>
      </section>
      {bp && (
        <section className="blueprint-review">
          <div className="section-title">
            <h2>
              <Eye size={18} /> 故事蓝图，待你确认
            </h2>
            <span className="pill">规划草案</span>
          </div>
          <h3>{bp.logline}</h3>
          <div className="alternatives">
            {bp.alternatives?.map((a: string, i: number) => (
              <article key={i}>
                <span>方案 {String(i + 1).padStart(2, "0")}</span>
                <p>{a}</p>
              </article>
            ))}
          </div>
          <div className="prose pre-wrap">{bp.outline}</div>
          <details>
            <summary>查看十份记忆草案</summary>
            {Object.entries(bp.memory || {}).map(([k, v]) => (
              <article key={k} className="memory-preview">
                <h4>{k}</h4>
                <p className="pre-wrap">{String(v)}</p>
              </article>
            ))}
          </details>
          <div className="modal-footer">
            <span>确认后才会成为正式的故事规划。</span>
            <Button variant="primary" onClick={() => approve()}>
              <CheckCheck size={16} />
              确认故事蓝图
            </Button>
          </div>
        </section>
      )}
      <section className="surface outline-surface">
        <div className="section-title">
          <h2>
            <GitBranch size={17} /> 本卷故事线
          </h2>
          {p.outline && (
            <Button variant="small ghost" onClick={() => setEdit(!edit)}>
              <PenLine size={13} />
              {edit ? "取消编辑" : "编辑卷纲"}
            </Button>
          )}
        </div>
        {edit ? (
          <>
            <textarea
              className="outline-editor"
              aria-label="编辑卷纲"
              value={text}
              onChange={(e) => setText(e.target.value)}
            />
            <Button variant="primary" onClick={save}>
              保存卷纲
            </Button>
          </>
        ) : p.outline ? (
          <div className="prose pre-wrap">{p.outline}</div>
        ) : (
          <Empty icon={GitBranch} title="为故事画一条生长的线">
            点击“生成故事蓝图”，一起明确主角目标、核心冲突和每次成长的代价。
          </Empty>
        )}
      </section>
      {p.volumes.length > 1 && (
        <section className="surface outline-surface">
          <div className="section-title">
            <h2>分卷故事线</h2>
          </div>
          {p.volumes.map((v) => (
            <details key={v.number}>
              <summary>
                第 {v.number} 卷 · {v.title}
              </summary>
              <div className="prose pre-wrap">{v.outline || p.outline}</div>
            </details>
          ))}
        </section>
      )}
      <div className="section-title">
        <h2>
          章节规划<span>{p.chapters.length} 章</span>
        </h2>
        <Button
          variant="small"
          disabled={
            !!run &&
            ["queued", "running", "awaiting_approval", "interrupted"].includes(
              run.status,
            )
          }
          onClick={() => onAppend(p.volumes.at(-1)?.number || 1)}
        >
          新增章节
        </Button>
      </div>
      <div className="plan-grid">
        {p.chapters.map((c) => (
          <article className="plan-card" key={c.id}>
            <header>
              <span className="plan-number">
                {p.volumes.length > 1 ? "卷" + c.volume + " · " : ""}
                {String(c.number).padStart(2, "0")}
              </span>
              <span className={"status-tag " + (c.stale ? "stale" : c.status)}>
                {c.stale ? "待复核" : labels[c.status]}
              </span>
            </header>
            <h3>{c.title}</h3>
            <p>{c.plan.goal || "这一章的故事，还在等待一个方向。"}</p>
            {c.plan.scenes?.slice(0, 2).map((s, i) => (
              <div className="scene-line" key={i}>
                <i />
                {s.title}
              </div>
            ))}
            <footer>
              <button
                className="text-action"
                onClick={() => {
                  setPlanEdit(c);
                  setDraftPlan({
                    ...c.plan,
                    characters: c.plan.characters || [],
                    constraints: c.plan.constraints || [],
                    hooks: c.plan.hooks || [],
                    scenes: c.plan.scenes || [],
                  });
                }}
              >
                调整规划
              </button>
              <button
                className="icon-btn"
                title="进入章节"
                onClick={() => openChapter(c.id)}
              >
                <ArrowUpRight size={17} />
              </button>
            </footer>
          </article>
        ))}
      </div>
      {direction && (
        <Modal
          title="调整故事方向"
          subtitle="先看清变化会影响哪里，再继续创作。"
          onClose={() => setDirection(null)}
          wide
        >
          <div className="form">
            <label>
              故事方向
              <textarea
                rows={5}
                value={direction.premise}
                onChange={(e) =>
                  setDirection({ ...direction, premise: e.target.value })
                }
              />
            </label>
            <label>
              文风与表达要求
              <textarea
                rows={3}
                value={direction.style}
                onChange={(e) =>
                  setDirection({ ...direction, style: e.target.value })
                }
              />
            </label>
            <div className="impact-preview">
              <h4>保存后需要核对</h4>
              <p>
                {p.chapters.length} 章计划与 {p.memory.length}{" "}
                份核心记忆；已有审核会失效。请核对人物目标、冲突、伏笔及成长条件是否仍适合新方向。
              </p>
              <div>
                {p.memory.map((m) => (
                  <span key={m.name}>{m.name}</span>
                ))}
              </div>
              <details>
                <summary>查看关联章节</summary>
                {p.chapters.map((c) => (
                  <p key={c.id}>
                    {c.title} · {c.plan.goal}
                  </p>
                ))}
              </details>
            </div>
            <Button
              variant="primary"
              onClick={async () => {
                try {
                  await put("/projects/" + p.id + "/direction", {
                    ...direction,
                    project_revision: p.revision,
                  });
                  setDirection(null);
                  await refresh();
                  notify("方向已更新，请核对关联计划与记忆");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              保存方向并重新核对
            </Button>
          </div>
        </Modal>
      )}
      {planEdit && draftPlan && (
        <Modal
          title={"调整「" + planEdit.title + "」"}
          subtitle="让本章的目标、人物和伏笔更接近你的想法。"
          onClose={() => setPlanEdit(null)}
          wide
        >
          <div className="form">
            <label>
              章节标题
              <input
                value={draftPlan.title}
                onChange={(e) =>
                  setDraftPlan({ ...draftPlan, title: e.target.value })
                }
              />
            </label>
            <label>
              本章目标
              <textarea
                rows={3}
                value={draftPlan.goal}
                onChange={(e) =>
                  setDraftPlan({ ...draftPlan, goal: e.target.value })
                }
              />
            </label>
            <div className="form-row">
              <label>
                叙述视角
                <input
                  value={draftPlan.pov || ""}
                  onChange={(e) =>
                    setDraftPlan({ ...draftPlan, pov: e.target.value })
                  }
                />
              </label>
              <label>
                字数目标（含标点）
                <input
                  type="number"
                  min={200}
                  max={12000}
                  value={draftPlan.target_words}
                  onChange={(e) =>
                    setDraftPlan({
                      ...draftPlan,
                      target_words: +e.target.value,
                    })
                  }
                />
              </label>
            </div>
            <div className="form-row">
              <label>
                篇幅下限（含标点字符，可留空）
                <input
                  type="number"
                  min={100}
                  max={20000}
                  value={draftPlan.min_characters ?? ""}
                  onChange={(e) =>
                    setDraftPlan({
                      ...draftPlan,
                      min_characters: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                />
              </label>
              <label>
                篇幅上限（含标点字符，可留空）
                <input
                  type="number"
                  min={100}
                  max={20000}
                  value={draftPlan.max_characters ?? ""}
                  onChange={(e) =>
                    setDraftPlan({
                      ...draftPlan,
                      max_characters: e.target.value
                        ? Number(e.target.value)
                        : null,
                    })
                  }
                />
              </label>
            </div>
            <p className="quiet-note">
              上下限需一起填写，留空沿用目标±20%。超过范围会提示修订，三轮后仍超限由作者记录原因取舍。
            </p>
            {[
              ["characters", "本章人物（每行一项）"],
              ["constraints", "不可违背的约束（每行一项）"],
              ["hooks", "伏笔与回响（每行一项）"],
            ].map(([key, label]) => (
              <label key={key}>
                {label}
                <textarea
                  rows={3}
                  value={draftPlan[key].join("\n")}
                  onChange={(e) =>
                    setDraftPlan({
                      ...draftPlan,
                      [key]: e.target.value.split("\n").filter(Boolean),
                    })
                  }
                />
              </label>
            ))}
            {draftPlan.scenes.map((s: any, i: number) => (
              <fieldset key={i}>
                <legend>场景 {i + 1}</legend>
                {[
                  ["title", "场景名"],
                  ["goal", "目标"],
                  ["conflict", "冲突"],
                  ["cost", "代价"],
                  ["change", "变化"],
                ].map(([k, l]) => (
                  <label key={k}>
                    {l}
                    <input
                      value={s[k] || ""}
                      onChange={(e) =>
                        setDraftPlan({
                          ...draftPlan,
                          scenes: draftPlan.scenes.map((x: any, j: number) =>
                            j === i ? { ...x, [k]: e.target.value } : x,
                          ),
                        })
                      }
                    />
                  </label>
                ))}
              </fieldset>
            ))}
            <Button
              variant="primary"
              onClick={async () => {
                try {
                  await put("/chapters/" + planEdit.id + "/plan", {
                    plan: draftPlan,
                    base_revision: planEdit.draft_revision,
                  });
                  await refresh();
                  setPlanEdit(null);
                  notify("章节规划已更新");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              保存章节规划
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
