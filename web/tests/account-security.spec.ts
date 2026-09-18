/**
 * 账户安全页的「公开展示授权」区块（R22 / C18）。
 *
 * 覆盖：
 * - 只给当前真的会公开的条目「撤回公开展示」按钮，未公开/已撤回只显示状态；
 * - 撤回原因太短时贴字段报错且不发请求；
 * - 确认撤回后按契约提交，并重新拉取列表、展示「立即生效」的说明；
 * - 列表为空时是诚实空态（不编造可撤回项）。
 */
import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createRouter, createMemoryHistory, type Router } from "vue-router";

import AccountSecurityPage from "@/modules/identity/pages/AccountSecurityPage.vue";

interface FetchCall {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
}

let calls: FetchCall[] = [];
let authorizations: Array<Record<string, unknown>> = [];

function jsonResponse(payload: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: async () => JSON.stringify(payload),
    json: async () => payload,
  } as unknown as Response;
}

function installFetchMock(): void {
  calls = [];
  vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const method = (init?.method ?? "GET").toUpperCase();
    let body: Record<string, unknown> | null = null;
    if (typeof init?.body === "string") body = JSON.parse(init.body) as Record<string, unknown>;
    calls.push({ url, method, body });

    if (url.includes("/session/csrf")) return jsonResponse({ csrf_token: "csrf-test", expires_in: 1800 });
    if (url.includes("/me/display-authorizations/retract")) {
      return jsonResponse({
        resource_type: "work",
        resource_id: "content_1",
        display_authorized: false,
        already_retracted: false,
        prebuild_job_id: "job_1",
        effective: "immediate",
      });
    }
    if (url.includes("/me/display-authorizations")) {
      return jsonResponse({ items: authorizations, total: authorizations.length });
    }
    if (url.includes("/me/sessions")) {
      // 06 文档：会话列表返回数组本身，不是分页对象。
      return jsonResponse([]);
    }
    if (url.includes("/content/settings")) {
      return jsonResponse({ "commission.policy": { terms_version: "commission-1" } });
    }
    return jsonResponse({});
  });
}

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div />" } },
      { path: "/account", name: "account", component: { template: "<div />" } },
      { path: "/account/security", name: "account-security", component: { template: "<div />" } },
      { path: "/contact", name: "contact", component: { template: "<div />" } },
      { path: "/login", name: "login", component: { template: "<div />" } },
    ],
  });
}

async function flush(times = 60): Promise<void> {
  // 页面挂载后会串起「取 CSRF → 读配置 → 读会话 → 读公开展示授权」多条 Promise 链，
  // 中间还有 await 与 microtask 边界，因此同时推进 microtask 与一个 macrotask。
  for (let index = 0; index < times; index += 1) await Promise.resolve();
  await new Promise((resolve) => setTimeout(resolve, 0));
  for (let index = 0; index < times; index += 1) await Promise.resolve();
}

let wrapper: VueWrapper | null = null;

async function mountPage() {
  installFetchMock();
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = testRouter();
  await router.push("/account/security");
  await router.isReady();
  wrapper = mount(AccountSecurityPage, {
    global: { plugins: [pinia, router], stubs: { RouterLink: { template: "<a><slot /></a>" } } },
  });
  await flush();
  return wrapper;
}

function retractButton(text = "撤回公开展示") {
  return wrapper!.findAll("button").filter((button) => button.text().includes(text));
}

beforeEach(() => {
  authorizations = [];
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.unstubAllGlobals();
});

describe("账户安全页：公开展示授权", () => {
  it("只给公开中的条目撤回入口，未公开与已撤回只显示状态", async () => {
    authorizations = [
      {
        resource_type: "work",
        resource_id: "content_1",
        title: "霜龙全装",
        slug: "frost-dragon",
        route: "/works/frost-dragon",
        client_display_name: "授权客户",
        display_authorized: true,
        published: true,
        visible_now: true,
        state: "ACTIVE",
        granted_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-02T00:00:00+00:00",
      },
      {
        resource_type: "work",
        resource_id: "content_2",
        title: "未发布作品",
        slug: "draft-work",
        route: "/works/draft-work",
        client_display_name: null,
        display_authorized: true,
        published: false,
        visible_now: false,
        state: "PENDING",
        granted_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-01T00:00:00+00:00",
      },
      {
        resource_type: "work",
        resource_id: "content_3",
        title: "已撤回作品",
        slug: "retracted-work",
        route: "/works/retracted-work",
        client_display_name: null,
        display_authorized: false,
        published: true,
        visible_now: false,
        state: "RETRACTED",
        granted_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-03T00:00:00+00:00",
      },
    ];

    const page = await mountPage();
    expect(page.text()).toContain("公开展示授权");
    expect(page.text()).toContain("霜龙全装");
    expect(page.text()).toContain("当前公开中：作品页会展示它");
    expect(page.text()).toContain("尚未公开：发布后才会公开，撤回入口保留");
    expect(page.text()).toContain("已撤回：公开页面与直链都不再展示");
    expect(retractButton()).toHaveLength(1);
  });

  it("撤回原因太短时贴字段报错，并且不发请求", async () => {
    authorizations = [
      {
        resource_type: "media",
        resource_id: "asset_1",
        title: "棚拍原图.png",
        slug: null,
        route: null,
        client_display_name: null,
        display_authorized: true,
        published: true,
        visible_now: true,
        state: "ACTIVE",
        granted_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-01T00:00:00+00:00",
      },
    ];
    const page = await mountPage();

    await retractButton()[0]!.trigger("click");
    await flush();
    await page.get("#retract-reason-asset_1").setValue("x");
    const confirm = page.findAll("button").find((button) => button.text().includes("确认撤回"));
    await confirm!.trigger("click");
    await flush();

    expect(page.text()).toContain("请填写至少 2 个字的撤回原因");
    expect(calls.filter((call) => call.url.includes("/display-authorizations/retract"))).toHaveLength(0);
  });

  it("确认撤回后按契约提交并展示立即生效说明", async () => {
    authorizations = [
      {
        resource_type: "work",
        resource_id: "content_1",
        title: "霜龙全装",
        slug: "frost-dragon",
        route: "/works/frost-dragon",
        client_display_name: "授权客户",
        display_authorized: true,
        published: true,
        visible_now: true,
        state: "ACTIVE",
        granted_at: "2026-01-01T00:00:00+00:00",
        updated_at: "2026-01-02T00:00:00+00:00",
      },
    ];
    const page = await mountPage();

    await retractButton()[0]!.trigger("click");
    await flush();
    await page.get("#retract-reason-content_1").setValue("不希望公开姓名");
    await page.findAll("button").find((button) => button.text().includes("确认撤回"))!.trigger("click");
    await flush(40);

    const posted = calls.filter((call) => call.url.includes("/display-authorizations/retract"));
    expect(posted).toHaveLength(1);
    expect(posted[0]!.method).toBe("POST");
    expect(posted[0]!.body).toEqual({
      resource_type: "work",
      resource_id: "content_1",
      reason: "不希望公开姓名",
    });
    expect(page.text()).toContain("已撤回并立即生效");
    // 撤回后重新拉取列表
    expect(calls.filter((call) => call.url.endsWith("/me/display-authorizations")).length).toBeGreaterThan(1);
  });

  it("没有可撤回项时是诚实空态", async () => {
    const page = await mountPage();
    expect(page.text()).toContain("没有可撤回的公开展示");
    expect(retractButton()).toHaveLength(0);
  });
});
