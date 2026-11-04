import { expect, test } from "@playwright/test";

test("FAQ 双语：直达、搜索、切换语言重新读取正文", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  // 测试替身只提供运营内容与英文入口，不修改服务器配置或发布正式文案。
  await page.route("**/api/v1/content/settings", async (route) => {
    const response = await route.fetch();
    const settings = await response.json();
    settings["platform.feature_flags"] = { ...settings["platform.feature_flags"], en_locale: true };
    await route.fulfill({ json: settings });
  });
  await page.route("**/api/v1/content/faq**", async (route) => {
    const url = new URL(route.request().url());
    const locale = url.searchParams.get("locale");
    expect(["zh-CN", "en-US"]).toContain(locale);
    const english = locale === "en-US";
    const item = { id: "faq-language", kind: "faq", slug: "language", locale,
      title: english ? "How do I clean the fur?" : "如何清洁毛面？", summary: "", tags: [],
      route: english ? "/en/faq" : "/faq", cover: null,
      body_html: english ? "<p>Test English care answer.</p>" : "<p>测试中文养护答案。</p>",
    };
    await route.fulfill({ json: url.pathname.endsWith("/faq")
      ? { items: [item], total: 1, page: 1, limit: 50, has_more: false }
      : item });
  });
  await page.goto("/en/faq");
  await expect(page.getByRole("heading", { name: "Frequently asked questions", exact: true })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search questions" }).fill("clean");
  await expect(page.getByText("1 of 1 questions match.", { exact: true })).toBeVisible();
  await page.getByText("How do I clean the fur?", { exact: true }).click();
  await expect(page.getByText("Test English care answer.", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  if ((page.viewportSize()?.width ?? 1000) < 640) {
    await page.getByRole("button", { name: "Open navigation menu" }).click();
    await page.getByRole("button", { name: "简体中文", exact: true }).click();
  } else {
    await page.getByRole("button", { name: "Language：简体中文", exact: true }).click();
  }
  await expect(page).toHaveURL(/\/faq$/);
  await expect(page.getByRole("heading", { name: "常见问题", exact: true })).toBeVisible();
  await expect(page.getByRole("searchbox", { name: "搜索问题" })).toHaveValue("");
  if (!(await page.getByText("如何清洁毛面？", { exact: true }).isVisible())) {
    await page.getByText("其他问题", { exact: true }).click();
  }
  await page.getByText("如何清洁毛面？", { exact: true }).click();
  await expect(page.getByText("测试中文养护答案。", { exact: true })).toBeVisible();
  await expect(page.getByText("Test English care answer.", { exact: true })).toHaveCount(0);
  expect(errors).toEqual([]);
});
