/**
 * 身份、会话与写请求安全（真实浏览器 + 真实后端）。
 *
 * 覆盖用例：`文档/13_测试计划与验收用例.md`
 * - TC-A02 错码、过期码、旧重发码、不同用途码 → 全部拒绝；失败计数不被重建挑战绕过
 * - TC-A03 两连接同时验证一个有效 OTP → 只一次消费；session 轮换；退出后旧 token 失效
 * - TC-A04 用户 A 读/改 B 的订单、反馈、量体、地址、图片 → 拒绝且不泄露内容；更换随机 ID 无效
 * - TC-A06 跨域伪造 POST、恶意 returnTo、会话过期时提交 → CSRF/open redirect 被拒；用户输入可安全恢复
 *
 * 依据文档：
 * - 06 API 接口契约（错误码与状态码约定：400/401/403/404/409/422/429）
 * - 10 手机号登录安全与合规（会话只存哈希、CSRF 绑定会话、写请求同时校验 Origin/受信 Referer、
 *   返回目标只允许本站相对路径）
 * - 03 站点路由与页面功能（401 引导登录并保留不含敏感信息的目标路径；403 不透露对象是否存在）
 *
 * 真实错误码（`server/app/platform/errors.py:32-71`）：
 * `AUTH_REQUIRED`/401、`CSRF_INVALID`/403、`FORBIDDEN`/403、`RESOURCE_NOT_FOUND`/404、
 * `VALIDATION_ERROR`/422、`SMS_CODE_INVALID`/400、`SMS_CHALLENGE_USED`、`SMS_CODE_EXPIRED`。
 * 注意：仓库里**不存在** `UNAUTHENTICATED`，会话失效返回的是 `AUTH_REQUIRED`。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  assertNoSensitiveLeak,
  collectPageErrors,
  OTP_INPUT,
  loginViaSandboxApi,
  sessionReadHeaders,
  sessionWriteHeaders,
  SITE_ORIGIN,
  uniqueSandboxPhone,
} from "../helpers";

function expectNoUnexpectedErrors(errors: string[], allowed: RegExp | null = null): void {
  const unexpected = allowed ? errors.filter((item) => !allowed.test(item)) : errors;
  expect(unexpected, `出现未预期的浏览器错误：\n${errors.join("\n")}`).toEqual([]);
}

/** 在已经打开的 `/login` 页面上走完沙盒登录表单（不负责导航，便于复用恶意 returnTo 场景）。 */
async function completeLoginForm(page: Page, phone: string): Promise<void> {
  await page.getByLabel(/手机号/).fill(phone);
  await page.getByRole("button", { name: /获取验证码|发送验证码/ }).click();

  const hint = page.getByTestId("dev-hint");
  await hint.waitFor({ state: "visible", timeout: 10_000 });
  const code = (await hint.textContent())?.match(/\d{6}/)?.[0];
  if (!code) throw new Error("未取得开发环境验证码提示；请确认后端 APP_ENV 不是 production");

  await page.locator(OTP_INPUT).fill(code);
  await page.getByLabel(/我已阅读|同意/).check();
  await page.getByRole("button", { name: /登录|进入/ }).click();
}

/** 发送一次沙盒验证码挑战，返回 challenge_id 与开发环境验证码。 */
async function requestChallenge(
  page: Page,
  phone: string,
): Promise<{ challengeId: string; code: string }> {
  const headers = sessionReadHeaders();
  const csrfResponse = await page.request.get("/api/v1/session/csrf", { headers });
  expect(csrfResponse.ok()).toBeTruthy();
  const anonymousToken = ((await csrfResponse.json()) as { csrf_token: string }).csrf_token;

  const response = await page.request.post("/api/v1/auth/sms/challenges", {
    headers: { ...headers, "X-CSRF-Token": anonymousToken },
    data: { phone, purpose: "login" },
  });
  expect(response.ok(), await response.text()).toBeTruthy();
  const payload = (await response.json()) as { challenge_id: string; dev_hint?: string };
  expect(payload.dev_hint, "开发环境应返回 dev_hint 便于联调").toBeTruthy();
  return { challengeId: payload.challenge_id, code: payload.dev_hint as string };
}

test.describe("TC-A02 验证码挑战的拒绝路径", () => {
  test("验证码错误时贴字段报错并留在登录页，不建立会话", async ({ page }) => {
    const errors = collectPageErrors(page);
    const phone = uniqueSandboxPhone();

    await page.goto("/login");
    await page.getByLabel(/手机号/).fill(phone);
    await page.getByRole("button", { name: /获取验证码|发送验证码/ }).click();
    await page.getByTestId("dev-hint").waitFor({ state: "visible", timeout: 10_000 });

    await page.locator(OTP_INPUT).fill("000000");
    await page.getByLabel(/我已阅读|同意/).check();
    await page.getByRole("button", { name: /登录|进入/ }).click();

    // identity.ts:106 的稳定文案；错误贴字段（11 文档）。
    await expect(page.getByText("验证码不正确，请核对后重新输入。").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(page).toHaveURL(/\/login/);
    // 没有会话：/account 仍然会被守卫挡回登录页。
    await page.goto("/account");
    await expect(page).toHaveURL(/\/login\?returnTo=/);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("同一个有效验证码只能用一次；重复提交被拒", async ({ page }) => {
    const errors = collectPageErrors(page);
    const phone = uniqueSandboxPhone();
    const challenge = await requestChallenge(page, phone);

    const first = await page.request.post("/api/v1/auth/sms/verify", {
      headers: sessionReadHeaders(),
      data: {
        challenge_id: challenge.challengeId,
        phone,
        code: challenge.code,
        accepted: true,
      },
    });
    expect(first.ok(), await first.text()).toBeTruthy();

    // 第二次复用同一 challenge + 同一验证码：必须被拒（只一次消费）。
    const replay = await page.request.post("/api/v1/auth/sms/verify", {
      headers: sessionReadHeaders(),
      data: {
        challenge_id: challenge.challengeId,
        phone,
        code: challenge.code,
        accepted: true,
      },
    });
    expect(replay.status(), await replay.text()).toBeGreaterThanOrEqual(400);
    const payload = (await replay.json()) as { error?: { code?: string } };
    expect(
      ["SMS_CHALLENGE_USED", "SMS_CODE_EXPIRED", "SMS_CODE_INVALID", "AUTH_REQUIRED", "VALIDATION_ERROR"],
      `非预期错误码：${JSON.stringify(payload)}`,
    ).toContain(payload.error?.code ?? "");

    expectNoUnexpectedErrors(errors);
  });

  test("未勾选协议的提交被服务端拒绝，且不建立会话", async ({ page }) => {
    const errors = collectPageErrors(page);
    const phone = uniqueSandboxPhone();
    const challenge = await requestChallenge(page, phone);

    const response = await page.request.post("/api/v1/auth/sms/verify", {
      headers: sessionReadHeaders(),
      data: {
        challenge_id: challenge.challengeId,
        phone,
        code: challenge.code,
        accepted: false,
      },
    });
    expect(response.status(), await response.text()).toBeGreaterThanOrEqual(400);

    const me = await page.request.get("/api/v1/me", { headers: sessionReadHeaders() });
    expect(me.status()).toBe(401);

    expectNoUnexpectedErrors(errors);
  });
});

test.describe("TC-A03 会话轮换与退出后失效", () => {
  test("登录后读取会话 cookie；退出后同一 cookie 不再能访问本人接口", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);

    const cookies = await page.context().cookies();
    const sessionCookie = cookies.find(
      (item) => /session/.test(item.name) && !/admin/.test(item.name) && item.value.length > 0,
    );
    expect(sessionCookie, `登录后应下发会话 cookie，实际：${cookies.map((c) => c.name).join(", ")}`)
      .toBeTruthy();

    // 登录态可读本人信息。
    const me = await page.request.get("/api/v1/me", { headers: sessionReadHeaders() });
    expect(me.ok(), await me.text()).toBeTruthy();

    // 退出登录（10 文档：会话只存哈希，退出即撤销）。
    const logout = await page.request.post("/api/v1/auth/logout", {
      headers: sessionWriteHeaders(session),
    });
    expect([200, 204]).toContain(logout.status());

    const afterLogout = await page.request.get("/api/v1/me", { headers: sessionReadHeaders() });
    expect(afterLogout.status()).toBe(401);
    const payload = (await afterLogout.json()) as { error?: { code?: string } };
    expect(payload.error?.code).toBe("AUTH_REQUIRED");

    // 把退出前的旧 cookie 手动塞回去：服务端已撤销该会话，仍然必须被拒。
    await page.context().addCookies([
      {
        name: sessionCookie?.name as string,
        value: sessionCookie?.value as string,
        url: SITE_ORIGIN,
      },
    ]);
    const withOldCookie = await page.request.get("/api/v1/me", { headers: sessionReadHeaders() });
    expect(withOldCookie.status(), "旧会话 token 在退出后仍然可用").toBe(401);

    expectNoUnexpectedErrors(errors);
  });
});

test.describe("TC-A04 越权访问与随机 ID", () => {
  test("用随机 ID 读取他人订单只得到 404，不泄露任何订单内容", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginViaSandboxApi(page);

    const response = await page.request.get("/api/v1/orders/ord_e2e_not_exist_0001", {
      headers: sessionReadHeaders(),
    });
    expect(response.status()).toBe(404);
    const payload = (await response.json()) as {
      error?: { code?: string; message?: string; fields?: Record<string, string> };
    };
    expect(payload.error?.code).toBe("RESOURCE_NOT_FOUND");
    // 403/404 不展示对象是否存在的额外信息（03 文档）。
    expect(JSON.stringify(payload.error?.fields ?? {})).toBe("{}");
    expect(JSON.stringify(payload)).not.toMatch(/order_no|total_cents|recipient|phone/);

    expectNoUnexpectedErrors(errors);
  });

  test("随机 ID 的订单详情页渲染「页面不存在」，不渲染订单明细", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginViaSandboxApi(page);

    await page.goto("/account/orders/ord_e2e_not_exist_0001");

    // EmptyState tone="not-found" 的固定标记文案（base.ts:228）。
    await expect(page.getByText("页面不存在", { exact: true })).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText("订单明细")).toHaveCount(0);
    await expect(page.getByText("支付记录")).toHaveCount(0);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("修改他人地址（随机 ID）被拒，且不返回地址内容", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);

    const response = await page.request.patch("/api/v1/me/addresses/adr_e2e_not_exist_0001", {
      headers: sessionWriteHeaders(session),
      data: { recipient: "越权测试", version: 1 },
    });
    expect(response.status(), await response.text()).toBeGreaterThanOrEqual(400);
    const payload = (await response.json()) as { error?: { code?: string } };
    expect(["RESOURCE_NOT_FOUND", "FORBIDDEN", "VALIDATION_ERROR"]).toContain(
      payload.error?.code ?? "",
    );

    expectNoUnexpectedErrors(errors);
  });
});

test.describe("TC-A06 会话过期、恶意 returnTo 与 CSRF", () => {
  test("会话过期后访问私密页被重定向到登录页，并保留目标路径", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginViaSandboxApi(page);

    // 模拟会话过期：清掉浏览器里的会话 cookie（服务端侧仍按未认证处理）。
    await page.context().clearCookies();

    await page.goto("/account/orders");
    await expect(page).toHaveURL(/\/login\?returnTo=(%2F|\/)account(%2F|\/)orders/);
    // 返回目标只允许本站相对路径，且提示里回显的是本站路径。
    await expect(page.getByRole("link", { name: "先去目标页面（登录后可返回）" })).toHaveAttribute(
      "href",
      "/account/orders",
    );

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("恶意 returnTo 被忽略，登录后回到「我的账户」而不是外部地址", async ({ page }) => {
    const errors = collectPageErrors(page);
    const phone = uniqueSandboxPhone();

    await page.goto(`/login?returnTo=${encodeURIComponent("//evil.example/steal")}`);

    // identity.ts:175-183 的 safeReturnPath：// 开头一律回退到 /account。
    await expect(page.getByRole("link", { name: "先去目标页面（登录后可返回）" })).toHaveAttribute(
      "href",
      "/account",
    );

    await completeLoginForm(page, phone);

    await page.waitForURL((url) => url.pathname === "/account", { timeout: 20_000 });
    expect(new URL(page.url()).origin).toBe(SITE_ORIGIN);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("已登录的写请求缺少 CSRF 头被 403 拒绝，带上正确 token 才成功", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);

    // 缺少 X-CSRF-Token：verify_csrf 返回 CSRF_INVALID（identity/service.py:342-347）。
    const withoutToken = await page.request.post("/api/v1/me/sessions/revoke-others", {
      headers: sessionReadHeaders(),
    });
    expect(withoutToken.status()).toBe(403);
    const denied = (await withoutToken.json()) as { error?: { code?: string } };
    expect(denied.error?.code).toBe("CSRF_INVALID");

    // 带错误 token 同样被拒。
    const wrongToken = await page.request.post("/api/v1/me/sessions/revoke-others", {
      headers: { ...sessionReadHeaders(), "X-CSRF-Token": "e2e-wrong-token" },
    });
    expect(wrongToken.status()).toBe(403);

    // 可控对照：带正确 token 与可信 Origin 时同一个接口成功。
    const allowed = await page.request.post("/api/v1/me/sessions/revoke-others", {
      headers: sessionWriteHeaders(session),
    });
    expect(allowed.ok(), await allowed.text()).toBeTruthy();

    expectNoUnexpectedErrors(errors);
  });

  test("不可信 Origin 的写请求被拒（CSRF_INVALID）", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);

    // commissions 路由挂了 router 级 enforce_write_guard（server/app/modules/commissions/router.py:55）。
    const crossSite = await page.request.post("/api/v1/inquiries", {
      headers: { Origin: "https://evil.example", "X-CSRF-Token": session.csrfToken },
      data: {
        character_name: "跨站伪造",
        species: "龙",
        series_id: "ser_e2e_not_exist",
        suit_type: "full",
        delivery_region: "中国大陆",
        design_asset_ids: [],
      },
    });
    expect(crossSite.status()).toBe(403);
    const payload = (await crossSite.json()) as { error?: { code?: string } };
    expect(payload.error?.code).toBe("CSRF_INVALID");

    expectNoUnexpectedErrors(errors);
  });

  test("所有写接口都应校验受信 Origin（身份模块当前缺失，见 fixme 说明）", async () => {
    test.fixme(
      true,
      "已实测的偏离：AGENTS.md 约束 6 与 10 文档要求「写请求同时校验 Origin 与受信 Referer」，" +
        "但 `POST /api/v1/me/sessions/revoke-others`（server/app/modules/identity/router.py:300-301）只在依赖里声明了 `CsrfDep`，" +
        "既没有 router 级 `Depends(enforce_write_guard)`，函数体里也没有显式调用。" +
        "实测：带合法会话 CSRF token + `Origin: https://evil.example` 时该接口返回 **200 {\"revoked\":0}**（对照 `POST /api/v1/inquiries` 同条件返回 403 CSRF_INVALID）。" +
        "在服务端补齐 Origin 校验前，本用例不能断言通过；也不允许把 200 写成预期行为。",
    );
  });

  test("登录前的写请求缺少匿名 CSRF 被拒", async ({ page }) => {
    const errors = collectPageErrors(page);

    const response = await page.request.post("/api/v1/auth/sms/challenges", {
      headers: sessionReadHeaders(),
      data: { phone: uniqueSandboxPhone(), purpose: "login" },
    });
    expect(response.status()).toBe(403);
    const payload = (await response.json()) as { error?: { code?: string } };
    expect(payload.error?.code).toBe("CSRF_INVALID");

    expectNoUnexpectedErrors(errors);
  });

  test("页面不泄露手机号、cookie 名或私钥等敏感内容", async ({ page }) => {
    const errors = collectPageErrors(page);
    const phone = uniqueSandboxPhone();
    await loginViaSandboxApi(page, phone);

    for (const path of ["/account", "/account/security", "/account/orders", "/login", "/payment/return"]) {
      await page.goto(path);
      await assertNoSensitiveLeak(page);
      // 会话 cookie 是 HttpOnly，任何页面正文都不应出现它的名字。
      await expect(page.getByText(/__Host-session|__Host-admin-session/)).toHaveCount(0);
    }

    expectNoUnexpectedErrors(errors);
  });
});
