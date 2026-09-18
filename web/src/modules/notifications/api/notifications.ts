/**
 * 通知模块 API 客户端（FR-017 / 03 文档 `/account/notifications`）。
 *
 * 说明（C27）：站内通知由邮件同步发送，邮件为必选通道；营销邮件默认不订阅，可退订。
 * 通知的 link_path 只作为站内相对路径使用，跳转前再过一次白名单。
 */

import { api } from "@/shared/lib/http";

import { safeReturnPath } from "@/modules/identity/api/identity";

export type NotificationStatus = "UNREAD" | "READ" | "ARCHIVED";

export type NotificationCategory =
  | "transaction"
  | "production"
  | "loyalty"
  | "feedback"
  | "security"
  | "marketing";

export interface NotificationItem {
  id: string;
  event_type: string;
  category: NotificationCategory | string;
  title: string;
  body: string;
  link_path: string | null;
  status: NotificationStatus | string;
  created_at: string;
  read_at: string | null;
}

export interface NotificationPage {
  items: NotificationItem[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
  page: number;
  limit: number;
}

export interface NotificationQuery {
  status?: NotificationStatus;
  category?: NotificationCategory;
  page?: number;
  limit?: number;
}

export const NOTIFICATION_CATEGORY_LABEL: Record<string, string> = {
  transaction: "交易",
  production: "制作",
  loyalty: "星绒币",
  feedback: "反馈",
  security: "安全",
  marketing: "活动",
};

export const NOTIFICATION_CATEGORY_HINT: Record<string, string> = {
  transaction: "订单、付款、退款与发票结果。",
  production: "排期、制作节点与发货通知。",
  loyalty: "星绒币入账、冻结与调整结果。",
  feedback: "反馈受理、回复与奖励结论。",
  security: "登录、换绑与敏感操作提醒。",
  marketing: "活动与新品，默认不订阅，可随时退订。",
};

export function notificationCategoryLabel(category: string): string {
  return NOTIFICATION_CATEGORY_LABEL[category] ?? "其他";
}

/** 通知详情跳转：只接受本站相对路径，拒绝 `//evil.example` 与编码绕过。 */
export function notificationLink(item: Pick<NotificationItem, "link_path">): string | null {
  if (!item.link_path) return null;
  const safe = safeReturnPath(item.link_path, "");
  return safe || null;
}

export async function fetchNotifications(
  query: NotificationQuery = {},
  signal?: AbortSignal,
): Promise<NotificationPage> {
  return api.get<NotificationPage>("/notifications", {
    query: {
      status: query.status,
      category: query.category,
      page: query.page,
      limit: query.limit,
    },
    signal,
  });
}

export async function markNotificationRead(id: string): Promise<void> {
  await api.post<void>(`/notifications/${encodeURIComponent(id)}/read`);
}

export async function markAllNotificationsRead(): Promise<{ updated?: number }> {
  return api.post<{ updated?: number }>("/notifications/read-all");
}
