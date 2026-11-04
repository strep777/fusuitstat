import { expect, test } from "@playwright/test";

/**
 * FR-026 公开参考估价。
 *
 * 访客不登录即可查看参考区间并提交估价请求；提交只生成估价记录，
 * 不进入询价、报价或订单流程。
 */
test.describe("公开参考估价", () => {
  test("匿名查看区间并提交估价请求", async ({ page }) => {
    await page.goto("/estimate");

    await expect(page.getByRole("heading", { name: "先估个价，再决定" })).toBeVisible();
    await expect(page.getByText("无需登录", { exact: true })).toBeVisible();

    await page.getByLabel("怎么称呼你").fill("公开估价测试");
    await page.getByLabel("角色名").fill("星绒龙");
    await page.getByLabel("物种 / 设定").fill("龙");
    await page.getByLabel("如何联系你").fill("estimate-e2e@example.com");
    await page.getByLabel("补充说明").fill("希望先了解全装与半装的区间差异。");
    await page.getByRole("checkbox", { name: /我同意工作室使用以上联系方式/ }).check();

    await page.getByRole("button", { name: "提交估价请求" }).click();

    await expect(page.getByRole("heading", { name: "参考区间已生成" })).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText(/est_[a-z0-9]+/i)).toBeVisible();
    await expect(page.getByText("3 个工作日").first()).toBeVisible();
    await expect(page).toHaveURL(/\/estimate$/);
  });
});
