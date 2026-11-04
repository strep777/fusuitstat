/**
 * 委托询价流程（真实浏览器 + 真实后端 + 真实前端）。
 *
 * 覆盖用例：`文档/13_测试计划与验收用例.md`
 * - TC-C01 草稿刷新、提交网络失败、重复提交、补件/撤回 → 保存版本明确、重复请求同一对象、终态不可越权修改
 * - TC-C02 接受过期/旧版报价、两标签同时接受 → 前两者拒绝，后者只一个订单与名额预留
 *
 * 依据文档：
 * - 03 站点路由与页面功能（/commission/new、/account/quotes、/account/quotes/:id）
 * - 06 API 接口契约（`POST /api/v1/inquiries`、`PATCH /inquiries/{id}`、`POST /inquiries/{id}/submit`、
 *   `POST /quotes/{id}/accept`；错误 envelope `{error:{code,message,fields,trace_id}}`）
 * - 07 委托报价与制作流程（草稿版本、提交后不可越权修改、报价版本与有效期）
 * - 11 界面交互与素材规范（dirty/saving/saved/validation-error 状态必须可见）
 *
 * 选择器取自 `web/src/modules/commissions/pages/InquiryNewPage.vue`、
 * `AccountQuotesPage.vue`、`AccountQuoteDetailPage.vue` 与 `components/QuoteVersionCard.vue`。
 * 该模块只有 InquiryNewPage 带 data-testid（submitted / step-N / next / submit / save-state）。
 */

import fs from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import {
  assertNoSensitiveLeak,
  collectPageErrors,
  loginViaSandboxApi,
  loginWithSandboxOtp,
  sessionReadHeaders,
  sessionWriteHeaders,
  uniqueSandboxPhone,
} from "../helpers";

/** 从 cwd（web/ 或仓库根）向上找到仓库内文件，避免依赖调用目录。 */
function repoFile(relative: string): string {
  let dir = process.cwd();
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = path.join(dir, relative);
    if (fs.existsSync(candidate)) return candidate;
    dir = path.dirname(dir);
  }
  throw new Error(`找不到仓库文件 ${relative}（起点 ${process.cwd()}）`);
}

/** 真实的 192x192 PNG 品牌素材，用作询价设定图（13 文档：上传仅允许 JPEG/PNG/WebP）。 */
const DESIGN_IMAGE = () => repoFile("web/public/assets/brand/app-icon-192.png");

function expectNoUnexpectedErrors(errors: string[], allowed: RegExp | null = null): void {
  const unexpected = allowed ? errors.filter((item) => !allowed.test(item)) : errors;
  expect(unexpected, `出现未预期的浏览器错误：\n${errors.join("\n")}`).toEqual([]);
}

/** 第 1 步：委托范围。系列由 seed-demo 提供；没有可选系列时用例明确跳过而不是假装通过。 */
async function fillScopeStep(page: Page, characterName: string): Promise<void> {
  await page.locator("#character_name").fill(characterName);
  await page.locator("#species").fill("龙");

  const seriesSelect = page.locator("#series_id");
  // 同上：先等异步选项加载，再判断环境里是否真的没有系列。
  await page
    .waitForFunction(
      () => {
        const element = document.querySelector("#series_id") as HTMLSelectElement | null;
        return Boolean(element && element.options.length > 1);
      },
      { timeout: 20_000 },
    )
    .catch(() => undefined);
  const optionCount = await seriesSelect.locator("option").count();
  test.skip(
    optionCount <= 1,
    "当前环境没有已启用的系列（`/api/v1/content/series` 为空），第 1 步必填的「系列」无法选择；" +
      "依赖 `cd server && uv run python -m app.cli seed-demo` 写入演示系列数据。",
  );
  await seriesSelect.selectOption({ index: 1 });
  await declareRights(page);
  await page
    .waitForFunction(
      () => {
        const labels = Array.from(document.querySelectorAll("label"));
        const target = labels.find((item) => item.textContent?.includes("我确认有权提供这份角色设定"));
        const element = target?.querySelector("input[type=checkbox]") as HTMLInputElement | null;
        return !element || element.checked;
      },
      { timeout: 10_000 },
    )
    .catch(() => undefined);
  // 制作类型默认 full（commissions.ts:762），这里显式再点一次，确认单选真实可交互。
  await page.locator('input[name="suit_type"][value="full"]').check();
}

async function clickNext(page: Page): Promise<void> {
  await page.getByRole("button", { name: "下一步" }).click();
}

/** 完整走完 5 步表单并提交，返回询价编号。 */
async function submitInquiry(page: Page, characterName: string): Promise<string> {
  await page.goto("/commission/new");
  await expect(page.getByRole("heading", { level: 1, name: "提交询价" })).toBeVisible();

  // 第 1 步 · 委托范围
  await expect(page.getByRole("heading", { name: "第 1 步 · 委托范围" })).toBeVisible();
  await fillScopeStep(page, characterName);
  await clickNext(page);

  // 第 2 步 · 设定图与角色：至少 1 张真实图片，且必须通过安全检查才能继续。
  await expect(page.getByRole("heading", { name: "第 2 步 · 设定图与角色" })).toBeVisible();
  await page.locator('input[type="file"]').setInputFiles(DESIGN_IMAGE());
  await expect(page.getByText("全部设定图已通过安全检查，可以继续。")).toBeVisible({ timeout: 30_000 });
  await clickNext(page);

  // 第 3 步 · 制作需求：收货区域必填。
  await expect(page.getByRole("heading", { name: "第 3 步 · 制作需求" })).toBeVisible();
  await page.locator("#requirements").fill("E2E 自动化用例：验证草稿、提交与状态回读。");
  await page.locator("#delivery_region").fill("中国大陆 / 上海");
  await clickNext(page);

  // 第 4 步 · 联系偏好（可选）
  await expect(page.getByRole("heading", { name: "第 4 步 · 联系偏好" })).toBeVisible();
  await clickNext(page);

  // 第 5 步 · 预览与同意
  await expect(page.getByRole("heading", { name: "第 5 步 · 预览与同意" })).toBeVisible();
  await page.getByLabel(/我确认有权提供这份角色设定/).check();
  await page.getByRole("button", { name: "提交询价" }).click();

  const submitted = page.getByTestId("submitted");
  await expect(submitted).toBeVisible({ timeout: 30_000 });
  await expect(submitted).toContainText("询价已提交，进入人工处理");

  const badge = await submitted.getByText(/询价编号/).first().textContent();
  const inquiryId = badge?.match(/(inq_[A-Za-z0-9]+)/)?.[1];
  expect(inquiryId, `提交结果里没有可识别的询价编号：${badge}`).toBeTruthy();
  return inquiryId as string;
}

/**
 * 选择第一个可用的系列。
 *
 * `series_id` 是 `InquiryCreate` 的必填字段（07 文档）；只填角色名与物种就触发自动保存
 * 会拿到 422 `VALIDATION_ERROR`，那是**表单还没填完**，不是保存功能坏了。
 */
async function selectFirstSeries(page: Page): Promise<void> {
  const series = page.locator("#series_id");
  // 系列来自 `/content/series`，是异步加载的：必须等选项到位，否则会误判为「没有系列」。
  await page.waitForFunction(
    () => {
      const element = document.querySelector("#series_id") as HTMLSelectElement | null;
      return Boolean(element && element.options.length > 1);
    },
    { timeout: 20_000 },
  );
  const options = await series.locator("option").all();
  for (const option of options) {
    const value = await option.getAttribute("value");
    if (value) {
      await series.selectOption(value);
      return;
    }
  }
  throw new Error("询价表单没有可选的系列；请先执行 `uv run python -m app.cli seed-demo`");
}

/**
 * 勾选「你拥有所提交角色设定的使用权」声明。
 *
 * `InquiryCreate.rights_declaration` 是提交与草稿保存的前置条件（07 文档：不得代替
 * 客户确认权利声明）。不勾选时服务端返回字段错误「请确认你拥有所提交角色设定的使用权。」，
 * 那是**表单没填完**，不是保存坏了。
 */
async function declareRights(page: Page): Promise<void> {
  const box = page.getByRole("checkbox", { name: /我确认有权提供这份角色设定/ });
  if ((await box.count()) === 0) return;
  await box.first().check();
}

test.describe("TC-C01 询价草稿 → 提交 → 客户查看状态", () => {
  test("未勾选权利声明时不发保存请求并写明下一步；勾选后自动保存、刷新可恢复", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginWithSandboxOtp(page, uniqueSandboxPhone());
    const characterName = `E2E 草稿角色 ${Date.now()}`;

    await page.goto("/commission/new");
    await selectFirstSeries(page);
    await page.locator("#character_name").fill(characterName);
    await page.locator("#species").fill("狐");

    // 权利声明在第 5 步，而服务端在建单时就要求它（`_check_rights`）。
    // 因此第 1 步填写后**不应发保存请求**，也绝不能显示成「已保存」——只给出下一步动作。
    const saveState = page.getByTestId("save-state");
    await expect(saveState).toContainText("我确认有权提供这份角色设定", { timeout: 15_000 });
    await expect(saveState).not.toContainText("草稿已保存到你的账户，刷新后可恢复。");
    await expect(page.locator("#character_name")).toHaveValue(characterName);
    await expect(page.locator("#species")).toHaveValue("狐");

    // 走到第 5 步勾选权利声明后，草稿才会真正落库；刷新后可以恢复。
    await clickNext(page);
    await page.locator('input[type="file"]').setInputFiles(DESIGN_IMAGE());
    await expect(page.getByText("全部设定图已通过安全检查，可以继续。")).toBeVisible({
      timeout: 30_000,
    });
    await clickNext(page);
    await page.locator("#requirements").fill("E2E 草稿用例：验证权利声明后自动保存。");
    await page.locator("#delivery_region").fill("中国大陆 / 上海");
    await clickNext(page);
    await clickNext(page);
    await expect(page.getByRole("heading", { name: "第 5 步 · 预览与同意" })).toBeVisible();
    await declareRights(page);
    await expect(saveState).toContainText("草稿已保存到你的账户，刷新后可恢复。", { timeout: 20_000 });

    await page.reload();
    await expect(page.locator("#character_name")).toHaveValue(characterName, { timeout: 15_000 });
    await expect(page.locator("#species")).toHaveValue("狐");

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("草稿保存失败时明确报错、保留已填内容，且绝不显示成已保存", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginWithSandboxOtp(page, uniqueSandboxPhone());
    const characterName = `E2E 断网草稿 ${Date.now()}`;

    await page.goto("/commission/new");
    await selectFirstSeries(page);
    await page.locator("#character_name").fill(characterName);
    // 第 1 步的必填项包含物种；不填会导致「下一步」被校验拦住，走不到触发保存的地方。
    await page.locator("#species").fill("狐");

    // 未勾选权利声明时页面必须给出明确下一步，且绝不显示成已保存。
    const saveState = page.getByTestId("save-state");
    await expect(saveState).not.toContainText("草稿已保存到你的账户，刷新后可恢复。", {
      timeout: 15_000,
    });
    await expect(saveState).toContainText("我确认有权提供这份角色设定", { timeout: 15_000 });
    // 用户输入必须还在页面上。
    await expect(page.locator("#character_name")).toHaveValue(characterName);

    // 一旦服务端不可用（断网），保存失败也必须明确报错且不丢内容。
    // 权利声明在第 5 步，所以先按真实流程走到那一步，再断网触发写请求。
    await clickNext(page);
    await page.locator('input[type="file"]').setInputFiles(DESIGN_IMAGE());
    await expect(page.getByText("全部设定图已通过安全检查，可以继续。")).toBeVisible({
      timeout: 30_000,
    });
    await clickNext(page);
    await page.locator("#requirements").fill("E2E 断网用例：验证保存失败不丢内容。");
    await page.locator("#delivery_region").fill("中国大陆 / 上海");
    await clickNext(page);
    await clickNext(page);
    await expect(page.getByRole("heading", { name: "第 5 步 · 预览与同意" })).toBeVisible();

    // 先断网，再勾选权利声明：勾选会改动草稿并触发一次自动保存，正好落到失败路径。
    await page.route("**/api/v1/inquiries**", (route) => route.abort("failed"));
    await declareRights(page);
    await expect(saveState).toContainText(/失败|网络|稍后重试/, { timeout: 20_000 });
    // 第 5 步不再渲染第 1 步的输入框，改用预览区断言内容仍在（没有静默丢失）。
    await expect(page.getByText(characterName).first()).toBeVisible();
    // 保存失败也绝不能显示成已保存。
    await expect(saveState).not.toContainText("草稿已保存到你的账户，刷新后可恢复。");

    await page.unroute("**/api/v1/inquiries**");
    expectNoUnexpectedErrors(errors, /api\/v1\/inquiries|ERR_FAILED|ERR_ABORTED/);
  });

  test("设定图缺失时按字段报错并停在当前步，不生成任何「已提交」结果", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginWithSandboxOtp(page, uniqueSandboxPhone());

    await page.goto("/commission/new");
    await fillScopeStep(page, `E2E 缺图角色 ${Date.now()}`);
    await clickNext(page);

    await expect(page.getByRole("heading", { name: "第 2 步 · 设定图与角色" })).toBeVisible();
    await clickNext(page);

    // 校验失败：贴字段的错误（11 文档），并留在第 2 步。
    await expect(page.getByText("至少上传 1 张设定图，最多 3 张。")).toBeVisible();
    await expect(page.getByRole("heading", { name: "第 2 步 · 设定图与角色" })).toBeVisible();
    await expect(page.getByTestId("submitted")).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("完整提交询价后可在客户中心看到状态，且不能重复提交", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginWithSandboxOtp(page, uniqueSandboxPhone());
    const characterName = `E2E 提交角色 ${Date.now()}`;

    const inquiryId = await submitInquiry(page, characterName);

    // 终态不可越权修改：表单与提交按钮都不再出现（InquiryNewPage.vue:729/772 的 v-if/v-else）。
    const submitted = page.getByTestId("submitted");
    await expect(submitted).toContainText("这份询价已经提交，不能重复提交。");
    await expect(page.getByRole("button", { name: "提交询价" })).toHaveCount(0);

    // 客户查看状态：列表 → 详情。
    await page.getByRole("link", { name: "查看我的询价与报价" }).click();
    await expect(page).toHaveURL(/\/account\/quotes$/);
    await expect(page.getByRole("heading", { level: 1, name: "我的询价与报价" })).toBeVisible();
    await expect(page.getByText(characterName)).toBeVisible();
    await expect(page.getByText("处理中").first()).toBeVisible();

    await page.getByRole("link", { name: "查看详情" }).first().click();
    await expect(page).toHaveURL(new RegExp(`/account/quotes/${inquiryId}$`));
    await expect(page.getByRole("heading", { level: 1 })).toContainText(characterName);
    await expect(page.getByText(`编号 ${inquiryId}`)).toBeVisible();
    await expect(page.getByText("处理中").first()).toBeVisible();
    // 还没有报价：必须显示诚实的空态，而不是伪造一份报价。
    await expect(page.getByText("还没有收到报价")).toBeVisible();
    // 没有报价时不存在「接受这份报价」按钮。
    await expect(page.getByRole("button", { name: /接受这份报价/ })).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("已提交的询价可以撤回，撤回后状态为已撤回且不再显示提交入口", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginWithSandboxOtp(page, uniqueSandboxPhone());
    const characterName = `E2E 撤回角色 ${Date.now()}`;

    const inquiryId = await submitInquiry(page, characterName);
    await page.goto(`/account/quotes/${inquiryId}`);

    const withdraw = page.getByRole("button", { name: "撤回询价" });
    await expect(withdraw).toBeVisible();

    await withdraw.click();
    await expect(page.getByText(`确认撤回编号 ${inquiryId} 的询价？`)).toBeVisible();
    await page.getByRole("button", { name: "确认撤回" }).click();

    await expect(page.getByText("已撤回").first()).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("本次操作没有完成")).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });
});

test.describe("TC-C02 报价版本与接受", () => {
  test("不存在的报价编号不能转成订单，也不会产生任何订单", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);

    const before = await page.request.get("/api/v1/orders", { headers: sessionReadHeaders() });

    const response = await page.request.post("/api/v1/quotes/qte_e2e_not_exist/accept", {
      headers: sessionWriteHeaders(session),
      // 服务端 QuoteAcceptInput：{quote_version:int>=1, terms_version:str, accepted:bool}
      data: { quote_version: 1, terms_version: "commission-1", accepted: true },
    });

    // 拒绝：不能是 2xx，也不能返回订单号。
    expect(response.status(), await response.text()).toBeGreaterThanOrEqual(400);
    const payload = (await response.json()) as { error?: { code?: string } };
    expect(
      ["RESOURCE_NOT_FOUND", "QUOTE_EXPIRED", "QUOTE_CHANGED", "FORBIDDEN"],
      `非预期错误码：${JSON.stringify(payload)}`,
    ).toContain(payload.error?.code ?? "");

    // 没有任何订单被创建（新账号在本次用例前订单列表为空）。
    const after = await page.request.get("/api/v1/orders", { headers: sessionReadHeaders() });
    expect(after.ok()).toBeTruthy();
    const payloadAfter = (await after.json()) as { items?: unknown[] };
    expect(payloadAfter.items ?? []).toHaveLength(0);
    expect(before.ok()).toBeTruthy();

    expectNoUnexpectedErrors(errors);
  });

  test("接受过期/旧版报价被拒绝，且两标签同时接受只生成一个订单", async () => {
    test.fixme(
      true,
      "依赖真实报价数据：需要在后台把询价推进到 QUOTED 并发布报价版本（`/admin/quotes` 需要 quote.review 权限的员工账号）；" +
        "E2E 环境目前没有可用员工账号（未执行 `cd server && uv run python -m app.cli create-owner --login owner --name \"工作室负责人\"`）。" +
        "在具备该账号前，「接受过期/旧版报价被拒」与「两标签同时接受只生成一个订单与名额预留」无法构造，不能标记为通过。",
    );
  });
});
