/**
 * 运营后台的入口、登录与权限边界（真实浏览器 + 真实后端）。
 *
 * 覆盖用例：`文档/13_测试计划与验收用例.md` TC-A05
 * 「编辑员直接调用退款/导出/角色授权 API；管理员未 MFA → 服务端拒绝；不可通过 UI 隐藏代替授权」
 *
 * 依据文档：
 * - 03 站点路由与页面功能（后台独立入口 `/admin`，独立员工会话与 MFA；客户登录不继承员工权限）
 * - 06 API 接口契约（`AUTH_REQUIRED`/401、`AUTH_FAILED`/401、`FORBIDDEN`/403、`CSRF_INVALID`/403）
 * - 10 手机号登录安全与合规（后台会话与前台会话完全分离）
 *
 * 真实实现要点（决定本文件的选择器与 URL）：
 * - 后台 API 前缀 `/api/v1/admin`（`server/app/main.py:98`）；数据接口无会话 → 403 `FORBIDDEN`，
 *   `GET /api/v1/admin/auth/me` 无会话 → 401 `AUTH_REQUIRED`
 *   （`server/app/api/dependencies.py:196-203`、`server/app/modules/identity/ports.py:46-52`）。
 * - 口令步骤永远拿不到会话：成功只返回 `MFA_REQUIRED` / `MFA_ENROLL_REQUIRED` + `challenge_token`
 *   （`server/app/modules/identity/admin_auth.py:108-135`），只有第二步 `/admin/auth/mfa` 才下发后台 cookie。
 * - dev server 下**只能**用 `page.goto("/admin")` 进入后台：Vite 的 htmlFallback 会把 `/admin`
 *   重写到 `web/admin.html`；而 `/admin/login`、`/admin/dashboard` 这类子路径不存在对应 html，
 *   会回退到前台 `index.html` 并渲染前台 404。进入后台后的路由切换全部由前端完成。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  assertNoSensitiveLeak,
  collectPageErrors,
  loginViaSandboxApi,
  sessionReadHeaders,
  SITE_ORIGIN,
} from "../helpers";

function expectNoUnexpectedErrors(errors: string[], allowed: RegExp | null = null): void {
  const unexpected = allowed ? errors.filter((item) => !allowed.test(item)) : errors;
  expect(unexpected, `出现未预期的浏览器错误：\n${errors.join("\n")}`).toEqual([]);
}

/** 后台入口：dev server 下用 `/admin`（Vite htmlFallback → admin.html）。 */
async function openAdmin(page: Page): Promise<void> {
  await page.goto("/admin");
  await expect(page.getByRole("heading", { level: 1, name: "员工登录" })).toBeVisible({
    timeout: 20_000,
  });
}

test.describe("TC-A05 后台入口与登录", () => {
  test("未登录访问后台被挡到员工登录页，并保留不含敏感信息的目标路径", async ({ page }) => {
    const errors = collectPageErrors(page);

    await openAdmin(page);

    // returnTo 允许已编码或未编码（两者都是合法查询串），只断言目标路径出现在参数里。
    await expect(page).toHaveURL(/\/admin\/login\?returnTo=(%2F|\/)admin(%2F|\/)dashboard/);
    await expect(page.getByText("第一步 · 口令")).toBeVisible();
    await expect(page.getByText("口令由管理员设置；忘记口令需联系管理员重置并审计。")).toBeVisible();
    // 无公开员工注册入口。
    await expect(page.getByRole("link", { name: /注册/ })).toHaveCount(0);
    // 后台页面 noindex（admin-router.ts:158-169）。
    await expect(page.locator('meta[name="robots"]')).toHaveAttribute(
      "content",
      "noindex, nofollow, noarchive",
    );
    // 口令字段必须是密码框。
    await expect(page.locator("#admin-login-password")).toHaveAttribute("type", "password");

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("后台登录第一步不下发会话：口令只换取 MFA 挑战", async ({ page }) => {
    const errors = collectPageErrors(page);

    // 用错误口令验证「口令正确与否都拿不到会话」这一半；不依赖任何已存在的员工账号。
    const response = await page.request.post("/api/v1/admin/auth/login", {
      headers: sessionReadHeaders(),
      data: { login_name: "owner", password: "e2e-not-a-real-password" },
    });
    expect(response.status(), await response.text()).toBe(401);
    const payload = (await response.json()) as { error?: { code?: string; message?: string } };
    expect(payload.error?.code).toBe("AUTH_FAILED");
    // 未知账号与错误口令必须给出完全相同的响应，不泄露账号是否存在。
    expect(payload.error?.message).toBe("账号或密码不正确。");

    const cookies = await page.context().cookies();
    expect(
      cookies.filter((item) => /admin/.test(item.name)).map((item) => item.name),
      "口令步骤不得下发后台会话 cookie",
    ).toEqual([]);

    // 未登录时后台首页不可读。
    const me = await page.request.get("/api/v1/admin/auth/me", { headers: sessionReadHeaders() });
    expect(me.status()).toBe(401);

    expectNoUnexpectedErrors(errors);
  });

  test("登录后按权限显示入口 / 越权访问返回拒绝态（依赖已建员工账号）", async () => {
    test.fixme(
      true,
      "依赖可用员工账号：需要先执行 `cd server && uv run python -m app.cli create-owner --login owner --name \"工作室负责人\"`， " +
        "再在后台登录页完成 TOTP 首次绑定（`data-testid=\"otpauth-uri\"` / `recovery-codes`）。" +
        "当前环境没有该账号，无法构造「登录后侧边栏按权限显示入口」与「编辑员缺权限时 PermissionGate 说明」的断言。" +
        "在具备账号前，这部分不能标记为通过。",
    );
  });
});

test.describe("TC-A05 权限边界（服务端逐动作校验）", () => {
  test("未登录直接调用后台数据接口被拒（403 FORBIDDEN）", async ({ page }) => {
    const errors = collectPageErrors(page);

    for (const path of ["/api/v1/admin/orders", "/api/v1/admin/dashboard"]) {
      const response = await page.request.get(path, { headers: sessionReadHeaders() });
      expect(response.status(), `${path} 未登录时不应返回 ${response.status()}`).toBe(403);
      const payload = (await response.json()) as { error?: { code?: string } };
      expect(payload.error?.code).toBe("FORBIDDEN");
    }

    expectNoUnexpectedErrors(errors);
  });

  test("前台客户登录不能继承员工权限（同一 cookie 调后台接口仍是 403）", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginViaSandboxApi(page);

    // 前台会话确实有效。
    const me = await page.request.get("/api/v1/me", { headers: sessionReadHeaders() });
    expect(me.ok(), await me.text()).toBeTruthy();

    // 但后台接口仍然拒绝：UI 隐藏不能代替授权，会话也不共享。
    for (const path of ["/api/v1/admin/orders", "/api/v1/admin/dashboard"]) {
      const response = await page.request.get(path, { headers: sessionReadHeaders() });
      expect(response.status()).toBe(403);
      const payload = (await response.json()) as { error?: { code?: string; message?: string } };
      expect(payload.error?.code).toBe("FORBIDDEN");
      // 不泄露任何后台数据。
      expect(JSON.stringify(payload)).not.toMatch(/order_no|amount_cents|staff|permission/);
    }

    expectNoUnexpectedErrors(errors);
  });

  test("未登录的写操作（退款审批 / 角色授权 / 素材授权）被拒，且不产生任何业务效果", async ({ page }) => {
    const errors = collectPageErrors(page);

    // 三条都是真实存在且带 require_permission 的后台写接口；无员工会话时在鉴权阶段就被拒。
    const writes: Array<{ label: string; run: () => Promise<import("@playwright/test").APIResponse> }> = [
      {
        label: "退款审批 POST /admin/refunds/{id}/approve",
        run: () =>
          page.request.post("/api/v1/admin/refunds/rfd_e2e_not_exist/approve", {
            headers: { ...sessionReadHeaders(), "Idempotency-Key": `e2e-${Date.now()}` },
            data: { approved: true },
          }),
      },
      {
        label: "角色授权 POST /admin/characters/{id}/release",
        run: () =>
          page.request.post("/api/v1/admin/characters/chr_e2e_not_exist/release", {
            headers: { ...sessionReadHeaders(), "Idempotency-Key": `e2e-${Date.now()}` },
            data: { reason: "e2e" },
          }),
      },
      {
        label: "素材授权 PATCH /admin/media/{id}",
        run: () =>
          page.request.patch("/api/v1/admin/media/ast_e2e_not_exist", {
            headers: { ...sessionReadHeaders(), "Idempotency-Key": `e2e-${Date.now()}` },
            data: { public: true },
          }),
      },
    ];

    for (const item of writes) {
      const response = await item.run();
      expect(response.status(), `${item.label} 返回了 ${response.status()}`).toBe(403);
      const payload = (await response.json()) as { error?: { code?: string; message?: string } };
      expect(payload.error?.code).toBe("FORBIDDEN");
      expect(payload.error?.message).toBe("该操作仅限工作室员工。");
    }

    expectNoUnexpectedErrors(errors);
  });

  test("后台登录页不泄露员工侧私密信息", async ({ page }) => {
    const errors = collectPageErrors(page);
    await openAdmin(page);

    const body = await page.locator("body").innerText();
    // 登录前不应出现 otpauth URI 或恢复码清单。
    expect(body).not.toMatch(/otpauth:\/\//);
    await expect(page.getByTestId("otpauth-uri")).toHaveCount(0);
    await expect(page.getByTestId("recovery-codes")).toHaveCount(0);
    await assertNoSensitiveLeak(page);

    // 后台与前台是不同入口：前台不存在 /admin 路由。
    await page.goto(SITE_ORIGIN);
    await expect(page.getByRole("heading", { level: 1 })).toContainText("把设定变成");

    expectNoUnexpectedErrors(errors);
  });
});
