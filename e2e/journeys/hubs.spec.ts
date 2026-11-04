/**
 * 保养服务、角色汤与合并商城（FR-025 / C31–C34）。
 *
 * 覆盖：
 * - 保养服务套餐与预约表单（登录 → 套餐 → 期望日期 → 返还方式 → 提交）；
 * - 角色汤与合并商城的公开入口、真实目录与筛选；
 * - 旧 /props、/merch 地址重定向到合并商城对应分类；
 * - 提交后的记录字段（preferred_date / return_method）由服务端回读确认；
 * - 全程没有 JS 异常或真实网络失败。
 */

import { expect, test } from "@playwright/test";

import {
  assertNoSensitiveLeak,
  collectPageErrors,
  loginViaSandboxApi,
  SITE_ORIGIN,
} from "../helpers";

/** 北京时间今天 +N 天（YYYY-MM-DD）。 */
function shanghaiDatePlus(days: number): string {
  const value = new Date(Date.now() + days * 86_400_000);
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(value);
}

test.describe("保养服务 / 角色汤 / 合并商城", () => {
  test("新入口可直接打开，旧专题地址重定向到商城分类", async ({ page }) => {
    const errors = collectPageErrors(page);

    await page.goto("/wash");
    await expect(page.getByRole("heading", { level: 1, name: /洗掉旅途/ })).toBeVisible();
    await expect(page.getByRole("heading", { level: 2, name: "保养套餐" })).toBeVisible();
    await expect(page.locator('a[href^="/shop/demo-"]').first()).toBeVisible();
    expect(await page.locator('a[href^="/shop/demo-"]').count()).toBeGreaterThanOrEqual(4);
    await assertNoSensitiveLeak(page);

    await page.goto("/characters");
    await expect(page.getByRole("heading", { level: 1, name: "角色汤" })).toBeVisible();
    await expect(page.getByText(/位角色可查看/)).toBeVisible();

    await page.goto("/shop?category=props");
    await expect(page.getByRole("heading", { level: 1, name: "商城" })).toBeVisible();
    await expect(page.locator('a[href^="/shop/demo-prop-"]').first()).toBeVisible();

    await page.goto("/shop?category=merch");
    await expect(page.getByRole("heading", { level: 1, name: "商城" })).toBeVisible();
    await expect(page.locator('[data-display-only="true"]').first()).toBeVisible();

    await page.goto("/props");
    await expect(page).toHaveURL(/\/shop\?category=props/);
    await page.goto("/merch");
    await expect(page).toHaveURL(/\/shop\?category=merch/);

    expect(errors, `新入口出现浏览器错误：\n${errors.join("\n")}`).toEqual([]);
  });

  test("登录后提交保养预约并回读日期与返还方式", async ({ page }) => {
    await loginViaSandboxApi(page);

    await page.goto("/wash");
    await expect(page.locator("#wash-package option")).not.toHaveCount(0);

    const optionCount = await page.locator("#wash-package option").count();
    await page.locator("#wash-package").selectOption({ index: optionCount - 1 });

    const preferredDate = shanghaiDatePlus(3);
    await page.locator("#wash-date").fill(preferredDate);
    await page.locator("#wash-return").selectOption("ship");

    const note = `E2E 保养预约 ${Date.now()}：请先检查毛面。`;
    await page.locator("#wash-note").fill(note);
    await page.getByRole("button", { name: /提交预约/ }).click();

    await expect(page.getByRole("status").filter({ hasText: "预约已提交" })).toBeVisible({ timeout: 15_000 });

    const response = await page.request.get("/api/v1/stock-applications", {
      headers: { Origin: SITE_ORIGIN },
    });
    expect(response.ok(), `回读申请失败：HTTP ${response.status()}`).toBeTruthy();
    const applications = (await response.json()) as Array<{
      kind: string;
      status: string;
      preferred_date: string | null;
      return_method: string | null;
      measurement_note: string | null;
    }>;
    const created = applications.find((item) => item.measurement_note === note);
    expect(created, "提交成功但回读不到该预约").toBeTruthy();
    expect(created?.kind).toBe("cleaning");
    expect(created?.status).toBe("SUBMITTED");
    expect(created?.preferred_date).toBe(preferredDate);
    expect(created?.return_method).toBe("ship");
  });
});
