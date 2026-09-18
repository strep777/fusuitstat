/**
 * 合同签署状态展示（R18 / C07、C19）。
 *
 * 背景：服务端已实现签署生命周期（登记 → 发送 → 平台回调/查单 → 留证）与开工门禁，
 * 页面侧的约束是：
 * 1. **不能假装已发送**：未接入电子签渠道时如实说明"无法在线签署"，不显示假的"待签署"或"已发送"；
 * 2. **不能假装已签署**：只有服务端回 `SIGNED` 才显示签署完成；
 * 3. **监护人**在未成年委托里必须单独列出并显示各自状态；
 * 4. 「查询签署结果」只是向平台查单：它把服务端返回的状态展示出来，不产生本地"已签"结论。
 */

import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

const apiMocks = vi.hoisted(() => ({
  fetchInquiry: vi.fn(),
  fetchInquiryQuotes: vi.fn(),
  fetchInquiryContract: vi.fn(),
  refreshContract: vi.fn(),
}));

vi.mock("@/modules/commissions/api/commissions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/modules/commissions/api/commissions")>();
  return {
    ...actual,
    fetchInquiry: apiMocks.fetchInquiry,
    fetchInquiryQuotes: apiMocks.fetchInquiryQuotes,
    fetchInquiryContract: apiMocks.fetchInquiryContract,
    refreshContract: apiMocks.refreshContract,
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
    commissionPolicy: {
      prepayment_percent: 50,
      quote_valid_days: 7,
      slot_hold_minutes: 30,
      balance_due_days: 7,
      free_revision_count: 2,
      terms_version: "commission-1",
    },
    loyaltyRules: { points_name: "星绒币" },
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

import AccountQuoteDetailPage from "@/modules/commissions/pages/AccountQuoteDetailPage.vue";

function inquiry(overrides: Record<string, unknown> = {}) {
  return {
    id: "inq_1",
    status: "ACCEPTED",
    version: 3,
    character_name: "星轨测试兽",
    species: "狼",
    series_id: "series_1",
    suit_type: "full",
    design_asset_ids: [],
    requirements: "需要可拆卸尾巴。",
    budget_min_cents: 300000,
    budget_max_cents: 600000,
    target_date: null,
    delivery_region: "CN-31",
    contact_preference: null,
    rights_declaration: true,
    guardian_required: false,
    guardian_name: null,
    guardian_email_masked: null,
    guardian_relation: null,
    created_at: "2026-01-01T10:00:00+00:00",
    updated_at: "2026-01-05T10:00:00+00:00",
    submitted_at: "2026-01-02T10:00:00+00:00",
    needs_info_note: null,
    reject_reason: null,
    accepted_quote_id: "quote_1",
    project_id: "prj_1",
    sla_workdays: 3,
    response_due_at: "2026-01-07T02:00:00Z",
    first_responded_at: "2026-01-03T02:00:00Z",
    response_overdue: false,
    ...overrides,
  };
}

function contract(overrides: Record<string, unknown> = {}) {
  return {
    available: true,
    id: "env_1",
    quote_id: "quote_1",
    provider: "sandbox",
    status: "SENT",
    subject: "委托合同 · 报价版本 1",
    signers: [
      { role: "customer", name: "星绒委托人", relation: "", status: "PENDING", signed_at: null },
    ],
    guardian_required: false,
    awaiting_roles: ["customer"],
    sign_url: null,
    sent_at: "2026-01-05T02:00:00Z",
    expires_at: "2026-01-08T02:00:00Z",
    completed_at: null,
    decline_reason: null,
    failure_reason: null,
    provider_configured: true,
    esign_provider: "sandbox",
    ...overrides,
  };
}

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/account", component: { template: "<div />" } },
      { path: "/account/quotes", component: { template: "<div />" } },
      { path: "/account/quotes/:id", component: { template: "<div />" } },
      { path: "/account/orders/:id", component: { template: "<div />" } },
      { path: "/contact", component: { template: "<div />" } },
    ],
  });
}

async function flush(times = 40): Promise<void> {
  for (let index = 0; index < times; index += 1) await Promise.resolve();
}

async function mountPage(
  contractValue: Record<string, unknown>,
  detailOverrides: Record<string, unknown> = {},
): Promise<VueWrapper> {
  apiMocks.fetchInquiry.mockResolvedValue(inquiry(detailOverrides));
  apiMocks.fetchInquiryQuotes.mockResolvedValue([]);
  apiMocks.fetchInquiryContract.mockResolvedValue(contractValue);
  const pinia = createPinia();
  const router = testRouter();
  await router.push("/account/quotes/inq_1");
  await router.isReady();
  const wrapper = mount(AccountQuoteDetailPage, { global: { plugins: [pinia, router] } });
  await flush();
  return wrapper;
}

describe("账户内的合同签署状态", () => {
  beforeEach(() => {
    localStorage.clear();
    apiMocks.fetchInquiry.mockReset();
    apiMocks.fetchInquiryQuotes.mockReset();
    apiMocks.fetchInquiryContract.mockReset();
    apiMocks.refreshContract.mockReset();
  });

  it("未发起签署任务时不显示合同区块（不编造状态）", async () => {
    const wrapper = await mountPage({ available: false, reason: "NO_ENVELOPE" });
    expect(wrapper.text()).not.toContain("合同签署");
    expect(wrapper.text()).not.toContain("已签署完成");
  });

  it("渠道未接入时如实说明无法在线签署，且不显示「已发送/待签署」", async () => {
    const wrapper = await mountPage(
      contract({ status: "PENDING_SEND", provider_configured: false, esign_provider: "disabled", sent_at: null }),
    );
    expect(wrapper.text()).toContain("合同签署");
    expect(wrapper.text()).toContain("尚未接入电子签渠道");
    expect(wrapper.text()).not.toContain("签署链接已由工作室通过邮件发送");
    expect(wrapper.text()).not.toContain("已签署完成");
  });

  it("未成年委托列出监护人与各自状态", async () => {
    const wrapper = await mountPage(
      contract({
        status: "PARTIALLY_SIGNED",
        guardian_required: true,
        awaiting_roles: ["guardian"],
        signers: [
          { role: "customer", name: "星绒委托人", relation: "", status: "SIGNED", signed_at: "2026-01-06T02:00:00Z" },
          { role: "guardian", name: "监护人甲", relation: "母亲", status: "PENDING", signed_at: null },
        ],
      }),
      {
        guardian_required: true,
        guardian_name: "监护人甲",
        guardian_email_masked: "g***@example.test",
        guardian_relation: "母亲",
      },
    );
    expect(wrapper.text()).toContain("需监护人签署");
    expect(wrapper.text()).toContain("监护人：监护人甲");
    expect(wrapper.text()).toContain("母亲");
    expect(wrapper.text()).toContain("待签署");
    expect(wrapper.text()).toContain("部分签署完成");
  });

  it("已签署完成显示完成时间与留证说明", async () => {
    const wrapper = await mountPage(
      contract({
        status: "SIGNED",
        awaiting_roles: [],
        completed_at: "2026-01-07T02:00:00Z",
        signers: [
          { role: "customer", name: "星绒委托人", relation: "", status: "SIGNED", signed_at: "2026-01-07T02:00:00Z" },
        ],
      }),
    );
    expect(wrapper.text()).toContain("已签署完成");
    expect(wrapper.text()).toContain("签署完成时间");
    expect(wrapper.text()).toContain("留证");
  });

  it("拒签显示原因，并说明制作不会开始", async () => {
    const wrapper = await mountPage(
      contract({ status: "DECLINED", decline_reason: "客户暂时不做了" }),
    );
    expect(wrapper.text()).toContain("合同被拒签");
    expect(wrapper.text()).toContain("客户暂时不做了");
    expect(wrapper.text()).toContain("制作不会开始");
  });

  it("「查询签署结果」向服务端查单，按返回值展示（不本地推断已签）", async () => {
    const wrapper = await mountPage(contract());
    apiMocks.refreshContract.mockResolvedValue(
      contract({ status: "SIGNED", awaiting_roles: [], completed_at: "2026-01-07T02:00:00Z" }),
    );

    const button = wrapper.findAll("button").find((item) => item.text().includes("查询签署结果"));
    expect(button, "待签署时应提供查单入口").toBeTruthy();
    await button!.trigger("click");
    await flush();

    expect(apiMocks.refreshContract).toHaveBeenCalledWith("env_1");
    expect(wrapper.text()).toContain("已签署完成");
  });

  it("合同状态读取失败时单独说明，不影响询价与报价展示", async () => {
    const { ApiError } = await import("@/shared/lib/http");
    apiMocks.fetchInquiry.mockResolvedValue(inquiry());
    apiMocks.fetchInquiryQuotes.mockResolvedValue([]);
    apiMocks.fetchInquiryContract.mockRejectedValue(
      new ApiError(500, {
        code: "INTERNAL_ERROR",
        message: "加载失败",
        fields: {},
        trace_id: "t",
      }),
    );
    const pinia = createPinia();
    const router = testRouter();
    await router.push("/account/quotes/inq_1");
    await router.isReady();
    const wrapper = mount(AccountQuoteDetailPage, { global: { plugins: [pinia, router] } });
    await flush();

    expect(wrapper.text()).toContain("合同签署状态暂时无法加载");
    expect(wrapper.text()).toContain("星轨测试兽");
  });
});
