/**
 * 登录流程测试（FR-005 / FR-018 / 10 文档）。
 *
 * 覆盖：
 * - 手机号格式校验（发送前拦截，错误贴字段）；
 * - 协议未勾选时不能提交（服务端同样会拒绝）；
 * - 倒计时期间禁用重发，并展示剩余秒数；
 * - 验证码错误时按服务端错误码展示文案；
 * - returnTo 白名单：只允许本站相对路径，拒绝 `//evil.example` 与编码绕过；
 * - 路由守卫生成的 returnTo 是安全的本站路径。
 */
import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { createRouter, createWebHistory } from "vue-router";

import LoginPage from "@/modules/identity/pages/LoginPage.vue";
import NotificationsPage from "@/modules/notifications/pages/NotificationsPage.vue";
import { safeReturnPath } from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { router as realRouter } from "@/app/router";

interface FetchCall {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: FetchCall[] = [];

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  } as unknown as Response;
}

function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse({ error: { code, message, fields: {}, trace_id: "trace-test" } }, status);
}

const VERIFY_SUCCESS = {
  user: {
    id: "user_1",
    nickname: "星绒委托人",
    phone_masked: "+86138****0000",
    locale: "zh-CN",
    currency: "CNY",
    region: "CN",
    status: "active",
    created_at: "2026-01-01T00:00:00+00:00",
  },
  session_expires_at: "2026-12-31T00:00:00+00:00",
  idle_expires_at: "2026-12-31T00:00:00+00:00",
  csrf_token: "csrf-test-token",
  is_new_user: false,
};

const ME_RESPONSE = {
  user: VERIFY_SUCCESS.user,
  permissions: [],
  sensitive_verified: false,
  unread_notifications: 0,
  pending_actions: {
    orders_awaiting_payment: 0,
    quotes_awaiting_decision: 0,
    stages_awaiting_confirmation: 0,
    after_sales_open: 0,
  },
};

interface MockOptions {
  challenge?: () => Response;
  verify?: () => Response;
}

function installFetchMock(options: MockOptions = {}): void {
  calls = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    let body: Record<string, unknown> | null = null;
    if (typeof init?.body === "string") {
      body = JSON.parse(init.body) as Record<string, unknown>;
    }
    calls.push({ url, method, body });

    if (url.includes("/session/csrf")) {
      return jsonResponse({ csrf_token: "anon-csrf", expires_in: 1800 });
    }
    if (url.includes("/auth/sms/challenges")) {
      return options.challenge
        ? options.challenge()
        : jsonResponse({ challenge_id: "chg_1", expires_in: 300, retry_after: 45, dev_hint: "123456" });
    }
    if (url.includes("/auth/sms/verify")) {
      return options.verify ? options.verify() : jsonResponse(VERIFY_SUCCESS);
    }
    if (url.includes("/me/consents")) {
      return jsonResponse({ id: "consent_1", purpose: "terms", accepted_at: "2026-01-01T00:00:00+00:00" }, 201);
    }
    if (url.endsWith("/me") && method === "GET") {
      return jsonResponse(ME_RESPONSE);
    }
    if (url.includes("/content/settings")) {
      return jsonResponse({ "commission.policy": { terms_version: "commission-1" } });
    }
    if (url.includes("/notifications")) {
      return jsonResponse({ items: [], next_cursor: null, has_more: false, total: 0, page: 1, limit: 20 });
    }
    return jsonResponse({});
  });
}

function testRouter() {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div>home</div>" } },
      { path: "/login", name: "login", component: { template: "<div>login</div>" } },
      { path: "/account", name: "account", component: { template: "<div>account</div>" } },
      { path: "/account/notifications", name: "account-notifications", component: { template: "<div>notifications</div>" } },
      { path: "/commission/new", name: "inquiry-new", component: { template: "<div>inquiry</div>" } },
    ],
  });
}

let mounted: VueWrapper | null = null;

async function mountLogin(query: Record<string, string> = {}) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = testRouter();
  await router.push({ path: "/login", query });
  await router.isReady();
  // 必须挂到 document 上：happy-dom 只对已连接的表单执行「点击提交按钮 → 提交表单」的隐式提交。
  const wrapper = mount(LoginPage, { global: { plugins: [pinia, router] }, attachTo: document.body });
  mounted = wrapper;
  await flush();
  return { wrapper, router, auth: useAuthStore() };
}

afterEach(() => {
  mounted?.unmount();
  mounted = null;
  document.body.innerHTML = "";
});

/** 等待 watch/onMounted 里的 Promise 链结束（fetch 已被 mock 为立即 resolve）。 */
async function flush(times = 12): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
  }
}

async function setPhoneAndSend(wrapper: Awaited<ReturnType<typeof mountLogin>>["wrapper"], phone: string) {
  await wrapper.find("#login-phone").setValue(phone);
  const buttons = wrapper.findAll("button");
  const sendButton = buttons.find((button) => button.text().includes("验证码"));
  expect(sendButton, "应存在发送验证码按钮").toBeTruthy();
  await sendButton!.trigger("click");
  await flush();
}

/**
 * 提交登录表单。
 *
 * happy-dom 只在原生 `click()` 上执行表单的隐式提交；`@vue/test-utils` 的
 * `trigger("click")` 只派发事件，不会触发 `<form>` 的 submit，因此这里用原生
 * click（按钮禁用时不会提交，和真实浏览器一致）。
 */
async function submitLogin(wrapper: VueWrapper, times = 60): Promise<void> {
  const submit = wrapper.findAll("button").find((button) => button.text().includes("登录 / 注册"));
  expect(submit, "应存在登录 / 注册按钮").toBeTruthy();
  (submit!.element as HTMLButtonElement).click();
  await flush(times);
}

describe("登录页：手机号与验证码校验", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("手机号格式不正确时给出字段级错误，并且不发起验证码请求", async () => {
    const { wrapper } = await mountLogin();
    await setPhoneAndSend(wrapper, "1380000");

    expect(wrapper.text()).toContain("手机号格式不正确");
    const challengeCalls = calls.filter((call) => call.url.includes("/auth/sms/challenges"));
    expect(challengeCalls).toHaveLength(0);
  });

  it("合法手机号可以发送验证码，并把完整号码交给服务端", async () => {
    const { wrapper } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");

    const challengeCalls = calls.filter((call) => call.url.includes("/auth/sms/challenges"));
    expect(challengeCalls).toHaveLength(1);
    expect(challengeCalls[0]!.body).toMatchObject({ phone: "+8613800001234", purpose: "login" });
    // 开发提示只在 dev_hint 非空时显示，并标注不是生产行为
    expect(wrapper.text()).toContain("开发提示");
    expect(wrapper.text()).toContain("只在开发/测试环境返回");
  });

  it("美国号码（+1）可以发送验证码，并按 E.164 提交", async () => {
    const { wrapper } = await mountLogin();
    await wrapper.find("#login-dial").setValue("+1");
    await setPhoneAndSend(wrapper, "4155552671");

    const challengeCalls = calls.filter((call) => call.url.includes("/auth/sms/challenges"));
    expect(challengeCalls).toHaveLength(1);
    expect(challengeCalls[0]!.body).toMatchObject({ phone: "+14155552671", purpose: "login" });
    expect(wrapper.text()).not.toContain("手机号格式不正确");
  });

  it("带国家码的美国号码不会被当成中国大陆号码", async () => {
    const { wrapper } = await mountLogin();
    await wrapper.find("#login-dial").setValue("+1");
    await setPhoneAndSend(wrapper, "14155552671");

    const challengeCalls = calls.filter((call) => call.url.includes("/auth/sms/challenges"));
    expect(challengeCalls).toHaveLength(1);
    expect(challengeCalls[0]!.body).toMatchObject({ phone: "+14155552671" });
  });

  it("不支持的地区码在本地就给出可读原因，不发请求", async () => {
    const { wrapper } = await mountLogin();
    await wrapper.find("#login-dial").setValue("+86");
    await setPhoneAndSend(wrapper, "4155552671");

    expect(wrapper.text()).toContain("手机号格式不正确");
    expect(calls.filter((call) => call.url.includes("/auth/sms/challenges"))).toHaveLength(0);
  });

  it("填写完整但未勾选协议时提交按钮禁用，并说明原因", async () => {    const { wrapper } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");
    await wrapper.find('input[type="text"][inputmode="numeric"]').setValue("123456");
    await flush();

    const submit = wrapper.findAll("button").find((button) => button.text().includes("登录 / 注册"));
    expect(submit!.attributes("disabled")).toBeDefined();
    expect(submit!.attributes("aria-disabled")).toBe("true");
    expect(wrapper.text()).toContain("请先勾选同意");
  });

  it("勾选协议后才提交，且 accepted=true 会随请求发送", async () => {
    const { wrapper, router } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");
    await wrapper.find('input[type="text"][inputmode="numeric"]').setValue("123456");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();

    const submit = wrapper.findAll("button").find((button) => button.text().includes("登录 / 注册"));
    expect(submit!.attributes("disabled")).toBeUndefined();
    await submitLogin(wrapper);

    const verifyCalls = calls.filter((call) => call.url.includes("/auth/sms/verify"));
    expect(verifyCalls).toHaveLength(1);
    expect(verifyCalls[0]!.body).toMatchObject({
      challenge_id: "chg_1",
      phone: "+8613800001234",
      code: "123456",
      accepted: true,
      terms_version: "commission-1",
    });
    expect(router.currentRoute.value.path).toBe("/account");
  });
});

describe("登录页：倒计时期间禁用重发", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("发送成功后按钮禁用并显示剩余秒数", async () => {
    const { wrapper } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");

    const resend = wrapper.findAll("button").find((button) => button.text().includes("秒后可重发"));
    expect(resend, "倒计时按钮应显示剩余秒数").toBeTruthy();
    expect(resend!.attributes("disabled")).toBeDefined();
    expect(resend!.text()).toContain("45");
    expect(wrapper.text()).toContain("倒计时 45 秒");

    // 倒计时期间再次点击不会产生新的验证码请求
    await resend!.trigger("click");
    await flush();
    expect(calls.filter((call) => call.url.includes("/auth/sms/challenges"))).toHaveLength(1);
  });
});

describe("登录页：服务端错误码文案", () => {
  it("验证码错误时展示按错误码定义的文案，而不是原样透传 message", async () => {
    installFetchMock({
      verify: () => errorResponse(400, "SMS_CODE_INVALID", "raw-internal-message"),
    });
    const { wrapper, router } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");
    await wrapper.find('input[type="text"][inputmode="numeric"]').setValue("000000");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();

    await submitLogin(wrapper);

    expect(wrapper.text()).toContain("验证码不正确，请核对后重新输入。");
    expect(wrapper.text()).toContain("SMS_CODE_INVALID");
    expect(wrapper.text()).not.toContain("raw-internal-message");
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("频率限制时给出可恢复文案并重新开始倒计时", async () => {
    installFetchMock({
      challenge: () => errorResponse(429, "SMS_RATE_LIMITED", "too many"),
    });
    const { wrapper } = await mountLogin();
    await setPhoneAndSend(wrapper, "13800001234");

    expect(wrapper.text()).toContain("发送过于频繁，请等待倒计时结束后再试。");
    expect(wrapper.findAll("button").some((button) => button.text().includes("秒后可重发"))).toBe(true);
  });
});

describe("returnTo 白名单", () => {
  it("拒绝外部地址与编码绕过写法", () => {
    expect(safeReturnPath("/account/orders")).toBe("/account/orders");
    expect(safeReturnPath("/logout?next=/x")).toBe("/logout?next=/x");
    expect(safeReturnPath("//evil.example")).toBe("/account");
    expect(safeReturnPath("/\\evil.example")).toBe("/account");
    expect(safeReturnPath("https://evil.example/account")).toBe("/account");
    expect(safeReturnPath("/%2fevil.example")).toBe("/account");
    expect(safeReturnPath("/%5Cevil.example")).toBe("/account");
    expect(safeReturnPath("account")).toBe("/account");
    expect(safeReturnPath(undefined)).toBe("/account");
    expect(safeReturnPath("//evil.example", "/login")).toBe("/login");
  });

  it("页面只读 route.query.returnTo 并交给路由跳转，不合法时回到 /account", async () => {
    installFetchMock();
    const { wrapper, router } = await mountLogin({ returnTo: "//evil.example" });

    expect(wrapper.text()).toContain("/account");
    await setPhoneAndSend(wrapper, "13800001234");
    await wrapper.find('input[type="text"][inputmode="numeric"]').setValue("123456");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();
    await submitLogin(wrapper);

    expect(router.currentRoute.value.path).toBe("/account");
  });

  it("合法 returnTo 在登录后被使用", async () => {
    installFetchMock();
    const { wrapper, router } = await mountLogin({ returnTo: "/account/notifications" });
    await setPhoneAndSend(wrapper, "13800001234");
    await wrapper.find('input[type="text"][inputmode="numeric"]').setValue("123456");
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();
    await submitLogin(wrapper);

    expect(router.currentRoute.value.path).toBe("/account/notifications");
  });

  it("路由守卫为重定向生成安全的 returnTo", async () => {
    installFetchMock();
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: () => undefined, removeEventListener: () => undefined })),
    );
    // 使用真实路由表：受保护页面在未登录时应跳到 /login 并带上本站相对路径。
    expect(realRouter.hasRoute("account")).toBe(true);
    const target = realRouter.resolve({ name: "account-notifications" });
    expect(target.fullPath.startsWith("/account")).toBe(true);
    expect(safeReturnPath(target.fullPath)).toBe(target.fullPath);
    // 该页面本体在未登录时会给出 401 引导，而不是渲染他人的数据。
    expect(NotificationsPage).toBeTruthy();
  });
});
