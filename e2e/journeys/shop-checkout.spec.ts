/**
 * 商城下单与支付（真实浏览器 + 真实后端 + 真实前端 + 沙盒渠道）。
 *
 * 覆盖用例：`文档/13_测试计划与验收用例.md`
 * - TC-O01 客户改价、负数量、失效地址、下架 SKU → 服务端拒绝或要求重新确认，订单金额正确
 * - TC-P01 正常全款/预付款/尾款与取消支付 → 只有真实成功款入账
 * - TC-P06 回调先于下单响应、成功后又收到旧失败状态 → 成功不可回退，金额只分配一次
 *
 * 依据文档：
 * - 06 API 接口契约（`PUT /cart/items/{skuId}`、`POST /checkout/preview`、`POST /orders`、
 *   `POST /installments/{id}/payment-attempts`；错误 envelope `{error:{code,message,fields,trace_id}}`）
 * - 08 商城订单支付与退款（金额由服务端裁定；成功只认已验签通知或主动查单；返回页不入账）
 * - 11 界面交互与素材规范（未确认状态必须写成「结果确认中」，不得渲染成已支付/已取消）
 * - 13 测试计划与验收用例（AGENTS.md 约束 11：不生成伪成功页）
 *
 * ⚠️ 已知前端缺陷（导致 `/checkout` 与 `/cart` 无法按设计工作，见本文件末尾的 fixme 用例）：
 * `web/src/modules/commerce/api/commerce.ts` 与后端契约不一致——
 * 1. `fetchAddresses()`（:570-571）按 `{items: Address[]}` 解析，而 `GET /api/v1/me/addresses`
 *    返回的是裸数组（`server/app/modules/identity/router.py:614` 的 `response_model=list[AddressResponse]`），
 *    `CheckoutPage.loadBase()`（CheckoutPage.vue:137-141）会在 `addressList.items.find(...)` 抛 TypeError；
 * 2. `AddressInput`（:561-568）提交 `region`，而服务端 `AddressInput`（identity/schemas.py:156-165，
 *    extra=forbid）只接受 `region_code/province/city`；实测 `POST /me/addresses` 返回 422。
 * 因此本文件用真实接口准备订单前置数据（与前端登录后的会话/cookie 完全一致），
 * 并把「结算页可提交」这一步单独标成 fixme，绝不把缺陷写成预期行为。
 */

import { expect, test, type Page } from "@playwright/test";

import {
  assertNoSensitiveLeak,
  collectPageErrors,
  loginViaSandboxApi,
  sessionReadHeaders,
  sessionWriteHeaders,
  type SandboxSession,
} from "../helpers";

function expectNoUnexpectedErrors(errors: string[], allowed: RegExp | null = null): void {
  const unexpected = allowed ? errors.filter((item) => !allowed.test(item)) : errors;
  expect(unexpected, `出现未预期的浏览器错误：\n${errors.join("\n")}`).toEqual([]);
}

interface PreparedOrder {
  orderId: string;
  orderNo: string;
  installmentId: string;
  totalCents: number;
  productTitle: string;
  skuId: string;
}

/**
 * 用真实接口准备一笔待付款订单：选可售 SKU → 建地址 → 加购 → 结算预览 → 创建订单。
 *
 * 每一步都是服务端裁定（08 文档：金额、库存与可售性不来自前端），
 * 走的是与浏览器同一个会话 cookie，因此后续 `page.goto('/pay/:id')` 即为已登录状态。
 */
async function preparePendingOrder(page: Page, session: SandboxSession): Promise<PreparedOrder> {
  const productsResponse = await page.request.get("/api/v1/products?limit=50", {
    headers: sessionReadHeaders(),
  });
  expect(productsResponse.ok(), await productsResponse.text()).toBeTruthy();
  const products = (await productsResponse.json()) as {
    items: Array<{
      slug: string;
      title: string;
      requires_approval: boolean;
      category?: string | null;
      skus: Array<{ id: string; in_stock: boolean }>;
    }>;
  };

  const product = products.items.find(
    (item) =>
      item.category !== "merch" &&
      !item.requires_approval &&
      item.skus.some((sku) => sku.in_stock),
  );
  test.skip(
    !product,
    "当前环境没有「无需审批且有可售 SKU」的商品；依赖 `cd server && uv run python -m app.cli seed-demo`。",
  );
  const sku = product?.skus.find((item) => item.in_stock);
  expect(sku, `商品 ${product?.title} 没有可售 SKU`).toBeTruthy();

  const addressResponse = await page.request.post("/api/v1/me/addresses", {
    headers: sessionWriteHeaders(session),
    data: {
      label: "E2E 收货地址",
      recipient: "端到端测试",
      phone: session.phone,
      region_code: "CN",
      province: "上海市",
      city: "徐汇区",
      detail: "端到端用例路 1 号",
      postal_code: "200030",
      is_default: true,
    },
  });
  expect(addressResponse.ok(), await addressResponse.text()).toBeTruthy();
  const address = (await addressResponse.json()) as { id: string };

  const headers = sessionWriteHeaders(session);
  const cartResponse = await page.request.put(`/api/v1/cart/items/${sku?.id}`, {
    headers,
    data: { quantity: 1 },
  });
  expect(cartResponse.ok(), await cartResponse.text()).toBeTruthy();

  const previewResponse = await page.request.post("/api/v1/checkout/preview", {
    headers,
    data: { items: [{ sku_id: sku?.id, quantity: 1 }], address_id: address.id, coupon_code: null },
  });
  expect(previewResponse.ok(), await previewResponse.text()).toBeTruthy();
  const preview = (await previewResponse.json()) as {
    checkout_token: string;
    total_cents: number;
    expires_at: string;
  };

  const orderResponse = await page.request.post("/api/v1/orders", {
    headers,
    data: {
      checkout_token: preview.checkout_token,
      address_id: address.id,
      terms_version: "shop-1",
      accepted: true,
    },
  });
  expect(orderResponse.ok(), await orderResponse.text()).toBeTruthy();
  const order = (await orderResponse.json()) as {
    order_id: string;
    order_no: string;
    installment_id: string;
    total_cents: number;
    status: string;
  };
  expect(order.status).toBe("PENDING_PAYMENT");
  expect(order.installment_id).toBeTruthy();

  return {
    orderId: order.order_id,
    orderNo: order.order_no,
    installmentId: order.installment_id,
    totalCents: order.total_cents,
    productTitle: product?.title ?? "",
    skuId: sku?.id ?? "",
  };
}

test.describe("TC-O01 加购、结算预览与创建订单（服务端裁定金额与库存）", () => {
  test("商品详情可以加入购物车，购物车里能看到该商品", async ({ page }) => {
    const errors = collectPageErrors(page);
    await loginViaSandboxApi(page);

    await page.goto("/shop");
    const card = page
      .locator('a[href^="/shop/"]:not([data-display-only="true"])')
      .filter({ hasNotText: "审批" })
      .first();
    await card.waitFor({ state: "visible" });
    const href = await card.getAttribute("href");
    const title = (
    await card.evaluate((element) => {
      const inside = element.querySelector("h1, h2, h3, h4");
      const outer = element.closest("h1, h2, h3, h4");
      return (inside ?? outer ?? element).textContent ?? "";
    })
  )?.trim();
    expect(href && title).toBeTruthy();

    await page.goto(href as string);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(title as string);
    // 商品详情在数据未就绪时渲染加载态（此时按钮虽已存在但不会发起请求）。
    // 先等加载态结束，避免整轮并行跑用例时点到「还没接管」的按钮，把竞态误判成产品缺陷。
    await expect(page.getByText("正在读取商品信息")).toHaveCount(0);
    // 会话是页面加载后异步恢复的：页头仍处于未登录态时点击加购会按未登录跳转（用例竞态）。
    // 「手机号登录」与「我的账户」是 v-if/v-else 互斥渲染，用它判断与视口无关。
    await expect(page.getByRole("link", { name: "手机号登录" })).toHaveCount(0);

    const addButton = page.getByRole("button", { name: "加入购物车" });
    test.skip((await addButton.count()) === 0, "该商品没有可售规格，无法验证加购路径");

    if (await addButton.isEnabled()) {
      // 加购是异步写请求：必须先等到服务端回执，否则立刻 goto 会中断在途请求，
      // 购物车自然读不到刚才的商品——那是用例竞态，不是产品坏了。
      // 直接等待 PUT 响应，比等提示文案更确定（提示可能被后续渲染顶掉）。
      const [addResponse] = await Promise.all([
        page.waitForResponse(
          (response) =>
            response.request().method() === "PUT" && response.url().includes("/api/v1/cart/items/"),
          { timeout: 20_000 },
        ),
        addButton.click(),
      ]);
      expect(addResponse.ok(), `加购请求失败：HTTP ${addResponse.status()}`).toBeTruthy();
      await page.goto("/cart");
      await expect(page.getByText("购物车是空的")).toHaveCount(0);
      await expect(page.getByText(title as string).first()).toBeVisible();
    } else {
      // 售罄时按钮必须写明原因（11 文档：禁用按钮旁说明原因），且不能加购成功。
      const reason = page.getByText(/已售罄|售罄|暂不可加入购物车/).first();
      await expect(reason).toBeVisible();
    }

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("服务端拒绝改价、负数量与未知 SKU，订单金额只由服务端给出", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);
    const order = await preparePendingOrder(page, session);

    // 1) 订单金额来自服务端预览，不是前端提交的。
    expect(order.totalCents).toBeGreaterThan(0);

    // 2) 负数量 / 0 数量被拒（CheckoutItemInput quantity ge=1）。
    const negative = await page.request.post("/api/v1/checkout/preview", {
      headers: sessionWriteHeaders(session),
      data: { items: [{ sku_id: order.skuId, quantity: 0 }], address_id: "adr_whatever" },
    });
    expect(negative.status()).toBeGreaterThanOrEqual(400);
    const negativePayload = (await negative.json()) as { error?: { code?: string } };
    expect(negativePayload.error?.code).toBe("VALIDATION_ERROR");

    // 3) 未知 SKU / 失效地址被拒，且不产生订单。
    const bogus = await page.request.post("/api/v1/checkout/preview", {
      headers: sessionWriteHeaders(session),
      data: { items: [{ sku_id: "sku_e2e_not_exist", quantity: 1 }], address_id: "adr_e2e_missing" },
    });
    expect(bogus.status(), await bogus.text()).toBeGreaterThanOrEqual(400);

    // 4) 客户端伪造金额：StrictDto 直接拒绝额外字段，金额不可作为可信输入（08 文档）。
    const tampered = await page.request.post("/api/v1/orders", {
      headers: sessionWriteHeaders(session),
      data: {
        checkout_token: "e2e-tampered-checkout-token",
        address_id: "adr_e2e_missing",
        terms_version: "shop-1",
        accepted: true,
        total_cents: 1,
      },
    });
    expect(tampered.status(), await tampered.text()).toBeGreaterThanOrEqual(400);
    const tamperedPayload = (await tampered.json()) as { error?: { code?: string } };
    expect(tamperedPayload.error?.code).toBe("VALIDATION_ERROR");

    expectNoUnexpectedErrors(errors);
  });

  test("结算页能把购物车交给服务端结算（依赖前端契约修复）", async ({ page }) => {
    test.fixme(
      true,
      "前端缺陷阻塞：`web/src/modules/commerce/api/commerce.ts:570-571` 的 fetchAddresses() 按 {items:[]} 解析，" +
        "而后端 `GET /api/v1/me/addresses` 返回裸数组（server/app/modules/identity/router.py:614），" +
        "CheckoutPage.loadBase()（CheckoutPage.vue:137-141）因此抛 TypeError 并恒定渲染「结算信息加载失败」；" +
        "同时 commerce.ts:561-568 的 AddressInput 提交 region，后端 identity/schemas.py:156-165 只接受 region_code/province/city，" +
        "实测 POST /me/addresses 返回 422。「加购 → 结算预览 → 提交订单」的 UI 路径在修复前无法验证，不能标记为通过。",
    );
  });
});

test.describe("TC-P01 / TC-P06 支付创建与结果核对", () => {
  test("支付返回页不把未确认状态渲染成已支付（访客）", async ({ page }) => {
    const errors = collectPageErrors(page);

    // 渠道返回时常见的参数一律不被采信（PaymentReturnPage.vue:24-32）。
    await page.goto("/payment/return?success=1&trade_status=SUCCESS&order=SO_E2E_FAKE");

    await expect(page.getByRole("heading", { level: 1, name: "支付结果核对" })).toBeVisible();
    await expect(page.getByText("请以订单详情里的支付记录为准")).toBeVisible();
    await expect(page.getByText(/只有服务端收到已验签的支付通知/)).toBeVisible();
    // 未登录时一律不展示订单或支付信息。
    await expect(page.getByText("登录后核对订单")).toBeVisible();
    await expect(page.getByRole("link", { name: "打开该订单" })).toHaveCount(0);

    // 关键：不能出现任何「成交」结论（页面正文里的免责说明不算）。
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/^\s*(付款成功|支付成功|已确认收款|已支付)\s*$/m);
    expect(body).not.toContain("SO_E2E_FAKE");

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("支付返回页只引导去订单详情核对，不展示金额或订单明细（已登录）", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);
    const order = await preparePendingOrder(page, session);

    await page.goto(`/payment/return?success=1&trade_status=SUCCESS&order=${order.orderId}`);

    await expect(page.getByRole("heading", { level: 1, name: "支付结果核对" })).toBeVisible();
    await expect(page.getByText("下一步：核对你的订单")).toBeVisible();
    await expect(page.getByRole("link", { name: "查看我的订单" })).toBeVisible();
    await expect(page.getByText("不生成“支付成功”结果页，也不展示支付渠道的原始报文。")).toBeVisible();
    await expect(page.getByText(/显示“结果确认中”表示服务端还没有拿到渠道结果/)).toBeVisible();

    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/^\s*(付款成功|支付成功|已确认收款|已支付)\s*$/m);
    // 本页不展示订单号与金额。
    expect(body).not.toContain(order.orderNo);

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("支付页发起支付后不出现伪造的成功态：未启用就说明未启用，未确认就写结果确认中", async ({ page }) => {
    const errors = collectPageErrors(page);
    const session = await loginViaSandboxApi(page);
    const order = await preparePendingOrder(page, session);

    await page.goto(`/pay/${order.installmentId}`);

    await expect(page.getByRole("heading", { level: 1, name: "完成付款" })).toBeVisible();
    await expect(page.getByText("付款成功只以服务端查单结果为准")).toBeVisible();
    // 尚未发起支付时状态是「尚未创建支付」（PayPage.vue:80）。
    await expect(page.getByText("尚未创建支付")).toBeVisible();

    const createButton = page.getByRole("button", { name: /发起支付|重新获取支付参数/ });
    await expect(createButton).toBeVisible();
    await createButton.click();

    // 沙盒渠道未开通时必须写明「未启用」；已开通时只能是「结果确认中 / 等待付款」这类未确认状态。
    await expect(
      page
        .getByText(
          /在线支付当前未启用|该支付方式当前不可用|结果尚未确认|结果确认中|已创建，等待付款|扫码支付|前往渠道完成付款|支付单已关闭|渠道返回失败/,
        )
        .first(),
    ).toBeVisible({ timeout: 30_000 });

    // TC-P01/P06 的核心：没有服务端验签通知或查单确认，就绝不能显示已收款。
    // exact 匹配：页面上的说明文案可能包含这些词（例如「以服务端查单确认后才显示已确认收款」），
    // 这里要断言的是**状态本身**没有出现，而不是说明里没提到这个词。
    await expect(page.getByText("已确认收款", { exact: true })).toHaveCount(0);
    await expect(page.getByText("服务端查单确认时间", { exact: true })).toHaveCount(0);
    const body = await page.locator("body").innerText();
    expect(body).not.toMatch(/^\s*(付款成功|支付成功|已确认收款|已支付)\s*$/m);
    // 金额由服务端给出，并且等于订单总额。
    expect(body).toContain((order.totalCents / 100).toFixed(2));

    await assertNoSensitiveLeak(page);
    expectNoUnexpectedErrors(errors);
  });

  test("支付成功只在服务端查单确认后成立（依赖沙盒渠道与回执）", async () => {
    test.fixme(
      true,
      "依赖沙盒支付渠道开通：本机实测 `POST /api/v1/installments/{id}/payment-attempts` 返回 " +
        "403 `FEATURE_DISABLED`（「该功能尚未开放。」），因此「创建支付尝试 → 沙盒回执 → 服务端查单确认 → 入账」" +
        "整条成功路径无法在当前环境构造。TC-P01 的「只有真实成功款入账」需要在开通沙盒渠道后补充，" +
        "TC-P06 的「回调先于下单响应 / 成功后又收到旧失败状态」还需要能主动触发 `POST /api/v1/payment-notifications/sandbox`。",
    );
  });
});
