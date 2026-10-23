import { expect, test, type Page } from "@playwright/test";

// All API calls are intercepted: these tests never read private works or call a model.
async function workbench(
  page: Page,
  status = "awaiting_approval",
  passed = true,
) {
  const chapter = {
    id: "chapter-1",
    project_id: "book",
    volume: 1,
    number: 1,
    title: "第一章",
    content: "学徒收好工具，走向旧炉。",
    canonical: "",
    canonical_revision: 0,
    draft_revision: 1,
    status: "review",
    stale: 0,
    updated: "2026-01-01",
    plan: { title: "第一章", goal: "修复旧炉", target_words: 1000, scenes: [] },
    review: {
      passed,
      total: 8,
      scores: { 剧情一致性: 8 },
      summary: "测试审阅",
      findings: [],
    },
    proposal: {
      chapter_summary: "学徒准备修炉。",
      patches: Array.from({ length: 10 }, (_, i) => ({
        name: "记忆" + i,
        content: "新的故事状态。",
        reason: "本章发生的变化。".repeat(20),
        quotes: [],
      })),
      facts: [],
      state_changes: [],
    },
    context: {},
  };
  const run = {
    id: "run",
    chapter_id: chapter.id,
    project_id: "book",
    kind: "chapter",
    status,
    node: "approval",
    result: {},
    error: "",
    created: "2026-01-01",
    config: {},
  };
  const project = {
    id: "book",
    title: "测试作品",
    genre: "玄幻",
    premise: "修复旧炉",
    style: "克制",
    target_chapters: 2,
    chapter_words: 1000,
    revision: 0,
    outline: "",
    logline: "",
    is_demo: 0,
    words: 100,
    updated: "2026-01-01",
    chapters: [chapter],
    runs: status === "none" ? [] : [run],
    memory: [],
    facts: [],
    volumes: [{ number: 1, title: "第一卷", outline: "" }],
  };
  const calls: { path: string; data: any }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    let result: unknown;
    if (request.method() === "POST") {
      calls.push({ path, data: request.postDataJSON() });
      if (path.endsWith("/reopen-approval")) {
        run.status = "awaiting_approval";
        result = run;
      } else if (path.endsWith("/approve")) {
        run.status = "completed";
        chapter.status = "final";
        chapter.canonical_revision = 1;
        result = run;
      } else if (path === "/api/runs") {
        result = {
          ...run,
          kind: "volume",
          status: "awaiting_approval",
          chapter_id: null,
          result: { outline: "新卷规划", alternatives: [], memory: {} },
        };
      }
    } else if (path === "/api/projects") result = [project];
    else if (path === "/api/projects/book") result = project;
    else if (path === "/api/settings")
      result = {
        configured: true,
        model: "deepseek-flash",
        budget: 300,
        vector_enabled: false,
        retrieval_mode: "关键词",
        usage: { spent: 0, reserved: 0, calls: 0 },
      };
    if (result === undefined)
      throw new Error("Unexpected API call: " + request.method() + " " + path);
    await route.fulfill({ json: result });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "写作工作台", exact: true }).click();
  return calls;
}

for (const viewport of [
  { width: 1440, height: 720 },
  { width: 900, height: 700 },
  { width: 390, height: 844 },
]) {
  test(`confirmation remains visible at ${viewport.width}px`, async ({
    page,
  }) => {
    await page.setViewportSize(viewport);
    const calls = await workbench(page);
    await page.getByRole("button", { name: "收起审阅侧栏" }).click();
    await page
      .getByRole("button", { name: "审阅并确认本章", exact: true })
      .click();
    const button = page.getByRole("button", {
      name: "确认本章与 10 项记忆",
      exact: true,
    });
    await expect(button).toBeVisible();
    const box = await button.boundingBox();
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(viewport.height);
    await button.click();
    await expect
      .poll(() => calls.filter((c) => c.path.endsWith("/approve")).length)
      .toBe(1);
    expect(calls.at(-1)!.data.accepted_patches).toHaveLength(10);
  });
}

test("banner opens review on a narrow screen and requires a reason for failed review", async ({
  page,
}) => {
  await page.setViewportSize({ width: 900, height: 720 });
  const calls = await workbench(page, "awaiting_approval", false);
  await page.getByRole("button", { name: "收起审阅侧栏" }).click();
  await page.getByRole("button", { name: "审阅并确认", exact: true }).click();
  const confirm = page.getByRole("button", {
    name: "确认本章与 10 项记忆",
    exact: true,
  });
  await expect(confirm).toBeDisabled();
  await page
    .getByRole("textbox", { name: "定稿备注", exact: true })
    .fill("保留此稿，后续再校对。");
  await confirm.click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0].data.note).toBe("保留此稿，后续再校对。");
});

test("cancelled review can be reopened from the banner", async ({ page }) => {
  const calls = await workbench(page, "cancelled");
  await page.getByRole("button", { name: "继续审阅本章" }).click();
  await expect(
    page.getByRole("button", { name: "确认本章与 10 项记忆", exact: true }),
  ).toBeVisible();
  expect(calls[0].path).toBe("/api/runs/run/reopen-approval");
});

test("new volume is available without a blueprint or project revision", async ({
  page,
}) => {
  const calls = await workbench(page, "none");
  await page
    .locator(".chapter-sidebar")
    .getByRole("button", { name: "创建下一卷", exact: true })
    .click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0].data).toMatchObject({ kind: "volume", chapter_id: null });
  await expect(
    page.getByRole("button", { name: "确认创建第 2 卷", exact: true }),
  ).toBeVisible();
});

test("outline always exposes new volume and blocks it during pending chapter review", async ({
  page,
}) => {
  await workbench(page);
  await page.getByRole("button", { name: "故事规划", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "创建下一卷", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByText("请先完成或确认当前任务，再创建下一卷。"),
  ).toBeVisible();
});

test("new volume can also be planned from the outline on mobile", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const calls = await workbench(page, "none");
  await page.getByRole("button", { name: "故事规划", exact: true }).click();
  await page.getByRole("button", { name: "创建下一卷", exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  expect(calls[0].data.kind).toBe("volume");
  await expect(
    page.getByRole("button", { name: "确认创建第 2 卷", exact: true }),
  ).toBeVisible();
});
