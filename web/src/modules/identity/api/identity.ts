/**
 * 身份与账户模块 API 客户端（06 文档接口目录，路径与后端 router 一致）。
 *
 * 约束：
 * - 只按 `payload.error.code` 分支，不对 message 做逻辑判断（06 文档）。
 * - 每个函数的返回类型对应后端 Pydantic DTO 的实际字段，不做乐观猜测。
 * - 手机号/地址等隐私字段只用于本人页面渲染；服务端返回脱敏手机号。
 */

import { ApiError, api, newIdempotencyKey } from "@/shared/lib/http";

export type { UserSummary } from "@/modules/identity/stores/auth";

// ------------------------------------------------------------------ 枚举与标签

/** 后端 `MeResponse.pending_actions` 的键（10 文档 / router.get_me）。 */
export type PendingActionKey =
  | "orders_awaiting_payment"
  | "quotes_awaiting_decision"
  | "stages_awaiting_confirmation"
  | "after_sales_open";

export type ConsentPurpose =
  | "terms"
  | "privacy"
  | "commission_rights"
  | "marketing_email"
  | "showcase_identity"
  | "showcase_work"
  | "miner_guardian"
  | "measurement_data";

/**
 * 换绑/敏感动作验证码的角色（与后端 `PhoneChangeChallengeRequest.role` 一致）。
 * `sensitive` 表示「向当前绑定号码下发验证码」，服务端按会话号码下发，前端不提交手机号。
 */
export type PhoneChangeRole = "old" | "new" | "sensitive";

export type ClosureStatus = "BLOCKED" | "REQUESTED" | string;

export interface PendingActionMeta {
  key: PendingActionKey;
  label: string;
  /** 深链只在相关模块路由已注册时可用；这里是固定声明。 */
  to: string;
  description: string;
}

/**
 * 待办深链。后端目前返回四个键；「待补件」由询价/报价模块承载，
 * 因此指向询价列表而不是编造一个不存在的路由。
 */
export const PENDING_ACTION_META: readonly PendingActionMeta[] = [
  {
    key: "orders_awaiting_payment",
    label: "待付款订单",
    to: "/account/orders",
    description: "付款期限以订单页倒计时为准；超时后服务端释放库存预留。",
  },
  {
    key: "quotes_awaiting_decision",
    label: "待决定的报价",
    to: "/account/quotes",
    description: "报价有有效期，过期或旧版本不能转为订单。",
  },
  {
    key: "stages_awaiting_confirmation",
    label: "待确认阶段",
    to: "/account/orders",
    description: "制作节点需要你确认后才会进入下一步。",
  },
  {
    key: "after_sales_open",
    label: "处理中的售后",
    to: "/account/after-sales",
    description: "售后进度与补件要求在该列表内查看。",
  },
] as const;

export const CONSENT_PURPOSE_LABEL: Record<ConsentPurpose, string> = {
  terms: "用户服务协议",
  privacy: "隐私政策与第三方清单",
  commission_rights: "委托与授权条款",
  marketing_email: "营销邮件",
  showcase_identity: "公开展示委托人身份",
  showcase_work: "公开展示成品",
  miner_guardian: "未成年监护人确认",
  measurement_data: "量体数据收集",
};

/** 可在账号页自助撤回的同意项（FR-018：营销单独选择，展示授权可单独撤回）。 */
export const OPTIONAL_CONSENT_PURPOSES: readonly ConsentPurpose[] = [
  "marketing_email",
  "showcase_identity",
  "showcase_work",
  "measurement_data",
] as const;

// ------------------------------------------------------------------ 错误码文案

/**
 * 服务端稳定错误码 → 用户可读文案（06 文档「稳定业务错误码」）。
 * 未列出的码回落到服务端 message，仍然只按 code 分流，不做字符串匹配。
 */
export const IDENTITY_ERROR_MESSAGE: Record<string, string> = {
  AUTH_REQUIRED: "登录状态已失效，请重新登录后继续。",
  SESSION_EXPIRED: "登录状态已过期，请重新登录后继续。",
  CSRF_INVALID: "页面停留时间过长，请刷新页面后重试。",
  SMS_RATE_LIMITED: "发送过于频繁，请等待倒计时结束后再试。",
  SMS_CODE_INVALID: "验证码不正确，请核对后重新输入。",
  SMS_CODE_EXPIRED: "验证码已过期，请重新获取。",
  SMS_CHALLENGE_USED: "该验证码已被使用，请重新获取。",
  REAUTH_REQUIRED: "该操作需要重新验证身份，请先完成一次短信验证码验证。",
  SENSITIVE_VERIFICATION_REQUIRED: "该操作需要先完成一次短信验证码验证。",
  VALIDATION_ERROR: "提交内容未通过校验，请按字段提示修改。",
  VERSION_CONFLICT: "资料已被其他操作更新，请刷新后重新提交。",
  STATE_CONFLICT: "当前状态不允许该操作，请刷新后重试。",
  IDEMPOTENCY_CONFLICT: "请求重复，已返回第一次的处理结果。",
  RESOURCE_NOT_FOUND: "没有找到符合条件的记录。",
  FORBIDDEN: "当前账号无权执行该操作。",
  RATE_LIMITED: "操作过于频繁，请稍后再试。",
  PROVIDER_UNAVAILABLE: "服务暂时不可用，请稍后重试。",
  INTERNAL_ERROR: "服务出现异常，请稍后重试。",
};

/** 取错误码对应的中文文案；未知 code 时回落服务端 message。 */
export function describeIdentityError(error: unknown): string {
  if (error instanceof ApiError) {
    return IDENTITY_ERROR_MESSAGE[error.code] ?? error.message;
  }
  if (error instanceof Error) return error.message;
  return "操作失败，请稍后重试。";
}

/** 错误码取用：只在需要分支时使用，不解析 message。 */
export function errorCodeOf(error: unknown): string {
  return error instanceof ApiError ? error.code : "";
}

/**
 * 是否是「需要先做敏感操作重新验证」。
 *
 * 注意：后端 `REAUTH_REQUIRED` 的 HTTP 状态也是 401，因此 `ApiError.needsLogin`
 * 对它同样为真；这里必须先按 code 判定，避免把「重新验证一次」误渲染成「重新登录」。
 */
export function requiresSensitiveReauth(error: unknown): boolean {
  return error instanceof ApiError && (error.code === "REAUTH_REQUIRED" || error.status === 401);
}

// ------------------------------------------------------------------ 公开展示授权（C18）
export interface DisplayAuthorization {
  resource_type: "work" | "media";
  resource_id: string;
  title: string;
  slug: string | null;
  route: string | null;
  client_display_name: string | null;
  display_authorized: boolean;
  published: boolean;
  visible_now: boolean;
  state: "ACTIVE" | "PENDING" | "RETRACTED" | string;
  granted_at: string | null;
  updated_at: string | null;
}

export interface DisplayAuthorizationPage {
  items: DisplayAuthorization[];
  total: number;
}

export interface DisplayRetractInput {
  resource_type: "work" | "media";
  resource_id: string;
  reason: string;
}

export interface DisplayRetractResult {
  resource_type: string;
  resource_id: string;
  display_authorized: boolean;
  already_retracted: boolean;
  prebuild_job_id: string | null;
  effective: string;
}

export async function fetchDisplayAuthorizations(): Promise<DisplayAuthorizationPage> {
  const payload = await api.get<DisplayAuthorizationPage>("/me/display-authorizations");
  return { items: payload.items ?? [], total: payload.total ?? payload.items?.length ?? 0 };
}

/** 撤回公开展示授权：服务端立即生效并登记缓存失效任务。 */
export async function retractDisplayAuthorization(
  input: DisplayRetractInput,
): Promise<DisplayRetractResult> {
  return api.post<DisplayRetractResult>("/me/display-authorizations/retract", input, {
    idempotencyKey: newIdempotencyKey(),
  });
}

/** 真正的登录失效（排除只需敏感重验证的情况）。 */
export function isSessionExpired(error: unknown): boolean {
  return error instanceof ApiError && error.needsLogin && error.code !== "REAUTH_REQUIRED";
}

// ------------------------------------------------------------------ 本地校验

/**
 * 中国大陆手机号：11 位、1 开头，且号段符合已分配的移动前缀。
 * `14x` 只在第三位为 5–9 时算移动号段，避免把「美国 1 + 区号 415」误判成大陆号码。
 * 只做前端提示，服务端（`normalize_phone`）仍是权威。
 */
export const CN_MOBILE_PATTERN = /^1(?:[356789]\d{9}|4[5-9]\d{8})$/;

/** 北美编号计划（NANP）：10 位，区号与局号首位均不能是 0 或 1。 */
export const NANP_PATTERN = /^[2-9]\d{2}[2-9]\d{6}$/;

/** 中国大陆手机号（兼容旧调用）。 */
export function isMainlandPhone(value: string): boolean {
  return CN_MOBILE_PATTERN.test(value.trim());
}

/** 去掉空格、连字符、括号与 `+`，用于本地格式判断。 */
export function phoneDigits(value: string): string {
  return value.replace(/[\s\-()]/g, "").replace(/^\+/, "");
}

/**
 * 本地手机号校验（C02/C24：首发中国大陆 + 美国）。
 * 返回空字符串表示通过，否则返回可直接展示的原因。
 */
export function phoneFormatError(value: string): string {
  const digits = phoneDigits(value);
  if (digits === "") return "请填写手机号。";
  if (!/^\d+$/.test(digits)) return "手机号只能包含数字。";
  if (digits.startsWith("86") && CN_MOBILE_PATTERN.test(digits.slice(2))) return "";
  if (digits.startsWith("1") && NANP_PATTERN.test(digits.slice(1))) return "";
  if (CN_MOBILE_PATTERN.test(digits)) return "";
  if (NANP_PATTERN.test(digits)) return "";
  return "手机号格式不正确：中国大陆号码为 1 开头的 11 位数字，美国号码为 10 位数字（可带 +1）。";
}

/** 6 位数字验证码：保留前导零，因此用字符串而不是数字。 */
export function isSixDigitCode(value: string): boolean {
  return /^\d{6}$/.test(value);
}

/**
 * 区号只影响本地校验与拼装，服务端按 E.164 归一化。
 *
 * 用户把国家码也填进号码里时（例如选了 +1 又输入 `14155552671`）不重复拼接，
 * 否则会得到 `+114155552671` 这种既不是美国也不是大陆的号码。
 */
export function normalizePhoneForSubmit(dialCode: string, nationalNumber: string): string {
  const digits = nationalNumber.replace(/[\s\-()]/g, "").replace(/^\+/, "");
  const prefix = dialCode.startsWith("+") ? dialCode.slice(1) : dialCode;
  if (!/^\d+$/.test(prefix)) return digits;
  const duplicated =
    digits.startsWith(prefix) && digits.length > prefix.length + 5
      ? digits.slice(prefix.length)
      : digits;
  return `+${prefix}${duplicated}`;
}

/**
 * 返回目标白名单：只允许本站相对路径。
 * 与后端 `safe_return_path` 同一规则，拒绝 `//evil.example`、`/\`、反斜杠、
 * 绝对 URL 与 `/%2f`、`/%5c` 这类编码绕过（10 文档）。
 */
export function safeReturnPath(candidate: unknown, fallback = "/account"): string {
  if (typeof candidate !== "string") return fallback;
  const value = candidate.trim();
  if (!value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return fallback;
  if (value.includes("\\") || value.includes("://")) return fallback;
  if (Array.from(value).some((char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127)) return fallback;
  const lowered = value.toLowerCase();
  if (lowered.startsWith("/%2f") || lowered.startsWith("/%5c")) return fallback;
  return value;
}

// ------------------------------------------------------------------ DTO

export interface AddressPayload {
  label?: string;
  recipient: string;
  phone: string;
  region_code?: string;
  province?: string;
  city?: string;
  detail: string;
  postal_code?: string | null;
  is_default?: boolean;
}

export interface AddressPatchPayload extends Partial<AddressPayload> {
  version: number;
}

export interface Address {
  id: string;
  label: string;
  recipient: string;
  /** 服务端只返回脱敏手机号；编辑时请重新录入完整号码。 */
  phone_masked: string;
  region_code: string;
  province: string;
  city: string;
  detail: string;
  postal_code: string | null;
  is_default: boolean;
  version: number;
}

export interface AddressListResponse {
  items: Address[];
}

export interface UserSession {
  id: string;
  scope: string;
  user_agent: string | null;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
}

export interface PhoneChangeChallenge {
  challenge_id: string;
  expires_in: number;
  retry_after: number;
  dev_hint?: string | null;
}

export interface PhoneChangeConfirmPayload {
  old_challenge_id?: string;
  old_code?: string;
  new_challenge_id: string;
  new_code: string;
  manual_appeal?: boolean;
  evidence_note?: string;
}

export interface ClosureRequestPayload {
  reason?: string;
  challenge_id: string;
  code: string;
  acknowledged_blockers: boolean;
}

export interface ClosureResult {
  status: ClosureStatus;
  blockers: string[];
  scheduled_for: string | null;
  message: string;
}

export interface ConsentRecord {
  id: string;
  purpose: ConsentPurpose | string;
  document_version: string;
  accepted_at: string | null;
  revoked_at: string | null;
}

export interface ConsentInput {
  purpose: ConsentPurpose;
  document_version: string;
  accepted: boolean;
  resource_type?: string;
  resource_id?: string;
}

/** 登录页勾选协议时上报的文档版本。 */
export const DEFAULT_PRIVACY_VERSION = "privacy-1";

// ------------------------------------------------------------------ 账户只读

/** 兼容两种返回形状：`{items:[...]}` 或裸数组。 */
function toAddressList(payload: unknown): Address[] {
  if (Array.isArray(payload)) return payload as Address[];
  const items = (payload as AddressListResponse | null)?.items;
  return Array.isArray(items) ? items : [];
}

export async function fetchMySessions(signal?: AbortSignal): Promise<UserSession[]> {
  return api.get<UserSession[]>("/me/sessions", { signal });
}

export async function fetchAddresses(signal?: AbortSignal): Promise<Address[]> {
  const payload = await api.get<unknown>("/me/addresses", { signal });
  return toAddressList(payload);
}

export async function fetchConsents(signal?: AbortSignal): Promise<ConsentRecord[]> {
  return api.get<ConsentRecord[]>("/me/consents", { signal });
}

// ------------------------------------------------------------------ 会话与敏感动作

export async function revokeSession(sessionId: string): Promise<void> {
  await api.delete<void>(`/me/sessions/${encodeURIComponent(sessionId)}`);
}

/** 敏感动作前的短信重新验证（10 文档：最近 10 分钟重新验证）。 */
export async function requestSensitiveActionChallenge(phone: string): Promise<PhoneChangeChallenge> {
  return api.post<PhoneChangeChallenge>("/auth/sms/challenges", {
    phone,
    purpose: "sensitive_action",
  });
}

export interface SensitiveVerifyResult {
  sensitive_verified: boolean;
}

/**
 * 敏感操作重新验证：提交短信验证码，服务端核验通过后才把会话标记为「最近已验证」。
 *
 * 这里把 `challenge_id` 与 `code` 一并提交，验证码校验必须在服务端完成；
 * 如果服务端未消费验证码就标记会话（或接口不接受这两个字段），调用会失败，
 * 于是敏感动作保持不可用——宁可挡住用户，也不在前端伪造一次身份验证。
 */
export async function verifySensitiveChallenge(
  challengeId: string,
  code: string,
): Promise<SensitiveVerifyResult> {
  return api.post<SensitiveVerifyResult>("/me/sensitive-verify", {
    challenge_id: challengeId,
    code,
  });
}

// ------------------------------------------------------------------ 换绑手机号

export async function requestPhoneChangeChallenge(
  role: PhoneChangeRole,
  phone?: string,
): Promise<PhoneChangeChallenge> {
  return api.post<PhoneChangeChallenge>("/me/phone-change/challenges", { role, phone });
}

export async function confirmPhoneChange(payload: PhoneChangeConfirmPayload): Promise<void> {
  await api.post("/me/phone-change/confirm", payload, { idempotencyKey: newIdempotencyKey() });
}

// ------------------------------------------------------------------ 注销申请

export async function requestAccountClosure(payload: ClosureRequestPayload): Promise<ClosureResult> {
  return api.post<ClosureResult>("/me/closure-requests", payload, {
    idempotencyKey: newIdempotencyKey(),
  });
}

// ------------------------------------------------------------------ 同意记录

export async function recordConsent(payload: ConsentInput): Promise<void> {
  await api.post("/me/consents", payload);
}

// ------------------------------------------------------------------ 地址

export async function createAddress(payload: AddressPayload): Promise<Address> {
  return api.post<Address>("/me/addresses", payload, { idempotencyKey: newIdempotencyKey() });
}

export async function updateAddress(id: string, payload: AddressPatchPayload): Promise<Address> {
  return api.patch<Address>(`/me/addresses/${encodeURIComponent(id)}`, payload, {
    version: payload.version,
  });
}

export async function deleteAddress(id: string): Promise<void> {
  await api.delete<void>(`/me/addresses/${encodeURIComponent(id)}`);
}
