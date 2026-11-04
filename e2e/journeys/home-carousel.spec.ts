import { expect, test } from "@playwright/test";

/**
 * 首页大图轮播。
 *
 * 轮播是纯展示控件：前进/后退只切画面，不改变地址，也不阻塞首屏内容。
 */
test.describe("首页轮播", () => {
  test("可前进、后退，且不改变页面地址", async ({ page }) => {
    await page.goto("/");

    await expect(page.getByRole("heading", { name: /把设定变成/ })).toBeVisible();
    await expect(page.getByRole("button", { name: "第 1 张" })).toHaveAttribute(
      "aria-current",
      "true",
    );

    await page.getByRole("button", { name: "下一张" }).click();
    await expect(page.getByRole("button", { name: "第 2 张" })).toHaveAttribute(
      "aria-current",
      "true",
    );

    await page.getByRole("button", { name: "上一张" }).click();
    await expect(page.getByRole("button", { name: "第 1 张" })).toHaveAttribute(
      "aria-current",
      "true",
    );
    await expect(page).toHaveURL(/\/$/);
  });
});
