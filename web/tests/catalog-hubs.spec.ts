/**
 * 专题页测试（FR-025）：洗毛套餐与预约、道具/周边同源目录。
 *
 * 洗毛页只允许提交 kind=cleaning 的预约；日期窗、返还方式与备注必须原样
 * 交给服务端校验。这里通过真实 http 客户端 stub fetch，断言请求体而不是
 * 只断言「点了按钮」。
 */
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter, type Router } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MediaImage, Page, ProductSummary } from "@/modules/catalog/api/catalog";
import MerchPage from "@/modules/catalog/pages/MerchPage.vue";
import PropsPage from "@/modules/catalog/pages/PropsPage.vue";
import WashPage from "@/modules/catalog/pages/WashPage.vue";

vi.mock("@/modules/identity/stores/auth", () => ({
  useAuthStore: () => ({ isAuthenticated: true }),
}));

const IMAGE: MediaImage = {
  id: "img-wash",
  alt: "洗毛套餐示意图",
  width: 1200,
  height: 1200,
  src: "/assets/demo/wash.webp",
  thumb: "/assets/demo/wash-thumb.webp",
  srcset: [],
};

function makeProduct(overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    id: "prod-wash-head",
    slug: "demo-wash-head",
    title: "单头洗毛·基础护理（演示内容）",
    subtitle: "适合日常清洁",
    kind: "service",
    category: "service",
    service_kind: "cleaning",
    price_from_cents: 16800,
    currency: "CNY",
    availability: "available",
    requires_approval: true,
    images: [IMAGE],
    tags: [],
    ...overrides,
  };
}

interface Call {
  url: string;
  method: string;
  body: string | null;
}

function fakeResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

function installFetch(items: ProductSummary[]): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: unknown, init?: RequestInit) => {
      const url = typeof input === "string" ? input : String(input);
      calls.push({ url, method: init?.method ?? "GET", body: typeof init?.body === "string" ? init.body : null });
      if (url.includes("/session/csrf")) {
        return fakeResponse({ csrf_token: "csrf-test", expires_in: 1800 });
      }
      if (url.includes("/stock-applications")) {
        return fakeResponse({
          id: "application-wash-1",
          kind: "cleaning",
          status: "SUBMITTED",
          product_id: "prod-wash-head",
          product_title: "单头洗毛·基础护理（演示内容）",
          product_slug: "demo-wash-head",
          reason: "洗毛预约",
          measurement_note: null,
          preferred_date: "2026-11-06",
          return_method: "pickup",
          decision_reason: null,
          priority_rank: null,
          purchase_expires_at: null,
          created_at: "2026-11-04T00:00:00Z",
          decided_at: null,
        });
      }
      const page: Page<ProductSummary> = {
        items,
        next_cursor: null,
        has_more: false,
        total: items.length,
        page: 1,
        limit: 50,
      };
      return fakeResponse(page);
    }),
  );
  return { calls };
}

function createTestRouter(): Router {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: stub },
      { path: "/wash", component: stub },
      { path: "/props", component: stub },
      { path: "/merch", component: stub },
      { path: "/shop", component: stub },
      { path: "/shop/:slug", component: stub },
      { path: "/care", component: stub },
      { path: "/login", component: stub },
      { path: "/contact", component: stub },
      { path: "/account/applications", component: stub },
    ],
  });
}

async function mountAt(component: Parameters<typeof mount>[0], path: string): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createTestRouter();
  await router.push(path);
  await router.isReady();
  const pinia = createPinia();
  setActivePinia(pinia);
  const wrapper = mount(component, { global: { plugins: [pinia, router] } });
  await flushPromises();
  await flushPromises();
  return { wrapper, router };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("WashPage 洗毛预约", () => {
  it("只展示 cleaning 服务，并用清洗后的标题渲染套餐", async () => {
    installFetch([
      makeProduct(),
      makeProduct({ id: "prod-repair", slug: "demo-repair", title: "修补服务", service_kind: "repair" }),
      makeProduct({ id: "prod-wash-full", slug: "demo-wash-full", title: "全装洗毛（演示内容）", price_from_cents: 39800 }),
    ]);
    const { wrapper } = await mountAt(WashPage, "/wash");

    expect(wrapper.text()).toContain("单头洗毛·基础护理");
    expect(wrapper.text()).not.toContain("（演示内容）");
    expect(wrapper.text()).toContain("全装洗毛");
    expect(wrapper.text()).not.toContain("修补服务");
  });

  it("提交预约时带上期望日期、返还方式与 kind=cleaning", async () => {
    const { calls } = installFetch([makeProduct()]);
    const { wrapper } = await mountAt(WashPage, "/wash");

    const date = wrapper.find("#wash-date");
    const min = date.attributes("min") ?? "";
    expect(min).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    await date.setValue(min);
    await wrapper.find("#wash-return").setValue("ship");
    await wrapper.find("form").trigger("submit");
    await flushPromises();
    await flushPromises();

    const post = calls.find((call) => call.url.includes("/stock-applications") && call.method === "POST");
    expect(post).toBeTruthy();
    const body = JSON.parse(post?.body ?? "{}") as Record<string, unknown>;
    expect(body.kind).toBe("cleaning");
    expect(body.product_id).toBe("prod-wash-head");
    expect(body.preferred_date).toBe(min);
    expect(body.return_method).toBe("ship");
    expect(wrapper.text()).toContain("预约已提交");
  });

  it("没有洗毛套餐时给出空态，不渲染预约表单套餐", async () => {
    installFetch([]);
    const { wrapper } = await mountAt(WashPage, "/wash");
    expect(wrapper.text()).toContain("暂无");
    expect(wrapper.findAll("#wash-package option")).toHaveLength(0);
  });
});

describe("道具与周边专题页", () => {
  it("道具页请求 props 分类并渲染商品卡", async () => {
    const { calls } = installFetch([
      makeProduct({
        id: "prop-horn",
        slug: "demo-prop-glow-horn",
        title: "发光角饰·星轨",
        kind: "stock_goods",
        category: "props",
        service_kind: null,
        price_from_cents: 12800,
        requires_approval: false,
      }),
    ]);
    const { wrapper } = await mountAt(PropsPage, "/props");

    expect(calls.some((call) => call.url.includes("category=props"))).toBe(true);
    expect(wrapper.text()).toContain("发光角饰·星轨");
    expect(wrapper.find('a[href="/shop/demo-prop-glow-horn"]').exists()).toBe(true);
  });

  it("周边页请求 merch 分类，空结果给出空态", async () => {
    const { calls } = installFetch([]);
    const { wrapper } = await mountAt(MerchPage, "/merch");

    expect(calls.some((call) => call.url.includes("category=merch"))).toBe(true);
    expect(wrapper.text()).toContain("周边");
  });
});
