/**
 * 反馈页面的首响时效展示（R23 收尾 / C30：反馈 7 个工作日首响）。
 *
 * 背景：服务端已按客服日历算出承诺工作日数、首响截止时间与是否逾期（见
 * `server/tests/integration/test_support_sla.py` 的 4 项回归），但**页面怎么显示**此前
 * 只靠 e2e 兜底。本文件锁定三种状态的文案与终态处理：
 * 1. 尚未响应：显示承诺工作日数与本次截止时间；
 * 2. 已逾期：明确写出截止时间与「我们正在跟进」，不使用颜色以外的模糊表达；
 * 3. 已首次响应：显示实际首次响应时间；
 * 4. 终态（CLOSED/REJECTED/DUPLICATE）：写明不再计时，不显示假截止。
 */

import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

const apiMocks = vi.hoisted(() => ({
  fetchFeedbackList: vi.fn(),
  fetchFeedback: vi.fn(),
  postFeedbackMessage: vi.fn(),
  createFeedbackAppeal: vi.fn(),
}));

vi.mock("@/modules/feedback/api/feedback", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/modules/feedback/api/feedback")>();
  return {
    ...actual,
    fetchFeedbackList: apiMocks.fetchFeedbackList,
    fetchFeedback: apiMocks.fetchFeedback,
    postFeedbackMessage: apiMocks.postFeedbackMessage,
    createFeedbackAppeal: apiMocks.createFeedbackAppeal,
  };
});

vi.mock("@/modules/content/stores/site", () => ({
  useSiteStore: () => ({
    brand: {
      brand_name: "弈星纪元兽装工作室",
      legal_entity: "弈星纪元（上海）文化发展有限公司",
      domain: "starium.cn",
      support_email: "support@starium.cn",
      service_hours: "周一至周五 10:00–19:00（Asia/Shanghai）",
      response_sla: { inquiry_workdays: 3, feedback_workdays: 7 },
    },
    loyaltyRules: { points_name: "星绒币" },
    commercePolicy: { returns_reference: "参照平台规则", cross_border_note: "", invoice_enabled: true, shipping_template_note: "" },
    featureFlags: {},
    featureEnabled: () => false,
    loading: false,
    loaded: true,
    error: null,
    load: vi.fn(async () => undefined),
  }),
}));

vi.mock("@/modules/identity/stores/auth", () => ({
  useAuthStore: () => ({
    user: { id: "user_1", nickname: "星绒委托人", currency: "CNY" },
    isAuthenticated: true,
    loaded: true,
    displayName: "星绒委托人",
    currency: "CNY" as const,
    loadMe: vi.fn(async () => null),
  }),
}));

vi.mock("@/shared/lib/toast", () => ({
  useToast: () => ({ success: vi.fn(), info: vi.fn(), error: vi.fn(), dismiss: vi.fn(), items: { value: [] } }),
}));

import FeedbackDetailPage from "@/modules/feedback/pages/FeedbackDetailPage.vue";
import FeedbackListPage from "@/modules/feedback/pages/FeedbackListPage.vue";

const SLA_BASE = {
  sla_workdays: 7,
  response_due_at: "2026-01-14T02:00:00Z",
  first_responded_at: null as string | null,
  response_overdue: false,
};

function summary(overrides: Record<string, unknown> = {}) {
  return {
    id: "fb_1",
    category: "bug",
    title: "结算页金额与预期不一致",
    status: "SUBMITTED",
    reward_status: "PENDING_REVIEW",
    page_path: "/checkout",
    created_at: "2026-01-05T02:00:00Z",
    updated_at: "2026-01-05T02:00:00Z",
    duplicate_of_summary: null,
    ...SLA_BASE,
    ...overrides,
  };
}

function detail(overrides: Record<string, unknown> = {}) {
  return {
    ...summary(overrides),
    body: "购物车价格与结算页数量不同，刷新后恢复。",
    steps: null,
    asset_ids: [],
    messages: [],
    reward_reason: null,
    resolution_note: null,
    appeals: [],
  };
}

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/account", component: { template: "<div />" } },
      { path: "/account/feedback", component: { template: "<div />" } },
      { path: "/account/feedback/:id", component: { template: "<div />" } },
      { path: "/contact", component: { template: "<div />" } },
      { path: "/feedback/new", component: { template: "<div />" } },
    ],
  });
}

async function flush(times = 40): Promise<void> {
  for (let index = 0; index < times; index += 1) await Promise.resolve();
}

async function mountList(row: Record<string, unknown>): Promise<VueWrapper> {
  apiMocks.fetchFeedbackList.mockResolvedValue({ items: [row], total: 1, page: 1, limit: 20, has_more: false });
  const pinia = createPinia();
  const router = testRouter();
  await router.push("/account/feedback");
  await router.isReady();
  const wrapper = mount(FeedbackListPage, { global: { plugins: [pinia, router] } });
  await flush();
  return wrapper;
}

async function mountDetail(row: Record<string, unknown>): Promise<VueWrapper> {
  apiMocks.fetchFeedback.mockResolvedValue(row);
  const pinia = createPinia();
  const router = testRouter();
  await router.push("/account/feedback/fb_1");
  await router.isReady();
  const wrapper = mount(FeedbackDetailPage, { global: { plugins: [pinia, router] } });
  await flush();
  return wrapper;
}

describe("反馈首响时效展示", () => {
  beforeEach(() => {
    localStorage.clear();
    apiMocks.fetchFeedbackList.mockReset();
    apiMocks.fetchFeedback.mockReset();
  });

  it("尚未响应时显示承诺工作日数与本次截止时间", async () => {
    const wrapper = await mountList(summary());
    expect(wrapper.text()).toContain("首响承诺 7 个工作日");
    expect(wrapper.text()).toContain("2026/01/14");
    expect(wrapper.text()).not.toContain("逾期");
  });

  it("逾期时写出截止时间并说明正在跟进", async () => {
    const wrapper = await mountList(summary({ response_overdue: true }));
    expect(wrapper.text()).toContain("首响承诺 7 个工作日");
    expect(wrapper.text()).toContain("仍未响应");
    expect(wrapper.text()).toContain("正在跟进");
  });

  it("已首次响应时显示实际响应时间，而不是继续倒计时", async () => {
    const wrapper = await mountList(
      summary({ first_responded_at: "2026-01-09T02:00:00Z", response_overdue: false }),
    );
    expect(wrapper.text()).toContain("首次响应");
    expect(wrapper.text()).toContain("承诺 7 个工作日");
    expect(wrapper.text()).not.toContain("仍未响应");
  });

  it("详情页对终态反馈写明不再计时，不显示假截止", async () => {
    const wrapper = await mountDetail(detail({ status: "CLOSED", response_overdue: false }));
    expect(wrapper.text()).toContain("已有处理结论，首响时效不再计时");
    expect(wrapper.text()).not.toContain("本次截止");
    expect(wrapper.text()).not.toContain("仍未响应");
  });

  it("详情页在已逾期时给出催办入口说明", async () => {
    const wrapper = await mountDetail(detail({ response_overdue: true }));
    expect(wrapper.text()).toContain("仍未响应");
    expect(wrapper.text()).toContain("联系客服");
  });
});
