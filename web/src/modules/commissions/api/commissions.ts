/**
 * 委托模块 API 客户端与规则纯函数。
 *
 * 规则源：文档/07_委托报价与制作流程（询价、报价、名额、合同、制作）、
 * 文档/06_API接口契约（路径与错误码）。
 *
 * 边界：
 * - 前端不做定价。金额、有效期、名额余量、条款版本一律以服务端返回为权威。
 * - 前端估价与预算区间只用于沟通，不进订单金额。
 * - 草稿保存在服务端（11 文档：敏感草稿只存服务端），本地不写敏感数据。
 */

import { ApiError, api } from "@/shared/lib/http";

/* ------------------------------------------------------------------
   档期与名额
   ------------------------------------------------------------------ */

export type ScheduleStatus = "open" | "upcoming" | "closed";

export interface ScheduleRound {
  id: string;
  title: string;
  /** 普通申请或（已启用时的）限时申请；展示时原样说明，不翻译成“抢名额”。 */
  mode: string;
  status: ScheduleStatus;
  start_at: string;
  end_at: string;
  capacity: number;
  /** 匿名余量：公开排期只给数量，不含申请人信息。 */
  remaining: number;
  timezone: string;
}

export interface ScheduleRoundList {
  items: ScheduleRound[];
  total: number;
}

export async function fetchScheduleRounds(): Promise<ScheduleRoundList> {
  const payload = await api.get<ScheduleRoundList>("/schedule-rounds");
  return { items: payload.items ?? [], total: payload.total ?? payload.items?.length ?? 0 };
}

export interface ScheduleApplication {
  id: string;
  round_id: string;
  /** 申请本身的处理状态，与“是否已获得名额”分开表述。 */
  status: "SUBMITTED" | "SCREENING" | "SELECTED" | "REJECTED" | "WITHDRAWN" | string;
  created_at: string;
  /** 预留截止（预留期用于付款），服务端判定。 */
  hold_expires_at?: string | null;
}

export async function applyToScheduleRound(roundId: string): Promise<ScheduleApplication> {
  return api.post<ScheduleApplication>(`/schedule-rounds/${roundId}/applications`);
}

export const SCHEDULE_STATUS_LABEL: Record<ScheduleStatus, string> = {
  open: "开放中",
  upcoming: "即将开放",
  closed: "已结束",
};

/* ------------------------------------------------------------------
   询价
   ------------------------------------------------------------------ */

export type SuitType = "full" | "half" | "head" | "other";
export type InquiryStatus =
  | "DRAFT"
  | "SUBMITTED"
  | "NEEDS_INFO"
  | "QUOTED"
  | "ACCEPTED"
  | "EXPIRED"
  | "REJECTED"
  | "WITHDRAWN";

export type ContactPreference = "qq" | "wechat" | "email";

export const SUIT_TYPE_OPTIONS: Array<{ value: SuitType; label: string; hint: string }> = [
  { value: "full", label: "全装", hint: "头 + 身 + 手 + 脚 + 尾等全套" },
  { value: "half", label: "半装", hint: "上身为主，具体包含项由报价说明" },
  { value: "head", label: "单头", hint: "只制作头部" },
  { value: "other", label: "其他", hint: "局部、配件或组合需求，在要求里写清楚" },
];

export const CONTACT_PREFERENCE_OPTIONS: Array<{ value: ContactPreference; label: string }> = [
  { value: "qq", label: "QQ" },
  { value: "wechat", label: "微信" },
  { value: "email", label: "邮箱" },
];

export const INQUIRY_STATUS_LABEL: Record<InquiryStatus, string> = {
  DRAFT: "草稿",
  SUBMITTED: "处理中",
  NEEDS_INFO: "待补件",
  QUOTED: "已报价",
  ACCEPTED: "已接受",
  EXPIRED: "已过期",
  REJECTED: "未承接",
  WITHDRAWN: "已撤回",
};

export const INQUIRY_STATUS_TONE: Record<
  InquiryStatus,
  "neutral" | "amber" | "jade" | "wine" | "night"
> = {
  DRAFT: "neutral",
  SUBMITTED: "amber",
  NEEDS_INFO: "wine",
  QUOTED: "jade",
  ACCEPTED: "jade",
  EXPIRED: "neutral",
  REJECTED: "neutral",
  WITHDRAWN: "neutral",
};

/**
 * 已报价/已接受之外的“进行中”状态，用于账户列表的筛选分组。
 * 过期是独立筛选：它由服务端按有效期判定，不由前端算。
 */
export const INQUIRY_FILTER_GROUPS: Array<{
  key: "all" | "draft" | "processing" | "quoted" | "expired";
  label: string;
  statuses: InquiryStatus[];
  description: string;
}> = [
  { key: "all", label: "全部", statuses: [], description: "所有本人询价，包含草稿与终态记录。" },
  {
    key: "draft",
    label: "草稿",
    statuses: ["DRAFT"],
    description: "还没提交的询价，可以继续编辑。",
  },
  {
    key: "processing",
    label: "处理中",
    statuses: ["SUBMITTED", "NEEDS_INFO"],
    description: "已提交或待补件，等待工作室处理。",
  },
  {
    key: "quoted",
    label: "已报价",
    statuses: ["QUOTED", "ACCEPTED"],
    description: "已发布报价或你已接受报价。",
  },
  {
    key: "expired",
    label: "过期",
    statuses: ["EXPIRED"],
    description: "报价已过期，不能转到订单。",
  },
];

export interface InquiryInput {
  character_name: string;
  species: string;
  series_id: string;
  suit_type: SuitType;
  design_asset_ids: string[];
  requirements: string;
  /** 整数分；仅辅助沟通，不自动定价。 */
  budget_min_cents: number | null;
  budget_max_cents: number | null;
  /** 意向日期，不等于交付承诺。 */
  target_date: string | null;
  delivery_region: string;
  contact_preference: ContactPreference | null;
  /** 版本化留证：确认有权提供角色设定。 */
  rights_declaration: boolean;
  /** C19：未成年人需监护人同意，未确认前不收集监护人身份材料。 */
  guardian_required: boolean;
  /** C28：本人已通过审批的服务申请编号（服务先申请再报价）；普通询价不传。 */
  application_id?: string | null;
}

export interface Inquiry extends Omit<
  InquiryInput,
  | "design_asset_ids"
  | "requirements"
  | "budget_min_cents"
  | "budget_max_cents"
  | "target_date"
  | "delivery_region"
  | "contact_preference"
> {
  id: string;
  status: InquiryStatus;
  version: number;
  created_at: string;
  updated_at: string;
  submitted_at: string | null;
  /** 工作人员补件说明（NEEDS_INFO 时给出）。 */
  needs_info_note: string | null;
  /** 不承接的原因（REJECTED 时给出），不能只给“失败”。 */
  reject_reason: string | null;
  /**
   * 以下明细字段**只有详情接口**返回（后端 `InquiryDetail`）；列表行是
   * `InquirySummary`，这些字段不存在。类型上写死为必填会让「列表页读详情字段」
   * 逃过类型检查，运行期表现为对 `undefined` 取 `.length`，整个列表渲染失败留白。
   */
  design_asset_ids?: string[];
  requirements?: string;
  budget_min_cents?: number | null;
  budget_max_cents?: number | null;
  target_date?: string | null;
  delivery_region?: string | null;
  contact_preference?: string | null;
  /** 同样只有详情接口返回（列表行不含）。 */
  application_id?: string | null;
}

export interface InquiryListQuery {
  status?: InquiryStatus | "";
  page?: number;
  limit?: number;
}

export interface InquiryList {
  items: Inquiry[];
  total: number;
  page: number;
  limit: number;
}

export async function fetchInquiries(query: InquiryListQuery = {}): Promise<InquiryList> {
  const payload = await api.get<InquiryList>("/inquiries", {
    query: { status: query.status || undefined, page: query.page ?? 1, limit: query.limit ?? 20 },
  });
  return {
    items: payload.items ?? [],
    total: payload.total ?? payload.items?.length ?? 0,
    page: payload.page ?? query.page ?? 1,
    limit: payload.limit ?? query.limit ?? 20,
  };
}

export async function createInquiry(
  input: InquiryInput,
  idempotencyKey?: string,
): Promise<Inquiry> {
  return api.post<Inquiry>("/inquiries", input, { idempotencyKey });
}

export async function fetchInquiry(id: string): Promise<Inquiry> {
  return api.get<Inquiry>(`/inquiries/${id}`);
}

export async function updateInquiry(
  id: string,
  input: Partial<InquiryInput> & { version: number },
): Promise<Inquiry> {
  return api.patch<Inquiry>(`/inquiries/${id}`, input, { version: input.version });
}

export interface InquirySubmitResult {
  inquiry_id: string;
  status: InquiryStatus;
  version: number;
  submitted_at: string;
}

export async function submitInquiry(
  id: string,
  version: number,
  idempotencyKey?: string,
): Promise<InquirySubmitResult> {
  return api.post<InquirySubmitResult>(`/inquiries/${id}/submit`, { version }, { idempotencyKey });
}

export async function withdrawInquiry(id: string, version: number): Promise<Inquiry> {
  return api.post<Inquiry>(`/inquiries/${id}/withdraw`, { version }, { version });
}

export async function addInquirySupplement(
  id: string,
  input: { version: number; note: string; design_asset_ids?: string[] },
): Promise<Inquiry> {
  return api.post<Inquiry>(`/inquiries/${id}/supplements`, input, { version: input.version });
}

/* ------------------------------------------------------------------
   报价
   ------------------------------------------------------------------ */

export interface QuoteLineItem {
  code: string;
  label: string;
  /** 可为负数（优惠行），展示时保留符号。 */
  amount_cents: number;
  note: string | null;
}

export interface Quote {
  id: string;
  inquiry_id: string;
  version: number;
  status: "PUBLISHED" | "SUPERSEDED" | "ACCEPTED" | "EXPIRED" | "WITHDRAWN" | string;
  currency: string;
  line_items: QuoteLineItem[];
  total_cents: number;
  prepayment_cents: number;
  balance_cents: number;
  expires_at: string;
  estimated_start: string | null;
  estimated_delivery: string | null;
  /** 包含的免费修改次数；与 site.commissionPolicy.free_revision_count 对照展示。 */
  included_revisions: number;
  /** 不含项，必须与包含项分开列出。 */
  exclusions: string[];
  terms_version: string;
  issuer_name: string;
  created_at: string;
}

export async function fetchInquiryQuotes(inquiryId: string): Promise<Quote[]> {
  const payload = await api.get<{ items: Quote[] }>(`/inquiries/${inquiryId}/quotes`);
  return payload.items ?? [];
}

export interface QuoteAcceptResult {
  order_id: string;
  installment_id: string;
  amount_cents: number;
  currency: string;
  expires_at: string;
}

/**
 * 接受报价。必须带 `quote_version` 与 `terms_version`；
 * 同一报价重复请求返回同一订单（服务端幂等）。
 */
export async function acceptQuote(
  quoteId: string,
  input: { quote_version: number; terms_version: string },
  idempotencyKey?: string,
): Promise<QuoteAcceptResult> {
  return api.post<QuoteAcceptResult>(
    `/quotes/${quoteId}/accept`,
    { ...input, accepted: true },
    { idempotencyKey },
  );
}

export interface RequestReassessmentInput {
  version: number;
  reason: string;
}

/** 要求重新评估：把 QUOTED 退回 SUBMITTED，不产生订单。 */
export async function requestQuoteReassessment(
  inquiryId: string,
  input: RequestReassessmentInput,
): Promise<Inquiry> {
  return api.post<Inquiry>(`/inquiries/${inquiryId}/reassess`, input, { version: input.version });
}

/**
 * 报价是否已过期。前端只按服务端 `expires_at` 做展示提示；
 * 真正判定在服务端（过期接受返回 QUOTE_EXPIRED），前端不作为裁决依据。
 */
export function isQuoteExpired(quote: Pick<Quote, "expires_at">, now = Date.now()): boolean {
  const target = new Date(quote.expires_at).getTime();
  return !Number.isNaN(target) && target <= now;
}

/** 服务端价格区间：首期 + 尾款必须等于总额，差额照实显示而不是四舍五入。 */
export function quoteBalanceCents(quote: Pick<Quote, "total_cents" | "prepayment_cents">): number {
  return quote.total_cents - quote.prepayment_cents;
}

/** 只有在存在更新版本时，旧版本才给出“查看最新版本”入口。 */
export function latestQuote(quotes: Quote[]): Quote | null {
  if (quotes.length === 0) return null;
  return quotes.reduce((best, item) => (item.version > best.version ? item : best), quotes[0]!);
}

/* ------------------------------------------------------------------
   制作与量体
   ------------------------------------------------------------------ */

export interface ProductionStage {
  id: string;
  code: string;
  title: string;
  status: "PENDING" | "IN_PROGRESS" | "DONE" | "BLOCKED" | string;
  /** 负责人（工作室内部称呼或岗位，不泄露无关个人信息）。 */
  owner_name: string | null;
  started_at: string | null;
  estimated_at: string | null;
  finished_at: string | null;
  description: string;
  /** 私密阶段图片的媒体 ID；下载需经 GET /media/{id}/access 换取短期地址。 */
  private_asset_ids: string[];
  requires_customer_confirm: boolean;
  confirm_version: number | null;
  confirmed_at: string | null;
  /** 等待客户回复造成的延期记录，可查。 */
  waiting_customer_since: string | null;
}

export interface MeasurementInput {
  kind: string;
  /** 单位由服务端校验：高度/围度 cm，重量 kg；表单显示单位并支持小数。 */
  value: number;
  unit: string;
  note: string | null;
}

export interface MeasurementVersion {
  id: string;
  version: number;
  created_at: string;
  items: MeasurementInput[];
}

export interface ChangeRequest {
  id: string;
  status: "PENDING" | "ACCEPTED" | "DECLINED" | "WITHDRAWN" | string;
  description: string;
  /** 变更后的新增金额（分）；接受后生成单独补款 installment。 */
  additional_cents: number | null;
  delay_days: number | null;
  terms_version: string;
  created_at: string;
  decided_at: string | null;
  /** 变更单版本：决策时随请求提交，带版本才回。 */
  version?: number;
}

export interface CommissionProject {
  id: string;
  inquiry_id: string;
  order_id: string;
  status: string;
  title: string;
  series_title: string | null;
  suit_type: SuitType | string;
  created_at: string;
  /** 报价接受时确认的条款版本，后续变更单不能改写旧快照。 */
  terms_version: string;
  estimated_start: string | null;
  estimated_delivery: string | null;
  balance_due_at: string | null;
  stages: ProductionStage[];
  measurements: MeasurementVersion[];
  change_requests: ChangeRequest[];
  version: number;
}

export async function fetchCommissionProject(id: string): Promise<CommissionProject> {
  return api.get<CommissionProject>(`/commission-projects/${id}`);
}

export async function submitMeasurements(
  projectId: string,
  input: { version: number; items: MeasurementInput[] },
): Promise<MeasurementVersion> {
  return api.post<MeasurementVersion>(`/commission-projects/${projectId}/measurements`, input, {
    version: input.version,
  });
}

export async function confirmProductionStage(
  stageId: string,
  input: { version: number; confirmed: boolean; note?: string },
): Promise<ProductionStage> {
  return api.post<ProductionStage>(`/production-stages/${stageId}/confirm`, input, {
    version: input.version,
  });
}

export async function requestProductionChange(
  stageId: string,
  input: { version: number; description: string },
): Promise<ChangeRequest> {
  return api.post<ChangeRequest>(`/production-stages/${stageId}/changes`, input, {
    version: input.version,
  });
}

export async function decideChangeRequest(
  changeId: string,
  input: { accepted: boolean; version: number },
): Promise<ChangeRequest> {
  return api.post<ChangeRequest>(`/change-requests/${changeId}/decision`, input, {
    version: input.version,
  });
}

/** 量体单位说明：表单必须显示单位，避免只填数字（07 文档）。 */
export const MEASUREMENT_FIELDS: Array<{ kind: string; label: string; unit: "cm" | "kg"; hint: string }> = [
  { kind: "height", label: "身高", unit: "cm", hint: "立姿，赤足" },
  { kind: "head_circumference", label: "头围", unit: "cm", hint: "眉上最宽处" },
  { kind: "shoulder_width", label: "肩宽", unit: "cm", hint: "两肩端点直线距离" },
  { kind: "chest", label: "胸围", unit: "cm", hint: "自然站立，不吸气" },
  { kind: "waist", label: "腰围", unit: "cm", hint: "最细处" },
  { kind: "hip", label: "臀围", unit: "cm", hint: "最宽处" },
  { kind: "arm_length", label: "臂长", unit: "cm", hint: "肩点到腕点" },
  { kind: "leg_length", label: "腿长", unit: "cm", hint: "腰侧到脚踝" },
  { kind: "foot_length", label: "脚长", unit: "cm", hint: "赤足，脚跟到最长趾" },
  { kind: "weight", label: "体重", unit: "kg", hint: "用于结构承重评估" },
];

/** 单位换算与异常范围只用于提示；特殊体型交工作人员确认，不硬编码排除用户。 */
export function measurementHint(kind: string, value: number, unit: string): string {
  if (!Number.isFinite(value) || value <= 0) return "";
  if (unit === "kg") {
    return "体重只用于结构与承重评估，不用于健康判断。";
  }
  if (kind === "height" && (value < 120 || value > 220)) {
    return "身高超出常见范围，请再核对一次；特殊体型由工作人员单独确认，不会因此拒绝委托。";
  }
  return "";
}

/** 有小数需求时保留一位；整数不补 .0，避免出现无意义的精度。 */
export function formatMeasurement(value: number, unit: string): string {
  if (!Number.isFinite(value)) return "—";
  const text = Number.isInteger(value) ? String(value) : value.toFixed(1);
  return `${text} ${unit}`;
}

/* ------------------------------------------------------------------
   上传（设定图）
   ------------------------------------------------------------------ */

export const UPLOAD_MAX_BYTES = 10 * 1024 * 1024;
export const UPLOAD_MIN_COUNT = 1;
export const UPLOAD_MAX_COUNT = 3;
export const UPLOAD_ACCEPT = "image/jpeg,image/png,image/webp";
export const UPLOAD_ALLOWED_MIME: readonly string[] = ["image/jpeg", "image/png", "image/webp"];

export type UploadScanStatus = "pending" | "scanning" | "ready" | "rejected";

export interface UploadSession {
  asset_id: string;
  object_key: string;
  visibility: "private" | "public" | string;
  upload_url: string;
  expires_in: number;
  max_bytes: number;
}

export interface UploadStatus {
  id: string;
  asset_id?: string;
  filename?: string;
  mime?: string;
  size?: number;
  scan_status: UploadScanStatus;
  /** 拒绝原因（UPLOAD_REJECTED 时给出），不能只给“失败”。 */
  reject_reason?: string | null;
}

export const UPLOAD_SCAN_LABEL: Record<UploadScanStatus, string> = {
  pending: "等待扫描",
  scanning: "安全扫描中",
  ready: "可提交",
  rejected: "未通过安全检查",
};

export async function createUpload(input: {
  purpose: "design";
  filename: string;
  mime: string;
  size: number;
}): Promise<UploadSession> {
  return api.post<UploadSession>("/uploads", input);
}

export async function uploadFileContent(uploadId: string, file: Blob, filename: string): Promise<void> {
  const form = new FormData();
  form.append("file", file, filename);
  await api.post<unknown>(`/uploads/${uploadId}/content`, undefined, { formData: form });
}

export async function completeUpload(uploadId: string): Promise<UploadStatus> {
  return api.post<UploadStatus>(`/uploads/${uploadId}/complete`);
}

export async function fetchUpload(uploadId: string): Promise<UploadStatus> {
  return api.get<UploadStatus>(`/uploads/${uploadId}`);
}

/** 私密附件短期下载地址（no-store）；返回的地址不写入本地存储。 */
export async function fetchMediaAccess(mediaId: string): Promise<{ url: string; expires_at: string }> {
  return api.get<{ url: string; expires_at: string }>(`/media/${mediaId}/access`);
}

/**
 * 上传前本地校验。与 10 文档的文件安全规则一致：
 * 单张 ≤10MiB、仅 JPEG/PNG/WebP、1–3 张。
 * 本地校验只用于尽早提示；服务端仍会独立校验并返回 UPLOAD_* 错误码。
 */
export function validateDesignFile(file: { name: string; type: string; size: number }): string {
  if (!UPLOAD_ALLOWED_MIME.includes(file.type)) {
    return `${file.name}：只支持 JPEG / PNG / WebP 格式。`;
  }
  if (file.size <= 0) {
    return `${file.name}：文件为空，请重新导出后再上传。`;
  }
  if (file.size > UPLOAD_MAX_BYTES) {
    return `${file.name}：单张不能超过 10MiB，请压缩后重试。`;
  }
  return "";
}

export function validateDesignFileCount(count: number): string {
  if (count < UPLOAD_MIN_COUNT) return "至少上传 1 张设定图，最多 3 张。";
  if (count > UPLOAD_MAX_COUNT) return "最多上传 3 张设定图，请先移除多余的文件。";
  return "";
}

export interface UploadedAsset {
  asset_id: string;
  filename: string;
  size: number;
  scan_status: UploadScanStatus;
  reject_reason: string | null;
}

/**
 * 完整上传流程：建会话 → 传内容 → complete 确认 → 取扫描状态。
 * 三方（询价设定图、反馈附件）共用，避免各页各写一份不一致的调用顺序。
 * 失败时抛出 ApiError，由调用方按 `code` 展示原因；已上传的附件仍归本人所有，可复用。
 */
export async function runUploadFlow(
  file: File,
  options: { purpose: "design" } = { purpose: "design" },
): Promise<UploadedAsset> {
  const session = await createUpload({
    purpose: options.purpose,
    filename: file.name,
    mime: file.type,
    size: file.size,
  });
  if (file.size > session.max_bytes) {
    throw new ApiError(400, {
      code: "UPLOAD_LIMIT_EXCEEDED",
      message: `单张不能超过 ${Math.round(session.max_bytes / (1024 * 1024))}MiB。`,
      fields: {},
      trace_id: "",
    });
  }
  await uploadFileContent(session.asset_id, file, file.name);
  const completed = await completeUpload(session.asset_id);
  const status =
    completed.scan_status === "ready" || completed.scan_status === "rejected"
      ? completed
      : await fetchUpload(session.asset_id);
  return {
    asset_id: session.asset_id,
    filename: file.name,
    size: file.size,
    scan_status: status.scan_status,
    reject_reason: status.reject_reason ?? null,
  };
}

/* ------------------------------------------------------------------
   询价草稿校验（纯函数，供表单与测试共用）
   ------------------------------------------------------------------ */

export function validateBudgetRange(
  minCents: number | null,
  maxCents: number | null,
): string {
  if (minCents !== null && (!Number.isInteger(minCents) || minCents < 0)) {
    return "预算下限必须是不小于 0 的整数金额。";
  }
  if (maxCents !== null && (!Number.isInteger(maxCents) || maxCents < 0)) {
    return "预算上限必须是不小于 0 的整数金额。";
  }
  if (minCents !== null && maxCents !== null && minCents > maxCents) {
    return "预算下限不能高于上限；如果还没有明确区间，可以留空。";
  }
  return "";
}

export const INQUIRY_LIMITS = {
  character_name: { min: 1, max: 60 },
  species: { min: 1, max: 60 },
  requirements: { min: 0, max: 3000 },
} as const;

export type InquiryFieldErrors = Partial<Record<keyof InquiryInput, string>>;

/** 单字段校验：`first` 表示只返回第一条（逐步表单的即时提示）。 */
export function validateInquiryField(
  field: keyof InquiryInput,
  draft: InquiryInput,
): string {
  switch (field) {
    case "character_name": {
      const value = draft.character_name.trim();
      if (value.length < INQUIRY_LIMITS.character_name.min) return "请填写角色名称。";
      if (value.length > INQUIRY_LIMITS.character_name.max) return "角色名称最多 60 字。";
      return "";
    }
    case "species": {
      const value = draft.species.trim();
      if (value.length < INQUIRY_LIMITS.species.min) return "请填写物种，例如“龙”“狼”“猫科”。";
      if (value.length > INQUIRY_LIMITS.species.max) return "物种最多 60 字。";
      return "";
    }
    case "series_id":
      return draft.series_id ? "" : "请选择要询价的系列；系列决定报价范围。";
    case "suit_type":
      return draft.suit_type ? "" : "请选择制作类型（全装 / 半装 / 单头 / 其他）。";
    case "design_asset_ids": {
      const count = draft.design_asset_ids.length;
      return validateDesignFileCount(count);
    }
    case "requirements":
      return draft.requirements.length > INQUIRY_LIMITS.requirements.max
        ? "需求说明最多 3000 字，请精简后再提交。"
        : "";
    case "budget_min_cents":
    case "budget_max_cents":
      return validateBudgetRange(draft.budget_min_cents, draft.budget_max_cents);
    case "target_date":
      return "";
    case "delivery_region":
      return draft.delivery_region.trim() ? "" : "请填写收货的国家或省级范围，用于评估运输可行性。";
    case "contact_preference":
      return "";
    case "rights_declaration":
      return draft.rights_declaration
        ? ""
        : "请确认你拥有或已获得该角色设定的使用授权，否则不能提交询价。";
    case "guardian_required":
      return "";
    default:
      return "";
  }
}

/** 逐步表单的步序（11 文档页面布局基线的固定顺序）。 */
export const INQUIRY_STEP_FIELDS: ReadonlyArray<ReadonlyArray<keyof InquiryInput>> = [
  ["character_name", "species", "series_id", "suit_type"],
  ["design_asset_ids"],
  ["requirements", "budget_min_cents", "budget_max_cents", "target_date", "delivery_region"],
  ["contact_preference"],
  ["rights_declaration", "guardian_required"],
];

export function validateInquiryStep(
  stepIndex: number,
  draft: InquiryInput,
  extras: { uploadsReady: boolean } = { uploadsReady: true },
): InquiryFieldErrors {
  const fields = INQUIRY_STEP_FIELDS[stepIndex] ?? [];
  const errors: InquiryFieldErrors = {};
  for (const field of fields) {
    const message = validateInquiryField(field, draft);
    if (message) errors[field] = message;
  }
  if (stepIndex === 1 && !errors.design_asset_ids && !extras.uploadsReady) {
    errors.design_asset_ids = "设定图还在安全检查中，通过后才能提交询价。";
  }
  return errors;
}

export function validateInquiryDraft(
  draft: InquiryInput,
  extras: { uploadsReady: boolean } = { uploadsReady: true },
): InquiryFieldErrors {
  const errors: InquiryFieldErrors = {};
  for (let index = 0; index < INQUIRY_STEP_FIELDS.length; index += 1) {
    Object.assign(errors, validateInquiryStep(index, draft, extras));
  }
  return errors;
}

export function hasFieldErrors(errors: InquiryFieldErrors): boolean {
  return Object.values(errors).some((value) => Boolean(value));
}

export function emptyInquiryDraft(): InquiryInput {
  return {
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
  };
}

/**
 * 把服务端询价记录还原成表单草稿。
 * 只取表单字段，丢弃状态、版本与工作人员备注，避免把内部信息带进输入框。
 */
export function inquiryToDraft(inquiry: Inquiry): InquiryInput {
  return {
    character_name: inquiry.character_name ?? "",
    species: inquiry.species ?? "",
    series_id: inquiry.series_id ?? "",
    suit_type: inquiry.suit_type ?? "full",
    design_asset_ids: [...(inquiry.design_asset_ids ?? [])],
    requirements: inquiry.requirements ?? "",
    budget_min_cents: inquiry.budget_min_cents ?? null,
    budget_max_cents: inquiry.budget_max_cents ?? null,
    target_date: inquiry.target_date ?? null,
    delivery_region: inquiry.delivery_region ?? "",
    contact_preference: (inquiry.contact_preference ?? null) as ContactPreference | null,
    rights_declaration: Boolean(inquiry.rights_declaration),
    guardian_required: Boolean(inquiry.guardian_required),
  };
}

/** 草稿是否有未保存的改动（用于 dirty / saved 状态与离开提示）。 */
export function draftEquals(left: InquiryInput, right: InquiryInput): boolean {
  return (
    left.character_name === right.character_name &&
    left.species === right.species &&
    left.series_id === right.series_id &&
    left.suit_type === right.suit_type &&
    left.requirements === right.requirements &&
    left.budget_min_cents === right.budget_min_cents &&
    left.budget_max_cents === right.budget_max_cents &&
    left.target_date === right.target_date &&
    left.delivery_region === right.delivery_region &&
    left.contact_preference === right.contact_preference &&
    left.rights_declaration === right.rights_declaration &&
    left.guardian_required === right.guardian_required &&
    left.design_asset_ids.length === right.design_asset_ids.length &&
    left.design_asset_ids.every((id, index) => id === right.design_asset_ids[index])
  );
}

/* ------------------------------------------------------------------
   状态与错误映射
   ------------------------------------------------------------------ */

export type InquiryAction = "edit" | "submit" | "withdraw" | "supplement" | "accept";

/** 状态机（07 文档）：客户可做的动作由当前状态决定，已接受询价不能直接撤回。 */
export function inquiryAllowedActions(status: InquiryStatus): InquiryAction[] {
  switch (status) {
    case "DRAFT":
      return ["edit", "submit", "withdraw"];
    case "SUBMITTED":
      return ["withdraw"];
    case "NEEDS_INFO":
      return ["supplement", "withdraw"];
    case "QUOTED":
      return ["accept", "withdraw"];
    default:
      return [];
  }
}

export const ERROR_MESSAGE: Record<string, string> = {
  QUOTE_EXPIRED: "这份报价已过期，不能转到订单。请查看最新版本或要求重新评估。",
  QUOTE_CHANGED: "报价内容已被更新，你看到的版本不再是当前版本，不能按旧价格下单。",
  SCHEDULE_CLOSED: "本轮档期已关闭，新的申请要等下一轮开放。",
  NO_CAPACITY: "本轮名额已经用完，可以等下一轮开放或先提交询价沟通。",
  UPLOAD_NOT_READY: "设定图还没通过安全检查，完成后才能提交。",
  UPLOAD_LIMIT_EXCEEDED: "设定图数量或大小超出限制：1–3 张，单张不超过 10MiB。",
  UPLOAD_REJECTED: "设定图未通过安全检查，请更换文件后重新上传。",
  VERSION_CONFLICT: "内容已更新，请查看最新版本后重新操作。",
  STATE_CONFLICT: "当前状态不允许这个操作，请刷新查看最新状态。",
  FEATURE_DISABLED: "这个功能当前未开放。",
  POINTS_INSUFFICIENT: "星绒币不足，无法完成这个操作。",
};

/** 错误码 → 用户可读原因；未知码回落到服务端 message，不吞掉信息。 */
export function describeErrorCode(code: string, fallback = "操作失败，请稍后重试。"): string {
  return ERROR_MESSAGE[code] ?? fallback;
}

/* ------------------------------------------------------------------
   页面标题（私密页面同样要设置 document.title；集中在这里避免重复）
   ------------------------------------------------------------------ */

const SITE_NAME = "弈星纪元兽装工作室";

export function useDocumentTitle(): (suffix: string) => void {
  return (suffix: string) => {
    document.title = suffix ? `${suffix} · ${SITE_NAME}` : SITE_NAME;
  };
}

/* ------------------------------------------------------------------
   展示辅助
   ------------------------------------------------------------------ */

export function formatRegion(region: string): string {
  return region.trim() || "未填写";
}

export function scheduleCapacityText(round: ScheduleRound): string {
  if (round.capacity <= 0) return "名额以公告为准";
  return `总名额 ${round.capacity} · 剩余 ${round.remaining}`;
}
