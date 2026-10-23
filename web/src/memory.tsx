import { useState } from "react";
import {
  ArrowUpRight,
  Network,
  BookOpen,
  Clock3,
  GitBranch,
  RefreshCw,
  Loader2,
} from "lucide-react";
import { post, put, type Project } from "./api";
import { Button, Empty, Modal } from "./ui";
import { StoryLedger } from "./state-review";
const hints: Record<string, string> = {
  世界观: "世界运行的规则与边界",
  主角形象状态: "不变的底色，变化的处境",
  前卷剧情概述: "已走过的路，仍在发生的影响",
  本卷核心剧情: "正在展开的冲突与承诺",
  下一卷剧情: "为更远的故事保留可能",
  伏笔: "每一次埋下，都值得被记住",
  有关角色: "每个人都有自己的来处",
  目前剧情: "最近十章，故事正在这里",
  事件: "主线与支线的交汇",
  当前章节设计规划: "下一页将发生什么",
};
export function MemoryPage({
  project: p,
  refresh,
  notify,
  onSource,
}: {
  project: Project;
  refresh: () => Promise<void>;
  notify: (s: string) => void;
  onSource: (id: string) => void;
}) {
  const [tab, setTab] = useState("documents"),
    [edit, setEdit] = useState<string | null>(null),
    [text, setText] = useState(""),
    [filter, setFilter] = useState("all"),
    [reindexing, setReindexing] = useState(false);
  const facts = p.facts.filter((f) => filter === "all" || f.kind === filter);
  return (
    <div className="page memory-page">
      <div className="page-heading">
        <div>
          <span className="eyebrow">A WORLD THAT REMEMBERS</span>
          <h1>你创造的世界，都在这里。</h1>
          <p>规则、人物与那些等待回响的细节，沿着故事留下清晰的脉络。</p>
        </div>
        <Button
          disabled={reindexing}
          onClick={async () => {
            setReindexing(true);
            try {
              const r = await post("/projects/" + p.id + "/reindex");
              notify(r.mode);
            } catch (e) {
              notify((e as Error).message);
            } finally {
              setReindexing(false);
            }
          }}
        >
          {reindexing ? (
            <Loader2 className="spin" size={15} />
          ) : (
            <RefreshCw size={15} />
          )}
          重建检索索引
        </Button>
      </div>
      <div className="page-tabs">
        {[
          ["documents", "核心记忆", p.memory.length],
          ["facts", "事实摘录", p.facts.length],
          ["ledger", "状态账本"],
          [
            "timeline",
            "事件时间线",
            p.facts.filter((f) => f.kind === "event" || f.kind === "hook")
              .length,
          ],
        ].map(([id, t, n]) => (
          <button
            className={tab === id ? "active" : ""}
            key={id}
            onClick={() => setTab(String(id))}
          >
            {t}
            {n !== undefined && <span>{n}</span>}
          </button>
        ))}
      </div>
      {tab === "ledger" && (
        <StoryLedger
          projectId={p.id}
          revision={p.revision}
          onSource={onSource}
        />
      )}
      {tab === "documents" && (
        <div className="memory-grid">
          {p.memory.map((m, i) => (
            <button
              className="memory-card"
              key={m.name}
              onClick={() => {
                setEdit(m.name);
                setText(m.content);
              }}
            >
              <header>
                <span className={"memory-card-icon tint" + (i % 4)}>
                  {i === 0 ? (
                    <Network size={21} />
                  ) : i === 5 ? (
                    <GitBranch size={21} />
                  ) : i === 7 ? (
                    <Clock3 size={21} />
                  ) : (
                    <BookOpen size={21} />
                  )}
                </span>
                <span className="quiet-tag">
                  {m.revision ? "版本 " + m.revision : "待建立"}
                </span>
              </header>
              <h3>{m.name}</h3>
              <p>{hints[m.name]}</p>
              <div className="memory-excerpt">
                {m.content.replace(/[#|\x60*-]/g, "").slice(0, 150)}
              </div>
              <footer>
                <span>{m.content.length} 字</span>
                <span>
                  阅读与编辑
                  <ArrowUpRight size={14} />
                </span>
              </footer>
            </button>
          ))}
        </div>
      )}
      {tab === "facts" && (
        <>
          <div className="filter-row">
            {[
              ["all", "全部"],
              ["character", "人物状态"],
              ["item", "道具"],
              ["realm", "境界"],
              ["knowledge", "知情范围"],
              ["relationship", "人物关系"],
              ["rule", "世界规则"],
              ["hook", "伏笔"],
              ["event", "事件"],
            ].map(([id, t]) => (
              <button
                key={id}
                onClick={() => setFilter(id)}
                className={filter === id ? "active" : ""}
              >
                {t}
              </button>
            ))}
          </div>
          {facts.length ? (
            <div className="facts-table">
              <div className="facts-table-head">
                <span>人物 / 对象</span>
                <span>状态与变化</span>
                <span>来源</span>
              </div>
              {facts.map((f, i) => (
                <button
                  key={i}
                  className="fact-row"
                  onClick={() => f.chapter_id && onSource(f.chapter_id)}
                >
                  <div>
                    <span className="fact-node" />
                    <strong>{f.subject}</strong>
                    <small>{f.predicate}</small>
                  </div>
                  <div>
                    <p>{f.value}</p>
                    <blockquote>{f.quote}</blockquote>
                  </div>
                  <span>
                    第 {Number(f.position) % 100000} 章
                    <ArrowUpRight size={13} />
                  </span>
                </button>
              ))}
            </div>
          ) : (
            <Empty icon={Network} title="人物与世界的变化，会被记住">
              章节定稿后，这里会显示带原文来源的状态记录。
            </Empty>
          )}
        </>
      )}
      {tab === "timeline" && (
        <div className="timeline">
          {p.facts
            .filter((f) => f.kind === "event" || f.kind === "hook")
            .map((f, i) => (
              <button
                key={i}
                onClick={() => f.chapter_id && onSource(f.chapter_id)}
              >
                <span className="timeline-dot" />
                <small>
                  第 {Number(f.position) % 100000} 章 ·{" "}
                  {f.kind === "hook" ? "伏笔" : "事件"}
                </small>
                <h3>
                  {f.subject} · {f.predicate}
                </h3>
                <p>{f.value}</p>
                <blockquote>{f.quote}</blockquote>
                <span className="text-action">
                  查看原文
                  <ArrowUpRight size={13} />
                </span>
              </button>
            ))}
          {!p.facts.some((f) => f.kind === "event" || f.kind === "hook") && (
            <Empty icon={Clock3} title="等故事发生，时间线就会生长">
              这里呈现已定稿章节里的事件与伏笔。
            </Empty>
          )}
        </div>
      )}
      {edit && (
        <Modal
          title={edit}
          subtitle={hints[edit]}
          onClose={() => setEdit(null)}
          wide
        >
          <textarea
            className="memory-editor"
            aria-label="编辑核心记忆"
            value={text}
            onChange={(e) => setText(e.target.value)}
          />
          <div className="modal-footer">
            <span>保存后，相关章节审核将需要重新确认。</span>
            <Button
              variant="primary"
              onClick={async () => {
                try {
                  await put(
                    "/projects/" + p.id + "/memory/" + encodeURIComponent(edit),
                    { content: text, project_revision: p.revision },
                  );
                  setEdit(null);
                  await refresh();
                  notify("记忆已保存");
                } catch (e) {
                  notify((e as Error).message);
                }
              }}
            >
              确认保存记忆
            </Button>
          </div>
        </Modal>
      )}
    </div>
  );
}
