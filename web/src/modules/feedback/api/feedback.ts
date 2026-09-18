/**
 * 反馈 API 客户端与输入校验。
 *
 * 规则源：文档/09_积分与反馈功能规格（反馈处理、审核标准）、
 * 文档/06_API接口契约（反馈输入与响应）。
 *
 * 边界：
 * - 提交响应只给 `reward_status: PENDING_REVIEW`，页面不显示确定积分。
 * - `page_path` 只保留本站路径，并去掉 query／hash 中的敏感值；不存 token、手机号、订单号。
 * - 不要求用户删除差评、给好评或说正面内容；负面反馈不因立场被拒。
 */

import { api } from "@/shared/lib/http";

export type FeedbackCategory =
  | "bug"
  | "experience"
  | "suggestion"
  | "correction"
  | "after_sales"
  | "other";

export const FEEDBACK_CATEGORIES: Array<{
  value: FeedbackCategory;
  label: string;
  hint: string;
}> = [
  { value: "bug", label: "功能问题", hint: "页面报错、按钮无响应、数据不正确" },
  { value: "experience", label: "使用体验", hint: "流程绕、提示不清楚、手机端难用" },
  { value: "suggestion", label: "产品建议", hint: "希望增加或调整的功能" },
  { value: "correction", label: "内容纠错", hint: "文案、参数、作品署名有误" },
  { value: "after_sales", label: "售后相关", hint: "涉及具体订单的售后会转到售后单处理" },
  { value: "other", label: "其他", hint: "不属于以上分类的问题" },
];

export const FEEDBACK_CATEGORY_LABEL: Record<string, string> = Object.fromEntries(
  FEEDBACK_CATEGORIES.map((item) => [item.value, item.label]),
);

export type FeedbackStatus =
  | "SUBMITTED"
  | "TRIAGED"
  | "NEEDS_INFO"
  | "IN_PROGRESS"
  | "RESOLVED"
  | "CLOSED"
  | "DUPLICATE"
  | "REJECTED";

export const FEEDBACK_STATUS_STEPS: Array<{ value: FeedbackStatus; label: string }> = [
  { value: "SUBMITTED", label: "已提交" },
  { value: "TRIAGED", label: "已受理" },
  { value: "NEEDS_INFO", label: "待补充" },
  { value: "IN_PROGRESS", label: "处理中" },
  { value: "RESOLVED", label: "已解决" },
  { value: "CLOSED", label: "已关闭" },
];

export const FEEDBACK_STATUS_LABEL: Record<string, string> = {
  SUBMITTED: "已提交",
  TRIAGED: "已受理",
  NEEDS_INFO: "待补充",
  IN_PROGRESS: "处理中",
  RESOLVED: "已解决",
  CLOSED: "已关闭",
  DUPLICATE: "重复反馈",
  REJECTED: "未采纳",
};

export type RewardStatus = "PENDING_REVIEW" | "APPROVED" | "GRANTED" | "NOT_ELIGIBLE" | "REVOKED";

export const REWARD_STATUS_LABEL: Record<string, string> = {
  PENDING_REVIEW: "待审核",
  APPROVED: "审核通过，等待入账",
  GRANTED: "已入账",
  NOT_ELIGIBLE: "本次不奖励",
  REVOKED: "已撤销",
};

export interface FeedbackMessage {
  id: string;
  author: "user" | "staff" | string;
  body: string;
  created_at: string;
  asset_ids: string[];
}

export interface FeedbackSummary {
  id: string;
  category: FeedbackCategory | string;
  title: string;
  status: FeedbackStatus | string;
  reward_status: RewardStatus | string;
  page_path: string;
  created_at: string;
  updated_at: string;
  /** 重复反馈关联原反馈的匿名摘要，不公开原提交者。 */
  duplicate_of_summary: string | null;
}

export interface FeedbackDetail extends FeedbackSummary {
  body: string;
  steps: string | null;
  asset_ids: string[];
  messages: FeedbackMessage[];
  /** 奖励或不奖励的理由，必须用户可读。 */
  reward_reason: string | null;
  /** 不采纳/重复的理由。 */
  resolution_note: string | null;
  appeals: FeedbackAppeal[];
}

export interface FeedbackAppeal {
  id: string;
  reason: string;
  status: string;
  created_at: string;
  resolution_note: string | null;
}

export interface FeedbackInput {
  category: FeedbackCategory;
  title: string;
  body: string;
  page_path: string;
  steps: string | null;
  asset_ids: string[];
}

export interface FeedbackCreateResult {
  id: string;
  status: "SUBMITTED";
  reward_status: "PENDING_REVIEW";
}

export async function createFeedback(
  input: FeedbackInput,
  idempotencyKey?: string,
): Promise<FeedbackCreateResult> {
  return api.post<FeedbackCreateResult>("/feedback", input, { idempotencyKey });
}

export interface FeedbackListQuery {
  status?: string;
  page?: number;
  limit?: number;
}

export interface FeedbackList {
  items: FeedbackSummary[];
  total: number;
  page: number;
  limit: number;
}

export async function fetchFeedbackList(query: FeedbackListQuery = {}): Promise<FeedbackList> {
  const payload = await api.get<FeedbackList>("/feedback", {
    query: { status: query.status || undefined, page: query.page ?? 1, limit: query.limit ?? 20 },
  });
  return {
    items: payload.items ?? [],
    total: payload.total ?? payload.items?.length ?? 0,
    page: payload.page ?? query.page ?? 1,
    limit: payload.limit ?? query.limit ?? 20,
  };
}

export async function fetchFeedback(id: string): Promise<FeedbackDetail> {
  const payload = await api.get<FeedbackDetail>(`/feedback/${id}`);
  return {
    ...payload,
    asset_ids: payload.asset_ids ?? [],
    messages: payload.messages ?? [],
    appeals: payload.appeals ?? [],
  };
}

export async function postFeedbackMessage(
  id: string,
  input: { body: string; asset_ids?: string[] },
): Promise<FeedbackMessage> {
  return api.post<FeedbackMessage>(`/feedback/${id}/messages`, input);
}

export async function createFeedbackAppeal(id: string, reason: string): Promise<FeedbackAppeal> {
  return api.post<FeedbackAppeal>(`/feedback/${id}/appeals`, { reason });
}

/* ------------------------------------------------------------------
   校验
   ------------------------------------------------------------------ */

export const FEEDBACK_LIMITS = {
  title: { min: 1, max: 100 },
  body: { min: 10, max: 5000 },
  assets: { min: 0, max: 3 },
  steps: { max: 2000 },
} as const;

export type FeedbackFieldErrors = Partial<Record<keyof FeedbackInput, string>>;

export function validateFeedbackField(field: keyof FeedbackInput, draft: FeedbackInput): string {
  switch (field) {
    case "category":
      return FEEDBACK_CATEGORIES.some((item) => item.value === draft.category)
        ? ""
        : "请选择反馈分类。";
    case "title": {
      const value = draft.title.trim();
      if (value.length < FEEDBACK_LIMITS.title.min) return "请填写标题，1–100 字。";
      if (value.length > FEEDBACK_LIMITS.title.max) return "标题最多 100 字。";
      return "";
    }
    case "body": {
      const value = draft.body.trim();
      if (value.length < FEEDBACK_LIMITS.body.min) return "请写清楚问题或建议，至少 10 字。";
      if (value.length > FEEDBACK_LIMITS.body.max) return "内容最多 5000 字，请精简后再提交。";
      return "";
    }
    case "page_path":
      // 页面路径只允许本站相对路径；去 query/hash 在写入前完成。
      return draft.page_path.startsWith("/") ? "" : "页面路径只能是本站以 / 开头的路径。";
    case "steps":
      return (draft.steps ?? "").length > FEEDBACK_LIMITS.steps.max
        ? "复现步骤最多 2000 字。"
        : "";
    case "asset_ids":
      return draft.asset_ids.length > FEEDBACK_LIMITS.assets.max
        ? "附件最多 3 张，请先移除多余的文件。"
        : "";
    default:
      return "";
  }
}

export function validateFeedbackDraft(draft: FeedbackInput): FeedbackFieldErrors {
  const errors: FeedbackFieldErrors = {};
  for (const field of ["category", "title", "body", "page_path", "steps", "asset_ids"] as const) {
    const message = validateFeedbackField(field, draft);
    if (message) errors[field] = message;
  }
  return errors;
}

export function hasFeedbackErrors(errors: FeedbackFieldErrors): boolean {
  return Object.values(errors).some((value) => Boolean(value));
}

/* ------------------------------------------------------------------
   页面路径清洗
   ------------------------------------------------------------------ */

/** 明确属于敏感参数的键名（小写子串匹配）。 */
const SENSITIVE_KEYWORDS = [
  "token",
  "code",
  "session",
  "signature",
  "sign",
  "auth",
  "key",
  "secret",
  "password",
  "passwd",
  "phone",
  "mobile",
  "tel",
  "email",
  "invite",
  "coupon",
  "order",
  "payment",
  "idempotency",
  "nonce",
];

/**
 * 值本身像敏感数据时整段丢弃：
 * 长十六进制/Base64 串（token、签名）、手机号、邮箱、长数字 ID。
 */
const SENSITIVE_VALUE_PATTERNS: RegExp[] = [
  /^[0-9a-f]{16,}$/i,
  /^[A-Za-z0-9_-]{24,}$/,
  /^\+?\d[\d\s-]{6,}$/,
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/,
];

function isSensitiveKey(key: string): boolean {
  const lower = key.toLowerCase();
  return SENSITIVE_KEYWORDS.some((keyword) => lower.includes(keyword));
}

function isSensitiveValue(value: string): boolean {
  return SENSITIVE_VALUE_PATTERNS.some((pattern) => pattern.test(value));
}

/**
 * 把任意地址收敛成可保存的页面路径：
 * 去源站、去 query 与 hash 中的敏感值、去掉地址中的凭据。
 * 保留普通筛选参数（如 `?status=QUOTED`），便于定位问题。
 */
export function sanitizePagePath(input: string): string {
  const raw = (input ?? "").trim();
  if (!raw) return "";
  if (!raw.startsWith("/")) return "";
  // 去掉 #fragment（登录回跳可能带 token）。
  const withoutHash = raw.split("#")[0] ?? "";
  const queryIndex = withoutHash.indexOf("?");
  const path = queryIndex === -1 ? withoutHash : withoutHash.slice(0, queryIndex);
  if (queryIndex === -1) return path;

  const search = withoutHash.slice(queryIndex + 1);
  const kept: string[] = [];
  for (const pair of search.split("&")) {
    if (!pair) continue;
    const separator = pair.indexOf("=");
    const key = separator === -1 ? pair : pair.slice(0, separator);
    const value = separator === -1 ? "" : pair.slice(separator + 1);
    if (isSensitiveKey(key)) continue;
    let decoded = value;
    try {
      decoded = decodeURIComponent(value.replace(/\+/g, " "));
    } catch {
      decoded = value;
    }
    if (decoded && isSensitiveValue(decoded)) continue;
    kept.push(value ? `${key}=${value}` : key);
  }
  return kept.length > 0 ? `${path}?${kept.join("&")}` : path;
}

/** 从当前地址读取页面路径；用于表单默认值。 */
export function currentPagePath(location: { pathname: string; search: string; hash?: string }): string {
  return sanitizePagePath(`${location.pathname}${location.search}${location.hash ?? ""}`);
}

export const FEEDBACK_PRIVACY_NOTICE =
  "只填写复现问题需要的信息。请勿在标题、内容或附件里放身份证件、银行卡、完整住址或他人隐私；附件最多 3 张。";

export const FEEDBACK_FAIRNESS_NOTICE =
  "负面反馈不会因为立场被拒绝，我们也不会要求你删除差评、给好评或只写正面内容。奖励只与反馈是否有效、可复现、可改进有关。";
