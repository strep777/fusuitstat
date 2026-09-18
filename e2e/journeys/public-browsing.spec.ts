/**
 * 公开浏览关键路径（真实浏览器 + 真实后端 + 真实前端）。
 *
 * 覆盖用例：`文档/13_测试计划与验收用例.md`「界面与内容验收」一节——
 * 每个公共路由都要测**直接打开、刷新、返回、窄屏、无数据、接口错误**，检查素材与文案，
 * 并确认私密信息不会出现在公开页面。
 *
 * 依据文档：
 * - 03 站点路由与页面功能（路由、筛选进 URL、空态/错误态/404 口径）
 * - 06 API 接口契约（错误 envelope `{error:{code,message,fields,trace_id}}` 与状态码）
 * - 11 界面交互与素材规范（每页 loading/empty/error；不靠颜色单独表达状态）
 * - 13 测试计划与验收用例（本节）
 *
 * 选择器都取自页面真实实现（`web/src/modules/**`），不依赖任何 data-testid：
 * 这 10 个公开页面全仓没有任何 `data-testid`，可用的稳定锚点是
 * `role=heading/alert/status`、`aria-label`、`aria-live`、`label[for]` 与真实 href。
 */

import { expect, test, type Page } from "@playwright/test";

import { assertNoSensitiveLeak, collectPageErrors, SITE_ORIGIN } from "../helpers";

/** 断言没有未预期的控制台错误与失败请求。 */
function expectNoUnexpectedErrors(errors: string[], allowed: RegExp | null = null): void {
  const unexpected = allowed ? errors.filter((item) => !allowed.test(item)) : errors;
  expect(unexpected, `出现未预期的浏览器错误：\n${errors.join("\n")}`).toEqual([]);
}

/**
 * 浏览器会把「响应状态不是 2xx」自动记进 console.error。
 * 在接口错误态用例里这是我们主动打桩的结果，需要按 URL 放行这一条；
 * 其余任何控制台错误仍然会让用例失败。
 */
const STUBBED_RESPONSE_NOISE = /Failed to load resource|503|502|500/;

/** 打开列表页，取第一条详情链接的真实 href —— 不依赖种子数据里写死的 slug。 */
async function firstDetailHref(page: Page, listPath: string, detailPrefix: string): Promise<string> {
  await page.goto(listPath);
  const link = page.locator(`a[href^="${detailPrefix}"]`).first();
  await link.waitFor({ state: "visible", timeout: 15_000 });
  const href = await link.getAttribute("href");
  expect(href, `${listPath} 上没有 ${detailPrefix} 开头的详情链接`).toBeTruthy();
  return href as string;
}

/** 详情页 h1 必须与列表卡片里的标题一致（证明直达的是同一条内容）。 */
async function cardTitle(page: Page, listPath: string, detailPrefix: string): Promise<string> {
  await page.goto(listPath);
  const card = page.locator(`a[href^="${detailPrefix}"]`).first();
  await card.waitFor({ state: "visible", timeout: 15_000 });
  // 卡片结构不同：作品/商品把 h3 放在 <a> 里，公告列表则是 <h2><a>…</a></h2>。
  // 先找链接内部的标题，再退回「包含该链接的标题元素」，避免把徽标与元信息一起读进来。
  const title = (
    await card.evaluate((element) => {
      const inside = element.querySelector("h1, h2, h3, h4");
      const outer = element.closest("h1, h2, h3, h4");
      return (inside ?? outer ?? element).textContent ?? "";
    })
  )?.trim();
  expect(title, `${listPath} 的第一张卡片没有标题`).toBeTruthy();
  return title as string;
}

/** 窄屏验收：文档根节点不允许出现横向溢出（11 文档）。 */
async function expectNoHorizontalOverflow(page: Page, label: string): Promise<void> {
  const metrics = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
  }));
  expect(
    metrics.scrollWidth,
    `${label} 在 ${metrics.clientWidth}px 视口下出现横向溢出：scrollWidth=${metrics.scrollWidth}`,
  ).toBeLessThanOrEqual(metrics.clientWidth + 1);
}

test.describe("公开浏览 · 桌面与移动视口", () => {
  test("首页直接打开即渲染首屏内容，不是加载动画", async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto("/");

    // HomePage.vue:79-81 的 h1（源码里有 <br>，这里按文本前缀匹配）。
    await expect(page.getByRole("heading", { level: 1 })).toContainText("把设定变成");
    await expect(page.getByRole("heading", { name: "代表作品" })).toBeVisible();
    // 首屏加载动画必须已经消失（StarLoader message="正在读取作品与系列"）。
    await expect(page.getByText("正在读取作品与系列")).toHaveCount(0);
    // 首页不允许出现加载失败态（HomePage.vue:189 的 EmptyState tone="error"）。
    await expect(page.getByRole("alert")).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("作品列表：筛选写进 URL、刷新可恢复、空结果走诚实空态、返回可回到全部", async ({ page }) => {
    const errors = collectPageErrors(page);
    const keyword = `e2e-none-${Date.now()}`;

    await page.goto("/works");
    await expect(page.getByRole("heading", { level: 1, name: "作品集" })).toBeVisible();

    // WorksPage.vue:336-346：label[for=works-search] + 提交按钮「搜索」。
    await page.locator("#works-search").fill(keyword);
    await page.getByRole("button", { name: "搜索" }).click();

    await expect(page).toHaveURL((url) => url.searchParams.get("q") === keyword);
    // 空态：WorksPage.vue:387-398。
    await expect(page.getByText("没有符合当前筛选条件的作品")).toBeVisible();
    await expect(page.getByText(`关键词=${keyword}`)).toBeVisible();
    // 空态不等于错误态。
    await expect(page.getByRole("alert")).toHaveCount(0);

    // 刷新后同一个筛选条件仍然生效（03 文档：筛选条件进 URL）。
    await page.reload();
    await expect(page.getByText("没有符合当前筛选条件的作品")).toBeVisible();

    // 浏览器返回回到未筛选的列表。
    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: "作品集" })).toBeVisible();
    await expect(page.getByText("没有符合当前筛选条件的作品")).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("作品详情：用 URL 直达并刷新，标题与列表卡片一致", async ({ page }) => {
    const errors = collectPageErrors(page);

    const title = await cardTitle(page, "/works", "/works/");
    const href = await firstDetailHref(page, "/works", "/works/");

    await page.goto(href); // 直接打开详情 URL，不经过点击
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    await expect(page.getByText("作品类型")).toBeVisible();
    await expect(page.getByText("这件作品不存在或已下架")).toHaveCount(0);

    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("公告列表与正文：直接打开、刷新、返回列表", async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto("/news");
    await expect(page.getByRole("heading", { level: 1, name: "工作室公告" })).toBeVisible();

    const title = await cardTitle(page, "/news", "/news/");
    const href = await firstDetailHref(page, "/news", "/news/");

    await page.goto(href);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    await expect(page.getByText("需要人工确认？")).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: "工作室公告" })).toBeVisible();

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("商城列表与商品详情：直达链接、刷新、售罄仍可浏览", async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto("/shop");
    await expect(page.getByRole("heading", { level: 1, name: "商城" })).toBeVisible();
    // ShopPage.vue:142 的筛选区块有 aria-label。
    await expect(page.getByLabel("商品筛选")).toBeVisible();

    const title = await cardTitle(page, "/shop", "/shop/");
    const href = await firstDetailHref(page, "/shop", "/shop/");

    await page.goto(href);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    // ProductDetailPage.vue:387-392：规格统计始终渲染。
    await expect(page.getByText(/共 \d+ 个规格/)).toBeVisible();
    await expect(page.getByText(/库存与可售性以下单时服务端校验为准/)).toBeVisible();
    await expect(page.getByText("找不到这个商品")).toHaveCount(0);
    await expect(page.getByText("商品详情暂时无法加载")).toHaveCount(0);

    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

    await page.goBack();
    await expect(page.getByRole("heading", { level: 1, name: "商城" })).toBeVisible();

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("角色列表与角色详情：直达链接、刷新、保留不等成交的说明可见", async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto("/characters");
    await expect(page.getByRole("heading", { level: 1, name: "角色领养" })).toBeVisible();
    await expect(page.getByLabel("角色筛选")).toBeVisible();
    await expect(page.getByText(/保留状态不等于成交/)).toBeVisible();

    const title = await cardTitle(page, "/characters", "/characters/");
    const href = await firstDetailHref(page, "/characters", "/characters/");

    await page.goto(href);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);
    await expect(page.getByRole("heading", { name: "授权说明" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "角色故事" })).toBeVisible();

    await page.reload();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("未知地址返回 404 页面而不是首页 200 或空白页", async ({ page }) => {
    const errors = collectPageErrors(page);
    const missing = `/e2e-not-exist-${Date.now()}`;

    await page.goto(missing);

    await expect(page.getByRole("heading", { level: 1, name: "这个地址没有对应页面" })).toBeVisible();
    await expect(page.getByText(`请求的地址：`)).toBeVisible();
    await expect(page.getByText(missing)).toBeVisible();
    // 路由 afterEach 把 404 标成 noindex, follow（router.ts:304-305）。
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, follow");

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("接口错误态：显示可重试的加载失败，而不是空态或伪成功", async ({ page }) => {
    const errors = collectPageErrors(page);
    const body = JSON.stringify({
      error: {
        code: "PROVIDER_UNAVAILABLE",
        message: "服务暂时不可用，请稍后重试。",
        fields: {},
        trace_id: "e2e-stub",
      },
    });

    // 只打桩商品接口；其余请求仍然真实发出。
    await page.route("**/api/v1/products**", (route) =>
      route.fulfill({ status: 503, contentType: "application/json", body }),
    );

    await page.goto("/shop");

    // ShopPage.vue:223-232 的 error 态：tone="error" → role="alert"。
    const errorState = page.getByRole("alert");
    await expect(errorState).toContainText("商品列表暂时无法加载");
    await expect(errorState).toContainText("服务暂时不可用，请稍后重试。");
    // 不能把加载失败显示成「没有数据」，也不能显示商品。
    await expect(page.getByText("商城商品正在上架")).toHaveCount(0);
    await expect(page.getByText("没有符合条件的商品")).toHaveCount(0);

    await page.unroute("**/api/v1/products**");
    await page.getByRole("button", { name: "重新加载" }).click();
    await expect(page.getByRole("heading", { level: 1, name: "商城" })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);

    expectNoUnexpectedErrors(errors, STUBBED_RESPONSE_NOISE);
  });

  test("移动端微信 WebView 近似视口下公开页面仍可读（393x852）", async ({ page }) => {
    test.skip(
      page.viewportSize()?.width === 360,
      "360px 由下面的窄屏用例覆盖，这里只跑桌面与 393px 近似微信视口",
    );
    const errors = collectPageErrors(page);
    await page.setViewportSize({ width: 393, height: 852 });

    for (const path of ["/", "/works", "/shop", "/characters"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoHorizontalOverflow(page, path);
    }

    expectNoUnexpectedErrors(errors);
  });
});

test.describe("公开浏览 · 360px 窄屏", () => {
  // 显式固定 360x780，保证无论跑在哪个 project 下都有窄屏断言（13 文档：窄屏验收）。
  test.use({ viewport: { width: 360, height: 780 } });

  test("关键公开页面在 360px 下没有横向溢出", async ({ page }) => {
    const errors = collectPageErrors(page);

    for (const path of ["/", "/works", "/news", "/shop", "/characters", "/login"]) {
      await page.goto(path);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await expectNoHorizontalOverflow(page, path);
    }

    // 详情页也要满足同样的约束（含动态内容最长的作品详情）。
    const href = await firstDetailHref(page, "/works", "/works/");
    await page.goto(href);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await expectNoHorizontalOverflow(page, href);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("360px 下作品筛选仍可操作，空态不产生横向溢出", async ({ page }) => {
    const errors = collectPageErrors(page);
    const keyword = `e2e-narrow-${Date.now()}`;

    await page.goto("/works");
    await page.locator("#works-search").fill(keyword);
    await page.getByRole("button", { name: "搜索" }).click();

    await expect(page.getByText("没有符合当前筛选条件的作品")).toBeVisible();
    await expectNoHorizontalOverflow(page, "/works?q=<keyword>");

    expectNoUnexpectedErrors(errors);
  });
});

test.describe("公开页面基础可达性", () => {
  test("私密页面的 robots 为 noindex，公开页面可索引", async ({ page }) => {
    await page.goto("/login");
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow",
    );

    await page.goto(`${SITE_ORIGIN}/works`);
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "index, follow");
  });

  test("登录页是私密页：noindex 且不泄露未脱敏联系信息", async ({ page }) => {
    const errors = collectPageErrors(page);
    await page.goto("/login");

    await expect(page.getByRole("heading", { level: 1, name: "手机号验证码登录" })).toBeVisible();
    // 03/11 文档要求私密页不可被搜索引擎索引；OG 标签本身没有文档约束，
    // 因此这里不断言它的有无，只断言真正被要求的 robots 与敏感信息。
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute("content", "noindex, nofollow");
    await assertNoSensitiveLeak(page);

    expectNoUnexpectedErrors(errors);
  });
});
