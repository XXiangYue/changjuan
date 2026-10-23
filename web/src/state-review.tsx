import { useEffect, useState } from "react";
import { ArrowUpRight, AlertCircle, GitBranch } from "lucide-react";
import { api, type Chapter } from "./api";

type StateError = { event_id: string; precondition: string; code: string };
type Preview = {
  status: "ready" | "invalid";
  changes: {
    index: number;
    summary: string;
    schema_valid: boolean;
    error?: StateError;
    change: { quote?: string };
  }[];
  errors: StateError[];
};
export function StateChangeReview({
  chapter,
  selected,
  onSelect,
  onValid,
  disabled,
}: {
  chapter: Chapter;
  selected: number[];
  onSelect: (value: number[]) => void;
  onValid: (value: boolean) => void;
  disabled: boolean;
}) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    onValid(selected.length === 0);
    api<Preview>(
      `/chapters/${chapter.id}/state-preview?selected=${selected.join(",")}`,
    )
      .then((value) => {
        if (!active) return;
        setPreview(value);
        onValid(value.status === "ready" || selected.length === 0);
      })
      .catch((e) => {
        if (active) {
          setError((e as Error).message);
          onValid(selected.length === 0);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [chapter.id, chapter.draft_revision, selected, onValid]);
  return (
    <div className="side-section state-change-review">
      <h4>这章改变了什么</h4>
      <p className="quiet-note">
        逐项勾选后，才会进入状态账本。未知数量会保持未知，预演会检查转移、消耗和伏笔的前置条件。
      </p>
      {loading && (
        <p className="quiet-note" role="status">
          正在核对你选择的变化…
        </p>
      )}
      {error && (
        <p className="state-error" role="alert">
          {error}。可以取消状态选择，或重新核对稿件。
        </p>
      )}
      {preview?.changes.map((item) => (
        <article className="patch" key={item.index}>
          <header>
            <label>
              <input
                type="checkbox"
                checked={selected.includes(item.index)}
                disabled={disabled || !item.schema_valid}
                onChange={(event) => {
                  onValid(false);
                  onSelect(
                    event.target.checked
                      ? [...selected, item.index]
                      : selected.filter((i) => i !== item.index),
                  );
                }}
              />
              <strong>{item.summary}</strong>
            </label>
          </header>
          <blockquote className="fact-quote">{item.change.quote}</blockquote>
          {item.error && (
            <p className="state-error">{item.error.precondition}</p>
          )}
        </article>
      ))}
      {preview?.status === "invalid" && selected.length > 0 && (
        <div className="state-error" role="alert">
          <AlertCircle size={14} />
          <span>
            选中的变化尚不成立：
            {preview.errors.map((e) => e.precondition).join("；")}
            。请调整选择或修改正文后重新检查。
          </span>
        </div>
      )}
      <small>已选择 {selected.length} 项状态变化</small>
    </div>
  );
}

type Ledger = {
  status: "ready" | "invalid";
  empty: boolean;
  entity_labels: Record<string, string>;
  errors: StateError[];
  snapshot: null | {
    inventories: Record<
      string,
      Record<string, { quantity: number; updated_by: string }>
    >;
    knowledge: Record<string, Record<string, { learned_by_event: string }>>;
    facts: Record<string, string>;
    hooks: Record<
      string,
      {
        description: string;
        payoff_condition: string;
        window_start: number;
        window_end: number;
        status: string;
        planted_by: string;
        resolved_by?: string;
        resolution_explanation?: string;
        resolution_timing?: string;
      }
    >;
    event_sources: Record<
      string,
      { chapter_id: string; version: number; quote: string }
    >;
  };
};
function at(position: number) {
  return position === 0
    ? "初始设定"
    : `第${Math.floor(position / 100000)}卷第${position % 100000}章`;
}
export function StoryLedger({
  projectId,
  revision,
  onSource,
}: {
  projectId: string;
  revision: number;
  onSource: (id: string) => void;
}) {
  const [data, setData] = useState<Ledger | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setData(null);
    setError("");
    api<Ledger>(`/projects/${projectId}/story-state`)
      .then((value) => {
        if (active) setData(value);
      })
      .catch((e) => {
        if (active) setError((e as Error).message);
      });
    return () => {
      active = false;
    };
  }, [projectId, revision]);
  const name = (id: string) => data?.entity_labels[id] || id;
  const source = (eventId: string) => {
    const s = data?.snapshot?.event_sources[eventId];
    return s ? (
      <button className="text-action" onClick={() => onSource(s.chapter_id)}>
        查看原文 · 版本{s.version}
        <ArrowUpRight size={13} />
      </button>
    ) : null;
  };
  if (error)
    return (
      <div className="surface state-error" role="alert">
        {error}
      </div>
    );
  if (!data)
    return (
      <div className="surface" role="status">
        正在读取已确认的故事状态…
      </div>
    );
  if (data.status === "invalid")
    return (
      <div className="surface state-error" role="alert">
        前文修改后，有状态需要复核：
        {data.errors.map((e) => e.precondition).join("；")}
      </div>
    );
  if (data.empty)
    return (
      <div className="surface empty-ledger">
        <GitBranch size={28} />
        <h2>从一次明确的变化开始</h2>
        <p>
          在章节审阅的「记忆」中逐项确认资源、角色知情与伏笔变化，账本会保留发生顺序和原文来源。
        </p>
        <p className="quiet-note">
          既有稿件继续保留事实摘录；系统会等待你确认，不会直接把推测变成状态。
        </p>
      </div>
    );
  const s = data.snapshot!;
  return (
    <div className="story-ledger">
      <section className="surface">
        <h2>谁持有什么</h2>
        <p className="quiet-note">
          只显示明确建立过的资源数量。未记录的库存保持未知。
        </p>
        {Object.entries(s.inventories).flatMap(([holder, resources]) =>
          Object.entries(resources).map(([resource, value]) => (
            <article key={holder + resource}>
              <strong>
                {name(holder)} · {name(resource)}
              </strong>
              <span>数量 {value.quantity}</span>
              {source(value.updated_by)}
            </article>
          )),
        )}
        {!Object.keys(s.inventories).length && (
          <p className="quiet-note">尚无已确认资源。</p>
        )}
      </section>
      <section className="surface">
        <h2>每个人知道什么</h2>
        <p className="quiet-note">
          记录角色已获知的事实，不把作者知道的秘密自动传给所有人。
        </p>
        {Object.entries(s.knowledge).flatMap(([actor, facts]) =>
          Object.entries(facts).map(([fact, value]) => (
            <article key={actor + fact}>
              <strong>{name(actor)}</strong>
              <p>{s.facts[fact]}</p>
              {source(value.learned_by_event)}
            </article>
          )),
        )}
        {!Object.keys(s.knowledge).length && (
          <p className="quiet-note">尚无已确认知情变化。</p>
        )}
      </section>
      <section className="surface">
        <h2>伏笔与兑现</h2>
        {Object.entries(s.hooks).map(([id, hook], index) => (
          <article key={id}>
            <header>
              <strong>{data.entity_labels[id] || `伏笔 ${index + 1}`}</strong>
              <span className="pill">
                {hook.status === "resolved" ? "已确认兑现" : "等待兑现"}
              </span>
            </header>
            <p>{hook.description}</p>
            <p>兑现条件：{hook.payoff_condition}</p>
            <p className="quiet-note">
              计划窗口：{at(hook.window_start)} 至 {at(hook.window_end)}
            </p>
            {hook.resolution_explanation && (
              <p>兑现依据：{hook.resolution_explanation}</p>
            )}
            {source(hook.resolved_by || hook.planted_by)}
          </article>
        ))}
        {!Object.keys(s.hooks).length && (
          <p className="quiet-note">尚无已确认伏笔。</p>
        )}
      </section>
    </div>
  );
}
