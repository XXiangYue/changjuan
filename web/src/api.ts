export type Plan = {
  title: string;
  goal: string;
  pov?: string;
  characters?: string[];
  scenes?: {
    title: string;
    goal: string;
    conflict: string;
    cost: string;
    change: string;
  }[];
  constraints?: string[];
  hooks?: string[];
  target_words: number;
  min_characters?: number | null;
  max_characters?: number | null;
};
export type Finding = {
  dimension: string;
  severity: "critical" | "warning" | "suggestion";
  quote: string;
  evidence: string;
  suggestion: string;
};
export type Review = {
  review_mode?: string;
  scores?: Record<string, number>;
  total?: number;
  passed?: boolean;
  summary?: string;
  findings?: Finding[];
  length_check?: {
    characters: number;
    target: number;
    minimum?: number;
    maximum?: number;
    within_range: boolean;
  };
};
export type Patch = {
  name: string;
  content: string;
  reason: string;
  quotes: string[];
};
export type Chapter = {
  id: string;
  project_id: string;
  volume: number;
  number: number;
  title: string;
  content: string;
  canonical: string;
  draft_revision: number;
  canonical_revision: number;
  status: string;
  stale: number;
  plan: Plan;
  review: Review;
  proposal: {
    patches?: Patch[];
    facts?: Fact[];
    chapter_summary?: string;
    accepted_facts?: number[];
    accepted_patches?: string[];
    state_changes?: {
      operation: string;
      paragraph: number;
      quote?: string;
      labels?: Record<string, string>;
    }[];
    accepted_state_changes?: number[];
  };
  context: { evidence?: Evidence[]; retrieval_mode?: string };
  updated: string;
};
export type Fact = {
  id?: string;
  subject: string;
  predicate: string;
  value: string;
  kind: string;
  status: string;
  quote: string;
  chapter_id?: string;
  version?: number;
  position?: number;
  related?: string;
};
export type Evidence = {
  id: string;
  chapter_id: string;
  version: number;
  content: string;
  score?: number;
};
export type Run = {
  id: string;
  project_id: string;
  chapter_id?: string;
  kind: string;
  status: string;
  node: string;
  error: string;
  created: string;
  result: any;
  config: any;
};
export type Project = {
  id: string;
  title: string;
  genre: string;
  premise: string;
  style: string;
  target_chapters: number;
  chapter_words: number;
  revision: number;
  outline: string;
  logline: string;
  is_demo: number;
  words: number;
  completed?: number;
  updated: string;
  chapters: Chapter[];
  memory: { name: string; content: string; revision: number }[];
  facts: Fact[];
  runs: Run[];
  volumes: { number: number; title: string; outline: string }[];
};
export type Settings = {
  configured: boolean;
  budget: number;
  model: string;
  vector_enabled: boolean;
  retrieval_policy: "keyword" | "semantic" | "hybrid" | "reranked";
  reranker?: { ready: boolean; state: string };
  retrieval_mode: string;
  usage: {
    spent: number;
    reserved: number;
    input_tokens: number;
    output_tokens: number;
    calls: number;
  };
};
export type Pair = {
  id: string;
  a: string;
  b: string;
  preference?: string;
  reason?: string;
};
export type Research = {
  usage: Settings["usage"];
  experiments: {
    id: string;
    name: string;
    status: string;
    config: any;
    result: any;
    created: string;
  }[];
  pairs: Pair[];
  methodology: any;
};
export async function api<T = any>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  const res = await fetch("/api" + path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options.headers },
  });
  if (!res.ok) {
    const d = await res.json().catch(() => ({ detail: "服务暂时不可用" }));
    throw new Error(
      typeof d.detail === "string"
        ? d.detail
        : "输入内容不符合要求，请检查后重试",
    );
  }
  return res.json();
}
export const post = <T = any>(path: string, body: unknown = {}) =>
  api<T>(path, { method: "POST", body: JSON.stringify(body) });
export const put = <T = any>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export function words(n: number) {
  return n >= 10000 ? (n / 10000).toFixed(1) + "万" : n.toLocaleString();
}
export const labels: Record<string, string> = {
  planned: "待创作",
  draft: "草稿",
  review: "待审阅",
  final: "已定稿",
  queued: "等待开始",
  running: "进行中",
  awaiting_approval: "待你确认",
  interrupted: "已暂停",
  cancelled: "已取消",
  failed: "需要处理",
  completed: "已完成",
  context: "查阅故事记忆",
  scene_plan: "构思场景",
  planning: "规划故事",
  writing: "创作正文",
  reviewing: "编辑审阅",
  memory: "整理故事记忆",
  approval: "等待确认",
  committing: "保存定稿",
};
