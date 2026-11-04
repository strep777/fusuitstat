/**
 * 结算与支付流程测试（FR-011～FR-013 / 08 文档 / 06 文档「支付」）。
 *
 * 覆盖：
 * - 价格变化（PRICE_CHANGED）必须让用户看见具体变化后再次确认，不能静默下单；
 * - 库存不足（STOCK_NOT_ENOUGH）需要指出具体 SKU；
 * - 提交请求体只含 SKU/数量/地址/条款，不含单价、金额或积分；
 * - 重复点击「提交订单」只发一次请求，且整份提交复用同一个 Idempotency-Key；
 * - 支付结果未知不渲染为已取消，页面写明“以服务端查单为准”；
 * - 支付轮询退避序列：初始 2 秒，最大 15 秒。
 */
import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createRouter, createWebHistory, type Router } from "vue-router";

import CheckoutPage from "@/modules/commerce/pages/CheckoutPage.vue";
import PayPage from "@/modules/payments/pages/PayPage.vue";
import {
  PAYMENT_POLL_INITIAL_MS,
  PAYMENT_POLL_MAX_MS,
  nextPaymentPollDelay,
} from "@/modules/payments/api/payments";
import { useAuthStore } from "@/modules/identity/stores/auth";

interface FetchCall {
  url: string;
  method: string;
  body: Record<string, unknown> | null;
  headers: Record<string, string>;
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

function errorResponse(status: number, code: string, message: string, fields: Record<string, string> = {}): Response {
  return jsonResponse({ error: { code, message, fields, trace_id: "trace-test" } }, status);
}

function readHeaders(init?: RequestInit): Record<string, string> {
  const raw = init?.headers;
  if (!raw || Array.isArray(raw)) return {};
  return { ...(raw as Record<string, string>) };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

// 夹具必须镜像真实服务端契约（06 文档 / contracts/openapi.json）：
// 地址用 AddressResponse 字段，购物车用 CartDto/CartItemDto，预览用 CheckoutPreviewDto。
const ADDRESS = {
  id: "addr_1",
  label: "默认地址",
  recipient: "星绒委托人",
  phone_masked: "138****0000",
  region_code: "CN-31",
  province: "上海市",
  city: "徐汇区",
  detail: "示例路 1 号 101 室",
  postal_code: null,
  is_default: true,
  version: 1,
};

const CART = {
  id: "cart_1",
  items: [
    {
      sku_id: "sku_1",
      quantity: 1,
      sku_code: "SKU-1",
      label: "L 码",
      product_id: "prod_1",
      product_slug: "tail-1",
      product_title: "绒尾配件",
      product_kind: "stock_goods",
      unit_price_cents: 19900,
      currency: "CNY",
      max_per_order: 5,
      available: 5,
      sellable: true,
      requires_approval: false,
      cover_asset_id: null,
    },
  ],
  item_count: 1,
  goods_cents: 19900,
  currency: "CNY",
};

const PREVIEW_LINES = [
  {
    sku_id: "sku_1",
    product_id: "prod_1",
    product_slug: "tail-1",
    product_title: "绒尾配件",
    label: "L 码",
    quantity: 1,
    unit_price_cents: 19900,
    subtotal_cents: 19900,
    currency: "CNY",
    available: 5,
  },
];

const PREVIEW = {
  terms_version: "shop-2",
  checkout_token: "ct_1",
  expires_at: "2099-01-01T00:00:00+00:00",
  lines: PREVIEW_LINES,
  goods_cents: 19900,
  discount_cents: 0,
  shipping_cents: 0,
  total_cents: 19900,
  currency: "CNY",
  coupon_code: null,
  coupon_note: null,
  address_id: ADDRESS.id,
};

const AUTH_USER = {
  id: "user_1",
  nickname: "星绒委托人",
  phone_masked: "138****0000",
  locale: "zh-CN",
  currency: "CNY",
  region: "CN",
  status: "active",
  created_at: "2026-01-01T00:00:00+00:00",
};

const ORDER_RESPONSE = {
  order_id: "order_1",
  order_no: "SO20260101001",
  status: "PENDING_PAYMENT",
  installment_id: "inst_1",
  total_cents: 19900,
  currency: "CNY",
  expires_at: "2099-01-01T00:00:00+00:00",
};

const ORDER_DETAIL = {
  id: "order_1",
  order_no: "SO20260101001",
  status: "PENDING_PAYMENT",
  fulfillment_type: "stock_goods",
  total_cents: 19900,
  paid_cents: 0,
  refunded_cents: 0,
  currency: "CNY",
  created_at: "2026-01-01T00:00:00+00:00",
  expires_at: "2099-01-01T00:00:00+00:00",
  item_count: 1,
  items: [
    {
      id: "item_1",
      sku_id: "sku_1",
      product_slug: "tail-1",
      product_title: "绒尾配件",
      sku_name: "L 码",
      quantity: 1,
      unit_price_cents: 19900,
      line_total_cents: 19900,
      currency: "CNY",
      image: null,
      snapshot_note: null,
    },
  ],
  installments: [
    {
      id: "inst_1",
      kind: "full",
      title: "全款",
      amount_cents: 19900,
      currency: "CNY",
      status: "DUE",
      due_at: "2099-01-01T00:00:00+00:00",
      paid_cents: 0,
      active_payment_attempt_id: null,
    },
  ],
  payment_attempts: [],
  address: null,
  shipments: [],
  after_sales: [],
  invoices: [],
  terms_version: "commission-1",
  terms_accepted_at: "2026-01-01T00:00:00+00:00",
  cancel_reason: null,
  cancelled_at: null,
  commission_project_id: null,
  notes: null,
};

interface MockOptions {
  /** 创建订单（POST /orders）的响应 */
  createOrder?: () => Response | Promise<Response>;
  /** 支付尝试查询（GET /payment-attempts/:id）的响应 */
  paymentAttemptView?: () => Response;
  /** 创建支付尝试（POST /installments/:id/payment-attempts）的响应 */
  createAttempt?: () => Response;
  /** 站点公开配置（`/content/settings`）的额外键，例如 `loyalty.rules` 与功能开关 */
  settings?: Record<string, unknown>;
  /** 结算预览（POST /checkout/preview）的响应，可按请求体返回不同报价 */
  addresses?: (typeof ADDRESS)[];
  preview?: (body: Record<string, unknown> | null) => Response | Promise<Response>;
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
    calls.push({ url, method, body, headers: readHeaders(init) });

    if (url.includes("/session/csrf")) {
      return jsonResponse({ csrf_token: "csrf-test", expires_in: 1800 });
    }
    if (url.includes("/content/settings")) {
      return jsonResponse({
        "commission.policy": { terms_version: "commission-1" },
        ...(options.settings ?? {}),
      });
    }
    if (url.includes("/me/addresses")) {
      // 06 文档：地址列表返回数组本身，不是分页对象。
      return jsonResponse(options.addresses ?? [ADDRESS]);
    }
    if (url.endsWith("/cart") && method === "GET") {
      return jsonResponse(CART);
    }
    if (url.includes("/checkout/preview")) {
      return options.preview ? options.preview(body) : jsonResponse(PREVIEW);
    }
    if (url.includes("/points/redemption-quote")) {
      const amount = Number(new URL(url, "http://localhost").searchParams.get("amount_cents") ?? 0);
      const requested = Number(new URL(url, "http://localhost").searchParams.get("points") ?? 0);
      const enabled = options.settings?.["loyalty.rules"] !== undefined;
      if (!enabled) {
        return errorResponse(403, "FEATURE_DISABLED", "积分兑换尚未开放。");
      }
      const maxPoints = Math.min(3000, Math.floor((amount * 20) / 100));
      const applied = Math.min(requested, maxPoints);
      return jsonResponse({
        enabled: true,
        points_name: "星绒币",
        amount_cents: amount,
        available_points: 3000,
        max_points: maxPoints,
        max_cents: maxPoints,
        requested_points: requested,
        applied_points: applied,
        applied_cents: applied,
        capped: applied < requested,
        rounded_points: 0,
        min_points: 50,
        points_per_yuan: 100,
        max_ratio_percent: 20,
        expiry_days: 0,
        restore_on_refund: true,
        ratio_note: "兑换率与上限由运营配置。",
      });
    }
    if (url.endsWith("/orders") && method === "POST") {
      return options.createOrder ? options.createOrder() : jsonResponse(ORDER_RESPONSE, 201);
    }
    if (url.includes("/orders?")) {
      return jsonResponse({ items: [ORDER_DETAIL], total: 1, page: 1, limit: 20, has_more: false });
    }
    // 订单详情（支付页按 installment id 反查所属订单时用 GET /orders/:id）
    const orderDetail = /\/orders\/([^/?]+)$/.exec(url);
    if (orderDetail && method === "GET") {
      return jsonResponse({ ...ORDER_DETAIL, id: orderDetail[1] });
    }
    if (url.includes("/payment-attempts/")) {
      return options.paymentAttemptView
        ? options.paymentAttemptView()
        : jsonResponse({ id: "pa_1", status: "PENDING", order_id: "order_1", verified_at: null });
    }
    if (url.includes("/payment-attempts") && method === "POST") {
      return options.createAttempt
        ? options.createAttempt()
        : jsonResponse(
            {
              payment_attempt_id: "pa_1",
              status: "PENDING",
              action: { type: "qr", url: "https://pay.example/qr/abc" },
              expires_at: "2099-01-01T00:00:00+00:00",
            },
            201,
          );
    }
    return jsonResponse({});
  });
}

function testRouter(): Router {
  return createRouter({
    history: createWebHistory(),
    routes: [
      { path: "/", name: "home", component: { template: "<div>home</div>" } },
      { path: "/cart", name: "cart", component: { template: "<div>cart</div>" } },
      { path: "/checkout", name: "checkout", component: { template: "<div>checkout</div>" } },
      { path: "/shop", name: "shop", component: { template: "<div>shop</div>" } },
      { path: "/contact", name: "contact", component: { template: "<div>contact</div>" } },
      { path: "/login", name: "login", component: { template: "<div>login</div>" } },
      { path: "/account", name: "account", component: { template: "<div>account</div>" } },
      { path: "/account/orders", name: "account-orders", component: { template: "<div>orders</div>" } },
      {
        path: "/account/orders/:id",
        name: "account-order-detail",
        component: { template: "<div>order detail</div>" },
      },
      { path: "/account/after-sales", name: "account-after-sales", component: { template: "<div>after</div>" } },
      { path: "/pay/:id", name: "pay", component: { template: "<div>pay</div>" } },
    ],
  });
}

/**
 * 等待 watch/onMounted 里的 Promise 链结束（含 CSRF、配置、购物车、地址、预览、下单响应与
 * 跳转路由）。下单成功后的「响应解析 → toast → router.push」链路超过 30 个微任务，
 * 等待太短会在跳转发生前就断言路由。
 */
async function flush(times = 80): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
  }
}

/** 页面是否把状态渲染成了「已取消」（否定句里出现该词不算）。 */
function rendersCancelledStatus(wrapper: VueWrapper): boolean {
  return wrapper
    .findAll("*")
    .some((node) => ["已取消", "订单已取消"].includes(node.text().trim()));
}

function callsTo(fragment: string, method?: string): FetchCall[] {
  return calls.filter(
    (call) => call.url.includes(fragment) && (method === undefined || call.method === method),
  );
}

function findButton(wrapper: VueWrapper, text: string) {
  const button = wrapper.findAll("button").find((item) => item.text().includes(text));
  expect(button, `应存在按钮：${text}`).toBeTruthy();
  return button!;
}

async function mountCheckout(options: MockOptions = {}) {
  installFetchMock(options);
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.user = AUTH_USER;
  auth.loaded = true;

  const router = testRouter();
  await router.push("/checkout");
  await router.isReady();

  const wrapper = mount(CheckoutPage, { global: { plugins: [pinia, router] } });
  await flush();
  return { wrapper, router, auth };
}

async function mountPay(options: MockOptions = {}) {
  installFetchMock(options);
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.user = AUTH_USER;
  auth.loaded = true;

  const router = testRouter();
  await router.push("/pay/inst_1?order=order_1");
  await router.isReady();

  const wrapper = mount(PayPage, { global: { plugins: [pinia, router] } });
  await flush();
  return { wrapper, router };
}

describe("结算页：价格变化必须确认后才下单", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("快速切换地址时，较早请求的失败不能覆盖当前预览", async () => {
    const delayed = deferred<Response>();
    const { wrapper } = await mountCheckout({
      addresses: [ADDRESS, { ...ADDRESS, id: "addr_2", is_default: false }],
      preview: (body) => body?.address_id === "addr_2" ? delayed.promise : jsonResponse(PREVIEW),
    });
    expect(callsTo("/checkout/preview", "POST")).toHaveLength(1);
    const radios = wrapper.findAll('input[type="radio"]');
    await radios[1]!.setValue(true);
    await radios[0]!.setValue(true);
    await flush();
    delayed.resolve(errorResponse(409, "PRICE_CHANGED", "过期地址的预览"));
    await flush();
    expect(callsTo("/checkout/preview", "POST")).toHaveLength(3);
    expect(wrapper.text()).not.toContain("过期地址的预览");
    await wrapper.get('input[type="checkbox"][class*="accent-amber"]').setValue(true);
    expect(findButton(wrapper, "提交订单").attributes("disabled")).toBeUndefined();
  });

  it("未勾选条款时不能提交，并说明原因", async () => {
    const { wrapper } = await mountCheckout();
    const submit = findButton(wrapper, "提交订单");

    expect(submit.attributes("disabled")).toBeDefined();
    expect(submit.attributes("aria-disabled")).toBe("true");
    expect(wrapper.text()).toContain("请勾选并接受订单条款后再提交");
    expect(wrapper.text()).toContain("未勾选条款时不能提交订单");
  });

  it("PRICE_CHANGED 时展示具体变化、要求重新确认，并且提交体不含金额字段", async () => {
    let attempt = 0;
    const { wrapper, router } = await mountCheckout({
      createOrder: () => {
        attempt += 1;
        if (attempt === 1) {
          // 服务端重新校验后发现单价变化：返回新价格行，要求用户看见变化后再次确认
          return errorResponse(409, "PRICE_CHANGED", "价格已变化，请确认后重新提交。", {
            lines: JSON.stringify([{ ...PREVIEW_LINES[0], unit_price_cents: 21900, line_total_cents: 21900 }]),
          });
        }
        return jsonResponse(ORDER_RESPONSE, 201);
      },
    });

    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();
    expect(findButton(wrapper, "提交订单").attributes("disabled")).toBeUndefined();

    await findButton(wrapper, "提交订单").trigger("click");
    await flush();

    // 1. 用户能看见「价格已经变了」以及前后单价
    expect(wrapper.text()).toContain("价格已变化，请确认后再提交");
    expect(wrapper.text()).toContain("¥199.00");
    expect(wrapper.text()).toContain("¥219.00");
    // 2. 没有静默改价下单：订单没有创建，页面也没有跳转到支付页
    expect(router.currentRoute.value.path).toBe("/checkout");
    // 3. 必须重新勾选条款再次确认，此时提交被禁用并说明原因
    expect(findButton(wrapper, "提交订单").attributes("disabled")).toBeDefined();
    expect(wrapper.text()).toContain("请勾选并接受订单条款后再提交");
    // 4. 客户端只提交 checkout_token/address_id/terms_version/accepted 与积分「选择」，
    //    不提交单价、金额或抵扣结果（08/09 文档：金额由服务端裁定）
    const created = callsTo("/orders", "POST")[0]!;
    expect(created.body).toEqual({
      checkout_token: "ct_1",
      address_id: "addr_1",
      terms_version: "shop-2",
      accepted: true,
      redeem_points: 0,
    });
    expect(JSON.stringify(created.body)).not.toContain("cents");
    expect(created.body?.["points_discount_cents"]).toBeUndefined();

    // 5. 再次确认后下单成功
    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();
    await findButton(wrapper, "提交订单").trigger("click");
    await flush();

    expect(callsTo("/orders", "POST")).toHaveLength(2);
    expect(router.currentRoute.value.path).toBe("/pay/inst_1");
  });
});

describe("结算页：库存不足要指出具体 SKU", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("STOCK_NOT_ENOUGH 时列出 SKU 与服务端返回的字段，并说明未创建订单", async () => {
    const { wrapper, router } = await mountCheckout({
      createOrder: () =>
        errorResponse(409, "STOCK_NOT_ENOUGH", "库存不足，请调整数量后重试。", {
          items: "sku_1",
          available_quantity: "0",
        }),
    });

    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();
    await findButton(wrapper, "提交订单").trigger("click");
    await flush();

    expect(wrapper.text()).toContain("部分商品库存不足");
    expect(wrapper.text()).toContain("绒尾配件");
    expect(wrapper.text()).toContain("L 码");
    expect(wrapper.text()).toContain("未创建订单");
    expect(wrapper.text()).toContain("当前可售 5 件，购物车中为 1 件");
    expect(router.currentRoute.value.path).toBe("/checkout");

    // 数量需要重新确认，提交按钮保持禁用并给出原因
    expect(findButton(wrapper, "提交订单").attributes("disabled")).toBeDefined();
  });
});

describe("结算页：重复点击只发一次请求并复用幂等键", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("连点两次提交只发一个 POST /orders，且重试复用同一个 Idempotency-Key", async () => {
    const gate = deferred<Response>();
    let attempt = 0;
    const { wrapper } = await mountCheckout({
      createOrder: () => {
        attempt += 1;
        if (attempt === 1) return gate.promise;
        return jsonResponse(ORDER_RESPONSE, 201);
      },
    });

    await wrapper.find('input[type="checkbox"]').setValue(true);
    await flush();

    const submit = findButton(wrapper, "提交订单");
    await submit.trigger("click");
    await flush(4);

    // 请求在途时按钮进入 disabled/loading，第二次点击不会产生新请求
    expect(findButton(wrapper, "提交订单").attributes("disabled")).toBeDefined();
    await findButton(wrapper, "提交订单").trigger("click");
    await flush(4);
    expect(callsTo("/orders", "POST")).toHaveLength(1);

    gate.resolve(jsonResponse(ORDER_RESPONSE, 201));
    await flush();

    const created = callsTo("/orders", "POST");
    expect(created).toHaveLength(1);
    const firstKey = created[0]!.headers["Idempotency-Key"];
    expect(firstKey).toBeTruthy();
    // 同一份提交（含价格确认后的重试）必须复用同一个键，而不是每次点击换新键
    expect(new Set(created.map((call) => call.headers["Idempotency-Key"])).size).toBe(1);
    expect(wrapper.text()).toContain("幂等保证由服务端按幂等键完成");
  });
});

describe("支付页：结果未知不能渲染为已取消", () => {
  beforeEach(() => {
    installFetchMock();
  });

  it("查单返回 UNKNOWN 时显示结果确认中，并写明以服务端查单为准", async () => {
    const { wrapper } = await mountPay({
      paymentAttemptView: () =>
        jsonResponse({ id: "pa_1", status: "UNKNOWN", order_id: "order_1", verified_at: null }),
    });

    await findButton(wrapper, "发起支付").trigger("click");
    await flush();

    // 1. 页面先写明判定口径
    expect(wrapper.text()).toContain("付款成功只以服务端查单结果为准");
    expect(wrapper.text()).toContain("本页同样不凭支付返回页认定付款成功");
    // 2. 结果未知渲染为「结果确认中」
    expect(wrapper.text()).toContain("结果确认中");
    expect(wrapper.text()).toContain("结果尚未确认");
    // 3. 绝不能把超时或未知渲染成已取消（页面里的“不等于订单已取消”是否定说明，不算状态）
    expect(rendersCancelledStatus(wrapper)).toBe(false);
    // 4. 提供手动查单入口
    expect(findButton(wrapper, "立即查询付款结果")).toBeTruthy();
  });

  it("PAYMENT_UNKNOWN 错误同样按结果确认中处理，不当作失败", async () => {
    const { wrapper } = await mountPay({
      createAttempt: () => errorResponse(409, "PAYMENT_UNKNOWN", "渠道未返回明确结果。"),
    });

    await findButton(wrapper, "发起支付").trigger("click");
    await flush();

    expect(wrapper.text()).toContain("已标记为“结果确认中”");
    expect(wrapper.text()).toContain("请不要重复付款");
    expect(rendersCancelledStatus(wrapper)).toBe(false);
  });

  it("渠道未开通（PAYMENT_PROVIDER_DISABLED）时说明不可用，不生成伪成功页", async () => {
    const { wrapper } = await mountPay({
      createAttempt: () => errorResponse(409, "PAYMENT_PROVIDER_DISABLED", "微信支付当前未开通。"),
    });

    await findButton(wrapper, "发起支付").trigger("click");
    await flush();

    expect(wrapper.text()).toContain("该支付方式当前不可用");
    expect(wrapper.text()).toContain("微信支付当前未开通。");
    expect(wrapper.text()).toContain("当前不可用");
    expect(wrapper.text()).not.toContain("支付成功");
  });
});

describe("支付轮询退避序列", () => {
  it("初始 2 秒，逐步退避且不超过 15 秒", () => {
    expect(PAYMENT_POLL_INITIAL_MS).toBe(2000);
    expect(PAYMENT_POLL_MAX_MS).toBe(15000);

    let delay = PAYMENT_POLL_INITIAL_MS;
    const sequence: number[] = [delay];
    for (let index = 0; index < 12; index += 1) {
      delay = nextPaymentPollDelay(delay);
      sequence.push(delay);
    }

    expect(sequence[0]).toBe(2000);
    for (let index = 1; index < sequence.length; index += 1) {
      expect(sequence[index]!).toBeGreaterThanOrEqual(sequence[index - 1]!);
      expect(sequence[index]!).toBeLessThanOrEqual(PAYMENT_POLL_MAX_MS);
    }
    expect(sequence[sequence.length - 1]).toBe(PAYMENT_POLL_MAX_MS);
  });
});

/* ------------------------------------------------------------------
   星绒币抵扣（C15 / R19c）
   ------------------------------------------------------------------ */

const REDEMPTION_SETTINGS = {
  "loyalty.rules": {
    points_name: "星绒币",
    denominator_yuan: 5,
    release_delay_days: 7,
    shipping_excluded: true,
    feedback_rewards: { minor: 5, normal: 20, major: 50 },
    feedback_daily_reward_limit: 1,
    redemption_enabled: true,
    redemption_points_per_yuan: 100,
    redemption_min_points: 50,
    redemption_max_ratio_percent: 20,
    redemption_expiry_days: 0,
    redemption_restore_on_refund: true,
  },
  "platform.feature_flags": { points_redemption: true },
};

function previewWithPoints(body: Record<string, unknown> | null): Response {
  const requested = Number(body?.["redeem_points"] ?? 0);
  if (requested <= 0) {
    return jsonResponse({ ...PREVIEW, points_discount_cents: 0, points_redeemed: 0, points_quote: null });
  }
  // 与独立报价接口同一口径：可用 3000，金额 199.00 × 20% = 3980 分 → 上限 3000。
  const maxPoints = 3000;
  const applied = Math.min(requested, maxPoints);
  return jsonResponse({
    ...PREVIEW,
    total_cents: PREVIEW.goods_cents - applied,
    points_discount_cents: applied,
    points_redeemed: applied,
    points_quote: {
      enabled: true,
      points_name: "星绒币",
      amount_cents: PREVIEW.goods_cents,
      available_points: 3000,
      max_points: maxPoints,
      max_cents: maxPoints,
      requested_points: requested,
      applied_points: applied,
      applied_cents: applied,
      capped: applied < requested,
      rounded_points: 0,
      min_points: 50,
      points_per_yuan: 100,
      max_ratio_percent: 20,
      expiry_days: 0,
      restore_on_refund: true,
      ratio_note: "兑换率与上限由运营配置。",
    },
  });
}

describe("结算页的星绒币抵扣", () => {
  it("开关关闭时不出现抵扣入口，预览请求也不带兑换选择", async () => {
    const { wrapper } = await mountCheckout();

    expect(wrapper.text()).not.toContain("星绒币抵扣");
    expect(wrapper.find("#checkout-redeem-points").exists()).toBe(false);

    const previewCall = callsTo("/checkout/preview", "POST")[0];
    expect(previewCall).toBeTruthy();
    expect(previewCall!.body?.["redeem_points"]).toBe(0);
  });

  it("开启后按服务端裁定展示抵扣，并把同一用量提交给建单接口", async () => {
    const { wrapper } = await mountCheckout({
      settings: REDEMPTION_SETTINGS,
      preview: previewWithPoints,
    });

    expect(wrapper.text()).toContain("星绒币抵扣");
    expect(wrapper.text()).toContain("每 1 元需要 100 星绒币");
    expect(wrapper.text()).toContain("本单最多可用");
    expect(wrapper.text()).toContain("3,000"); // 服务端给出的 max_points（可用余额与比例上限取小）
    expect(wrapper.text()).toContain("¥30.00");

    await wrapper.get("#checkout-redeem-points").setValue("5000");
    await findButton(wrapper, "使用星绒币").trigger("click");
    await flush();

    const previewCall = callsTo("/checkout/preview", "POST").at(-1);
    expect(previewCall!.body?.["redeem_points"]).toBe(5000);
    // 服务端把 5000 截断到上限 3000，页面展示裁定结果而不是本地计算值
    expect(wrapper.text()).toContain("请求数量超过上限或余额");
    expect(wrapper.text()).toContain("已按 3,000 星绒币");
    expect(wrapper.text()).toContain("星绒币抵扣（3,000 星绒币）");
    expect(wrapper.text()).toContain("-¥30.00");

    // 勾选条款后提交：请求体只带选择，且与预览一致
    const checkbox = wrapper.findAll('input[type="checkbox"]').at(-1)!;
    await checkbox.setValue(true);
    await flush();
    await findButton(wrapper, "提交订单").trigger("click");
    await flush();

    const orderCall = callsTo("/orders", "POST")[0];
    expect(orderCall).toBeTruthy();
    expect(orderCall!.body?.["redeem_points"]).toBe(3000);
    expect(orderCall!.body?.["total_cents"]).toBeUndefined();
  });

  it("低于最低使用数量时禁用提交并说明原因，不发请求", async () => {
    const { wrapper } = await mountCheckout({
      settings: REDEMPTION_SETTINGS,
      preview: previewWithPoints,
    });

    await wrapper.get("#checkout-redeem-points").setValue("20");
    await flush();

    expect(wrapper.text()).toContain("单笔至少使用 50 星绒币。");
    const before = callsTo("/checkout/preview", "POST").length;
    await findButton(wrapper, "使用星绒币").trigger("click");
    await flush();
    expect(callsTo("/checkout/preview", "POST").length).toBe(before);
  });
});

/* ------------------------------------------------------------------
   跨境配送与关税预估（C02/C24、C17；N07 首版比例待运营配置）
   ------------------------------------------------------------------ */

const CROSS_BORDER_NOTE = "跨境订单的关税与进口税费由清关时按目的地法规确定，本站不预估、不代收。";

describe("结算页的跨境关税预估", () => {
  it("境内地址不出现跨境提示，也不显示 TAX 估算", async () => {
    const { wrapper } = await mountCheckout({
      preview: () =>
        jsonResponse({
          ...PREVIEW,
          cross_border: false,
          duty_estimate_cents: null,
          duty_percent: 0,
          duty_note: "",
          estimate_available: false,
        }),
    });

    expect(wrapper.text()).not.toContain("跨境配送与关税");
    expect(wrapper.text()).not.toContain("关税与进口税费");
  });

  it("未配置比例时不给金额，写明以清关为准且不计入应付合计", async () => {
    const { wrapper } = await mountCheckout({
      preview: () =>
        jsonResponse({
          ...PREVIEW,
          cross_border: true,
          duty_estimate_cents: null,
          duty_percent: 0,
          duty_note: CROSS_BORDER_NOTE,
          estimate_available: false,
        }),
    });

    expect(wrapper.text()).toContain("跨境配送与关税");
    expect(wrapper.text()).toContain("该收货地址在中国大陆以外");
    expect(wrapper.text()).toContain("暂无预估金额");
    expect(wrapper.text()).toContain("以清关时海关核定为准");
    expect(wrapper.text()).toContain("不计入上方应付合计");
    expect(wrapper.text()).toContain(CROSS_BORDER_NOTE);
    // 不能把「没有预估」渲染成 0 元关税
    expect(wrapper.text()).not.toContain("按 0% 预估");
  });

  it("配置比例后展示预估金额，但应付合计仍不含预估", async () => {
    const { wrapper } = await mountCheckout({
      preview: () =>
        jsonResponse({
          ...PREVIEW,
          cross_border: true,
          duty_estimate_cents: 3980,
          duty_percent: 10,
          duty_note: CROSS_BORDER_NOTE,
          estimate_available: true,
        }),
    });

    expect(wrapper.text()).toContain("关税与进口税费预估");
    expect(wrapper.text()).toContain("¥39.80");
    expect(wrapper.text()).toContain("按 10% 预估");
    expect(wrapper.text()).toContain("预估金额不计入上方应付合计");
    // 合计依旧等于服务端给出的 total_cents（199.00），没有把预估加进去
    expect(wrapper.text()).toContain("¥199.00");
    expect(wrapper.text()).not.toContain("¥238.80");
  });
});
