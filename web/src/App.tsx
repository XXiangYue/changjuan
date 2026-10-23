import { useEffect, useRef, useState } from "react";
import {
  BookOpen,
  Library,
  PenLine,
  Network,
  FlaskConical,
  Settings2,
  Plus,
  ArrowUpRight,
  ArrowRight,
  ChevronDown,
  ChevronRight,
  Search,
  Sparkles,
  Download,
  Check,
  X,
  Loader2,
  RefreshCw,
  Pause,
  Play,
  Square,
  Feather,
  Layers,
  Clock3,
  Leaf,
  Upload,
  FileText,
  ShieldCheck,
  GitBranch,
  Eye,
  AlertCircle,
} from "lucide-react";
import {
  api,
  post,
  words,
  labels,
  type Project,
  type Run,
  type Settings,
  type Research,
} from "./api";
import { Button, Empty, Modal } from "./ui";
import { CreateModal, SettingsModal, ExportModal } from "./dialogs";
import { Outline, MemoryPage, ResearchPage } from "./pages";
import { EditorPane, ReviewPanel } from "./editor";

type Page = "home" | "books" | "outline" | "write" | "memory" | "research";
const navigation: [Page, string, typeof Library][] = [
  ["home", "创作概览", Layers],
  ["books", "我的作品", Library],
  ["outline", "故事规划", GitBranch],
  ["write", "写作工作台", PenLine],
  ["memory", "故事资料库", Network],
  ["research", "实验与洞察", FlaskConical],
];
const activeStates = ["queued", "running", "awaiting_approval", "interrupted"];

export default function App() {
  const [page, setPage] = useState<Page>("home"),
    [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState<Project | null>(null);
  const [cid, setCid] = useState(""),
    [settings, setSettings] = useState<Settings | null>(null),
    [research, setResearch] = useState<Research | null>(null);
  const [modal, setModal] = useState<
      "create" | "settings" | "export" | "help" | null
    >(null),
    [toast, setToast] = useState(""),
    [loading, setLoading] = useState(true),
    [stream, setStream] = useState(""),
    [stage, setStage] = useState(""),
    [focus, setFocus] = useState(false),
    [side, setSide] = useState(() => window.innerWidth > 1000);
  const [query, setQuery] = useState(""),
    [searchResults, setSearchResults] = useState<any[] | null>(null),
    [events, setEvents] = useState<any[]>([]),
    [showEvents, setShowEvents] = useState(false),
    [historyRun, setHistoryRun] = useState<Run | undefined>(undefined);
  const flushRef = useRef<() => Promise<void>>(async () => {}),
    pidRef = useRef(""),
    toastTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ch =
    project?.chapters.find((c) => c.id === cid) || project?.chapters[0];
  const run =
    project?.runs.find((r) => activeStates.includes(r.status)) ||
    (project?.runs[0]?.status === "failed" ? project.runs[0] : undefined);
  const busy = !!run && ["running", "queued"].includes(run.status);
  function notify(message: string) {
    setToast(message);
    if (toastTimer.current) clearTimeout(toastTimer.current);
    toastTimer.current = setTimeout(() => setToast(""), 6000);
  }
  async function refresh(pid = pidRef.current) {
    const [books, s] = await Promise.all([
      api<Project[]>("/projects"),
      api<Settings>("/settings"),
    ]);
    setProjects(books);
    setSettings(s);
    if (pid) {
      const p = await api<Project>("/projects/" + pid);
      if (pidRef.current === pid) setProject(p);
    }
  }
  async function choose(p: Project, go?: Page) {
    await flushRef.current();
    pidRef.current = p.id;
    localStorage.setItem("changjuan-project", p.id);
    setLoading(true);
    setStream("");
    setEvents([]);
    setHistoryRun(undefined);
    setShowEvents(false);
    try {
      const detail = await api<Project>("/projects/" + p.id);
      setProject(detail);
      const next =
        detail.chapters.find((c) => !c.canonical_revision || c.stale) ||
        detail.chapters.at(-1);
      setCid(next?.id || "");
      if (go) setPage(go);
    } catch (e) {
      notify((e as Error).message);
    } finally {
      setLoading(false);
    }
  }
  async function navigate(next: Page) {
    try {
      await flushRef.current();
      setPage(next);
      if (next === "research") setResearch(await api("/research"));
    } catch (e) {
      notify((e as Error).message);
    }
  }
  useEffect(() => {
    (async () => {
      try {
        const books = await api<Project[]>("/projects");
        setProjects(books);
        setSettings(await api("/settings"));
        const saved = localStorage.getItem("changjuan-project");
        const p = books.find((x) => x.id === saved) || books[0];
        if (p) await choose(p);
      } catch (e) {
        notify((e as Error).message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);
  useEffect(() => {
    if (!project) return;
    const timer = setInterval(
      () => {
        refresh().catch(() => {});
      },
      busy ? 1800 : 8000,
    );
    return () => clearInterval(timer);
  }, [project?.id, busy]);
  useEffect(() => {
    if (!run || !["running", "queued"].includes(run.status)) return;
    setStream("");
    setEvents([]);
    setStage(labels[run.node] || "正在开始");
    let source: EventSource | undefined;
    let disposed = false;
    (async () => {
      const rows = await api<any[]>("/runs/" + run.id + "/events?once=true");
      if (disposed) return;
      const last = rows.findLastIndex((e) => e.type === "draft.start");
      if (run.node === "writing" && last >= 0)
        setStream(
          rows
            .slice(last)
            .filter((e) => e.type === "token")
            .map((e) => e.data.text)
            .join(""),
        );
      setEvents(rows.filter((e) => e.type !== "token").slice(-80));
      const cursor = rows.at(-1)?.seq || 0;
      source = new EventSource(
        "/api/runs/" + run.id + "/events?after=" + cursor,
      );
      source.onmessage = (e) => {
        const ev = JSON.parse(e.data);
        if (ev.type === "draft.start") setStream("");
        if (ev.type === "token") setStream((t) => t + ev.data.text);
        else setEvents((t) => [...t.slice(-80), ev]);
        if (ev.type === "stage") setStage(ev.data.message);
        if (["state", "error", "completed"].includes(ev.type)) {
          refresh().catch(() => {});
          source?.close();
        }
      };
    })().catch((e) => notify((e as Error).message));
    return () => {
      disposed = true;
      source?.close();
    };
  }, [run?.id, run?.status]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        document.getElementById("global-search")?.focus();
      }
      if (e.key === "Escape") setFocus(false);
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  async function start(
    kind: string,
    instruction = "",
    selection = "",
    autoRevise = true,
  ) {
    if (!project) return;
    try {
      await flushRef.current();
      setStream("");
      setStage("正在开始创作…");
      const r = await post<Run>("/runs", {
        project_id: project.id,
        kind,
        chapter_id: ["blueprint", "volume"].includes(kind) ? null : ch?.id,
        instruction,
        selection,
        auto_revise: autoRevise,
      });
      setProject((p) => (p ? { ...p, runs: [r, ...p.runs] } : p));
      setPage(["blueprint", "volume"].includes(kind) ? "outline" : "write");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function viewRun(r: Run) {
    if (r.chapter_id) setCid(r.chapter_id);
    await navigate(
      ["blueprint", "volume"].includes(r.kind) ? "outline" : "write",
    );
  }
  async function control(action: string) {
    if (!run) return;
    try {
      await post("/runs/" + run.id + "/" + action);
      await refresh();
      notify(
        action === "pause"
          ? "暂停请求已发送"
          : action === "cancel"
            ? "任务已取消"
            : "正在恢复任务",
      );
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function approve(
    accepted: string[] = [],
    overrides: Record<string, string> = {},
    note = "",
    facts?: number[],
    summary?: string,
    stateChanges: number[] = [],
  ) {
    if (!run || !project) return;
    try {
      await post("/runs/" + run.id + "/approve", {
        base_revision: ch?.draft_revision || 0,
        project_revision: project.revision,
        accepted_patches: accepted,
        overrides,
        note,
        accepted_facts: facts ?? null,
        accepted_state_changes: stateChanges,
        summary_override: summary || null,
      });
      notify("正在保存你确认的版本");
      await refresh();
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function recover() {
    if (!run || !project) return;
    const current = project.chapters.find((x) => x.id === run.chapter_id);
    if (!current) return;
    try {
      await post("/runs/" + run.id + "/recover-draft", {
        base_revision: current.draft_revision,
      });
      await refresh();
      await openChapter(current.id);
      notify("已恢复为未审核草稿，请检查完整性后重新审核");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function search(e: React.FormEvent) {
    e.preventDefault();
    if (!project || !query.trim()) return;
    try {
      setSearchResults(
        await api(
          "/projects/" + project.id + "/search?q=" + encodeURIComponent(query),
        ),
      );
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function importBook(file: File) {
    try {
      const res = await fetch("/api/projects/import", {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file,
      });
      const p = await res.json();
      if (!res.ok) throw new Error(p.detail);
      await refresh();
      await choose(p);
      notify("导入完成；历史章节已标记待复核");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  async function openChapter(id: string) {
    await flushRef.current();
    setCid(id);
    setStream("");
    setPage("write");
  }
  async function appendChapter(volume: number) {
    if (!project) return;
    try {
      await flushRef.current();
      const added = await post("/projects/" + project.id + "/chapters", {
        volume,
        project_revision: project.revision,
      });
      await refresh();
      setCid(added.id);
      setPage("write");
      notify("新章节已加入，可以先调整章节规划。");
    } catch (e) {
      notify((e as Error).message);
    }
  }
  const completed =
    project?.chapters.filter((c) => c.canonical_revision && !c.stale).length ||
    0;
  const nextChapter = project?.chapters.find(
    (c) => !c.canonical_revision || c.stale || c.status !== "final",
  );
  const planComplete = !!project?.chapters.length && !nextChapter;
  const title = navigation.find((n) => n[0] === page)?.[1];
  return (
    <div className={"app " + (focus ? "focus" : "")}>
      {!focus && (
        <aside className="sidebar">
          <a
            className="brand"
            href="#"
            onClick={(e) => {
              e.preventDefault();
              navigate("home");
            }}
          >
            <span className="brand-seal">卷</span>
            <div>
              <strong>
                长卷<span>Changjuan</span>
              </strong>
              <small>让故事，慢慢生长。</small>
            </div>
          </a>
          <div className="workspace-label">
            你的创作空间 <span>LOCAL</span>
          </div>
          <nav>
            {navigation.map(([id, label, Icon]) => (
              <button
                key={id}
                aria-label={label}
                title={label}
                className={page === id ? "active" : ""}
                onClick={() => navigate(id)}
              >
                <Icon size={18} strokeWidth={1.6} />
                <span>{label}</span>
                {id === "write" && run?.status === "awaiting_approval" && (
                  <i className="nav-dot" />
                )}
              </button>
            ))}
          </nav>
          <div className="sidebar-divider" />
          <div className="sidebar-section">
            正在创作{" "}
            <button
              title="新建作品"
              className="icon-btn"
              onClick={() => setModal("create")}
            >
              <Plus size={15} />
            </button>
          </div>
          <div className="project-list">
            {projects.slice(0, 5).map((p, i) => (
              <button
                onClick={() => choose(p, "write")}
                key={p.id}
                className={p.id === project?.id ? "selected" : ""}
              >
                <span className={"mini-cover tone" + i}>
                  <BookOpen size={14} />
                </span>
                <span className="truncate">{p.title}</span>
                {p.id === project?.id && <span className="live-dot" />}
              </button>
            ))}
            {!projects.length && (
              <p className="sidebar-empty">你的第一部作品，正在等你。</p>
            )}
          </div>
          <div className="sidebar-bottom">
            <button className="help-card" onClick={() => setModal("help")}>
              <Feather size={18} />
              <span>
                从一个灵感开始<small>认识你的创作搭档</small>
              </span>
              <ArrowUpRight size={15} />
            </button>
            <div className="local-profile">
              <span className="avatar">作</span>
              <div>
                <strong>本地创作者</strong>
                <small>
                  <span className="live-dot" /> 数据保存在此电脑
                </small>
              </div>
              <button
                className="icon-btn"
                title="模型与设置"
                onClick={() => setModal("settings")}
              >
                <Settings2 size={18} />
              </button>
            </div>
          </div>
        </aside>
      )}
      <main className="main">
        {!focus && (
          <header className="topbar">
            <div className="breadcrumb">
              创作空间 <ChevronRight size={14} />
              <strong>{title}</strong>
            </div>
            <form onSubmit={search} className="global-search">
              <Search size={15} />
              <input
                id="global-search"
                aria-label="搜索故事资料"
                placeholder="搜索故事中的人、事、物…"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <kbd>⌘ K</kbd>
            </form>
            <button className="connection" onClick={() => setModal("settings")}>
              <span
                className={"live-dot " + (!settings?.configured ? "off" : "")}
              />
              {settings?.configured ? "DeepSeek Flash" : "连接创作模型"}
              <ChevronDown size={12} />
            </button>
          </header>
        )}
        {run && (
          <div
            className={"run-banner " + (run.status === "failed" ? "error" : "")}
          >
            <span className="run-indicator">
              {busy ? (
                <Loader2 size={15} className="spin" />
              ) : run.status === "awaiting_approval" ? (
                <Eye size={16} />
              ) : (
                <AlertCircle size={16} />
              )}
            </span>
            <span>
              <strong>
                {busy
                  ? stage || labels[run.node] || "正在创作"
                  : labels[run.status]}
              </strong>
              <small>
                {run.error ||
                  (run.status === "awaiting_approval"
                    ? "正文与记忆差异已准备好，等待你的决定。"
                    : "每一步进度都会保存。")}
              </small>
            </span>
            <div className="run-actions">
              <button
                onClick={() => {
                  setHistoryRun(run);
                  setShowEvents(!showEvents);
                }}
              >
                运行记录
              </button>
              {busy ? (
                <>
                  <button onClick={() => control("pause")}>
                    <Pause size={13} />
                    暂停
                  </button>
                  <button onClick={() => control("cancel")}>
                    <Square size={12} />
                    取消
                  </button>
                </>
              ) : run.status === "awaiting_approval" ? (
                <>
                  <button onClick={() => viewRun(run)}>
                    查看审阅
                    <ArrowRight size={13} />
                  </button>
                  <button onClick={() => control("cancel")}>放弃本次</button>
                </>
              ) : (
                <>
                  <button onClick={() => control("resume")}>
                    <Play size={12} />
                    恢复
                  </button>
                  {run.chapter_id && (
                    <button onClick={recover}>恢复已生成正文</button>
                  )}
                  <button onClick={() => control("cancel")}>关闭任务</button>
                </>
              )}
            </div>
          </div>
        )}
        {showEvents && (
          <div className="event-panel">
            <header>
              <strong>可追溯的创作过程</strong>
              <span>计划 · 依据 · 动作 · 结果</span>
              <button className="icon-btn" onClick={() => setShowEvents(false)}>
                <X size={16} />
              </button>
            </header>
            {!historyRun &&
              events
                .filter((e) => e.type !== "token")
                .map((e, i) => (
                  <div key={i}>
                    <span className="event-dot" />
                    <small>{e.type}</small>
                    <span>
                      {e.data.message ||
                        e.data.role ||
                        JSON.stringify(e.data).slice(0, 180)}
                    </span>
                  </div>
                ))}
            {(historyRun || !events.length) && (
              <EventHistory run={historyRun || run} />
            )}
          </div>
        )}
        {loading ? (
          <div className="loading">
            <Loader2 className="spin" />
            正在整理你的创作空间…
          </div>
        ) : (
          <>
            {(page === "home" || page === "books") && (
              <div className="page dashboard">
                <div className="page-heading">
                  <div>
                    <span className="eyebrow">A SPACE FOR YOUR STORIES</span>
                    <h1>
                      {page === "home"
                        ? "故事的下一页，从这里开始。"
                        : "每一个世界，都值得被写下。"}
                    </h1>
                    <p>
                      {page === "home"
                        ? "把灵感交给文字，把漫长的故事交给长卷一起记住。"
                        : "你创造的世界、人物和那些尚未兑现的承诺，都在这里。"}
                    </p>
                  </div>
                  <Button variant="primary" onClick={() => setModal("create")}>
                    <Plus size={16} />
                    新建作品
                  </Button>
                </div>
                <div className="section-title">
                  <h2>
                    {page === "home" ? "继续你的故事" : "我的作品"}
                    <span>{projects.length} 部作品</span>
                  </h2>
                  <label className="text-action">
                    <Upload size={14} />
                    导入作品
                    <input
                      hidden
                      type="file"
                      accept=".zip"
                      onChange={(e) => {
                        if (e.target.files?.[0]) importBook(e.target.files[0]);
                      }}
                    />
                  </label>
                </div>
                {project && page === "home" && (
                  <div className="hero-book">
                    <div className="hero-grain" />
                    <div className="book-object">
                      <div className="book-spine" />
                      <div className="book-cover">
                        <span className="cover-top">长卷 · 原创作品</span>
                        <div className="cover-lines" />
                        <div className="cover-title">{project.title}</div>
                        <div className="cover-landscape">
                          <i />
                          <i />
                          <i />
                        </div>
                        <span className="cover-bottom">
                          一字一句，自成山河
                          <br />
                          <small>CHANGJUAN / {project.genre}</small>
                        </span>
                      </div>
                    </div>
                    <div className="hero-info">
                      <div className="hero-meta">
                        <span className="pill">{project.genre}</span>
                        <span>
                          <span className="live-dot" />{" "}
                          {planComplete
                            ? "当前计划已完成"
                            : completed
                              ? "创作进行中"
                              : "故事正在萌芽"}
                        </span>
                      </div>
                      <h2>{project.title}</h2>
                      <p className="hero-logline">
                        {project.logline || project.premise}
                      </p>
                      <div className="book-stats">
                        <div>
                          <strong>{words(project.words)}</strong>
                          <small>已定稿字数</small>
                        </div>
                        <div>
                          <strong>
                            {completed}
                            <em> / {project.chapters.length}</em>
                          </strong>
                          <small>已完成章节</small>
                        </div>
                        <div>
                          <strong>
                            {
                              project.memory.filter((m) => m.revision > 0)
                                .length
                            }
                          </strong>
                          <small>故事记忆</small>
                        </div>
                      </div>
                      <div className="progress-row">
                        <div className="progress">
                          <i
                            style={{
                              width:
                                Math.min(
                                  100,
                                  (completed / (project.chapters.length || 1)) *
                                    100,
                                ) + "%",
                            }}
                          />
                        </div>
                        <span>
                          {Math.round(
                            (completed / (project.chapters.length || 1)) * 100,
                          )}
                          %
                        </span>
                      </div>
                      <div className="hero-buttons">
                        <Button
                          variant="primary"
                          onClick={() => navigate("write")}
                        >
                          <PenLine size={16} />
                          {planComplete
                            ? "回看定稿"
                            : completed
                              ? "继续创作"
                              : "开始创作"}
                          <ArrowRight size={15} />
                        </Button>
                        <Button
                          variant="ghost"
                          onClick={() => navigate("outline")}
                        >
                          查看故事规划
                          <ChevronRight size={14} />
                        </Button>
                      </div>
                    </div>
                    <span className="hero-mark">
                      {planComplete ? "PAGES COMPLETED" : "STORY IN PROGRESS"}
                      <br />
                      {planComplete ? "下一程，等你提笔。" : "每一页，都算数。"}
                    </span>
                  </div>
                )}
                {(page === "books" || !project) && (
                  <div className="books-grid">
                    {projects.map((p, i) => (
                      <button
                        key={p.id}
                        className="book-card"
                        onClick={() => choose(p, "write")}
                      >
                        <div className={"book-card-art tone" + i}>
                          <span>{p.title}</span>
                          <small>{p.genre} / 长卷原创</small>
                        </div>
                        <h3>{p.title}</h3>
                        <p>{p.logline || p.premise}</p>
                        <footer>
                          <span>
                            {words(p.words || 0)} 字 · {p.completed || 0} 章
                          </span>
                          <ArrowUpRight size={16} />
                        </footer>
                      </button>
                    ))}
                    <button
                      className="new-book-card"
                      onClick={() => setModal("create")}
                    >
                      <span>
                        <Plus size={25} />
                      </span>
                      <strong>一个新的世界</strong>
                      <small>从一个念头，写到故事的远方</small>
                    </button>
                  </div>
                )}
                {project && page === "home" && (
                  <>
                    <div className="overview-grid">
                      <section className="surface next-chapter">
                        <div className="section-title">
                          <h2>
                            <PenLine size={16} />{" "}
                            {planComplete ? "已写下的故事" : "下一页，写什么"}
                          </h2>
                          <span className="quiet-tag">
                            {planComplete
                              ? `${completed} 章已定稿`
                              : `第 ${nextChapter?.number || 1} 章`}
                          </span>
                        </div>
                        <h3>
                          {planComplete
                            ? "当前章节已全部定稿。"
                            : nextChapter?.title || "故事的开始"}
                        </h3>
                        <p>
                          {planComplete
                            ? "回看正文，带走文稿，或规划下一卷。接下来的方向，由你决定。"
                            : nextChapter?.plan.goal ||
                              "先和你的创作搭档聊聊这个世界，让故事的方向变得清晰。"}
                        </p>
                        <div className="chapter-pills">
                          {!planComplete &&
                            nextChapter?.plan.characters
                              ?.slice(0, 3)
                              .map((x) => <span key={x}>{x}</span>)}
                        </div>
                        <footer>
                          <span>
                            <FileText size={14} />{" "}
                            {planComplete
                              ? `${words(project.words)} 字 · 可导出`
                              : `目标 ${words(nextChapter?.plan.target_words || project.chapter_words)} 字`}
                          </span>
                          <button
                            className="text-action"
                            onClick={() => {
                              if (nextChapter) setCid(nextChapter.id);
                              navigate(
                                planComplete || !project.revision
                                  ? "outline"
                                  : "write",
                              );
                            }}
                          >
                            {planComplete
                              ? "规划下一卷"
                              : project.revision
                                ? "进入工作台"
                                : "规划我的故事"}
                            <ArrowRight size={15} />
                          </button>
                        </footer>
                      </section>
                      <section className="surface memory-overview">
                        <div className="section-title">
                          <h2>
                            <Leaf size={16} /> 故事，长卷替你记着
                          </h2>
                          <button
                            className="icon-btn"
                            title="查看故事资料库"
                            onClick={() => navigate("memory")}
                          >
                            <ArrowUpRight size={17} />
                          </button>
                        </div>
                        <div className="memory-mini-grid">
                          {[
                            ["世界观", "规则与边界"],
                            ["有关角色", "人物与关系"],
                            ["伏笔", "铺设与回响"],
                            ["目前剧情", "故事的此刻"],
                          ].map(([n, h], i) => (
                            <button key={n} onClick={() => navigate("memory")}>
                              <span className="memory-mini-icon">
                                {i === 0 ? (
                                  <Network size={19} />
                                ) : i === 1 ? (
                                  <BookOpen size={19} />
                                ) : i === 2 ? (
                                  <GitBranch size={19} />
                                ) : (
                                  <Clock3 size={19} />
                                )}
                              </span>
                              <strong>{n}</strong>
                              <small>{h}</small>
                              <ChevronRight size={13} />
                            </button>
                          ))}
                        </div>
                        <p className="quiet-note">
                          <ShieldCheck size={13} />{" "}
                          每一条事实，都能找到它在故事里的出处。
                        </p>
                      </section>
                    </div>
                    <section className="activity-section">
                      <div className="section-title">
                        <h2>最近的创作足迹</h2>
                        <span className="muted">
                          故事在生长，变化有迹可循。
                        </span>
                      </div>
                      {project.runs.slice(0, 4).map((r) => (
                        <button
                          className="activity-row"
                          key={r.id}
                          onClick={() => {
                            viewRun(r);
                            setHistoryRun(r);
                            setShowEvents(true);
                          }}
                        >
                          <span
                            className={
                              "activity-icon " +
                              (r.status === "completed" ? "done" : "")
                            }
                          >
                            {r.kind === "blueprint" ? (
                              <GitBranch size={17} />
                            ) : (
                              <Feather size={17} />
                            )}
                          </span>
                          <div>
                            <strong>
                              {r.kind === "blueprint"
                                ? "规划故事蓝图"
                                : r.kind === "volume"
                                  ? "筹备新一卷"
                                  : r.kind === "review"
                                    ? "重新审核稿件"
                                    : "创作章节正文"}
                            </strong>
                            <small>{labels[r.status]}</small>
                          </div>
                          <time>
                            {new Date(r.created).toLocaleString("zh-CN", {
                              month: "numeric",
                              day: "numeric",
                              hour: "2-digit",
                              minute: "2-digit",
                            })}
                          </time>
                          <ChevronRight size={15} />
                        </button>
                      ))}
                      {!project.runs.length && (
                        <p className="quiet-note">
                          还没有创作记录。从故事规划开始，留下第一笔。
                        </p>
                      )}
                    </section>
                  </>
                )}
                <footer className="page-footer">
                  <span>长卷 · 让故事生长</span>
                  <span>灵感由你掌舵，细节一起照顾。</span>
                  <span>
                    <span className="live-dot" /> 本地工作空间
                  </span>
                </footer>
              </div>
            )}
            {page === "outline" &&
              (project ? (
                <Outline
                  project={project}
                  run={run}
                  busy={busy}
                  start={start}
                  approve={approve}
                  refresh={refresh}
                  notify={notify}
                  openChapter={openChapter}
                  onAppend={appendChapter}
                />
              ) : (
                <Empty
                  title="每个故事，都始于一个念头"
                  action={
                    <Button
                      variant="primary"
                      onClick={() => setModal("create")}
                    >
                      创建第一部作品
                    </Button>
                  }
                >
                  告诉长卷你想写怎样的故事，一起为它画出方向。
                </Empty>
              ))}
            {page === "write" &&
              (project && ch ? (
                <div className={"writing-layout " + (!side ? "hide-side" : "")}>
                  {!focus && (
                    <aside className="chapter-sidebar">
                      <header>
                        <span className="eyebrow">MANUSCRIPT</span>
                        <h3>{project.title}</h3>
                        <small>
                          {words(project.words)} 字 · {completed} 章定稿
                        </small>
                      </header>
                      <div className="chapter-tree">
                        {project.volumes.map((v) => (
                          <section key={v.number}>
                            <h4>
                              <ChevronDown size={13} />{" "}
                              {v.title || "第" + v.number + "卷"}
                            </h4>
                            {project.chapters
                              .filter((c) => c.volume === v.number)
                              .map((c) => (
                                <button
                                  key={c.id}
                                  onClick={() => openChapter(c.id)}
                                  className={ch.id === c.id ? "active" : ""}
                                >
                                  <span
                                    className={
                                      "chapter-status " +
                                      (c.stale ? "stale" : c.status)
                                    }
                                  >
                                    {c.canonical_revision ? (
                                      <Check size={10} />
                                    ) : (
                                      c.number.toString().padStart(2, "0")
                                    )}
                                  </span>
                                  <span className="truncate">{c.title}</span>
                                  {!!c.stale && <AlertCircle size={12} />}
                                </button>
                              ))}
                          </section>
                        ))}
                      </div>
                      <button
                        className="chapter-export"
                        disabled={!!run && activeStates.includes(run.status)}
                        title={"为第" + ch.volume + "卷新增下一章"}
                        onClick={() => appendChapter(ch.volume)}
                      >
                        <Plus size={15} />
                        新增章节
                      </button>
                      <button
                        className="chapter-export"
                        onClick={() => setModal("export")}
                      >
                        <Download size={15} />
                        导出作品
                      </button>
                    </aside>
                  )}
                  <EditorPane
                    key={ch.id}
                    chapter={ch}
                    project={project}
                    busy={busy}
                    stream={stream}
                    run={run}
                    side={side}
                    focus={focus}
                    toggleSide={() => setSide(!side)}
                    toggleFocus={() => setFocus(!focus)}
                    onSelectChapter={openChapter}
                    onRun={start}
                    notify={notify}
                    onSaved={(saved) =>
                      setProject((p) =>
                        p
                          ? {
                              ...p,
                              chapters: p.chapters.map((c) =>
                                c.id === saved.id ? saved : c,
                              ),
                            }
                          : p,
                      )
                    }
                    registerFlush={(fn) => {
                      flushRef.current = fn;
                    }}
                  />
                  {side && !focus && (
                    <ReviewPanel
                      project={project}
                      chapter={ch}
                      run={run}
                      busy={busy}
                      approve={approve}
                      onSource={openChapter}
                    />
                  )}
                </div>
              ) : (
                <Empty
                  title="让第一章，成为一个开始"
                  action={
                    <Button
                      variant="primary"
                      onClick={() => setModal("create")}
                    >
                      创建作品
                    </Button>
                  }
                >
                  新建或选择一部作品，进入你的写作工作台。
                </Empty>
              ))}
            {page === "memory" &&
              (project ? (
                <MemoryPage
                  project={project}
                  refresh={refresh}
                  notify={notify}
                  onSource={openChapter}
                />
              ) : (
                <Empty title="故事的记忆，从第一笔开始">
                  先创建作品，人物、世界和伏笔会在这里慢慢生长。
                </Empty>
              ))}
            {page === "research" && (
              <ResearchPage
                data={research}
                onRefresh={async () => setResearch(await api("/research"))}
                notify={notify}
              />
            )}
          </>
        )}
      </main>
      {toast && (
        <div className="toast" role="status">
          <span>{toast}</span>
          <button
            className="icon-btn"
            onClick={() => setToast("")}
            aria-label="关闭通知"
          >
            <X size={14} />
          </button>
        </div>
      )}
      {modal === "create" && (
        <CreateModal
          onClose={() => setModal(null)}
          onCreate={async (p) => {
            setModal(null);
            await refresh();
            await choose(p, "outline");
            notify("作品已创建，现在可以规划故事蓝图。");
          }}
        />
      )}
      {modal === "settings" && (
        <SettingsModal
          settings={settings}
          onClose={() => setModal(null)}
          onSaved={() => {
            refresh();
            notify("设置已保存");
          }}
        />
      )}
      {modal === "export" && project && (
        <ExportModal project={project} onClose={() => setModal(null)} />
      )}
      {modal === "help" && (
        <Modal
          title="你掌舵故事，长卷照顾细节。"
          onClose={() => setModal(null)}
        >
          <div className="help-steps">
            {[
              ["01", "种下一个念头", "写下题材、主角处境和你想讲述的故事。"],
              ["02", "一起看清方向", "确认卷纲、人物目标与成长代价。"],
              [
                "03",
                "一章一章写下去",
                "长卷查阅记忆、创作和审阅；你可以随时编辑正文。",
              ],
              [
                "04",
                "确认，才成为故事",
                "一起审阅正文与记忆变化，定稿后保留版本与来源。",
              ],
            ].map(([n, t, d]) => (
              <div key={n}>
                <span>{n}</span>
                <article>
                  <h3>{t}</h3>
                  <p>{d}</p>
                </article>
              </div>
            ))}
          </div>
          <Button variant="primary" onClick={() => setModal("create")}>
            <Plus size={16} />
            开始一个新故事
          </Button>
        </Modal>
      )}
      {searchResults && (
        <Modal
          title={"在故事中寻找「" + query + "」"}
          onClose={() => setSearchResults(null)}
          wide
        >
          {searchResults.length ? (
            searchResults.map((x, i) => (
              <button
                className="search-result"
                key={i}
                onClick={() => {
                  openChapter(x.chapter_id);
                  setSearchResults(null);
                }}
              >
                <span className="eyebrow">已定稿来源 · 版本 {x.version}</span>
                <p>{x.content}</p>
                <span className="text-action">
                  打开来源章节
                  <ArrowUpRight size={14} />
                </span>
              </button>
            ))
          ) : (
            <Empty icon={Search} title="还没有找到相关记录">
              搜索只包含当前有效的已定稿正文。可以换个人名或关键词试试。
            </Empty>
          )}
        </Modal>
      )}
    </div>
  );
}

function EventHistory({ run }: { run?: Run }) {
  const [rows, setRows] = useState<any[]>([]);
  useEffect(() => {
    if (run)
      api("/runs/" + run.id + "/events?once=true")
        .then(setRows)
        .catch(() => {});
  }, [run?.id]);
  return (
    <>
      {rows
        .filter((x) => x.type !== "token")
        .map((e) => (
          <div key={e.seq}>
            <span className="event-dot" />
            <small>{e.type}</small>
            <span>
              {e.data.message ||
                e.data.role ||
                JSON.stringify(e.data).slice(0, 180)}
            </span>
          </div>
        ))}
    </>
  );
}
