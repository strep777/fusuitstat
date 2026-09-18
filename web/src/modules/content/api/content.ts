/**
 * 内容模块 API 客户端。
 * 路径与 06 文档一致；所有公开读取只返回已发布内容。
 */

import { ApiError, api } from "@/shared/lib/http";

export type ContentKind =
  | "page"
  | "work"
  | "news"
  | "series"
  | "faq"
  | "team"
  | "legal"
  | "story"
  | "designer"
  | "event";

export interface SeoMeta {
  title: string;
  description: string;
  canonical: string;
}

export interface MediaImage {
  id: string;
  alt: string;
  width: number;
  height: number;
  src: string;
  thumb: string | null;
  srcset: Array<{ url: string; width: number }>;
}

export interface ContentSummary {
  id: string;
  kind: ContentKind;
  slug: string;
  locale: string;
  title: string;
  summary: string;
  route: string;
  cover_asset_id: string | null;
  cover: MediaImage | null;
  tags: string[];
  species: string | null;
  work_type: string | null;
  published_at: string | null;
  updated_at: string | null;
  is_featured: boolean;
  seo: SeoMeta;
}

export interface ContentDetail extends ContentSummary {
  body_html: string;
  credits?: {
    designer: string | null;
    maker: string | null;
    photographer: string | null;
    client_display_name: string | null;
  };
  craft_notes?: string | null;
  gallery: MediaImage[];
}

export interface Facet {
  value: string;
  count: number;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
  has_more: boolean;
  total: number;
  page: number;
  limit: number;
}

export interface WorkQuery {
  category?: string;
  series?: string;
  species?: string;
  work_type?: string;
  q?: string;
  page?: number;
  limit?: number;
}

export async function fetchContentList(
  kind: ContentKind,
  query: Record<string, string | number | undefined> = {},
): Promise<Page<ContentSummary>> {
  return api.get<Page<ContentSummary>>(`/content/${kind}`, { query });
}

export async function fetchContentDetail(kind: ContentKind, slug: string): Promise<ContentDetail> {
  return api.get<ContentDetail>(`/content/${kind}/${slug}`);
}

export interface SeriesSpec {
  code: string;
  base_price_cents: number;
  currency: "CNY" | "USD";
  price_includes: string[];
  price_excludes: string[];
  lead_time_note: string | null;
  spec_schema: Record<string, unknown>;
  display_order: number;
  is_active: boolean;
}

export interface SeriesDetail extends ContentDetail {
  spec: SeriesSpec | null;
  faqs: ContentSummary[];
}

export async function fetchSeriesList(): Promise<Page<SeriesDetail>> {
  return api.get<Page<SeriesDetail>>("/content/series", { query: { limit: 20 } });
}

export async function fetchSeries(slug: string): Promise<SeriesDetail> {
  return api.get<SeriesDetail>(`/content/series/${slug}`);
}

export interface WorkFacets {
  work_types: Facet[];
  species: Facet[];
  series: Facet[];
}

export async function fetchWorkFacets(): Promise<WorkFacets> {
  return api.get<WorkFacets>("/content/work/facets");
}

export interface CommissionPolicy {
  prepayment_percent: number;
  quote_valid_days: number;
  slot_hold_minutes: number;
  balance_due_days: number;
  free_revision_count: number;
  terms_version: string;
}

export interface LoyaltyRules {
  points_name: string;
  denominator_yuan: number;
  release_delay_days: number;
  shipping_excluded: boolean;
  feedback_rewards: { minor: number; normal: number; major: number };
  feedback_daily_reward_limit: number;
  redemption_enabled: boolean;
  redemption_ratio_note?: string;
}

export interface BrandProfile {
  brand_name: string;
  legal_entity: string;
  domain: string;
  support_email: string;
  service_hours: string;
  response_sla: { inquiry_workdays: number; feedback_workdays: number };
}

export interface SocialLink {
  code: string;
  label: string;
  url: string;
}

export interface CommercePolicy {
  returns_reference: string;
  cross_border_note: string;
  invoice_enabled: boolean;
  shipping_template_note: string;
}

export interface PublicSettings {
  "brand.profile"?: BrandProfile;
  "brand.social_links"?: SocialLink[];
  "commission.policy"?: CommissionPolicy;
  "loyalty.rules"?: LoyaltyRules;
  "commerce.policy"?: CommercePolicy;
  "platform.feature_flags"?: Record<string, boolean>;
}

export interface HomeAggregate {
  hero: {
    brand_name: string;
    tagline: string;
    statement: string;
    primary_action: { label: string; to: string };
    secondary_action: { label: string; to: string };
  };
  schedule: ScheduleSummary | null;
  featured_works: { items: ContentSummary[]; total: number; more: string };
  series: Array<SeriesSpec & ContentSummary>;
  commission_steps: Array<{ step: string; title: string; description: string }>;
  products: {
    items: Array<{
      id: string;
      slug: string;
      title: string;
      subtitle: string;
      kind: string;
      price_from_cents: number | null;
      currency: string;
      availability: string;
      images: MediaImage[];
    }>;
    total: number;
    more: string;
  };
  news: { items: ContentSummary[]; total: number; more: string };
  faq: ContentDetail[];
  settings: PublicSettings;
  features: Record<string, boolean>;
}

export interface ScheduleSummary {
  id: string;
  title: string;
  mode: string;
  status: "open" | "upcoming" | "closed";
  start_at: string;
  end_at: string;
  capacity: number;
  remaining: number;
  timezone: string;
}

export async function fetchHome(): Promise<HomeAggregate> {
  return api.get<HomeAggregate>("/site/home");
}

export async function fetchPublicSettings(): Promise<PublicSettings> {
  return api.get<PublicSettings>("/content/settings");
}

/* ------------------------------------------------------------------
   以下为内容页面前台追加的共享类型与辅助函数（不修改上面任何已有导出）。
   依据：03 文档（过滤条件进 URL、401/403/404 分支）、
   11 文档（公开内容使用独立 title/description/canonical/OG，卸载时不还原）。
   ------------------------------------------------------------------ */

/** 公共内容列表查询：分页与筛选条件全部来自 URL，可刷新恢复。 */
export interface ContentListQuery {
  page?: number;
  limit?: number;
  q?: string;
  species?: string;
  work_type?: string;
  series?: string;
  featured?: boolean;
}

/**
 * 系列筛选维度。
 * `value` 是系列 code（作品的 `tags` 中存放该 code，服务端按它过滤），`label` 是系列标题。
 */
export interface SeriesFacet extends Facet {
  label: string;
}

/** `/content/work/facets` 的前端视图：只有系列维度带可读名称。 */
export type WorkFacetSet = Omit<WorkFacets, "series"> & { series: SeriesFacet[] };

export type PublicErrorKind = "not-found" | "forbidden" | "login" | "error";

export interface PublicErrorState {
  kind: PublicErrorKind;
  code: string;
  message: string;
}

/**
 * 公开读取的错误分支。
 * 404 → not-found（下架内容不公开）、403 → forbidden（不透露对象是否存在）、
 * 401 → 引导登录并保留当前路径；其余按可重试错误处理。
 */
export function classifyPublicError(caught: unknown, fallback = "内容加载失败，请稍后重试。"): PublicErrorState {
  if (caught instanceof ApiError) {
    if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") {
      return { kind: "not-found", code: caught.code, message: "这条内容不存在，或者已经被下架。" };
    }
    if (caught.status === 403 || caught.code === "FORBIDDEN" || caught.code === "PERMISSION_DENIED") {
      return { kind: "forbidden", code: caught.code, message: "当前账号没有查看这条内容的权限。" };
    }
    if (caught.needsLogin) {
      return { kind: "login", code: caught.code, message: "登录后才能继续查看这条内容。" };
    }
    return { kind: "error", code: caught.code, message: caught.message || fallback };
  }
  return { kind: "error", code: "", message: fallback };
}

const SITE_NAME = "弈星纪元兽装工作室";

export interface PageHeadInput {
  /** 页面标题，不含站点名后缀 */
  title: string;
  description?: string;
  /** 公开内容的规范地址；相对路径补当前 origin。不传则不写 canonical，也不写 OG。 */
  canonical?: string;
  /** 传了 ogType 才写 OG：私密页面与错误页不要传 */
  ogType?: "website" | "article";
  ogImage?: string;
  /** 覆盖路由默认的 robots 策略（如 404 使用 noindex, follow） */
  robots?: string;
}

function upsertMeta(attribute: "name" | "property", key: string, content: string): void {
  let element = document.head.querySelector<HTMLMetaElement>(`meta[${attribute}="${key}"]`);
  if (!element) {
    element = document.createElement("meta");
    element.setAttribute(attribute, key);
    document.head.appendChild(element);
  }
  element.setAttribute("content", content);
}

function absoluteUrl(value: string): string {
  if (/^https?:\/\//i.test(value)) return value;
  const origin = typeof location === "undefined" ? "" : location.origin;
  return value.startsWith("/") ? `${origin}${value}` : `${origin}/${value}`;
}

/**
 * 写入页面级 head（相当于极简 useHead）。
 * 公开内容传 canonical + ogType；错误页与维护页只传 robots，不输出 OG。
 * 按需求卸载时不做还原：下一次导航由 router.afterEach 与目标页面接管。
 */
export function applyPageHead(input: PageHeadInput): void {
  document.title = input.title.includes(SITE_NAME) ? input.title : `${input.title} · ${SITE_NAME}`;

  if (input.description) upsertMeta("name", "description", input.description);
  if (input.robots) upsertMeta("name", "robots", input.robots);
  if (!input.canonical) return;

  const href = absoluteUrl(input.canonical);
  let link = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!link) {
    link = document.createElement("link");
    link.rel = "canonical";
    document.head.appendChild(link);
  }
  link.href = href;

  if (!input.ogType) return;
  upsertMeta("property", "og:url", href);
  upsertMeta("property", "og:title", document.title);
  upsertMeta("property", "og:type", input.ogType);
  upsertMeta("property", "og:site_name", SITE_NAME);
  if (input.description) upsertMeta("property", "og:description", input.description);
  if (input.ogImage) upsertMeta("property", "og:image", absoluteUrl(input.ogImage));
}

/** 内容详情页 head：title/description/canonical 来自条目数据（服务端已生成 seo）。 */
export function applyEntryHead(
  entry: Pick<ContentSummary, "seo" | "cover">,
  options: { canonical?: string; ogType?: "website" | "article" } = {},
): void {
  applyPageHead({
    title: entry.seo.title,
    description: entry.seo.description,
    canonical: options.canonical ?? entry.seo.canonical,
    ogType: options.ogType ?? "article",
    ogImage: entry.cover?.src,
  });
}
