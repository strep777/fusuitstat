/**
 * 星绒币（积分）API 客户端。
 *
 * 规则源：文档/09_积分与反馈功能规格（账本与显示、购买奖励、申诉）。
 *
 * 边界：
 * - 不实现充值、提现、转账、投资收益，也不以积分替代现金退款。
 * - 兑换/抵扣（C15）未确认前关闭入口，不宣传未上线权益。
 * - 余额一律以服务端 `/points/account` 为权威；前端不做加总。
 */

import { api } from "@/shared/lib/http";

export interface PointsAccount {
  /** Σ point_ledger.delta；退款扣回可以让账面为负。 */
  posted_balance: number;
  reserved_points: number;
  available_points: number;
  /** 尚未入账的奖励，不能消费。 */
  pending_points: number;
  rules_version: string;
}

export async function fetchPointsAccount(): Promise<PointsAccount> {
  return api.get<PointsAccount>("/points/account");
}

export type LedgerReason =
  | "purchase_award"
  | "feedback_award"
  | "refund_reversal"
  | "adjustment"
  | "appeal_reversal"
  | string;

export interface PointsLedgerEntry {
  id: string;
  /** 正数为获得，负数为扣回；展示必须带符号与单位。 */
  delta: number;
  /** 业务中文描述，例如“订单 ST20260001 成交奖励”。 */
  reason_label: string;
  reason: LedgerReason;
  /** 来源摘要：订单号或反馈编号；不含他人反馈内容。 */
  source_summary: string | null;
  /** 关联被冲正的流水（纠错追加调整或冲正，不修改已发布流水）。 */
  related_entry_id: string | null;
  /** 关联的申诉入口（若该笔已被申诉或可申诉）。 */
  appeal_id: string | null;
  occurred_at: string;
  /** 入账时间（UTC 存储）；未入账条目的 posted_at 为空。 */
  posted_at: string | null;
}

export interface PointsLedgerPage {
  items: PointsLedgerEntry[];
  total: number;
  page: number;
  limit: number;
}

export interface PointsLedgerQuery {
  page?: number;
  limit?: number;
}

export async function fetchPointsLedger(query: PointsLedgerQuery = {}): Promise<PointsLedgerPage> {
  const payload = await api.get<PointsLedgerPage>("/points/ledger", {
    query: { page: query.page ?? 1, limit: query.limit ?? 20 },
  });
  return {
    items: payload.items ?? [],
    total: payload.total ?? payload.items?.length ?? 0,
    page: payload.page ?? query.page ?? 1,
    limit: payload.limit ?? query.limit ?? 20,
  };
}

export type AppealStatus = "SUBMITTED" | "IN_REVIEW" | "ACCEPTED" | "REJECTED" | string;

export const APPEAL_STATUS_LABEL: Record<string, string> = {
  SUBMITTED: "已提交",
  IN_REVIEW: "审核中",
  ACCEPTED: "已受理",
  REJECTED: "未通过",
};

export interface PointsAppeal {
  id: string;
  ledger_entry_id: string | null;
  reason: string;
  status: AppealStatus;
  resolution_note: string | null;
  created_at: string;
  resolved_at: string | null;
}

export interface PointsAppealInput {
  /** 针对具体流水申诉时填写；对规则或整体账目有疑问可留空。 */
  ledger_entry_id?: string | null;
  reason: string;
  contact_preference?: string | null;
}

export async function createPointsAppeal(input: PointsAppealInput): Promise<PointsAppeal> {
  return api.post<PointsAppeal>("/points/appeals", input);
}

export interface PointsAppealList {
  items: PointsAppeal[];
  total: number;
}

export async function fetchPointsAppeals(): Promise<PointsAppealList> {
  const payload = await api.get<PointsAppealList>("/points/appeals");
  return { items: payload.items ?? [], total: payload.total ?? payload.items?.length ?? 0 };
}

/** 积分用途说明：未开放兑换时必须直说，不能宣传未上线权益。 */
export const POINTS_USAGE_NOTICE =
  "星绒币用于记录你在本站的消费与有效反馈，可在未来的兑换或抵扣中使用；兑换功能开放前不产生任何可兑换权益。";

export const POINTS_PROHIBITION_NOTICE =
  "星绒币不支持充值、提现、转账，也不能用来替代现金退款。负账面余额不要求你支付现金，也不影响依法退款。";

/** 反馈奖励等级 → 数值；数值来自服务端配置 `loyalty.rules.feedback_rewards`。 */
export function feedbackRewardRows(rewards: { minor: number; normal: number; major: number }) {
  return [
    { key: "minor", label: "内容纠错或具体可用的建议", points: rewards.minor },
    { key: "normal", label: "有效且可复现的功能缺陷", points: rewards.normal },
    { key: "major", label: "经确认的严重缺陷或高价值改进", points: rewards.major },
  ];
}
