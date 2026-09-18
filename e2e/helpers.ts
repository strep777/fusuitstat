/**
 * 浏览器测试共享工具：沙盒登录、断言助手与错误收集。
 *
 * 开发环境登录使用沙盒短信通道：验证码写入 `server/var/storage/sandbox/sms.log`，
 * 同时登录接口在非生产环境返回 `dev_hint`。测试优先使用 `dev_hint`，
 * 不解析日志文件；**生产环境不存在该字段**。
 */

import type { Page } from "@playwright/test";

export const SITE_ORIGIN = process.env.E2E_SITE_ORIGIN ?? "http://127.0.0.1:5173";

/**
 * 短信验证码输入框的唯一选择器。
 *
 * `OtpInput` 把真实 input 渲染成 `autocomplete="one-time-code"` 的单一隐藏输入框
 * （六格只是可视层）。不要用 `getByLabel(/短信验证码/)`：登录页的条款勾选框
 * 文案里也含「短信验证码」，会命中两个元素并触发 strict mode 报错。
 */
export const OTP_INPUT = 'input[autocomplete="one-time-code"]';

/**
 * 浏览器把「HTTP 4xx/5xx 响应」也记成 console error。
 *
 * 接口按 06 文档用 401/403/404/409 表达业务结果（未登录、无权限、对象不存在…），
 * 这是**契约行为**而不是页面的静默失败，因此单独归类，由用例按需断言；
 * 真正要拦住的是 JS 异常（`pageerror`）与真实网络失败（`requestfailed`）。
 */
const NETWORK_STATUS_NOISE = /Failed to load resource: the server responded with a status of \d{3}/;

export interface PageSignals {
  /** JS 异常与真实网络失败：出现即为缺陷。 */
  errors: string[];
  /** 契约内的 HTTP 失败响应（401/403/404…），由用例决定是否可接受。 */
  httpFailures: string[];
}

/** 记录浏览器信号，便于断言「没有静默失败」，同时不把契约内的 4xx 当缺陷。 */
export function collectPageSignals(page: Page): PageSignals {
  const signals: PageSignals = { errors: [], httpFailures: [] };
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const text = message.text();
    if (NETWORK_STATUS_NOISE.test(text)) {
      signals.httpFailures.push(`console: ${text}`);
      return;
    }
    signals.errors.push(`console: ${text}`);
  });
  page.on("pageerror", (error) => signals.errors.push(`pageerror: ${error.message}`));
  page.on("requestfailed", (request) => {
    // 忽略被取消的导航请求（用户中途点击返回）。
    if (request.failure()?.errorText === "net::ERR_ABORTED") return;
    signals.errors.push(`requestfailed: ${request.url()} ${request.failure()?.errorText ?? ""}`);
  });
  return signals;
}

/** 只要「JS 异常与真实网络失败」列表；兼容既有用例的调用方式。 */
export function collectPageErrors(page: Page): string[] {
  return collectPageSignals(page).errors;
}

/** `loginWithSandboxOtp` 的可选参数；`returnTo` 只接受本站相对路径。 */
export interface SandboxLoginOptions {
  /** 登录页地址栏里的 `returnTo`（用于验证登录后回到目标页 / 拒绝外部跳转）。 */
  returnTo?: string;
}

/** 通过沙盒短信通道登录。返回使用的手机号，便于后续断言脱敏显示。 */
export async function loginWithSandboxOtp(
  page: Page,
  phone = "13800000001",
  options: SandboxLoginOptions = {},
): Promise<string> {
  const loginUrl = options.returnTo
    ? `${SITE_ORIGIN}/login?returnTo=${encodeURIComponent(options.returnTo)}`
    : `${SITE_ORIGIN}/login`;
  await page.goto(loginUrl);

  await page.getByLabel(/手机号/).fill(phone);
  await page.getByRole("button", { name: /获取验证码|发送验证码/ }).click();

  // 开发环境返回 dev_hint；测试依赖它，不读取服务端日志。
  const hint = page.getByTestId("dev-hint");
  await hint.waitFor({ state: "visible", timeout: 10_000 });
  const code = (await hint.textContent())?.match(/\d{6}/)?.[0];
  if (!code) throw new Error("未取得开发环境验证码提示；请确认后端 APP_ENV 不是 production");

  // OtpInput 是一个可视化六格的单一输入框（读屏只读一次），
  // 因此按稳定属性定位真实 input，而不是逐格填充或按可能歧义的 label 匹配。
  await page.locator(OTP_INPUT).fill(code);

  await page.getByLabel(/我已阅读|同意/).check();
  await page.getByRole("button", { name: /登录|进入/ }).click();
  if (options.returnTo) {
    await page.waitForURL((url) => url.pathname === options.returnTo, { timeout: 15_000 });
  } else {
    await page.waitForURL(/(account|commission)/, { timeout: 15_000 });
  }
  return phone;
}

/**
 * 每个用例使用独立手机号，避免命中服务端短信限流（`retry_after` 默认 60 秒）。
 * 中国大陆号码为 1 开头的 11 位数字。
 */
export function uniqueSandboxPhone(): string {
  const tail = String(Date.now()).slice(-6);
  const random = String(Math.floor(Math.random() * 90) + 10);
  return `139${tail}${random}`;
}

export interface SandboxSession {
  phone: string;
  /** 登录响应体里的会话 CSRF token（不是 cookie）；写请求必须带上它（10 文档）。 */
  csrfToken: string;
  userId: string;
}

/**
 * 直接用真实接口完成沙盒登录（不经过 UI），供需要自行准备前置数据的用例使用。
 *
 * 走的是与前端完全相同的接口链路：匿名 CSRF → 短信挑战 → 校验验证码；
 * 会话 cookie 落在 `page` 所属浏览器上下文里，之后的 `page.goto` 即为已登录状态。
 * 服务端写请求同时校验 Origin 与 `X-CSRF-Token`（`server/app/api/dependencies.py`），
 * 缺任一项都会被 403 `CSRF_INVALID` 拒绝，因此这里显式带上 Origin。
 */
export async function loginViaSandboxApi(page: Page, phone = uniqueSandboxPhone()): Promise<SandboxSession> {
  const originHeader = { Origin: SITE_ORIGIN };

  const csrfResponse = await page.request.get("/api/v1/session/csrf", { headers: originHeader });
  if (!csrfResponse.ok()) throw new Error(`取匿名 CSRF 失败：HTTP ${csrfResponse.status()}`);
  const anonymousToken = ((await csrfResponse.json()) as { csrf_token: string }).csrf_token;

  const challengeResponse = await page.request.post("/api/v1/auth/sms/challenges", {
    headers: { ...originHeader, "X-CSRF-Token": anonymousToken },
    data: { phone, purpose: "login" },
  });
  if (!challengeResponse.ok()) {
    throw new Error(
      `发送沙盒验证码失败：HTTP ${challengeResponse.status()} ${await challengeResponse.text()}`,
    );
  }
  const challenge = (await challengeResponse.json()) as { challenge_id: string; dev_hint?: string };
  if (!challenge.dev_hint) {
    throw new Error("未取得开发环境验证码提示；请确认后端 APP_ENV 不是 production");
  }

  const verifyResponse = await page.request.post("/api/v1/auth/sms/verify", {
    headers: originHeader,
    data: {
      challenge_id: challenge.challenge_id,
      phone,
      code: challenge.dev_hint,
      accepted: true,
    },
  });
  if (!verifyResponse.ok()) {
    throw new Error(`沙盒登录失败：HTTP ${verifyResponse.status()} ${await verifyResponse.text()}`);
  }
  const session = (await verifyResponse.json()) as { csrf_token: string; user: { id: string } };
  return { phone, csrfToken: session.csrf_token, userId: session.user.id };
}

/** 已登录会话的写请求头：Origin + X-CSRF-Token 缺一不可。 */
export function sessionWriteHeaders(session: SandboxSession): Record<string, string> {
  return {
    Origin: SITE_ORIGIN,
    "X-CSRF-Token": session.csrfToken,
    "Idempotency-Key": `e2e-${Date.now()}-${Math.floor(Math.random() * 1e6)}`,
  };
}

/** 已登录会话的读请求头：只需要可信 Origin。 */
export function sessionReadHeaders(): Record<string, string> {
  return { Origin: SITE_ORIGIN };
}

/** 断言页面上不出现禁止暴露的敏感内容（NFR-09）。 */
export async function assertNoSensitiveLeak(page: Page): Promise<void> {
  const html = await page.content();
  const forbidden: Array<{ pattern: RegExp; hint: string }> = [
    { pattern: /1[3-9]\d{9}(?![\d*])/, hint: "未脱敏手机号" },
    { pattern: /__Host-/, hint: "cookie 名出现在页面文本" },
    { pattern: /BEGIN [A-Z ]*PRIVATE KEY/, hint: "私钥内容" },
    { pattern: /otpauth:\/\//, hint: "员工 MFA 绑定信息不应出现在普通页面" },
  ];
  for (const { pattern, hint } of forbidden) {
    const match = pattern.exec(html);
    if (!match) continue;
    // 失败信息必须能定位到**具体位置**：泄漏值本身按手机号规则脱敏，只保留上下文，
    // 否则只报「出现了手机号」无法判断是哪一处渲染漏了脱敏。
    const start = Math.max(0, match.index - 120);
    const context = html
      .slice(start, match.index + match[0].length + 120)
      .replace(/\s+/g, " ")
      .replace(match[0], `${match[0].slice(0, 3)}****${match[0].slice(-2)}`);
    throw new Error(`页面出现不应暴露的内容（${hint}）：…${context}…`);
  }
}

/** 等待网络空闲后断言存在给定状态文案。 */
export async function expectStatusText(page: Page, text: string | RegExp): Promise<void> {
  await page.waitForLoadState("networkidle");
  await page.getByText(text).first().waitFor({ state: "visible", timeout: 10_000 });
}
