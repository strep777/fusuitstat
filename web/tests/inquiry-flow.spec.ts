/**
 * 询价流程测试（FR-006 / 07 文档 / 11 文档）。
 *
 * 覆盖范围：
 * 1. 必填校验：未填写时不进入下一步，错误贴字段并给出可读原因；
 * 2. 设定图数量上限 3：本地校验、服务端 UPLOAD_LIMIT_EXCEEDED、扫描未完成时不允许提交；
 * 3. 草稿恢复：服务端草稿按 draft 编号还原到表单；
 * 4. 价格区间 min ≤ max：下限高于上限时阻止下一步；
 * 5. 提交后不可重复提交：提交按钮不再触发新的提交请求。
 */
import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

/* ------------------------------------------------------------------
   依赖 mock：只在 HTTP 边界上打桩，业务逻辑仍走真实实现。
   ------------------------------------------------------------------ */

const designSeriesPage = {
  items: [
    {
      id: "series_dragon",
      title: "逐星绒龙系列",
      kind: "series",
      slug: "star-dragon",
      locale: "zh-CN",
      summary: "",
      route: "/fursuits/star-dragon",
      cover_asset_id: null,
      cover: null,
      tags: [],
      species: null,
      work_type: null,
      published_at: null,
      updated_at: null,
      is_featured: false,
      seo: { title: "逐星绒龙系列", description: "", canonical: "" },
      body_html: "",
      gallery: [],
      spec: {
        code: "SD",
        base_price_cents: 1_280_000,
        currency: "CNY" as const,
        price_includes: [],
        price_excludes: [],
        lead_time_note: null,
        spec_schema: {},
        display_order: 1,
        is_active: true,
      },
    },
  ],
  next_cursor: null,
  has_more: false,
  total: 1,
  page: 1,
  limit: 20,
};

vi.mock("@/modules/content/api/content", () => ({
  fetchSeriesList: vi.fn(async () => designSeriesPage),
  fetchPublicSettings: vi.fn(async () => ({})),
  fetchHome: vi.fn(async () => ({})),
}));

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
    loyaltyRules: {
      points_name: "星绒币",
      denominator_yuan: 5,
      release_delay_days: 7,
      shipping_excluded: true,
      feedback_rewards: { minor: 5, normal: 20, major: 50 },
      feedback_daily_reward_limit: 1,
      redemption_enabled: false,
    },
    featureFlags: {},
    featureEnabled: () => false,
    loading: false,
    loaded: true,
    error: null,
    load: vi.fn(async () => undefined),
    setScheduleSummary: vi.fn(),
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
  useToast: () => ({
    success: vi.fn(),
    info: vi.fn(),
    error: vi.fn(),
    dismiss: vi.fn(),
    items: { value: [] },
  }),
}));

const apiMocks = vi.hoisted(() => ({
  fetchInquiry: vi.fn(),
  createInquiry: vi.fn(),
  updateInquiry: vi.fn(),
  submitInquiry: vi.fn(),
  createUpload: vi.fn(),
  uploadFileContent: vi.fn(),
  completeUpload: vi.fn(),
  fetchUpload: vi.fn(),
  runUploadFlow: vi.fn(),
}));

vi.mock("@/modules/commissions/api/commissions", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/modules/commissions/api/commissions")>();
  return {
    ...actual,
    fetchInquiry: apiMocks.fetchInquiry,
    createInquiry: apiMocks.createInquiry,
    updateInquiry: apiMocks.updateInquiry,
    submitInquiry: apiMocks.submitInquiry,
    createUpload: apiMocks.createUpload,
    uploadFileContent: apiMocks.uploadFileContent,
    completeUpload: apiMocks.completeUpload,
    fetchUpload: apiMocks.fetchUpload,
    runUploadFlow: apiMocks.runUploadFlow,
  };
});

import InquiryNewPage from "@/modules/commissions/pages/InquiryNewPage.vue";
import { ApiError } from "@/shared/lib/http";

/* ------------------------------------------------------------------
   测试夹具
   ------------------------------------------------------------------ */

const DRAFT_STORAGE_KEY = "starium:inquiry-draft-id";

function inquiryRecord(overrides: Record<string, unknown> = {}) {
  return {
    id: "inq_1",
    status: "DRAFT",
    version: 3,
    created_at: "2026-01-01T10:00:00+00:00",
    updated_at: "2026-01-01T10:05:00+00:00",
    submitted_at: null,
    needs_info_note: null,
    reject_reason: null,
    character_name: "",
    species: "",
    series_id: "",
    suit_type: "full",
    design_asset_ids: [],
    requirements: "",
    budget_min_cents: null,
    budget_max_cents: null,
    target_date: null,
    delivery_region: "",
    contact_preference: null,
    rights_declaration: false,
    guardian_required: false,
    ...overrides,
  };
}

interface InquiryVm {
  draft: Record<string, unknown>;
  step: number;
  errors: Record<string, string>;
  stepError: string | null;
  assets: Array<{ id: string; filename: string; state: string }>;
  inquiryId: string | null;
  submitted: unknown;
  submitError: string | null;
  submitErrorCode: string;
  saveState: string;
  saveError: string | null;
  budgetMinYuan: string;
  budgetMaxYuan: string;
  uploadError: string | null;
  inquiryVersion: number;
  saveDraft: () => Promise<boolean>;
  validateCurrentStep: () => boolean;
  goNext: () => void;
  goTo: (index: number) => void;
  onBudgetInput: (which: "min" | "max") => void;
  onFilesChosen: (event: Event) => Promise<void>;
  submitDisabledReason: string;
  uploadsReady: boolean;
  onSubmit: () => Promise<void>;
}

let router: Router;
let wrapper: VueWrapper | null = null;

function testRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div />" } },
      { path: "/commission/new", name: "inquiry-new", component: { template: "<div />" } },
      { path: "/account/quotes", name: "account-quotes", component: { template: "<div />" } },
      { path: "/login", name: "login", component: { template: "<div />" } },
    ],
  });
}

async function mountPage(): Promise<VueWrapper> {
  const pinia = createPinia();
  wrapper = mount(InquiryNewPage, {
    global: {
      plugins: [pinia, router],
      stubs: { RouterLink: { template: "<a><slot /></a>" } },
    },
    attachTo: document.body,
  });
  await flush();
  return wrapper;
}

/** 从「已通过审批的服务申请」进入询价页（C28：服务先申请再报价）。 */
async function mountPageWithApplication(applicationId: string): Promise<VueWrapper> {
  await router.push({ path: "/commission/new", query: { application_id: applicationId } });
  await router.isReady();
  return mountPage();
}

function vm(): InquiryVm {
  if (!wrapper) throw new Error("组件尚未挂载");
  return wrapper.vm as unknown as InquiryVm;
}

async function flush(times = 8): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
  }
  await vi.advanceTimersByTimeAsync(0);
}

function nextButton(): HTMLButtonElement | null {
  const holder = document.querySelector<HTMLElement>('[data-testid="next"]');
  return holder?.querySelector("button") ?? null;
}

function submitButton(): HTMLButtonElement | null {
  const holder = document.querySelector<HTMLElement>('[data-testid="submit"]');
  return holder?.querySelector("button") ?? null;
}

beforeEach(async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  localStorage.clear();
  sessionStorage.clear();
  document.body.innerHTML = "";

  apiMocks.fetchInquiry.mockReset();
  apiMocks.createInquiry.mockReset();
  apiMocks.updateInquiry.mockReset();
  apiMocks.submitInquiry.mockReset();
  apiMocks.createUpload.mockReset();
  apiMocks.uploadFileContent.mockReset();
  apiMocks.completeUpload.mockReset();
  apiMocks.fetchUpload.mockReset();
  apiMocks.runUploadFlow.mockReset();

  // 默认：保存草稿成功、上传立刻 ready、扫描完成。
  apiMocks.createInquiry.mockImplementation(async () => inquiryRecord());
  apiMocks.updateInquiry.mockImplementation(async () => inquiryRecord({ version: 4 }));
  apiMocks.createUpload.mockImplementation(async () => ({
    asset_id: "asset_1",
    object_key: "design/asset_1",
    visibility: "private",
    upload_url: "/api/v1/uploads/asset_1/content",
    expires_in: 600,
    max_bytes: 10 * 1024 * 1024,
  }));
  apiMocks.uploadFileContent.mockImplementation(async () => undefined);
  apiMocks.completeUpload.mockImplementation(async () => ({ id: "asset_1", scan_status: "ready" }));
  apiMocks.fetchUpload.mockImplementation(async () => ({ id: "asset_1", scan_status: "ready" }));
  apiMocks.runUploadFlow.mockImplementation(async (file: File) => ({
    asset_id: "asset_uploaded",
    filename: file.name,
    size: file.size,
    scan_status: "ready",
    reject_reason: null,
  }));

  router = testRouter();
  await router.push("/commission/new");
  await router.isReady();
});

afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  vi.useRealTimers();
  document.body.innerHTML = "";
});

/* ------------------------------------------------------------------
   1. 必填校验
   ------------------------------------------------------------------ */

describe("服务申请带来的询价（C28）", () => {
  it("从已通过审批的服务申请进入时，建单请求带上申请编号", async () => {
    await mountPageWithApplication("application_service_1");
    const page = vm();
    page.draft.character_name = "服务申请角色";
    page.draft.species = "狼";
    page.draft.series_id = "series_dragon";
    page.draft.rights_declaration = true;
    await flush();

    await page.saveDraft();
    await flush();

    expect(apiMocks.createInquiry).toHaveBeenCalledTimes(1);
    expect(apiMocks.createInquiry.mock.calls[0]?.[0]).toMatchObject({
      character_name: "服务申请角色",
      application_id: "application_service_1",
    });
  });

  it("普通进入（无申请编号）时不携带 application_id，避免误用他人审批", async () => {
    await mountPage();
    const page = vm();
    page.draft.character_name = "普通询价角色";
    page.draft.species = "猫";
    page.draft.series_id = "series_dragon";
    page.draft.rights_declaration = true;
    await flush();

    await page.saveDraft();
    await flush();

    expect(apiMocks.createInquiry.mock.calls[0]?.[0]).toMatchObject({ application_id: null });
  });
});

describe("询价必填校验", () => {
  it("未填写角色名称与系列时停留在第一步，并给出贴字段的错误", async () => {
    await mountPage();

    expect(vm().step).toBe(0);
    nextButton()?.click();
    await flush();

    expect(vm().step).toBe(0);
    expect(vm().errors.character_name).toBe("请填写角色名称。");
    expect(vm().errors.series_id).toBe("请选择要询价的系列；系列决定报价范围。");
    expect(document.body.textContent).toContain("请填写角色名称。");
    expect(document.body.textContent).toContain("请先修正本步中标出的问题，再进入下一步。");
  });

  it("设定图未上传时不能进入第二步", async () => {
    await mountPage();
    const page = vm();
    page.draft.character_name = "星绒";
    page.draft.species = "龙";
    page.draft.series_id = "series_dragon";
    page.draft.suit_type = "full";
    await flush();

    page.goNext();
    await flush();
    // 进入第二步（设定图与角色）
    expect(vm().step).toBe(1);

    nextButton()?.click();
    await flush();

    expect(vm().step).toBe(1);
    expect(vm().errors.design_asset_ids).toBe("至少上传 1 张设定图，最多 3 张。");
  });

  it("未勾选设定权属确认时不能提交", async () => {
    await mountPage();
    const page = vm();
    Object.assign(page.draft, {
      character_name: "星绒",
      species: "龙",
      series_id: "series_dragon",
      suit_type: "full",
      design_asset_ids: ["asset_1"],
      delivery_region: "中国大陆 / 上海",
      rights_declaration: false,
    });
    page.assets.push({ id: "asset_1", filename: "design.png", state: "ready" });
    page.goTo(4);
    await flush();

    expect(vm().step).toBe(4);
    await vm().onSubmit();
    await flush();

    expect(apiMocks.submitInquiry).not.toHaveBeenCalled();
    // 提交前会回到第一个有问题的步骤，并把错误写进 errors。
    expect(vm().step).toBe(4);
    expect(vm().errors.rights_declaration).toBe(
      "请确认你拥有或已获得该角色设定的使用授权，否则不能提交询价。",
    );
  });

  it("提交前会回到第一个有问题的步骤", async () => {
    await mountPage();
    const page = vm();
    // 只有第 2 步（设定图）缺失内容
    Object.assign(page.draft, {
      character_name: "星绒",
      species: "龙",
      series_id: "series_dragon",
      suit_type: "full",
      delivery_region: "中国大陆 / 上海",
      rights_declaration: true,
    });
    page.goTo(4);
    await flush();

    await vm().onSubmit();
    await flush();

    expect(vm().step).toBe(1);
    expect(vm().errors.design_asset_ids).toBe("至少上传 1 张设定图，最多 3 张。");
    expect(apiMocks.submitInquiry).not.toHaveBeenCalled();
  });
});

/* ------------------------------------------------------------------
   2. 设定图数量上限 3
   ------------------------------------------------------------------ */

describe("设定图数量上限", () => {
  it("选择超过 3 张时只处理前 3 张并提示上限", async () => {
    await mountPage();
    const page = vm();

    const files = [1, 2, 3, 4].map(
      (index) => new File([new Uint8Array(8)], `design-${index}.png`, { type: "image/png" }),
    );
    const input = document.createElement("input");
    Object.defineProperty(input, "files", { value: files, configurable: true });

    await page.onFilesChosen({ target: input } as unknown as Event);
    await flush();

    expect(apiMocks.runUploadFlow).toHaveBeenCalledTimes(3);
    expect(page.assets).toHaveLength(3);
    expect(page.uploadError).toBe("最多上传 3 张设定图，本次只处理前 3 张。");
  });

  it("已经有 3 张时再选文件只提示上限，不再上传", async () => {
    await mountPage();
    const page = vm();
    page.assets.push(
      { id: "asset_1", filename: "a.png", state: "ready" },
      { id: "asset_2", filename: "b.png", state: "ready" },
      { id: "asset_3", filename: "c.png", state: "ready" },
    );
    await flush();

    const input = document.createElement("input");
    Object.defineProperty(input, "files", {
      value: [new File([new Uint8Array(4)], "d.png", { type: "image/png" })],
      configurable: true,
    });
    await page.onFilesChosen({ target: input } as unknown as Event);
    await flush();

    expect(apiMocks.runUploadFlow).not.toHaveBeenCalled();
    expect(page.uploadError).toBe("最多上传 3 张设定图，请先移除多余的文件。");
  });

  it("单张超过 10MiB 或格式不支持时给出可读原因", async () => {
    await mountPage();
    const page = vm();

    // 每次选择文件都会重置错误文案，因此两类原因分别触发一次。
    const tooBig = new File([new Uint8Array(4)], "big.png", { type: "image/png" });
    Object.defineProperty(tooBig, "size", { value: 11 * 1024 * 1024 });
    const bigInput = document.createElement("input");
    Object.defineProperty(bigInput, "files", { value: [tooBig], configurable: true });
    await page.onFilesChosen({ target: bigInput } as unknown as Event);
    await flush();

    expect(apiMocks.runUploadFlow).not.toHaveBeenCalled();
    expect(page.uploadError).toContain("单张不能超过 10MiB");
    expect(page.assets).toHaveLength(0);

    const wrongType = new File([new Uint8Array(4)], "note.txt", { type: "text/plain" });
    const textInput = document.createElement("input");
    Object.defineProperty(textInput, "files", { value: [wrongType], configurable: true });
    await page.onFilesChosen({ target: textInput } as unknown as Event);
    await flush();

    expect(apiMocks.runUploadFlow).not.toHaveBeenCalled();
    expect(page.uploadError).toBe("note.txt：只支持 JPEG / PNG / WebP 格式。");
    expect(page.assets).toHaveLength(0);
  });

  it("服务端返回 UPLOAD_LIMIT_EXCEEDED 时按错误码展示原因", async () => {
    apiMocks.runUploadFlow.mockRejectedValueOnce(
      new ApiError(400, {
        code: "UPLOAD_LIMIT_EXCEEDED",
        message: "超出限制",
        fields: {},
        trace_id: "t",
      }),
    );
    await mountPage();
    const page = vm();

    const input = document.createElement("input");
    Object.defineProperty(input, "files", {
      value: [new File([new Uint8Array(4)], "a.png", { type: "image/png" })],
      configurable: true,
    });
    await page.onFilesChosen({ target: input } as unknown as Event);
    await flush();

    expect(page.uploadError).toBe("设定图数量或大小超出限制：1–3 张，单张不超过 10MiB。");
    expect(page.assets).toHaveLength(0);
  });

  it("扫描未完成（scan_status 不是 ready）时不允许提交", async () => {
    apiMocks.runUploadFlow.mockResolvedValueOnce({
      asset_id: "asset_scanning",
      filename: "a.png",
      size: 8,
      scan_status: "scanning",
      reject_reason: null,
    });
    await mountPage();
    const page = vm();

    const input = document.createElement("input");
    Object.defineProperty(input, "files", {
      value: [new File([new Uint8Array(4)], "a.png", { type: "image/png" })],
      configurable: true,
    });
    await page.onFilesChosen({ target: input } as unknown as Event);
    await flush();

    expect(page.assets[0]?.state).toBe("scanning");
    expect(page.uploadsReady).toBe(false);
    expect(page.submitDisabledReason).toBe("设定图还未通过安全检查。");
  });
});

/* ------------------------------------------------------------------
   3. 草稿恢复
   ------------------------------------------------------------------ */

describe("服务端草稿恢复", () => {
  it("按 draft 编号还原上次填写的草稿", async () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, "inq_restore");
    apiMocks.fetchInquiry.mockResolvedValueOnce(
      inquiryRecord({
        id: "inq_restore",
        version: 5,
        character_name: "旧草稿角色",
        species: "狼",
        series_id: "series_dragon",
        suit_type: "half",
        design_asset_ids: ["asset_existing"],
        requirements: "需要可拆卸尾巴",
        budget_min_cents: 800_000,
        budget_max_cents: 1_200_000,
        delivery_region: "中国大陆 / 北京",
        contact_preference: "qq",
        rights_declaration: true,
      }),
    );

    await mountPage();

    expect(apiMocks.fetchInquiry).toHaveBeenCalledWith("inq_restore");
    const page = vm();
    expect(page.draft.character_name).toBe("旧草稿角色");
    expect(page.draft.species).toBe("狼");
    expect(page.draft.suit_type).toBe("half");
    expect(page.draft.design_asset_ids).toEqual(["asset_existing"]);
    expect(page.draft.budget_max_cents).toBe(1_200_000);
    expect(page.draft.contact_preference).toBe("qq");
    expect(page.draft.rights_declaration).toBe(true);
    expect(page.assets[0]?.state).toBe("ready");
    expect(page.saveState).toBe("saved");
    // 表单里能看到恢复后的角色名称：输入框的回填值不在 textContent 里，直接读 DOM。
    const nameField = document.querySelector<HTMLInputElement>("#character_name");
    expect(nameField?.value).toBe("旧草稿角色");
    const speciesField = document.querySelector<HTMLInputElement>("#species");
    expect(speciesField?.value).toBe("狼");
  });

  it("草稿编号失效时给出可读提示并清空本地编号", async () => {
    localStorage.setItem(DRAFT_STORAGE_KEY, "inq_missing");
    apiMocks.fetchInquiry.mockRejectedValueOnce(
      new ApiError(404, { code: "RESOURCE_NOT_FOUND", message: "不存在", fields: {}, trace_id: "t" }),
    );

    await mountPage();

    expect(localStorage.getItem(DRAFT_STORAGE_KEY)).toBeNull();
    expect(document.body.textContent).toContain("没有恢复上次的草稿");
  });

  it("自动保存失败时保留本地输入并提示重试", async () => {
    apiMocks.updateInquiry.mockRejectedValueOnce(
      new ApiError(0, { code: "PROVIDER_UNAVAILABLE", message: "网络连接失败", fields: {}, trace_id: "" }),
    );
    await mountPage();
    const page = vm();
    page.inquiryId = "inq_1";
    // 建单与整体 PATCH 都要求权利声明（服务端 _check_rights），勾选后才会真正发出自动保存请求。
    page.draft.rights_declaration = true;
    page.draft.character_name = "网络故障时输入的角色";
    await flush();

    await vi.advanceTimersByTimeAsync(1500);
    await flush();

    expect(page.saveState).toBe("error");
    expect(page.draft.character_name).toBe("网络故障时输入的角色");
    expect(document.body.textContent).toContain("网络连接失败");
  });

  it("未勾选设定权属确认时不发自动保存请求，并说明内容只保留在本页", async () => {
    await mountPage();
    const page = vm();
    page.inquiryId = "inq_1";
    page.draft.character_name = "未勾选声明时的输入";
    await flush();

    await vi.advanceTimersByTimeAsync(1500);
    await flush();

    // 与其发一个注定 422 的请求再显示通用报错，不如不发并给出下一步动作。
    expect(apiMocks.updateInquiry).not.toHaveBeenCalled();
    expect(page.saveState).toBe("idle");
    expect(page.saveError ?? "").toContain("只保留在本页");
    expect(page.draft.character_name).toBe("未勾选声明时的输入");
  });
});

/* ------------------------------------------------------------------
   4. 价格区间 min ≤ max
   ------------------------------------------------------------------ */

describe("预算区间校验", () => {
  it("下限高于上限时不能继续", async () => {
    await mountPage();
    const page = vm();
    page.goTo(2);
    await flush();

    page.budgetMinYuan = "12000";
    page.budgetMaxYuan = "8000";
    page.onBudgetInput("min");
    page.onBudgetInput("max");
    await flush();

    expect(page.draft.budget_min_cents).toBe(1_200_000);
    expect(page.draft.budget_max_cents).toBe(800_000);

    nextButton()?.click();
    await flush();

    expect(vm().step).toBe(2);
    expect(vm().errors.budget_min_cents).toBe(
      "预算下限不能高于上限；如果还没有明确区间，可以留空。",
    );
  });

  it("区间合法时可以继续，且金额按整数分保存", async () => {
    await mountPage();
    const page = vm();
    Object.assign(page.draft, {
      character_name: "星绒",
      species: "龙",
      series_id: "series_dragon",
      suit_type: "full",
      design_asset_ids: ["asset_1"],
      delivery_region: "中国大陆 / 上海",
    });
    page.goTo(2);
    await flush();

    page.budgetMinYuan = "8000.50";
    page.budgetMaxYuan = "12000";
    page.onBudgetInput("min");
    page.onBudgetInput("max");
    await flush();

    expect(page.draft.budget_min_cents).toBe(800_050);
    expect(page.draft.budget_max_cents).toBe(1_200_000);

    nextButton()?.click();
    await flush();
    expect(vm().step).toBe(3);
  });

  it("金额格式不合法时提示小数位限制", async () => {
    await mountPage();
    const page = vm();
    page.goTo(2);
    await flush();

    page.budgetMinYuan = "1.234";
    page.onBudgetInput("min");
    await flush();

    expect(vm().errors.budget_min_cents).toBe("金额最多两位小数，例如 8000 或 8000.50。");
  });
});

/* ------------------------------------------------------------------
   5. 提交后不可重复提交
   ------------------------------------------------------------------ */

describe("提交与重复提交", () => {
  async function prepareCompleteDraft(): Promise<InquiryVm> {
    await mountPage();
    const page = vm();
    Object.assign(page.draft, {
      character_name: "星绒",
      species: "龙",
      series_id: "series_dragon",
      suit_type: "full",
      design_asset_ids: ["asset_1"],
      requirements: "需要可拆卸尾巴",
      budget_min_cents: 800_000,
      budget_max_cents: 1_200_000,
      delivery_region: "中国大陆 / 上海",
      contact_preference: "qq",
      rights_declaration: true,
    });
    page.assets.push({ id: "asset_1", filename: "design.png", state: "ready" });
    page.inquiryId = "inq_1";
    page.goTo(4);
    await flush();
    return page;
  }

  it("提交成功后显示结果，并且再次点击不会重复提交", async () => {
    const page = await prepareCompleteDraft();
    // 提交前会先保存草稿；服务端确认的最新版本（beforeEach 里 mock 返回 4）必须用于乐观锁。
    await page.saveDraft();
    expect(page.inquiryVersion).toBe(4);

    apiMocks.submitInquiry.mockResolvedValueOnce({
      inquiry_id: "inq_1",
      status: "SUBMITTED",
      version: 6,
      submitted_at: "2026-01-02T09:00:00+00:00",
    });
    apiMocks.fetchInquiry.mockResolvedValueOnce(
      inquiryRecord({
        status: "SUBMITTED",
        version: 6,
        submitted_at: "2026-01-02T09:00:00+00:00",
        character_name: "星绒",
        species: "龙",
        series_id: "series_dragon",
        design_asset_ids: ["asset_1"],
        rights_declaration: true,
      }),
    );

    await vm().onSubmit();
    await flush();

    expect(apiMocks.submitInquiry).toHaveBeenCalledTimes(1);
    // 乐观锁版本取服务端确认过的最新草稿版本，而不是本地猜测的版本
    expect(apiMocks.submitInquiry).toHaveBeenCalledWith("inq_1", 4, expect.any(String));
    expect(page.submitted).not.toBeNull();
    expect(document.querySelector('[data-testid="submitted"]')).not.toBeNull();
    expect(document.body.textContent).toContain("询价已提交，进入人工处理");
    // 结果页不再渲染表单里的提交按钮
    expect(submitButton()).toBeNull();

    await vm().onSubmit();
    await flush();
    expect(apiMocks.submitInquiry).toHaveBeenCalledTimes(1);
  });

  it("提交失败时保留内容并给出错误码，重试使用同一个幂等键", async () => {
    const page = await prepareCompleteDraft();
    expect(page.submitError).toBeNull();

    apiMocks.submitInquiry.mockRejectedValueOnce(
      new ApiError(409, { code: "STATE_CONFLICT", message: "状态冲突", fields: {}, trace_id: "t" }),
    );

    await vm().onSubmit();
    await flush();

    expect(page.submitErrorCode).toBe("STATE_CONFLICT");
    expect(page.submitError).toBe("当前状态不允许这个操作，请刷新查看最新状态。");
    expect(page.draft.character_name).toBe("星绒");
    expect(page.submitted).toBeNull();

    const firstKey = apiMocks.submitInquiry.mock.calls[0]?.[2];

    apiMocks.submitInquiry.mockResolvedValueOnce({
      inquiry_id: "inq_1",
      status: "SUBMITTED",
      version: 7,
      submitted_at: "2026-01-02T09:30:00+00:00",
    });
    apiMocks.fetchInquiry.mockResolvedValueOnce(
      inquiryRecord({ status: "SUBMITTED", version: 7, submitted_at: "2026-01-02T09:30:00+00:00" }),
    );

    await vm().onSubmit();
    await flush();

    expect(apiMocks.submitInquiry).toHaveBeenCalledTimes(2);
    // 幂等键在同一个提交流程内固定，重试不会创建第二条提交记录
    expect(apiMocks.submitInquiry.mock.calls[1]?.[2]).toBe(firstKey);
  });
});
