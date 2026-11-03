/**
 * 格式化与业务计算工具。
 *
 * 金额一律以整数分在前后端之间传递；这里的格式化只用于显示。
 * 星绒币（积分）的显示统一带上单位文字（11 文档：每个图标同时显示积分数值与文字单位）。
 */

export type CurrencyCode = "CNY" | "USD";

const CURRENCY_SYMBOL: Record<string, string> = { CNY: "¥", USD: "$" };

export function formatMoney(cents: number | null | undefined, currency: CurrencyCode = "CNY"): string {
  if (cents === null || cents === undefined) return "—";
  const symbol = CURRENCY_SYMBOL[currency] ?? "";
  const value = cents / 100;
  const formatted = value.toLocaleString(currency === "CNY" ? "zh-CN" : "en-US", {
    minimumFractionDigits: Number.isInteger(value) ? 0 : 2,
    maximumFractionDigits: 2,
  });
  return `${symbol}${formatted}`;
}

export function formatMoneyExact(cents: number | null | undefined, currency: CurrencyCode = "CNY"): string {
  if (cents === null || cents === undefined) return "—";
  const symbol = CURRENCY_SYMBOL[currency] ?? "";
  return `${symbol}${(cents / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

export function formatPoints(points: number | null | undefined): string {
  if (points === null || points === undefined) return "—";
  return points.toLocaleString("zh-CN");
}

export function pointsWithUnit(points: number | null | undefined): string {
  return `${formatPoints(points)} 星绒币`;
}

export function formatDate(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
}

export function formatDateTime(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** 相对时间；用于通知与动态列表。 */
export function formatRelative(value: string | number | Date | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const diff = Date.now() - date.getTime();
  const minutes = Math.floor(diff / 60_000);
  if (minutes < 1) return "刚刚";
  if (minutes < 60) return `${minutes} 分钟前`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} 小时前`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days} 天前`;
  return formatDate(date);
}

/** 倒计时文本。付款到期、报价有效期、名额预留共用。 */
export function formatCountdown(deadline: string | number | null | undefined, now = Date.now(), locale: "zh-CN" | "en-US" = "zh-CN"): string {
  if (deadline === null || deadline === undefined || deadline === "") return "—";
  const target = typeof deadline === "number" ? deadline : new Date(deadline).getTime();
  if (Number.isNaN(target)) return "—";
  let remaining = Math.floor((target - now) / 1000);
  if (remaining <= 0) return locale === "en-US" ? "Expired" : "已到期";
  const days = Math.floor(remaining / 86400);
  remaining -= days * 86400;
  const hours = Math.floor(remaining / 3600);
  remaining -= hours * 3600;
  const minutes = Math.floor(remaining / 60);
  const seconds = remaining - minutes * 60;
  if (locale === "en-US") {
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
  }
  if (days > 0) return `${days} 天 ${hours} 小时`;
  if (hours > 0) return `${hours} 小时 ${minutes} 分`;
  return `${minutes} 分 ${String(seconds).padStart(2, "0")} 秒`;
}

export function isExpired(deadline: string | number | null | undefined, now = Date.now()): boolean {
  if (deadline === null || deadline === undefined || deadline === "") return false;
  const target = typeof deadline === "number" ? deadline : new Date(deadline).getTime();
  return !Number.isNaN(target) && target <= now;
}

/**
 * 星绒币目标值（仅用于前端预估展示）。
 * 权威计算在服务端：eligible_net_cents / (denominator_yuan * 100)，向下取整，运费不计。
 */
export function estimatePoints(eligibleNetCents: number, denominatorYuan = 5): number {
  if (eligibleNetCents <= 0 || denominatorYuan <= 0) return 0;
  return Math.floor(eligibleNetCents / (denominatorYuan * 100));
}

/** 价格区间展示：起价必须标注包含项与不含项，不承诺未核准交期。 */
export function formatPriceFrom(cents: number | null | undefined, currency: CurrencyCode = "CNY"): string {
  if (cents === null || cents === undefined) return "待报价";
  return `${formatMoney(cents, currency)} 起`;
}

export function percentOf(cents: number, totalCents: number): number {
  if (totalCents <= 0) return 0;
  return Math.round((cents / totalCents) * 1000) / 10;
}

export function truncate(value: string, limit: number): string {
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}…`;
}
