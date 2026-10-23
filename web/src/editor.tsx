import { useEffect, useRef, useState } from "react";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { Markdown } from "@tiptap/markdown";
import {
  ArrowRight,
  ArrowUpRight,
  Check,
  CheckCheck,
  Eye,
  Feather,
  GitBranch,
  History,
  Leaf,
  Loader2,
  Maximize2,
  Minimize2,
  PanelRightClose,
  PanelRightOpen,
  PenLine,
  Quote,
  ShieldCheck,
  Sparkles,
  Undo2,
  X,
} from "lucide-react";
import {
  api,
  post,
  put,
  words,
  labels,
  type Project,
  type Chapter,
  type Run,
} from "./api";
import { Button, Empty, Modal } from "./ui";
import { StateChangeReview } from "./state-review";
const activeStates = ["queued", "running", "awaiting_approval", "interrupted"];

export function EditorPane({
  chapter,
  project,
  busy,
  stream,
  run,
  side,
  focus,
  toggleSide,
  toggleFocus,
  onSelectChapter,
  onRun,
  onReview,
  onSaved,
  notify,
  registerFlush,
}: {
  chapter: Chapter;
  project: Project;
  busy: boolean;
  stream: string;
  run?: Run;
  side: boolean;
  focus: boolean;
  toggleSide: () => void;
  toggleFocus: () => void;
  onSelectChapter: (id: string) => void;
  onRun: (k: string, i?: string, s?: string, autoRevise?: boolean) => void;
  onReview: () => void;
  onSaved: (c: Chapter) => void;
  notify: (s: string) => void;
  registerFlush: (fn: () => Promise<void>) => void;
}) {
  const [status, setStatus] = useState("已保存"),
    [autoRevise, setAutoRevise] = useState(
      () => localStorage.getItem("changjuan-auto-revise") !== "false",
    ),
    [title, setTitle] = useState(chapter.title),
    [count, setCount] = useState(chapter.content.length),
    [instruction, setInstruction] = useState(""),
    [versions, setVersions] = useState<any[] | null>(null),
    [comparison, setComparison] = useState<any | null>(null);
  const rev = useRef(chapter.draft_revision),
    dirty = useRef(false),
    saving = useRef<Promise<void> | null>(null),
    timer = useRef<ReturnType<typeof setTimeout> | null>(null),
    titleRef = useRef(title),
    dataRef = useRef(chapter.content);
  const pending =
    run?.chapter_id === chapter.id && run.status === "awaiting_approval";
  const editor = useEditor({
    extensions: [StarterKit, Markdown],
    content: chapter.content,
    contentType: "markdown",
    editable: !busy,
    editorProps: {
      attributes: {
        class: "manuscript",
        spellcheck: "false",
        "aria-label": "章节正文",
      },
    },
    onUpdate: ({ editor }) => {
      if (!editor.isFocused) return;
      const text = editor.getMarkdown();
      dataRef.current = text;
      dirty.current = true;
      setCount(text.length);
      setStatus("正在保存…");
      backup();
      schedule();
    },
  });
  function backup() {
    localStorage.setItem(
      "changjuan-draft-" + chapter.id,
      JSON.stringify({
        content: dataRef.current,
        title: titleRef.current,
        revision: rev.current,
      }),
    );
  }
  function schedule() {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      flush().catch(() => {});
    }, 1000);
  }
  async function flush() {
    if (timer.current) clearTimeout(timer.current);
    if (saving.current) await saving.current;
    if (!dirty.current) return;
    const text = dataRef.current,
      t = titleRef.current;
    const promise = (async () => {
      try {
        const saved = await put<Chapter>("/chapters/" + chapter.id + "/draft", {
          content: text,
          title: t,
          base_revision: rev.current,
        });
        rev.current = saved.draft_revision;
        onSaved(saved);
        if (dataRef.current === text && titleRef.current === t) {
          dirty.current = false;
          setStatus("已保存");
          localStorage.removeItem("changjuan-draft-" + chapter.id);
        } else {
          backup();
          schedule();
        }
      } catch (e) {
        setStatus("未同步 · 本机已保留");
        notify((e as Error).message);
        throw e;
      }
    })();
    saving.current = promise;
    try {
      await promise;
    } finally {
      saving.current = null;
    }
  }
  useEffect(() => {
    registerFlush(flush);
    return () => {
      if (timer.current) clearTimeout(timer.current);
      registerFlush(async () => {});
    };
  }, [editor]);
  useEffect(() => {
    editor?.setEditable(!busy);
  }, [busy, editor]);
  useEffect(() => {
    if (!dirty.current && chapter.draft_revision !== rev.current) {
      rev.current = chapter.draft_revision;
      dataRef.current = chapter.content;
      titleRef.current = chapter.title;
      setTitle(chapter.title);
      editor?.commands.setContent(chapter.content, {
        contentType: "markdown",
        emitUpdate: false,
      });
      setCount(chapter.content.length);
      setStatus("已保存");
    }
  }, [chapter.draft_revision, chapter.content, editor]);
  useEffect(() => {
    const stored = localStorage.getItem("changjuan-draft-" + chapter.id);
    if (stored) {
      try {
        const b = JSON.parse(stored);
        if (
          b.content !== chapter.content &&
          b.revision === chapter.draft_revision
        ) {
          dataRef.current = b.content;
          titleRef.current = b.title;
          setTitle(b.title);
          editor?.commands.setContent(b.content, {
            contentType: "markdown",
            emitUpdate: false,
          });
          dirty.current = true;
          setStatus("已恢复本机草稿");
          setCount(b.content.length);
          schedule();
        }
      } catch {}
    }
  }, [editor]);
  useEffect(() => {
    const prevent = (e: BeforeUnloadEvent) => {
      if (dirty.current) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "s") {
        e.preventDefault();
        flush().catch(() => {});
      }
    };
    window.addEventListener("beforeunload", prevent);
    window.addEventListener("keydown", key);
    return () => {
      window.removeEventListener("beforeunload", prevent);
      window.removeEventListener("keydown", key);
    };
  }, [editor]);
  async function act(kind: string) {
    try {
      let selection = "";
      if (kind === "rewrite") {
        const { from, to } = editor!.state.selection;
        const doc = editor!.state.doc;
        if (from === to) {
          notify("请先在正文中选中要改写的片段");
          return;
        }
        // Persisted Markdown has blank lines between paragraphs. A plain
        // textBetween(..., '\n') cannot identify a multi-paragraph source.
        if (from <= 1 && to >= doc.content.size - 1) {
          selection = dataRef.current;
        } else {
          const candidates = [
            doc.textBetween(from, to, "\n\n", "\n"),
            editor!.storage.markdown.manager?.serialize(
              doc.cut(from, to).toJSON(),
            ),
          ];
          selection =
            candidates.find(
              (text) =>
                text?.trim() && dataRef.current.split(text).length === 2,
            ) || "";
          if (!selection) {
            notify("选段无法唯一对应原稿，请选完整段落或缩小选区后重试");
            return;
          }
        }
      }
      // Capture the range before saving can refresh the editor instance.
      await flush();
      onRun(kind, instruction, selection, autoRevise);
    } catch {}
  }
  return (
    <section className="editor-pane">
      <header className="editor-top">
        <div>
          <select
            className="chapter-select"
            aria-label="选择章节"
            value={chapter.id}
            disabled={busy}
            onChange={(e) => onSelectChapter(e.target.value)}
          >
            {project.chapters.map((c) => (
              <option key={c.id} value={c.id}>
                第{c.volume}卷 · {c.title}
              </option>
            ))}
          </select>
          <span className={"status-tag " + chapter.status}>
            {chapter.stale ? "历史稿待复核" : labels[chapter.status]}
          </span>
        </div>
        <div className="editor-tools">
          <span className="save-status">
            <Check size={12} />
            {status}
          </span>
          <button
            title="版本历史"
            className="icon-btn"
            onClick={async () => {
              try {
                setVersions(await api("/chapters/" + chapter.id + "/versions"));
              } catch (e) {
                notify((e as Error).message);
              }
            }}
          >
            <History size={17} />
          </button>
          <button
            title={focus ? "退出专注" : "专注写作"}
            className="icon-btn"
            onClick={toggleFocus}
          >
            {focus ? <Minimize2 size={17} /> : <Maximize2 size={17} />}
          </button>
          {!focus && (
            <button
              title="切换故事侧栏"
              className="icon-btn"
              onClick={toggleSide}
            >
              {side ? (
                <PanelRightClose size={17} />
              ) : (
                <PanelRightOpen size={17} />
              )}
            </button>
          )}
        </div>
      </header>
      <div className="editor-scroll">
        <div className="paper">
          <span className="paper-chapter">
            CHAPTER {String(chapter.number).padStart(2, "0")}
          </span>
          <input
            className="chapter-title-input"
            value={title}
            onChange={(e) => {
              setTitle(e.target.value);
              titleRef.current = e.target.value;
              dirty.current = true;
              backup();
              schedule();
            }}
            aria-label="章节标题"
            disabled={busy}
          />
          <div className="paper-divider">
            <i />
            <span>·</span>
            <i />
          </div>
          {busy &&
          run?.node === "writing" &&
          run.chapter_id === chapter.id &&
          stream ? (
            <div className="stream-manuscript pre-wrap">
              {stream}
              <span className="writing-cursor" />
            </div>
          ) : (
            <EditorContent editor={editor} />
          )}{" "}
          {!chapter.content && !busy && (
            <div className="editor-placeholder">
              <Feather size={30} strokeWidth={1} />
              <p>让故事，从这一行开始。</p>
              <span>你可以亲自写下第一句话，也可以和长卷一起创作。</span>
              <Button
                variant="primary"
                onClick={() => act("chapter")}
                disabled={!!run && activeStates.includes(run.status)}
              >
                <Sparkles size={15} />
                一起写这一章
              </Button>
            </div>
          )}
        </div>
      </div>
      {pending && (
        <div className="chapter-approval-callout" role="status">
          <div>
            <strong>本章已生成，等待你确认</strong>
            <p>审阅正文与记忆后，即可定稿并继续下一章。</p>
          </div>
          <Button variant="primary small" onClick={onReview}>
            <CheckCheck size={15} />
            审阅并确认本章
          </Button>
        </div>
      )}
      <div className="composer">
        <div className="composer-input">
          <Sparkles size={17} />
          <input
            aria-label="本章创作要求"
            placeholder="告诉长卷你的想法：让这场对话更克制一些…"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            disabled={busy}
          />
          <label
            className="revision-toggle"
            title="创作本章时最多自动修订三轮，可能增加等待和费用。关闭后仍会审阅，修改由你决定。"
          >
            <input
              type="checkbox"
              aria-label="自动修订"
              checked={autoRevise}
              disabled={busy}
              onChange={(e) => {
                setAutoRevise(e.target.checked);
                localStorage.setItem(
                  "changjuan-auto-revise",
                  String(e.target.checked),
                );
              }}
            />
            自动修订
          </label>
        </div>
        <div className="composer-actions">
          <div className="button-row">
            <Button
              variant="small ghost"
              disabled={
                busy || !chapter.content || run?.status === "awaiting_approval"
              }
              onClick={() => act("rewrite")}
            >
              <PenLine size={13} />
              改写选段
            </Button>
            <Button
              variant="small ghost"
              disabled={
                busy || !chapter.content || run?.status === "awaiting_approval"
              }
              onClick={() => act("review")}
            >
              <ShieldCheck size={13} />
              检查与记忆提案
            </Button>
          </div>
          <Button
            variant="primary small"
            disabled={busy || (!!run && activeStates.includes(run.status))}
            onClick={() => act("chapter")}
          >
            {busy ? (
              <Loader2 size={14} className="spin" />
            ) : (
              <Sparkles size={14} />
            )}{" "}
            {chapter.content ? "重新创作" : "创作本章"}
            <ArrowRight size={13} />
          </Button>
        </div>
      </div>
      <footer className="editor-footer">
        <span>
          {words(count)} 字（含标点） <i />
          目标 {words(chapter.plan.target_words || project.chapter_words)} 字
        </span>
        <span>
          每一笔，都有来处。<kbd>⌘ S</kbd>
        </span>
      </footer>
      {versions && (
        <Modal
          title="故事的每一个版本"
          subtitle="恢复会生成草稿，重新审核确认后才更新正式正文与记忆。"
          onClose={() => setVersions(null)}
          wide
        >
          {versions.length ? (
            versions.map((v) => (
              <div className="version-row" key={v.id}>
                <div>
                  <strong>
                    第 {v.version} 版 · {v.title}
                  </strong>
                  <small>
                    {new Date(v.created).toLocaleString("zh-CN")} ·{" "}
                    {v.content.length} 字
                  </small>
                </div>
                <Button variant="small" onClick={() => setComparison(v)}>
                  <Eye size={13} />
                  比较
                </Button>
                <Button
                  variant="small"
                  disabled={v.content === dataRef.current && v.title === title}
                  onClick={async () => {
                    try {
                      await flush();
                      const c = await post<Chapter>(
                        "/chapters/" + chapter.id + "/restore",
                        { version: v.version, base_revision: rev.current },
                      );
                      onSaved(c);
                      setVersions(null);
                      notify("历史版本已恢复为草稿");
                    } catch (e) {
                      notify((e as Error).message);
                    }
                  }}
                >
                  <Undo2 size={13} />
                  恢复草稿
                </Button>
              </div>
            ))
          ) : (
            <Empty icon={History} title="定稿后，这里会留下一份记忆">
              每次确认定稿都会保存一个完整版本。
            </Empty>
          )}
        </Modal>
      )}
      {comparison && (
        <Modal
          title={"比较第 " + comparison.version + " 版与当前稿"}
          onClose={() => setComparison(null)}
          wide
        >
          <div className="compare-grid">
            <article>
              <h4>第 {comparison.version} 版</h4>
              <div className="pre-wrap">{comparison.content}</div>
            </article>
            <article>
              <h4>当前稿</h4>
              <div className="pre-wrap">{dataRef.current}</div>
            </article>
          </div>
        </Modal>
      )}
    </section>
  );
}

export function ReviewPanel({
  project: p,
  chapter: ch,
  run,
  busy,
  approve,
  onSource,
  tab,
  setTab,
  onClose,
}: {
  project: Project;
  chapter: Chapter;
  run?: Run;
  busy: boolean;
  approve: (
    a?: string[],
    o?: Record<string, string>,
    n?: string,
    f?: number[],
    s?: string,
    stateChanges?: number[],
  ) => void;
  onSource: (id: string) => void;
  tab: string;
  setTab: (tab: string) => void;
  onClose: () => void;
}) {
  const [selected, setSelected] = useState<string[]>([]),
    [overrides, setOverrides] = useState<Record<string, string>>({}),
    [editing, setEditing] = useState(""),
    [note, setNote] = useState(""),
    [facts, setFacts] = useState<number[]>([]),
    [stateChanges, setStateChanges] = useState<number[]>([]),
    [stateValid, setStateValid] = useState(true),
    [summary, setSummary] = useState(ch.proposal.chapter_summary || "");
  const patches = ch.proposal.patches || [],
    review = ch.review;
  const pending =
    run?.chapter_id === ch.id && run.status === "awaiting_approval";
  const needsReason =
    !review.passed || review.length_check?.within_range === false;
  useEffect(() => {
    setSelected(ch.proposal.accepted_patches || patches.map((x) => x.name));
    setOverrides({});
    setNote("");
    setStateChanges(ch.proposal.accepted_state_changes || []);
    setStateValid(true);
    setSummary(ch.proposal.chapter_summary || "");
    setFacts(
      ch.proposal.accepted_facts || (ch.proposal.facts || []).map((_, i) => i),
    );
  }, [JSON.stringify(ch.proposal), pending]);
  return (
    <aside className="review-sidebar">
      <div className="review-tabs">
        {[
          ["plan", "章节规划"],
          ["review", "审阅"],
          ["memory", "记忆"],
        ].map(([k, t]) => (
          <button
            key={k}
            className={tab === k ? "active" : ""}
            onClick={() => setTab(k)}
          >
            {t}
            {k === "review" && pending && <i className="nav-dot" />}
          </button>
        ))}
        <button
          className="review-close"
          title="收起审阅侧栏"
          aria-label="收起审阅侧栏"
          onClick={onClose}
        >
          <X size={16} />
        </button>
      </div>
      <div className="review-content">
        {tab === "plan" && (
          <>
            <div className="side-section">
              <span className="eyebrow">THIS CHAPTER</span>
              <h3>这一章，要走向哪里</h3>
              <p>{ch.plan.goal || "先在故事规划中，为这一章明确一个目标。"}</p>
            </div>
            {ch.plan.scenes?.map((s, i) => (
              <div className="scene-detail" key={i}>
                <span>{String(i + 1).padStart(2, "0")}</span>
                <article>
                  <h4>{s.title}</h4>
                  <p>{s.conflict}</p>
                  {s.cost && <small>代价 · {s.cost}</small>}
                </article>
              </div>
            ))}
            <div className="side-section">
              <h4>必须记住的事</h4>
              {ch.plan.constraints?.map((c, i) => (
                <p className="constraint" key={i}>
                  <ShieldCheck size={13} />
                  {c}
                </p>
              ))}
              {!ch.plan.constraints?.length && (
                <p className="muted">这里会记录本章不可违背的设定。</p>
              )}
            </div>
            {!!ch.plan.hooks?.length && (
              <div className="side-section">
                <h4>伏笔与回响</h4>
                {ch.plan.hooks.map((c, i) => (
                  <p className="constraint" key={i}>
                    <GitBranch size={13} />
                    {c}
                  </p>
                ))}
              </div>
            )}
            <div className="side-section evidence-section">
              <h4>长卷查阅的依据</h4>
              <small>
                {ch.context.retrieval_mode ||
                  "创作开始后，相关历史会出现在这里。"}
              </small>
              {ch.context.evidence?.map((e, i) => (
                <button key={i} onClick={() => onSource(e.chapter_id)}>
                  <Quote size={12} />
                  <p>{e.content.slice(0, 140)}…</p>
                  <span>
                    正文来源 · 版本 {e.version}
                    <ArrowUpRight size={12} />
                  </span>
                </button>
              ))}
            </div>
          </>
        )}
        {tab === "review" && (
          <>
            {review.total !== undefined ? (
              <>
                <div className="review-score">
                  <span
                    className={
                      "score-ring " + (!review.passed ? "needs-work" : "")
                    }
                  >
                    {review.total.toFixed(1)}
                    <small>/ 10</small>
                  </span>
                  <div>
                    <strong>
                      {review.passed
                        ? review.length_check?.within_range === false
                          ? "编辑评分达标，篇幅仍需处理"
                          : "编辑评分已达标"
                        : "还有值得打磨的地方"}
                    </strong>
                    <p>
                      {review.review_mode || "加权编辑评分"} ·{" "}
                      {review.passed ? "已通过" : "需处理"}
                    </p>
                  </div>
                </div>
                {Object.entries(review.scores || {}).map(([k, v]) => (
                  <div className="dimension-score" key={k}>
                    <span>{k}</span>
                    <i>
                      <b style={{ width: v * 10 + "%" }} />
                    </i>
                    <strong>{v}</strong>
                  </div>
                ))}
                {review.length_check && !review.length_check.within_range && (
                  <p className="length-warning">
                    当前 {review.length_check.characters.toLocaleString()}{" "}
                    字符，目标 {review.length_check.target.toLocaleString()}
                    ，超出{" "}
                    {review.length_check.minimum != null
                      ? `${review.length_check.minimum}—${review.length_check.maximum}字符`
                      : "±20%"}{" "}
                    的篇幅范围。可继续修改，或记录原因后保留这一版。
                  </p>
                )}
                <p className="review-summary">{review.summary}</p>
                {review.findings?.map((f, i) => (
                  <article className={"finding " + f.severity} key={i}>
                    <header>
                      <span>
                        {f.severity === "critical"
                          ? "需优先处理"
                          : f.severity === "warning"
                            ? "编辑提醒"
                            : "打磨建议"}
                      </span>
                      <small>{f.dimension}</small>
                    </header>
                    {f.quote && <blockquote>{f.quote}</blockquote>}
                    <p>{f.suggestion}</p>
                    {f.evidence && <small>依据 · {f.evidence}</small>}
                  </article>
                ))}
                <p className="quiet-note">
                  原创性分数仅表示编辑风险判断，此结果未进行全网查重。
                </p>
              </>
            ) : (
              <Empty icon={ShieldCheck} title="多一双编辑的眼睛">
                完成正文后，长卷会从一致性、文笔、人设和原创性风险四个维度提出建议。
              </Empty>
            )}
            {pending && (
              <div className="review-next">
                <p>正文审阅完成后，还可以确认本章带来的记忆变化。</p>
                <Button variant="primary" onClick={() => setTab("memory")}>
                  审阅记忆变化
                  <ArrowRight size={14} />
                </Button>
              </div>
            )}
          </>
        )}
        {tab === "memory" && (
          <>
            <div className="side-section">
              <span className="eyebrow">STORY MEMORY</span>
              <h3>{patches.length ? "本章带来的变化" : "记忆，随故事生长"}</h3>
              <p>
                {patches.length
                  ? "选择你认可的变化，和正文一起定稿。"
                  : "本章审核后，长卷会提取事实、整理记忆并准备下一章。"}
              </p>
            </div>
            {!!ch.proposal.state_changes?.length && (
              <StateChangeReview
                chapter={ch}
                selected={stateChanges}
                onSelect={setStateChanges}
                onValid={setStateValid}
                disabled={!pending}
              />
            )}
            {ch.proposal.chapter_summary && (
              <div className="side-section">
                <h4>本章摘要</h4>
                <p>
                  核对发生时间和行动结果；这段摘要会用于后续检索，并同步到目前剧情。
                </p>
                <textarea
                  aria-label="本章摘要"
                  rows={5}
                  disabled={!pending}
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                />
              </div>
            )}
            {patches.map((patch) => (
              <article className="patch" key={patch.name}>
                <header>
                  <label>
                    <input
                      type="checkbox"
                      checked={selected.includes(patch.name)}
                      disabled={!pending}
                      onChange={(e) =>
                        setSelected((a) =>
                          e.target.checked
                            ? [...a, patch.name]
                            : a.filter((x) => x !== patch.name),
                        )
                      }
                    />
                    <strong>{patch.name}</strong>
                  </label>
                  <button
                    className="text-action"
                    onClick={() =>
                      setEditing(editing === patch.name ? "" : patch.name)
                    }
                  >
                    {editing === patch.name ? "收起" : "查看差异"}
                  </button>
                </header>
                <p>{patch.reason}</p>
                {editing === patch.name && (
                  <div className="patch-diff">
                    <details>
                      <summary>原记忆</summary>
                      <div className="pre-wrap">
                        {p.memory.find((m) => m.name === patch.name)?.content}
                      </div>
                    </details>
                    <label>
                      更新后的完整内容
                      <textarea
                        aria-label={"编辑记忆 " + patch.name}
                        rows={8}
                        value={overrides[patch.name] ?? patch.content}
                        onChange={(e) =>
                          setOverrides((o) => ({
                            ...o,
                            [patch.name]: e.target.value,
                          }))
                        }
                        disabled={!pending}
                      />
                    </label>
                  </div>
                )}
              </article>
            ))}
            {!!ch.proposal.facts?.length && (
              <div className="side-section">
                <h4>有来源的故事事实</h4>
                {ch.proposal.facts.map((f, i) => (
                  <article className="patch" key={i}>
                    <header>
                      <label>
                        <input
                          type="checkbox"
                          disabled={!pending}
                          checked={facts.includes(i)}
                          onChange={(e) =>
                            setFacts((xs) =>
                              e.target.checked
                                ? [...xs, i]
                                : xs.filter((x) => x !== i),
                            )
                          }
                        />
                        <strong>
                          {f.subject} · {f.predicate}
                        </strong>
                      </label>
                    </header>
                    <p>{f.value}</p>
                    <blockquote className="fact-quote">{f.quote}</blockquote>
                  </article>
                ))}
              </div>
            )}
          </>
        )}
      </div>
      {pending && (
        <div className="approval-box" aria-label="本章定稿操作">
          {tab !== "memory" ? (
            <Button variant="primary" onClick={() => setTab("memory")}>
              <ArrowRight size={15} />
              审阅记忆并定稿
            </Button>
          ) : (
            <>
              <label>
                {needsReason
                  ? "继续定稿的原因（必填）"
                  : "给这次定稿留句话（可选）"}
                <textarea
                  aria-label="定稿备注"
                  rows={2}
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder={
                    needsReason
                      ? "说明已知问题及你的创作决定"
                      : "例如：保留这一版更克制的结尾。"
                  }
                />
              </label>
              <Button
                variant="primary"
                disabled={
                  busy ||
                  (needsReason && !note.trim()) ||
                  (stateChanges.length > 0 && !stateValid)
                }
                onClick={() =>
                  approve(
                    selected,
                    Object.fromEntries(
                      Object.entries(overrides).filter(([k]) =>
                        selected.includes(k),
                      ),
                    ),
                    note,
                    facts,
                    summary,
                    stateChanges,
                  )
                }
              >
                <CheckCheck size={16} />
                确认本章与 {selected.length} 项记忆
                {stateChanges.length > 0
                  ? `、${stateChanges.length}项状态`
                  : ""}
              </Button>
              <small>保存版本、保留来源，并为下一章做好准备。</small>
            </>
          )}
        </div>
      )}
      <footer>
        <Leaf size={13} />
        细节被记住，故事才能走得远。
      </footer>
    </aside>
  );
}
