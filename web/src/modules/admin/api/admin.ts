/**
 * 运营后台 API 客户端与类型。
 *
 * 约束（03/06 文档）：
 * - 后台会话使用**独立 cookie**，登录请求不依赖前台会话；`api` 客户端已带 `credentials: "include"`，
 *   服务端按 `/api/v1/admin` 路径下发独立会话 cookie。
 * - 写操作由 `api` 统一注入 CSRF 头与幂等键；编辑与状态动作带 `version`（乐观锁）。
 * - 前端只按 `error.code` 分支，不对 message 做逻辑判断。
 * - 未实现的端点返回 404/501 时，页面必须显示「该模块接口尚未就绪」，不伪造数据。
 *
 * 所有路径相对 `/api/v1`，因此这里一律写成 `/admin/...`。
 */

import { ref, watch, type Ref } from "vue";

import { ApiError, api, newIdempotencyKey } from "@/shared/lib/http";

/** 后台 DTO 的结构在不同模块间不完全一致；统一用这个可读字典承载未建模字段。 */
export type Dict = Record<string, unknown>;

export type AdminList<T> = {
  items: T[];
  next_cursor?: string | null;
  has_more?: boolean;
  total?: number | null;
};

export type ListQuery = Record<string, string | number | boolean | undefined | null>;

/** `api` 的第三个参数类型（不导出，这里按结构复述，避免跨文件耦合）。 */
interface CallOptions {
  query?: ListQuery;
  idempotencyKey?: string;
  version?: number;
  signal?: AbortSignal;
}

/* ------------------------------------------------------------------ *
 * 会话与 MFA
 * ------------------------------------------------------------------ */

export interface EnrollPayload {
  secret: string;
  otpauth_uri: string;
  recovery_codes: string[];
}

export interface LoginResponse {
  status: "MFA_REQUIRED" | "MFA_ENROLL_REQUIRED";
  challenge_token: string;
  staff_id: string;
  enroll?: EnrollPayload | null;
}

export interface MfaResponse {
  staff_id: string;
  display_name: string;
  login_name: string;
  permissions: string[];
  roles: string[];
  session_expires_at: string;
  csrf_token: string;
  mfa_enabled: boolean;
}

export interface AdminMeResponse {
  staff_id: string;
  display_name: string;
  login_name: string;
  permissions: string[];
  roles: string[];
  session_expires_at: string;
  mfa_enabled: boolean;
}

/* ------------------------------------------------------------------ *
 * 仪表盘
 * ------------------------------------------------------------------ */

export interface DashboardPayload {
  /** 数组项可能为 null：表示该模块尚未就绪，界面要如实展示而不是当成 0。 */
  pending_payment_orders: number | null;
  pending_refunds: number | null;
  reconciliation_differences: number | null;
  open_inquiries: number | null;
  pending_feedback: number | null;
  low_stock_skus: number | null;
  dead_letter_events: number | null;
  feature_flags: Record<string, boolean> | null;
  generated_at?: string | null;
}

/* ------------------------------------------------------------------ *
 * 内容
 * ------------------------------------------------------------------ */

export interface AuthorAuthorization {
  /** 权利人（作者/委托人）；只填名称，不写联系方式。 */
  rights_holder?: string;
  license_scope?: string;
  authorization_note?: string;
  consent_confirmed?: boolean;
}

export interface ContentRecord extends Dict {
  id: string;
  type: string;
  slug: string;
  title: string;
  summary?: string | null;
  body_html?: string | null;
  status?: string | null;
  version?: number | null;
  authorization?: AuthorAuthorization | null;
  updated_at?: string | null;
  published_at?: string | null;
}

export interface ContentVersion {
  version: number;
  status?: string | null;
  title?: string | null;
  editor?: string | null;
  created_at?: string | null;
  note?: string | null;
}

export interface ContentWriteInput {
  type: string;
  slug: string;
  title: string;
  summary?: string;
  /** 富文本由后台 textarea 输入 HTML，服务端负责清洗后再入库。 */
  body_html?: string;
  authorization?: AuthorAuthorization;
}

/* ------------------------------------------------------------------ *
 * 素材
 * ------------------------------------------------------------------ */

export interface MediaRecord extends Dict {
  id: string;
  filename?: string | null;
  scan_status?: string | null;
  alt_text?: string | null;
  authorization?: AuthorAuthorization | null;
  references?: Array<{ kind?: string; label?: string; route?: string }> | null;
  ref_count?: number | null;
  created_at?: string | null;
}

export interface StorageStatus {
  ready: boolean;
  used_bytes?: number | null;
  orphan_count?: number | null;
  detail?: string | null;
}

/* ------------------------------------------------------------------ *
 * 商品、SKU、角色
 * ------------------------------------------------------------------ */

export interface ProductRecord extends Dict {
  id: string;
  title: string;
  slug?: string | null;
  status?: string | null;
  version?: number | null;
  sku_count?: number | null;
  price_from_cents?: number | null;
  currency?: string | null;
}

export interface SkuRecord extends Dict {
  id: string;
  product_id?: string | null;
  code?: string | null;
  spec?: string | null;
  price_cents?: number | null;
  currency?: string | null;
  stock_on_hand?: number | null;
  low_stock_threshold?: number | null;
  active?: boolean | null;
}

export interface CharacterRecord extends Dict {
  id: string;
  name: string;
  species?: string | null;
  status?: string | null;
  price_cents?: number | null;
  currency?: string | null;
  rights_note?: string | null;
  version?: number | null;
}

/* ------------------------------------------------------------------ *
 * 委托：报价、档期申请、制作单
 * ------------------------------------------------------------------ */

export type QuoteStatus = "OPEN" | "SUPPLEMENT_REQUIRED" | "QUOTED" | "REJECTED" | "ACCEPTED" | "EXPIRED";

export interface QuoteRecord extends Dict {
  id: string;
  inquiry_id?: string | null;
  status?: string | null;
  assignee?: string | null;
  character_name?: string | null;
  series?: string | null;
  version?: number | null;
  quote_version?: number | null;
  amount_cents?: number | null;
  currency?: string | null;
  valid_until?: string | null;
  rejection_reason?: string | null;
  updated_at?: string | null;
}

export interface StockApplicationRecord extends Dict {
  id: string;
  round_id?: string | null;
  kind?: string | null;
  status?: string | null;
  series?: string | null;
  applicant_label?: string | null;
  priority_rank?: number | null;
  created_at?: string | null;
  version?: number | null;
}

export interface ScheduleRoundRecord extends Dict {
  id: string;
  title: string;
  status?: string | null;
  start_at?: string | null;
  end_at?: string | null;
  timezone?: string | null;
  capacity?: number | null;
  used?: number | null;
  waitlist_count?: number | null;
  version?: number | null;
}

export interface CommissionProjectRecord extends Dict {
  id: string;
  code?: string | null;
  status?: string | null;
  stage?: string | null;
  series?: string | null;
  assignee?: string | null;
  version?: number | null;
  updated_at?: string | null;
}

/* ------------------------------------------------------------------ *
 * 订单、收款、退款、对账
 * ------------------------------------------------------------------ */

export interface OrderRecord extends Dict {
  id: string;
  order_no?: string | null;
  status?: string | null;
  total_cents?: number | null;
  paid_cents?: number | null;
  refunded_cents?: number | null;
  currency?: string | null;
  buyer_label?: string | null;
  created_at?: string | null;
  version?: number | null;
}

export interface OrderDetail extends OrderRecord {
  items?: Array<{ title?: string; sku_code?: string; quantity?: number; unit_price_cents?: number }> | null;
  installments?: Array<{ id: string; kind?: string; amount_cents?: number; status?: string; paid_at?: string | null }> | null;
  payments?: PaymentRecord[] | null;
}

export interface PaymentRecord extends Dict {
  id: string;
  order_id?: string | null;
  channel?: string | null;
  amount_cents?: number | null;
  currency?: string | null;
  status?: string | null;
  verified_at?: string | null;
  created_at?: string | null;
}

export interface PaymentAttentionItem {
  id: string;
  order_id?: string | null;
  kind?: string | null;
  status?: string | null;
  amount_cents?: number | null;
  created_at?: string | null;
}

export interface RefundRecord extends Dict {
  id: string;
  order_id?: string | null;
  amount_cents?: number | null;
  currency?: string | null;
  status?: string | null;
  reason?: string | null;
  requested_by?: string | null;
  approved_by?: string | null;
  channel?: string | null;
  failure_reason?: string | null;
  version?: number | null;
  created_at?: string | null;
}

export interface ReconciliationRunRecord extends Dict {
  id: string;
  business_date?: string | null;
  status?: string | null;
  channel?: string | null;
  channel_total_cents?: number | null;
  local_total_cents?: number | null;
  difference_cents?: number | null;
  currency?: string | null;
  attribution?: string | null;
  resolution?: string | null;
  created_at?: string | null;
}

/* ------------------------------------------------------------------ *
 * 反馈、积分
 * ------------------------------------------------------------------ */

export interface FeedbackRecord extends Dict {
  id: string;
  category?: string | null;
  title?: string | null;
  status?: string | null;
  reward_level?: string | null;
  duplicate_of?: string | null;
  reply_count?: number | null;
  appeal_status?: string | null;
  created_at?: string | null;
}

export type PointAdjustmentStatus = "PENDING" | "APPROVED" | "REJECTED";

export interface PointAdjustmentRecord extends Dict {
  id: string;
  user_label?: string | null;
  delta_points?: number | null;
  status?: PointAdjustmentStatus | string | null;
  reason?: string | null;
  requested_by?: string | null;
  approved_by?: string | null;
  created_at?: string | null;
}

/* ------------------------------------------------------------------ *
 * 用户、员工、设置、审计、任务
 * ------------------------------------------------------------------ */

export interface UserAdminSummary extends Dict {
  id: string;
  /** 只展示脱敏手机号（如 138****0000）；后台接口不下发明文。 */
  phone_masked?: string | null;
  nickname?: string | null;
  status?: string | null;
  restriction_status?: string | null;
  restriction_reason?: string | null;
  created_at?: string | null;
  last_login_at?: string | null;
  active_sessions?: number | null;
}

export interface UserSessionRecord extends Dict {
  id: string;
  device_label?: string | null;
  ip_masked?: string | null;
  created_at?: string | null;
  last_seen_at?: string | null;
  current?: boolean | null;
}

export interface StaffRecord extends Dict {
  id: string;
  login_name: string;
  display_name: string;
  roles?: string[];
  permissions?: string[];
  mfa_enabled?: boolean | null;
  status?: string | null;
  last_login_at?: string | null;
  version?: number | null;
}

export interface StaffWriteInput {
  login_name?: string;
  display_name: string;
  roles: string[];
  status?: string;
}

export interface RoleRecord extends Dict {
  id: string;
  code: string;
  name: string;
  permissions?: string[];
  description?: string | null;
}

export interface SettingRecord extends Dict {
  key: string;
  /** 标量或结构化配置（如 `commerce.policy` 的运费/售后/发票/跨境块）；后台不展示任何密钥类配置。 */
  value?: string | number | boolean | Record<string, unknown> | unknown[] | null;
  version?: number | null;
  updated_at?: string | null;
  updated_by?: string | null;
  description?: string | null;
}

export interface AuditLogRecord extends Dict {
  id: string;
  action?: string | null;
  actor_label?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  reason?: string | null;
  created_at?: string | null;
  before?: Dict | null;
  after?: Dict | null;
}

export interface JobRecord extends Dict {
  event_id: string;
  name?: string | null;
  status?: string | null;
  attempts?: number | null;
  dead_letter?: boolean | null;
  last_error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

/* ------------------------------------------------------------------ *
 * 端点封装
 * ------------------------------------------------------------------ */

export const adminApi = {
  /* --- 会话：独立后台 cookie，登录不带前台会话依赖 --- */
  login: (input: { login_name: string; password: string }) =>
    api.post<LoginResponse>("/admin/auth/login", input),
  mfa: (input: { challenge_token: string; code: string; staff_id: string }) =>
    api.post<MfaResponse>("/admin/auth/mfa", input, { idempotencyKey: newIdempotencyKey() }),
  logout: () => api.post<void>("/admin/auth/logout", undefined, { idempotencyKey: newIdempotencyKey() }),
  me: () => api.get<AdminMeResponse>("/admin/auth/me"),

  /* --- 概览 --- */
  dashboard: () => api.get<DashboardPayload>("/admin/dashboard"),

  /* --- 内容 --- */
  content: (query?: ListQuery) => api.get<AdminList<ContentRecord>>("/admin/content", { query }),
  createContent: (input: ContentWriteInput) => api.post<ContentRecord>("/admin/content", input),
  contentDetail: (id: string) => api.get<ContentRecord>(`/admin/content/${encodeURIComponent(id)}`),
  updateContent: (id: string, input: Partial<ContentWriteInput> & { version: number }) =>
    api.patch<ContentRecord>(`/admin/content/${encodeURIComponent(id)}`, input),
  contentVersions: (id: string) =>
    api.get<AdminList<ContentVersion>>(`/admin/content/${encodeURIComponent(id)}/versions`),
  publishContent: (id: string, input: { version: number; reason?: string }) =>
    api.post<ContentRecord>(`/admin/content/${encodeURIComponent(id)}/publish`, input),
  archiveContent: (id: string, input: { version: number; reason: string }) =>
    api.post<ContentRecord>(`/admin/content/${encodeURIComponent(id)}/archive`, input),

  /* --- 素材 --- */
  media: (query?: ListQuery) => api.get<AdminList<MediaRecord>>("/admin/media", { query }),
  updateMedia: (id: string, input: { alt_text?: string; authorization?: AuthorAuthorization; version?: number }) =>
    api.patch<MediaRecord>(`/admin/media/${encodeURIComponent(id)}`, input),
  linkMedia: (id: string, input: { target_type: string; target_id: string; note?: string }) =>
    api.post<MediaRecord>(`/admin/media/${encodeURIComponent(id)}/link`, input),
  cleanupMedia: (input?: { dry_run?: boolean; older_than_days?: number }) =>
    api.post<Dict>("/admin/media/cleanup", input ?? {}),
  mediaStorageStatus: () => api.get<StorageStatus>("/admin/media/storage-status"),

  /* --- 商品与 SKU --- */
  products: (query?: ListQuery) => api.get<AdminList<ProductRecord>>("/admin/products", { query }),
  createProduct: (input: Dict) => api.post<ProductRecord>("/admin/products", input),
  productDetail: (id: string) => api.get<ProductRecord>(`/admin/products/${encodeURIComponent(id)}`),
  updateProduct: (id: string, input: Dict & { version: number }) =>
    api.patch<ProductRecord>(`/admin/products/${encodeURIComponent(id)}`, input),
  createSku: (productId: string, input: Dict) =>
    api.post<SkuRecord>(`/admin/products/${encodeURIComponent(productId)}/skus`, input),
  updateSku: (id: string, input: Dict) => api.patch<SkuRecord>(`/admin/skus/${encodeURIComponent(id)}`, input),
  adjustStock: (id: string, input: { delta: number; reason: string }) =>
    api.post<SkuRecord>(`/admin/skus/${encodeURIComponent(id)}/stock`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  lowStockSkus: (query?: ListQuery) => api.get<AdminList<SkuRecord>>("/admin/skus/low-stock", { query }),
  catalogStats: () => api.get<Dict>("/admin/catalog/stats"),

  /* --- 角色（领养/掉落） --- */
  createCharacter: (input: Dict) => api.post<CharacterRecord>("/admin/characters", input),
  releaseCharacter: (id: string, input: { version?: number; reason: string }) =>
    api.post<CharacterRecord>(`/admin/characters/${encodeURIComponent(id)}/release`, input),

  /* --- 申请（掉落与服务） --- */
  stockApplications: (query?: ListQuery) =>
    api.get<AdminList<StockApplicationRecord>>("/admin/stock-applications", { query }),
  decideStockApplication: (
    id: string,
    input: { decision: "APPROVE" | "REJECT"; reason: string; priority_rank?: number; purchase_window_days?: number },
  ) =>
    api.post<StockApplicationRecord>(`/admin/stock-applications/${encodeURIComponent(id)}/decide`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),

  /* --- 委托：报价 / 档期 / 制作单 --- */
  // 列表以「询价」为单位（id 即询价编号），附带当前报价版本与金额；
  // 分派、补件、版本报价、拒绝都作用在询价上，与 06 文档的后台契约一致。
  quotes: (query?: ListQuery) => api.get<AdminList<QuoteRecord>>("/admin/quotes", { query }),
  quoteDetail: (id: string) => api.get<QuoteRecord>(`/admin/quotes/${encodeURIComponent(id)}`),
  assignQuote: (id: string, input: { assignee: string; note?: string }) =>
    api.post<Dict>(`/admin/inquiries/${encodeURIComponent(id)}/assign`, input),
  requestSupplement: (id: string, input: { reason: string; version: number }) =>
    api.post<Dict>(`/admin/inquiries/${encodeURIComponent(id)}/supplements`, {
      requirements: input.reason,
      request_reevaluation: true,
      version: input.version,
    }),
  publishQuote: (
    id: string,
    input: { amount_cents: number; currency: string; valid_days: number; terms_version: string; note?: string },
  ) =>
    api.post<QuoteRecord>(
      `/admin/inquiries/${encodeURIComponent(id)}/quotes`,
      {
        currency: input.currency,
        // 金额由服务端按明细重算：这里只提交一条整装明细，明细之和即报价总额。
        line_items: [
          {
            label: "整装委托",
            quantity: 1,
            unit_price_cents: input.amount_cents,
            note: input.note || undefined,
          },
        ],
        valid_days: input.valid_days,
        terms_version: input.terms_version,
        note: input.note,
      },
      { idempotencyKey: newIdempotencyKey() },
    ),
  rejectQuote: (id: string, input: { reason: string }) =>
    api.post<Dict>(`/admin/inquiries/${encodeURIComponent(id)}/reject`, input),

  scheduleRounds: (query?: ListQuery) =>
    api.get<AdminList<ScheduleRoundRecord>>("/admin/schedule-rounds", { query }),
  updateScheduleRound: (id: string, input: Dict & { version: number }) =>
    api.patch<ScheduleRoundRecord>(`/admin/schedule-rounds/${encodeURIComponent(id)}`, input),
  scheduleApplications: (roundId: string, query?: ListQuery) =>
    api.get<AdminList<StockApplicationRecord>>(`/admin/schedule-rounds/${encodeURIComponent(roundId)}/applications`, {
      query,
    }),

  commissionProjects: (query?: ListQuery) =>
    api.get<AdminList<CommissionProjectRecord>>("/admin/commission-projects", { query }),
  commissionDetail: (id: string) =>
    api.get<CommissionProjectRecord>(`/admin/commission-projects/${encodeURIComponent(id)}`),
  createProductionStage: (
    id: string,
    input: { stage_code: string; note: string; requires_confirmation?: boolean },
  ) =>
    api.post<Dict>(`/admin/commission-projects/${encodeURIComponent(id)}/stages`, {
      stage_code: input.stage_code,
      description: input.note,
      requires_confirmation: input.requires_confirmation ?? false,
    }),
  updateProductionStage: (stageId: string, input: { status?: string; note?: string; version: number }) =>
    api.patch<Dict>(`/admin/production-stages/${encodeURIComponent(stageId)}`, input),
  uploadPrivateAlbum: (id: string, input: { label: string; asset_ids: string[] }) =>
    api.post<Dict>(`/admin/commission-projects/${encodeURIComponent(id)}/album`, input),
  confirmRecords: (id: string) =>
    api.get<AdminList<Dict>>(`/admin/commission-projects/${encodeURIComponent(id)}/confirmations`),
  changeRequests: (id: string) =>
    api.get<AdminList<Dict>>(`/admin/commission-projects/${encodeURIComponent(id)}/change-requests`),
  decideChangeRequest: (changeId: string, input: { decision: "APPROVE" | "REJECT"; reason: string }) =>
    api.post<Dict>(`/admin/change-requests/${encodeURIComponent(changeId)}/decide`, input),

  /* --- 订单与发货 --- */
  orders: (query?: ListQuery) => api.get<AdminList<OrderRecord>>("/admin/orders", { query }),
  orderDetail: (id: string) => api.get<OrderDetail>(`/admin/orders/${encodeURIComponent(id)}`),
  createShipment: (input: { order_id: string; carrier: string; tracking_no: string; note?: string }) =>
    api.post<Dict>("/admin/shipments", input, { idempotencyKey: newIdempotencyKey() }),
  closeOrder: (id: string, input: { reason: string; version?: number }) =>
    api.post<OrderRecord>(`/admin/orders/${encodeURIComponent(id)}/close`, input),

  /* --- 收款与退款 --- */
  payments: (query?: ListQuery) => api.get<AdminList<PaymentRecord>>("/admin/payments", { query }),
  paymentAttention: (query?: ListQuery) =>
    api.get<AdminList<PaymentAttentionItem>>("/admin/payments/attention", { query }),
  queryPaymentChannel: (id: string) =>
    api.post<PaymentRecord>(`/admin/payments/${encodeURIComponent(id)}/query`, undefined, {
      idempotencyKey: newIdempotencyKey(),
    }),

  refunds: (query?: ListQuery) => api.get<AdminList<RefundRecord>>("/admin/refunds", { query }),
  requestRefund: (input: { order_id: string; amount_cents: number; reason: string; channel?: string }) =>
    api.post<RefundRecord>("/admin/refunds", input, { idempotencyKey: newIdempotencyKey() }),
  approveRefund: (id: string, input: { reason: string; version?: number }) =>
    api.post<RefundRecord>(`/admin/refunds/${encodeURIComponent(id)}/approve`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  retryRefundQuery: (id: string) =>
    api.post<RefundRecord>(`/admin/refunds/${encodeURIComponent(id)}/query`, undefined, {
      idempotencyKey: newIdempotencyKey(),
    }),

  /* --- 对账 --- */
  reconciliationRuns: (query?: ListQuery) =>
    api.get<AdminList<ReconciliationRunRecord>>("/admin/reconciliation-runs", { query }),
  attributeDifference: (id: string, input: { attribution: string; note: string }) =>
    api.post<ReconciliationRunRecord>(`/admin/reconciliation-runs/${encodeURIComponent(id)}/attributions`, input),
  resolveDifference: (id: string, input: { resolution: string; reason: string }) =>
    api.post<ReconciliationRunRecord>(`/admin/reconciliation-runs/${encodeURIComponent(id)}/resolutions`, input),
  exportReconciliation: (query?: ListQuery, input?: { reason?: string }) =>
    api.post<Dict>("/admin/reconciliation-runs/export", input ?? {}, { query }),

  /* --- 反馈与积分 --- */
  feedback: (query?: ListQuery) => api.get<AdminList<FeedbackRecord>>("/admin/feedback", { query }),
  feedbackDetail: (id: string) => api.get<FeedbackRecord>(`/admin/feedback/${encodeURIComponent(id)}`),
  // 审核唯一入口（06 文档）：结论、用户可读理由与发奖判据一并提交。
  // 奖励只在 result=RESOLVED 时允许，且必须给出 criteria；major 还需要 impact。
  reviewFeedback: (
    id: string,
    input: {
      result: string;
      reason: string;
      reward_level?: string;
      impact?: string;
      criteria?: string;
      duplicate_of?: string;
      resolution_note?: string;
    },
  ) =>
    api.post<FeedbackRecord>(`/admin/feedback/${encodeURIComponent(id)}/reviews`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  replyFeedback: (id: string, input: { body: string }) =>
    api.post<Dict>(`/admin/feedback/${encodeURIComponent(id)}/messages`, input),
  // 去重走同一审核入口：不能有绕过审核直接改状态的旁路。
  markDuplicate: (id: string, input: { duplicate_of: string; reason: string }) =>
    api.post<FeedbackRecord>(
      `/admin/feedback/${encodeURIComponent(id)}/reviews`,
      { result: "DUPLICATE", duplicate_of: input.duplicate_of, reason: input.reason },
      { idempotencyKey: newIdempotencyKey() },
    ),
  handleAppeal: (id: string, input: { decision: "APPROVE" | "REJECT"; reason: string }) =>
    api.post<Dict>(`/admin/feedback/${encodeURIComponent(id)}/appeals/decide`, input),

  pointLedger: (query?: ListQuery) => api.get<AdminList<Dict>>("/admin/points/ledger", { query }),
  pointAdjustments: (query?: ListQuery) =>
    api.get<AdminList<PointAdjustmentRecord>>("/admin/point-adjustments", { query }),
  requestPointAdjustment: (input: { user_id: string; delta_points: number; reason: string }) =>
    api.post<PointAdjustmentRecord>("/admin/point-adjustments", input, { idempotencyKey: newIdempotencyKey() }),
  approvePointAdjustment: (id: string, input: { reason: string }) =>
    api.post<PointAdjustmentRecord>(`/admin/point-adjustments/${encodeURIComponent(id)}/approve`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),

  /* --- 用户与员工 --- */
  users: (query?: ListQuery) => api.get<AdminList<UserAdminSummary>>("/admin/users", { query }),
  userDetail: (id: string) => api.get<UserAdminSummary>(`/admin/users/${encodeURIComponent(id)}`),
  userSessions: (id: string) => api.get<AdminList<UserSessionRecord>>(`/admin/users/${encodeURIComponent(id)}/sessions`),
  setUserRestriction: (id: string, input: { status: string; reason: string; until?: string }) =>
    api.post<UserAdminSummary>(`/admin/users/${encodeURIComponent(id)}/restriction`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  revokeUserSessions: (id: string, input: { session_ids?: string[]; reason: string }) =>
    api.post<{ revoked: number }>(`/admin/users/${encodeURIComponent(id)}/sessions/revoke`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),

  staff: (query?: ListQuery) => api.get<AdminList<StaffRecord>>("/admin/staff", { query }),
  createStaff: (input: StaffWriteInput & { login_name: string }) => api.post<StaffRecord>("/admin/staff", input),
  updateStaff: (id: string, input: Partial<StaffWriteInput> & { version?: number; reason?: string }) =>
    api.patch<StaffRecord>(`/admin/staff/${encodeURIComponent(id)}`, input),
  resetStaffPassword: (id: string, input: { reason: string }) =>
    api.post<Dict>(`/admin/staff/${encodeURIComponent(id)}/password-reset`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  roles: () => api.get<AdminList<RoleRecord>>("/admin/roles"),

  /* --- 设置 --- */
  settings: () => api.get<AdminList<SettingRecord>>("/admin/settings"),
  updateSetting: (
    key: string,
    input: {
      /** 结构化配置（如 `commerce.policy`）按 JSON 对象提交，服务端做字段级校验。 */
      value: string | number | boolean | Record<string, unknown> | unknown[];
      version: number;
      reason: string;
    },
  ) => api.patch<SettingRecord>(`/admin/settings/${encodeURIComponent(key)}`, input),

  /* --- 审计与任务 --- */
  auditLogs: (query?: ListQuery) => api.get<AdminList<AuditLogRecord>>("/admin/audit-logs", { query }),
  jobs: (query?: ListQuery) => api.get<AdminList<JobRecord>>("/admin/jobs", { query }),
  retryJob: (eventId: string, input: { reason: string }) =>
    api.post<Dict>(`/admin/jobs/${encodeURIComponent(eventId)}/retry`, input, {
      idempotencyKey: newIdempotencyKey(),
    }),
  cleanupJobs: (input?: { dry_run?: boolean }) => api.post<Dict>("/admin/jobs/cleanup", input ?? {}),
  reconcileJobs: () => api.post<Dict>("/admin/jobs/reconcile", undefined, { idempotencyKey: newIdempotencyKey() }),
};

/* ------------------------------------------------------------------ *
 * 错误分类：页面据此选择 loading/empty/error/forbidden/not-found 状态
 * ------------------------------------------------------------------ */

export type ApiFailureKind = "forbidden" | "not-found" | "not-ready" | "session-expired" | "version-conflict" | "error";

/**
 * 把 `ApiError` 归类到界面状态。
 * `not-ready` 表示该模块接口尚未就绪（404/405/501），页面必须如实说明而不是伪造数据。
 */
export function classifyFailure(error: unknown): ApiFailureKind {
  const candidate = error as Partial<ApiError> & { status?: number; code?: string };
  const status = candidate?.status ?? 0;
  const code = candidate?.code ?? "";
  if (code === "VERSION_CONFLICT") return "version-conflict";
  if (candidate?.needsLogin) return "session-expired";
  if (code === "FORBIDDEN" || code === "PERMISSION_DENIED" || status === 403) return "forbidden";
  if (code === "RESOURCE_NOT_FOUND" || status === 404) return "not-found";
  if (code === "NOT_IMPLEMENTED" || code === "ENDPOINT_NOT_READY" || status === 405 || status === 501) {
    return "not-ready";
  }
  return "error";
}

export interface FailureView {
  kind: ApiFailureKind;
  title: string;
  description: string;
  /** 可重试的动作说明；网络失败必须说明可重试而不是让用户重填。 */
  retryHint: string;
}

export function describeFailure(error: unknown, subject: string): FailureView {
  const kind = classifyFailure(error);
  const message = error instanceof Error && error.message ? error.message : "";
  switch (kind) {
    case "forbidden":
      return {
        kind,
        title: `无权访问${subject}`,
        description: "当前员工账号没有这个模块的权限码；服务端仍会逐动作校验权限，界面隐藏只是提示。",
        retryHint: "如职责需要，请联系管理员在「员工与权限」中授权。",
      };
    case "not-found":
      return {
        kind,
        title: `${subject}不存在或已被删除`,
        description: "该记录可能已被其他员工删除，或链接中的编号不正确。",
        retryHint: "可以返回列表重新选择。",
      };
    case "not-ready":
      return {
        kind,
        title: `${subject}接口尚未就绪`,
        description: "该模块的后台接口还没有上线，这里不会显示任何伪造数据。",
        retryHint: "接口上线后点击重试即可。",
      };
    case "session-expired":
      return {
        kind,
        title: "后台会话已过期",
        description: "为保护运营数据，后台会话到期后必须重新登录并完成 MFA。",
        retryHint: "请重新登录；未提交的编辑内容需要重新填写。",
      };
    case "version-conflict":
      return {
        kind,
        title: "内容已被他人更新",
        description: "你打开这条记录之后，其他员工已经提交了新版本。",
        retryHint: "请刷新后重新提交，避免覆盖他人的修改。",
      };
    default:
      return {
        kind,
        title: `${subject}加载失败`,
        description: message || "请求没有成功，可能是网络或服务端暂时不可用。",
        retryHint: "网络恢复后可以重试，已填写的内容不会丢失。",
      };
  }
}

export type { CallOptions };

/* ------------------------------------------------------------------ *
 * 状态语义：把 `FailureView.kind` 映射到 EmptyState 的 tone。
 * 无权限（403）与记录不存在（404）必须是各自的状态，不能渲染成普通错误；
 * 03 文档要求 403 不透露对象是否存在，因此两者的文案也由 `describeFailure` 分别给出。
 * ------------------------------------------------------------------ */
export function failureTone(
  error: FailureView | null | undefined,
): "error" | "forbidden" | "not-found" {
  if (error?.kind === "forbidden") return "forbidden";
  if (error?.kind === "not-found") return "not-found";
  return "error";
}

/* ------------------------------------------------------------------ *
 * 后台数据页通用加载状态
 *
 * 每个后台数据页都要有 loading / empty / error / forbidden / not-found 状态；
 * 这里统一状态机与 401 处理，避免各页各写一份：
 * - 401 时清理会话并回到登录页，不再向下抛；
 * - 403/404/501 由 `describeFailure` 归类，页面决定怎么显示；
 * - 网络错误保留重试入口，并且不清空用户已填写的内容。
 * ------------------------------------------------------------------ */

export interface PageState<T> {
  data: Ref<T>;
  loading: Ref<boolean>;
  error: Ref<FailureView | null>;
  saving: Ref<boolean>;
  saveError: Ref<string>;
  load: () => Promise<void>;
  reload: () => Promise<void>;
}

export type SessionExpiredHandler = () => void;

export function usePageData<T>(
  loader: () => Promise<T>,
  initial: T,
  subject: string,
  onSessionExpired: SessionExpiredHandler = () => undefined,
  onSavingChange: (value: boolean) => void = () => undefined,
): PageState<T> {
  const data = ref(initial) as Ref<T>;
  const loading = ref(true);
  const error = ref<FailureView | null>(null);
  const saving = ref(false);
  const saveError = ref("");

  async function load(): Promise<void> {
    loading.value = true;
    error.value = null;
    try {
      data.value = await loader();
    } catch (caught) {
      if (caught instanceof ApiError && caught.needsLogin) {
        onSessionExpired();
        return;
      }
      error.value = describeFailure(caught, subject);
    } finally {
      loading.value = false;
    }
  }

  watch(saving, (value) => onSavingChange(value));

  return { data, loading, error, saving, saveError, load, reload: load };
}

/** 并行读取同页面上的多个接口；任一失败不影响其它区块，失败项单独报告。 */
export async function collect<T extends Record<string, unknown>>(
  entries: { [K in keyof T]: () => Promise<T[K]> },
): Promise<{ ok: Partial<T>; failed: Array<{ key: keyof T; error: unknown }> }> {
  const keys = Object.keys(entries) as Array<keyof T>;
  const results = await Promise.allSettled(keys.map((key) => (entries[key] as () => Promise<T[keyof T]>)()));
  const ok: Partial<T> = {};
  const failed: Array<{ key: keyof T; error: unknown }> = [];
  results.forEach((result, index) => {
    const key = keys[index]!;
    if (result.status === "fulfilled") {
      ok[key] = result.value as T[keyof T];
      return;
    }
    failed.push({ key, error: result.reason as unknown });
  });
  return { ok, failed };
}

/**
 * 写操作失败时的统一文案。
 * `VERSION_CONFLICT` 必须提示「内容已被他人更新，请刷新后重新提交」并提供刷新入口。
 */
export function actionErrorMessage(caught: unknown, fallback: string): { message: string; versionConflict: boolean } {
  if (caught instanceof ApiError) {
    if (caught.isVersionConflict) {
      return { message: "内容已被他人更新，请刷新后重新提交。", versionConflict: true };
    }
    return { message: `${caught.message}（错误码 ${caught.code}）`, versionConflict: false };
  }
  return { message: fallback, versionConflict: false };
}

/** 授权信息摘要：表格里只显示权利人 + 范围，详情里再展开。 */
export function authorizationSummary(value: unknown): string {
  if (!value || typeof value !== "object") return "未登记";
  const record = value as Record<string, unknown>;
  const holder = typeof record.rights_holder === "string" ? record.rights_holder : "";
  const scope = typeof record.license_scope === "string" ? record.license_scope : "";
  if (!holder && !scope) return "未登记";
  return [holder, scope].filter(Boolean).join(" · ");
}

/** 空值统一显示为 —，避免把「没有值」渲染成空白。 */
export function text(value: unknown, fallback = "—"): string {
  if (value === null || value === undefined || value === "") return fallback;
  return String(value);
}
