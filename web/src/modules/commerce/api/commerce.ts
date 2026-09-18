/**
 * 商城模块 API 客户端：购物车、结算、订单、售后与收货地址。
 *
 * 规则来源：`文档/08_商城订单支付与退款.md` 与 `文档/06_API接口契约.md`。
 * 约束：
 * - 金额一律为整数分，币种由服务端显式返回；前端只展示，不参与计算权威金额。
 * - 客户端只提交 SKU、数量、地址 ID、条款版本与接受标记（06 文档：金额不再作为可信输入接收）。
 * - 写操作全部携带幂等键；同一份提交必须复用同一个键（08 文档：同键重试返回同资源）。
 * - 错误分支只看 `ApiError.code`，不对 message 做逻辑判断。
 */

import { api, newIdempotencyKey, type RequestOptions } from "@/shared/lib/http";

export type CurrencyCode = "CNY" | "USD";

/** 履约状态（08 文档「分离状态」表）。退款另有汇总标志，不与此枚举混用。 */
export type OrderStatus =
  | "PENDING_PAYMENT"
  | "ACTIVE"
  | "FULFILLING"
  | "SHIPPED"
  | "COMPLETED"
  | "CANCELLED";

/** 应付项状态：现货一个全款项；委托有预付款/尾款/已确认加项。 */
export type InstallmentStatus = "DUE" | "PAYING" | "PAID" | "CLOSING" | "CLOSED";

/** 支付尝试状态；UNKNOWN 必须查单，成功记录不改回失败。 */
export type PaymentAttemptStatus =
  | "CREATED"
  | "PENDING"
  | "UNKNOWN"
  | "SUCCEEDED"
  | "CLOSED"
  | "FAILED";

/** 成功款分配到应付项，或判为多付/待人工核对。 */
export type PaymentAllocationStatus = "APPLIED" | "OVERPAYMENT" | "QUARANTINED";

/** 售后流程状态，不等于退款渠道结果。 */
export type AfterSaleStatus =
  | "OPEN"
  | "NEEDS_INFO"
  | "APPROVED"
  | "RETURNING"
  | "RESOLVED"
  | "REJECTED";

/** 退款状态：失败或未知不能直接说钱已退（08 文档）。 */
export type RefundStatus =
  | "REQUESTED"
  | "APPROVED"
  | "PROCESSING"
  | "UNKNOWN"
  | "SUCCEEDED"
  | "FAILED"
  | "REJECTED";

export interface Money {
  cents: number;
  currency: CurrencyCode;
}

/** 订单/购物车行的可售状态；展示用，权威判定在服务端。 */
export type LineAvailability = "available" | "sold_out" | "insufficient" | "unavailable";

export interface CommerceImage {
  id: string;
  alt: string;
  width: number;
  height: number;
  src: string;
  thumb: string | null;
}

export interface CartLine {
  sku_id: string;
  product_id: string;
  product_slug: string;
  product_title: string;
  sku_name: string;
  /** 服务端权威单价；购物车价格只作展示（08 文档）。 */
  unit_price_cents: number;
  quantity: number;
  line_total_cents: number;
  currency: CurrencyCode;
  /** 可售上限；为 null 表示服务端未返回上限。 */
  available_quantity: number | null;
  availability: LineAvailability;
  availability_note: string | null;
  /** 履约类型：现货与定制不混车。 */
  fulfillment_type: string;
  image: CommerceImage | null;
  added_at: string | null;
}

export interface CartSummary {
  /** 购物车金额摘要只作展示，结算以 /checkout/preview 为准。 */
  goods_cents: number;
  discount_cents: number;
  shipping_cents: number;
  total_cents: number;
  currency: CurrencyCode;
}

export interface CartResponse {
  items: CartLine[];
  summary: CartSummary;
  /** 登录后合并本地车的服务端结果；未发生合并时为 null。 */
  merge: {
    merged_items: number;
    adjusted_items: number;
    removed_items: number;
    note: string;
  } | null;
}

/**
 * 服务端购物车 DTO（与后端 `CartItemDto` / `CartDto` 一致）。
 *
 * 后端只给权威事实（可售数量、是否可售、单价），界面需要的「可售状态文案、
 * 行小计、金额摘要」在客户端按同一份事实推导，避免前后端各写一套字段名。
 */
interface ServerCartItem {
  sku_id: string;
  quantity: number;
  sku_code: string | null;
  label: string | null;
  product_id: string | null;
  product_slug: string | null;
  product_title: string | null;
  product_kind: string | null;
  unit_price_cents: number;
  currency: string | null;
  max_per_order: number;
  available: number;
  sellable: boolean;
  requires_approval: boolean;
  cover_asset_id: string | null;
}

interface ServerCart {
  id: string | null;
  items: ServerCartItem[];
  item_count: number;
  goods_cents: number;
  currency: string | null;
}

function cartLineOf(item: ServerCartItem): CartLine {
  const currency = (item.currency ?? "CNY") as CurrencyCode;
  let availability: LineAvailability = "available";
  let note: string | null = null;
  if (!item.sellable) {
    availability = "unavailable";
    note = "该规格已下架，请从购物车移除后重新选择。";
  } else if (item.available <= 0) {
    availability = "sold_out";
    note = "暂时无货；补货后可再次下单。";
  } else if (item.available < item.quantity) {
    availability = "insufficient";
    note = `当前最多可买 ${item.available} 件。`;
  }
  return {
    sku_id: item.sku_id,
    product_id: item.product_id ?? "",
    product_slug: item.product_slug ?? "",
    product_title: item.product_title ?? "商品",
    sku_name: item.label ?? item.sku_code ?? "",
    unit_price_cents: item.unit_price_cents,
    quantity: item.quantity,
    line_total_cents: item.unit_price_cents * item.quantity,
    currency,
    available_quantity: item.available,
    availability,
    availability_note: note,
    fulfillment_type: item.product_kind ?? "stock_goods",
    // 购物车图需要单独的素材接口；这里不假装有图，界面按无图渲染。
    image: null,
    added_at: null,
  };
}

function cartResponseOf(cart: ServerCart): CartResponse {
  const items = cart.items.map(cartLineOf);
  const currency = (cart.currency ?? "CNY") as CurrencyCode;
  return {
    items,
    summary: {
      // 购物车只展示商品金额；优惠与运费在 /checkout/preview 由服务端裁定。
      goods_cents: cart.goods_cents,
      discount_cents: 0,
      shipping_cents: 0,
      total_cents: cart.goods_cents,
      currency,
    },
    merge: null,
  };
}

export async function fetchCart(options: RequestOptions = {}): Promise<CartResponse> {
  return cartResponseOf(await api.get<ServerCart>("/cart", options));
}

/** PUT 为绝对数量（06 文档），不是增量。 */
export async function setCartItemQuantity(
  skuId: string,
  quantity: number,
): Promise<CartResponse> {
  return cartResponseOf(
    await api.put<ServerCart>(`/cart/items/${encodeURIComponent(skuId)}`, { quantity }),
  );
}

export async function removeCartItem(skuId: string): Promise<CartResponse> {
  return api.delete<CartResponse>(`/cart/items/${encodeURIComponent(skuId)}`);
}

/** 结算预览行：同时作为「上一份报价」用于价格变更对比。 */
export interface CheckoutLine {
  sku_id: string;
  product_id: string;
  product_slug: string;
  product_title: string;
  sku_name: string;
  quantity: number;
  unit_price_cents: number;
  line_total_cents: number;
  currency: CurrencyCode;
  availability: LineAvailability;
  availability_note: string | null;
  image: CommerceImage | null;
}

export interface ShippingOption {
  code: string;
  name: string;
  fee_cents: number;
  currency: CurrencyCode;
  note: string | null;
}

export interface PointsQuote {
  enabled: boolean;
  points_name: string;
  amount_cents: number;
  available_points: number;
  max_points: number;
  max_cents: number;
  requested_points: number;
  applied_points: number;
  applied_cents: number;
  capped: boolean;
  rounded_points: number;
  min_points: number;
  points_per_yuan: number;
  max_ratio_percent: number;
  expiry_days: number;
  restore_on_refund: boolean;
  ratio_note: string;
}

export interface CheckoutPreview {
  /** 一次性令牌；创建订单时原样回传，不做本地校验。 */
  checkout_token: string;
  expires_at: string;
  lines: CheckoutLine[];
  goods_cents: number;
  discount_cents: number;
  shipping_cents: number;
  total_cents: number;
  currency: CurrencyCode;
  shipping_option: ShippingOption | null;
  /** 服务端可选返回的已选优惠；未启用时为 null。 */
  coupon_code: string | null;
  coupon_note: string | null;
  /** 积分抵扣（C15）：金额与用量都由服务端裁定；未使用时为 0 / null。 */
  points_discount_cents: number;
  points_redeemed: number;
  points_quote: PointsQuote | null;
  address_id: string;
}

export interface CheckoutPreviewInput {
  items: Array<{ sku_id: string; quantity: number }>;
  address_id: string;
  coupon_code?: string | null;
  /** 想使用的星绒币数量（选择，不是金额）；上限与折算由服务端给出。 */
  redeem_points?: number;
}

/** 服务端结算行 DTO（与后端 `CheckoutLineDto` 一致）。 */
interface ServerCheckoutLine {
  sku_id: string;
  product_id: string | null;
  product_slug: string | null;
  product_title: string | null;
  label: string | null;
  quantity: number;
  unit_price_cents: number;
  subtotal_cents: number;
  currency: string;
  available: number;
}

/** 服务端结算预览 DTO（与后端 `CheckoutPreviewDto` 一致）。 */
interface ServerCheckoutPreview {
  checkout_token: string;
  expires_at: string | null;
  lines: ServerCheckoutLine[];
  goods_cents: number;
  discount_cents: number;
  shipping_cents: number;
  total_cents: number;
  currency: string;
  address_id: string;
  coupon_code: string | null;
  coupon_note: string | null;
  points_discount_cents?: number;
  points_redeemed?: number;
  points_quote?: PointsQuote | null;
}

function checkoutPreviewOf(preview: ServerCheckoutPreview): CheckoutPreview {
  const currency = (preview.currency ?? "CNY") as CurrencyCode;
  const lines: CheckoutLine[] = preview.lines.map((line) => ({
    sku_id: line.sku_id,
    product_id: line.product_id ?? "",
    product_slug: line.product_slug ?? "",
    product_title: line.product_title ?? "商品",
    sku_name: line.label ?? "",
    quantity: line.quantity,
    unit_price_cents: line.unit_price_cents,
    line_total_cents: line.subtotal_cents,
    currency: (line.currency ?? currency) as CurrencyCode,
    availability: line.available > 0 ? "available" : "sold_out",
    availability_note: line.available > 0 ? null : "暂时无货；补货后可再次下单。",
    // 结算行不返回图片；避免前端凭编号拼直链（10 文档）。
    image: null,
  }));
  return {
    checkout_token: preview.checkout_token,
    expires_at: preview.expires_at ?? "",
    lines,
    goods_cents: preview.goods_cents,
    discount_cents: preview.discount_cents,
    shipping_cents: preview.shipping_cents,
    total_cents: preview.total_cents,
    currency,
    // 配送方式由服务端金额裁定；界面只展示费用，不提供未开通的选项。
    shipping_option: null,
    coupon_code: preview.coupon_code,
    coupon_note: preview.coupon_note,
    points_discount_cents: preview.points_discount_cents ?? 0,
    points_redeemed: preview.points_redeemed ?? 0,
    points_quote: preview.points_quote ?? null,
    address_id: preview.address_id,
  };
}

export async function previewCheckout(input: CheckoutPreviewInput): Promise<CheckoutPreview> {
  return checkoutPreviewOf(
    await api.post<ServerCheckoutPreview>("/checkout/preview", {
      items: input.items.map((item) => ({ sku_id: item.sku_id, quantity: item.quantity })),
      address_id: input.address_id,
      coupon_code: input.coupon_code ?? null,
      redeem_points: input.redeem_points ?? 0,
    }),
  );
}

export interface CreateOrderInput {
  checkout_token: string;
  address_id: string;
  terms_version: string;
  accepted: boolean;
  /** 想使用的星绒币数量；必须与预览时一致，否则服务端返回 PRICE_CHANGED。 */
  redeem_points?: number;
  /** 幂等键由调用方在首次点击时生成并在整份提交中复用。 */
  idempotencyKey: string;
}

export interface CreateOrderResult {
  order_id: string;
  order_no: string;
  status: "PENDING_PAYMENT";
  installment_id: string;
  total_cents: number;
  currency: CurrencyCode;
  expires_at: string;
  points_discount_cents: number;
  points_redeemed: number;
}

export async function createOrder(input: CreateOrderInput): Promise<CreateOrderResult> {
  const result = await api.post<Partial<CreateOrderResult> & CreateOrderResult>(
    "/orders",
    {
      checkout_token: input.checkout_token,
      address_id: input.address_id,
      terms_version: input.terms_version,
      accepted: input.accepted,
      redeem_points: input.redeem_points ?? 0,
    },
    { idempotencyKey: input.idempotencyKey },
  );
  return {
    ...result,
    points_discount_cents: result.points_discount_cents ?? 0,
    points_redeemed: result.points_redeemed ?? 0,
  };
}

export interface OrderItemSnapshot {
  id: string;
  sku_id: string;
  product_slug: string;
  product_title: string;
  sku_name: string;
  quantity: number;
  unit_price_cents: number;
  line_total_cents: number;
  currency: CurrencyCode;
  image: CommerceImage | null;
  /** 下单时的快照说明（规格、材质、赠品等），不是当前商品页内容。 */
  snapshot_note: string | null;
}

export interface OrderInstallment {
  id: string;
  kind: string;
  title: string;
  amount_cents: number;
  currency: CurrencyCode;
  status: InstallmentStatus;
  due_at: string | null;
  paid_cents: number;
  /** 该应付项的当前活动支付尝试；没有时为 null。 */
  active_payment_attempt_id: string | null;
}

export interface OrderPaymentAttempt {
  id: string;
  provider: string;
  provider_label: string;
  scene: string | null;
  status: PaymentAttemptStatus;
  amount_cents: number;
  currency: CurrencyCode;
  created_at: string;
  verified_at: string | null;
  allocation_status: PaymentAllocationStatus | null;
  note: string | null;
}

export interface OrderShipment {
  id: string;
  carrier: string;
  tracking_no: string;
  status: string;
  shipped_at: string | null;
  delivered_at: string | null;
  note: string | null;
}

export interface OrderAfterSaleSummary {
  id: string;
  status: AfterSaleStatus;
  reason: string;
  created_at: string;
}

export interface OrderInvoice {
  id: string;
  status: string;
  title: string;
  tax_no_masked: string | null;
  amount_cents: number;
  currency: CurrencyCode;
  created_at: string;
  note: string | null;
}

export interface OrderAddressSnapshot {
  recipient: string;
  phone_masked: string;
  region: string;
  detail: string;
}

export interface OrderSummary {
  id: string;
  order_no: string;
  status: OrderStatus;
  fulfillment_type: string;
  total_cents: number;
  paid_cents: number;
  refunded_cents: number;
  currency: CurrencyCode;
  created_at: string;
  /** 待付款到期时间；其他状态为 null。 */
  expires_at: string | null;
  item_count: number;
  items: OrderItemSnapshot[];
  refund_summary: {
    status: RefundStatus;
    refunded_cents: number;
    currency: CurrencyCode;
  } | null;
}

export interface OrderDetail extends OrderSummary {
  items: OrderItemSnapshot[];
  installments: OrderInstallment[];
  payment_attempts: OrderPaymentAttempt[];
  address: OrderAddressSnapshot | null;
  shipments: OrderShipment[];
  after_sales: OrderAfterSaleSummary[];
  invoices: OrderInvoice[];
  /** 已接受的条款版本与时间；不接受时条款不可回填修改。 */
  terms_version: string | null;
  terms_accepted_at: string | null;
  cancel_reason: string | null;
  cancelled_at: string | null;
  /** 合同/制作信息入口；现货为 null。 */
  commission_project_id: string | null;
  notes: string | null;
}

export interface OrderListQuery {
  status?: OrderStatus | "ALL";
  page?: number;
  limit?: number;
}

export interface OrderPage {
  items: OrderSummary[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export async function fetchOrders(query: OrderListQuery = {}): Promise<OrderPage> {
  return api.get<OrderPage>("/orders", {
    query: {
      status: query.status && query.status !== "ALL" ? query.status : undefined,
      page: query.page,
      limit: query.limit,
    },
  });
}

export async function fetchOrder(orderId: string): Promise<OrderDetail> {
  return api.get<OrderDetail>(`/orders/${encodeURIComponent(orderId)}`);
}

/** 取消订单：状态与原因由服务端校验并记录。 */
export async function cancelOrder(orderId: string, reason?: string): Promise<OrderDetail> {
  return api.post<OrderDetail>(
    `/orders/${encodeURIComponent(orderId)}/cancel`,
    { reason: reason ?? null },
    { idempotencyKey: newIdempotencyKey() },
  );
}

export async function confirmOrderReceipt(orderId: string): Promise<OrderDetail> {
  return api.post<OrderDetail>(
    `/orders/${encodeURIComponent(orderId)}/confirm-receipt`,
    {},
    { idempotencyKey: newIdempotencyKey() },
  );
}

export interface AfterSaleAttachment {
  id: string;
  /** 私密附件地址由服务端短期签发；前端不外链。 */
  url: string;
  alt: string;
  width: number;
  height: number;
}

export interface CreateAfterSaleInput {
  orderId: string;
  reason: string;
  description: string;
  assetIds: string[];
  idempotencyKey: string;
}

export async function createAfterSale(
  input: CreateAfterSaleInput,
): Promise<{ after_sale_id: string; status: AfterSaleStatus; created_at: string }> {
  return api.post<{ after_sale_id: string; status: AfterSaleStatus; created_at: string }>(
    `/orders/${encodeURIComponent(input.orderId)}/after-sales`,
    {
      reason: input.reason,
      description: input.description,
      asset_ids: input.assetIds,
    },
    { idempotencyKey: input.idempotencyKey },
  );
}

export interface CreateInvoiceInput {
  orderId: string;
  title: string;
  taxNo: string;
  email: string;
  note: string;
  idempotencyKey: string;
}

export async function createInvoice(
  input: CreateInvoiceInput,
): Promise<{ invoice_id: string; status: string; created_at: string }> {
  return api.post<{ invoice_id: string; status: string; created_at: string }>(
    `/orders/${encodeURIComponent(input.orderId)}/invoices`,
    {
      title: input.title,
      tax_no: input.taxNo,
      email: input.email,
      note: input.note,
    },
    { idempotencyKey: input.idempotencyKey },
  );
}

export interface AfterSaleMessage {
  id: string;
  author: "customer" | "staff";
  author_label: string;
  body: string;
  created_at: string;
  attachments: AfterSaleAttachment[];
}

export interface RefundRecord {
  id: string;
  status: RefundStatus;
  amount_cents: number;
  currency: CurrencyCode;
  requested_at: string;
  updated_at: string | null;
  /** 渠道原路退回；失败或未知时的说明由服务端给出。 */
  note: string | null;
  out_refund_no_masked: string | null;
}

export interface AfterSaleReturnShipment {
  carrier: string;
  tracking_no: string;
  shipped_at: string | null;
  note: string | null;
}

export interface AfterSaleSummary {
  id: string;
  order_id: string;
  order_no: string;
  status: AfterSaleStatus;
  reason: string;
  /** 现货或定制：两类售后条件分别呈现。 */
  fulfillment_type: string;
  created_at: string;
  updated_at: string | null;
  refund_status: RefundStatus | null;
  item: OrderItemSnapshot | null;
}

export interface AfterSaleDetail extends AfterSaleSummary {
  description: string;
  attachments: AfterSaleAttachment[];
  messages: AfterSaleMessage[];
  refunds: RefundRecord[];
  return_shipment: AfterSaleReturnShipment | null;
  /** 需要客户补充的信息（NEEDS_INFO 时由客服填写）。 */
  needs_info: string | null;
  /** 服务端依据配置给出的该类商品售后条件说明。 */
  policy_note: string | null;
}

export interface AfterSalePage {
  items: AfterSaleSummary[];
  total: number;
  page: number;
  limit: number;
  has_more: boolean;
}

export async function fetchAfterSales(
  query: { status?: AfterSaleStatus | "ALL"; page?: number; limit?: number } = {},
): Promise<AfterSalePage> {
  return api.get<AfterSalePage>("/after-sales", {
    query: {
      status: query.status && query.status !== "ALL" ? query.status : undefined,
      page: query.page,
      limit: query.limit,
    },
  });
}

export async function fetchAfterSale(afterSaleId: string): Promise<AfterSaleDetail> {
  return api.get<AfterSaleDetail>(`/after-sales/${encodeURIComponent(afterSaleId)}`);
}

export async function postAfterSaleMessage(
  afterSaleId: string,
  body: string,
  assetIds: string[] = [],
): Promise<AfterSaleMessage> {
  return api.post<AfterSaleMessage>(
    `/after-sales/${encodeURIComponent(afterSaleId)}/messages`,
    { body, asset_ids: assetIds },
    { idempotencyKey: newIdempotencyKey() },
  );
}

export interface ReturnShipmentInput {
  carrier: string;
  trackingNo: string;
  note: string;
}

export async function submitReturnShipment(
  afterSaleId: string,
  input: ReturnShipmentInput,
): Promise<AfterSaleDetail> {
  return api.post<AfterSaleDetail>(
    `/after-sales/${encodeURIComponent(afterSaleId)}/return-shipment`,
    {
      carrier: input.carrier,
      tracking_no: input.trackingNo,
      note: input.note,
    },
    { idempotencyKey: newIdempotencyKey() },
  );
}

export interface Address {
  id: string;
  label: string;
  recipient: string;
  /** 服务端只返回掩码手机号；明文需要重新录入才能提交修改。 */
  phone_masked: string;
  /** ISO 3166-2 区域码，例如 CN-31；跨境配送与税费按它裁定。 */
  region_code: string;
  province: string;
  city: string;
  detail: string;
  postal_code: string | null;
  is_default: boolean;
  version: number;
}

/** 新建/修改地址的请求体；字段与后端 AddressInput 一致（region 由 province + city 组成）。 */
export interface AddressInput {
  label?: string;
  recipient: string;
  phone: string;
  region_code?: string;
  province?: string;
  city?: string;
  detail: string;
  postal_code?: string | null;
  is_default: boolean;
}

export async function fetchAddresses(): Promise<Address[]> {
  // 06 文档：`GET /me/addresses` 返回地址数组本身（不是分页对象）。
  return api.get<Address[]>("/me/addresses");
}

export async function createAddress(input: AddressInput): Promise<Address> {
  return api.post<Address>("/me/addresses", { ...input }, { idempotencyKey: newIdempotencyKey() });
}

export async function updateAddress(
  id: string,
  input: Partial<AddressInput> & { version: number },
): Promise<Address> {
  return api.patch<Address>(`/me/addresses/${encodeURIComponent(id)}`, { ...input });
}

export async function deleteAddress(id: string): Promise<void> {
  await api.delete<void>(`/me/addresses/${encodeURIComponent(id)}`);
}
