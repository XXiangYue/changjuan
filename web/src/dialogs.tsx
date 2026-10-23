import { useState } from "react";
import {
  Plus,
  Feather,
  ArrowRight,
  Loader2,
  ShieldCheck,
  RefreshCw,
  ExternalLink,
  FileText,
  ArrowDownToLine,
} from "lucide-react";
import { post, put, type Project, type Settings } from "./api";
import { Button, Modal } from "./ui";

export function CreateModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (p: Project) => void;
}) {
  const [title, setTitle] = useState(""),
    [premise, setPremise] = useState(""),
    [genre, setGenre] = useState("玄幻成长"),
    [count, setCount] = useState(12),
    [length, setLength] = useState(2500),
    [style, setStyle] = useState(
      "克制、有画面感。以行动与对话展示人物；成长必须付出代价。",
    ),
    [saving, setSaving] = useState(false),
    [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError("");
    try {
      onCreate(
        await post("/projects", {
          title,
          premise,
          genre,
          target_chapters: count,
          chapter_words: length,
          style,
        }),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  }
  return (
    <Modal
      title="写下一个新的世界"
      subtitle="不用想好一切。一个人物、一场冲突，就足够开始。"
      onClose={onClose}
    >
      <form onSubmit={submit} className="form">
        <label>
          作品名称
          <input
            required
            maxLength={80}
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="给你的故事起个名字"
          />
        </label>
        <div className="form-row">
          <label>
            故事类型
            <select value={genre} onChange={(e) => setGenre(e.target.value)}>
              {[
                "玄幻成长",
                "仙侠修真",
                "悬疑奇幻",
                "都市现实",
                "历史幻想",
                "其他",
              ].map((x) => (
                <option key={x}>{x}</option>
              ))}
            </select>
          </label>
          <label>
            本卷章数
            <input
              type="number"
              min={1}
              max={3000}
              value={count}
              onChange={(e) => setCount(+e.target.value)}
            />
          </label>
          <label>
            每章字数（含标点）
            <input
              type="number"
              min={200}
              max={12000}
              step={100}
              value={length}
              onChange={(e) => setLength(+e.target.value)}
            />
          </label>
        </div>
        <label>
          故事的种子
          <textarea
            required
            minLength={5}
            rows={5}
            value={premise}
            onChange={(e) => setPremise(e.target.value)}
            placeholder="主角是谁？他身处怎样的困境？你希望他付出什么、得到什么？"
          />
        </label>
        <label>
          你喜欢怎样的文字
          <textarea
            rows={2}
            value={style}
            onChange={(e) => setStyle(e.target.value)}
          />
        </label>
        {error && <p className="form-error">{error}</p>}
        <footer className="modal-footer">
          <span>
            <ShieldCheck size={13} />
            作品与记忆保存在本机
          </span>
          <Button type="submit" variant="primary" disabled={saving}>
            {saving ? (
              <Loader2 className="spin" size={16} />
            ) : (
              <Feather size={16} />
            )}
            创建作品
            <ArrowRight size={15} />
          </Button>
        </footer>
      </form>
    </Modal>
  );
}

export function SettingsModal({
  settings,
  onClose,
  onSaved,
}: {
  settings: Settings | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [key, setKey] = useState(""),
    [vector, setVector] = useState(settings?.vector_enabled || false),
    [policy, setPolicy] = useState(settings?.retrieval_policy || "keyword"),
    [budget, setBudget] = useState(settings?.budget || 300),
    [state, setState] = useState(""),
    [busy, setBusy] = useState(false);
  async function save(test = false) {
    setBusy(true);
    setState("");
    try {
      await put("/settings", {
        api_key: key || null,
        budget,
        vector_enabled: vector,
        retrieval_policy: policy,
      });
      onSaved();
      if (test) {
        await post("/settings/connect");
        setState("连接成功 · deepseek-flash 已就绪");
      } else setState("设置已保存");
    } catch (e) {
      setState((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="你的创作搭档"
      subtitle="连接模型，设定预算，一起把故事写长。"
      onClose={onClose}
    >
      <div className="model-card">
        <span className="model-logo">D</span>
        <div>
          <strong>DeepSeek Flash</strong>
          <small>官方 API · 支持流式创作与工具调用</small>
        </div>
        <span className="pill">
          {settings?.configured ? "已配置" : "待连接"}
        </span>
      </div>
      <div className="form">
        <label>
          API 密钥
          <input
            type="password"
            autoComplete="new-password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder={
              settings?.configured
                ? "已安全保存 · 输入以更换"
                : "输入 DeepSeek 官方 API 密钥"
            }
          />
          <small>密钥仅保存在本机私有配置中。</small>
        </label>
        <label>
          本轮预算上限（元）
          <input
            type="number"
            min={1}
            max={300}
            value={budget}
            onChange={(e) => setBudget(+e.target.value)}
          />
          <small>
            已估算 ¥{(settings?.usage.spent || 0).toFixed(3)} · 已预留 ¥
            {(settings?.usage.reserved || 0).toFixed(3)}；按高峰单价保守估算。
          </small>
        </label>
        <label className="toggle-row">
          <span>
            <strong>语义检索</strong>
            <small>
              让语义与融合查找可用；实际方式由下方选择。首次需下载本地中文模型。
            </small>
          </span>
          <input
            type="checkbox"
            checked={vector}
            onChange={(e) => setVector(e.target.checked)}
          />
        </label>
        <p className="quiet-note">{settings?.retrieval_mode}</p>
        <label>
          历史片段查找方式
          <select
            value={policy}
            onChange={(e) => setPolicy(e.target.value as typeof policy)}
          >
            <option value="keyword">关键词 · 适合人物、道具和原句</option>
            <option value="hybrid">关键词与语义融合</option>
            <option value="semantic">语义优先 · 不可用时回退关键词</option>
            <option value="reranked">深度重排 · 更慢，需先准备本地模型</option>
          </select>
          <small>
            重排在真实正文开发集上更准，但本机可能额外等待约9秒。验收结果见实验与洞察。
          </small>
          {policy === "reranked" && !settings?.reranker?.ready && (
            <small>
              重排模型尚未准备，当前会回退融合查找。重启后需重新准备缓存，不会在查询时自动下载。
            </small>
          )}
        </label>
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const value = await post<{ ready: boolean; state: string }>(
                "/settings/reranker/prepare",
              );
              setState(
                value.ready
                  ? "本地重排模型准备成功"
                  : "未找到可用缓存；可显式下载后重试",
              );
              onSaved();
            } catch (e) {
              setState((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          准备已缓存的重排模型
        </Button>
        <Button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setState("正在下载并加载本地重排模型，可继续使用其他页面…");
            try {
              const value = await post<{ ready: boolean }>(
                "/settings/reranker/prepare?allow_download=true",
              );
              setState(
                value.ready
                  ? "本地重排模型准备成功"
                  : "模型准备未完成，当前仍可使用关键词与融合",
              );
              onSaved();
            } catch (e) {
              setState((e as Error).message);
            } finally {
              setBusy(false);
            }
          }}
        >
          下载并准备重排模型（约570MB）
        </Button>
        {state && (
          <p
            className={
              state.includes("成功") || state.includes("保存")
                ? "form-success"
                : "form-error"
            }
          >
            {state}
          </p>
        )}
        <footer className="modal-footer">
          <a
            href="https://platform.deepseek.com"
            target="_blank"
            rel="noreferrer"
            className="text-action"
          >
            DeepSeek 控制台
            <ExternalLink size={12} />
          </a>
          <div className="button-row">
            <Button disabled={busy} onClick={() => save(true)}>
              {busy ? (
                <Loader2 className="spin" size={14} />
              ) : (
                <RefreshCw size={14} />
              )}
              测试连接
            </Button>
            <Button variant="primary" disabled={busy} onClick={() => save()}>
              保存设置
            </Button>
          </div>
        </footer>
      </div>
    </Modal>
  );
}

export function ExportModal({
  project,
  onClose,
}: {
  project: Project;
  onClose: () => void;
}) {
  const [saving, setSaving] = useState(""),
    [savedPath, setSavedPath] = useState(""),
    [error, setError] = useState("");
  return (
    <Modal
      title="把故事带走"
      subtitle="保存到当前工作目录，或下载一份已定稿文稿。"
      onClose={onClose}
    >
      <div className="export-options">
        {[
          ["zip", "完整作品备份", "正文与十份记忆文档，可重新导入"],
          ["md", "Markdown 文稿", "按卷、按章整理的完整正文"],
          ["txt", "纯文字文稿", "方便粘贴到你的发布平台"],
        ].map(([fmt, label, desc]) => (
          <button
            key={fmt}
            disabled={!!saving}
            className="export-option"
            onClick={async () => {
              setSaving(fmt);
              setError("");
              try {
                const result = await post<{ path: string }>(
                  "/projects/" + project.id + "/exports?format=" + fmt,
                  {},
                );
                setSavedPath(result.path);
              } catch (e) {
                setError((e as Error).message);
              } finally {
                setSaving("");
              }
            }}
          >
            <FileText size={23} />
            <div>
              <strong>{label}</strong>
              <small>{desc}</small>
            </div>
            {saving === fmt ? (
              <Loader2 size={18} className="spin" />
            ) : (
              <ArrowDownToLine size={18} />
            )}
          </button>
        ))}
      </div>
      {savedPath && (
        <p role="status" className="export-saved">
          已保存到工作目录<code>{savedPath}</code>
        </p>
      )}
      {error && (
        <p role="alert" className="error-text">
          {error}
        </p>
      )}
      <div className="export-downloads">
        <span>也可通过浏览器下载：</span>
        {["zip", "md", "txt"].map((fmt) => (
          <a
            key={fmt}
            href={"/api/projects/" + project.id + "/export?format=" + fmt}
          >
            {fmt.toUpperCase()}
          </a>
        ))}
      </div>
      <p className="quiet-note">
        未定稿的草稿保留在工作台中。当前可导出{" "}
        {project.chapters.filter((c) => c.canonical_revision).length} 章。
      </p>
    </Modal>
  );
}
