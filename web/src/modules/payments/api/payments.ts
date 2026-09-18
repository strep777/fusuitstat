/**
 * 支付模块 API 客户端。
 *
 * 规则来源：`文档/08_商城订单支付与退款.md` 与 `文档/06_API接口契约.md`「支付」小节。
 * 约束：
 * - 创建支付只提交 `provider` 与 `scene`，不接收商户号或金额。
 * - 成功状态只能由已验签通知或服务端主动查单确认；返回页与前端回调都不入账。
 * - 查询返回本地权威状态；UNKNOWN/PENDING 必须显示“结果确认中”，不能渲染为已取消。
 */

import { api, newIdempotencyKey } from "@/shared/lib/http";

export type PaymentProvider = "wechat" | "alipay" | "paypal" | "card";

/** 渠道能力随开通情况变化；实际可用性以服务端创建结果为准。 */
export type PaymentScene = "native" | "h5" | "jsapi" | "page" | "redirect";

export type PaymentAttemptStatus =
  | "CREATED"
  | "PENDING"
  | "UNKNOWN"
  | "SUCCEEDED"
  | "CLOSED"
  | "FAILED";

export type PaymentActionType = "qr" | "redirect";

export interface PaymentAction {
  type: PaymentActionType;
  /** 二维码内容或渠道跳转地址；跳转目标由服务端 adapter 白名单校验。 */
  url: string;
}

export interface PaymentAttempt {
  payment_attempt_id: string;
  status: PaymentAttemptStatus;
  action: PaymentAction;
  expires_at: string;
}

export interface PaymentAttemptStatusView {
  id: string;
  status: PaymentAttemptStatus;
  order_id: string;
  /** 服务端查单确认时间；未确认为 null。 */
  verified_at: string | null;
}

export interface CreatePaymentAttemptInput {
  installmentId: string;
  provider: PaymentProvider;
  scene: PaymentScene;
  idempotencyKey: string;
}

export async function createPaymentAttempt(
  input: CreatePaymentAttemptInput,
): Promise<PaymentAttempt> {
  return api.post<PaymentAttempt>(
    `/installments/${encodeURIComponent(input.installmentId)}/payment-attempts`,
    { provider: input.provider, scene: input.scene },
    { idempotencyKey: input.idempotencyKey },
  );
}

/** 只读取服务端权威状态；不触发任何本地判定。 */
export async function fetchPaymentAttempt(
  attemptId: string,
  options: { signal?: AbortSignal } = {},
): Promise<PaymentAttemptStatusView> {
  return api.get<PaymentAttemptStatusView>(`/payment-attempts/${encodeURIComponent(attemptId)}`, {
    signal: options.signal,
  });
}

export function newPaymentIdempotencyKey(): string {
  return newIdempotencyKey();
}

/** 轮询退避序列：初始 2 秒，逐步退避到最大 15 秒（06 文档）。 */
export const PAYMENT_POLL_INITIAL_MS = 2_000;
export const PAYMENT_POLL_MAX_MS = 15_000;
export const PAYMENT_POLL_FACTOR = 1.6;

export function nextPaymentPollDelay(previousMs: number): number {
  const base = previousMs > 0 ? previousMs : PAYMENT_POLL_INITIAL_MS;
  return Math.min(Math.round(base * PAYMENT_POLL_FACTOR), PAYMENT_POLL_MAX_MS);
}

export interface ProviderOption {
  provider: PaymentProvider;
  label: string;
  /** 电脑网页场景；手机与微信内场景由服务端 scene 决定。 */
  scene: PaymentScene;
  description: string;
  actionType: PaymentActionType;
}

/**
 * 展示顺序与说明；可用性不由前端决定。
 * 服务端返回 `PAYMENT_PROVIDER_DISABLED` 时，页面按该渠道不可用处理并说明原因。
 */
export const PAYMENT_PROVIDERS: ProviderOption[] = [
  {
    provider: "wechat",
    label: "微信支付",
    scene: "native",
    description: "电脑网页展示二维码，用微信扫码完成付款。",
    actionType: "qr",
  },
  {
    provider: "alipay",
    label: "支付宝",
    scene: "page",
    description: "跳转到支付宝电脑网站支付页面完成付款。",
    actionType: "redirect",
  },
  {
    provider: "paypal",
    label: "PayPal",
    scene: "redirect",
    description: "跳转到 PayPal 页面完成付款，是否开通以服务端返回为准。",
    actionType: "redirect",
  },
  {
    provider: "card",
    label: "信用卡",
    scene: "redirect",
    description: "跳转到收单机构页面完成付款，是否开通以服务端返回为准。",
    actionType: "redirect",
  },
];

export function providerLabel(provider: string): string {
  const found = PAYMENT_PROVIDERS.find((item) => item.provider === provider);
  return found ? found.label : provider;
}
