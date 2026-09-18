/**
 * 目录模块的类型、API 客户端与共享展示元数据。
 *
 * 约束：
 * - 路径与 06 文档一致，全部经 `@/shared/lib/http` 的 api 客户端：写操作自动带 CSRF，错误形状统一，
 *   前端只按 `error.code` 分支。
 * - 列表返回形状复用内容模块的 `Page<T>`，同一资源不出现两套分页类型。
 * - 金额一律整数分。价格、库存与可售性最终由服务端在下单时校验；前端只展示 `available`、
 *   `in_stock`、`availability` 等由服务端给出的字段，不自行判定可否购买（03/08 文档）。
 * - 六个目录页面只被允许落在 catalog 模块内，因此状态文案、图片属性、页面 meta 与 Product
 *   结构化数据等共用展示元数据集中在本文件，避免每个页面各抄一份。
 */

import { api } from "@/shared/lib/http";
import type { CurrencyCode } from "@/shared/lib/format";
import type { BadgeTone } from "@/shared/ui/base";
import type { MediaImage, Page, SeoMeta } from "@/modules/content/api/content";

// ---- 系列：内容模块已实现同一组接口与类型，这里直接复用，避免两份定义漂移 ----
export { fetchSeries, fetchSeriesList } from "@/modules/content/api/content";
export type {
  MediaImage,
  Page,
  SeoMeta,
  SeriesDetail,
  SeriesSpec,
} from "@/modules/content/api/content";

/** 币种收敛：未识别的币种一律按人民币展示，不做隐式换算（C02/C24）。 */
export function toCurrency(value: string | null | undefined): CurrencyCode {
  return value === "USD" ? "USD" : "CNY";
}

// ---------------------------------------------------------------------------
// 商品与 SKU
// ---------------------------------------------------------------------------

/** 商品类型（08 文档）：现货/配件/周边为 stock_goods，独占角色、定制单与服务各自独立。 */
export type ProductKind = "stock_goods" | "exclusive_character" | "commission" | "service";

/** 服务端给出的整体库存状态；前端不据此推断具体 SKU 可售。 */
export type ProductAvailability = "available" | "sold_out" | "coming_soon";

export interface Sku {
  id: string;
  sku_code: string;
  label: string;
  attributes: Record<string, string>;
  price_cents: number;
  currency: string;
  compare_at_cents: number | null;
  /** 服务端判定的可售性：为 false 时前端不得加购 */
  available: boolean;
  in_stock: boolean;
  low_stock: boolean;
  /** 每单上限；为空表示未配置上限，仍以服务端校验为准 */
  max_per_order: number | null;
  weight_grams: number | null;
}

export interface ProductSummary {
  id: string;
  slug: string;
  title: string;
  subtitle: string;
  kind: string;
  category: string | null;
  price_from_cents: number | null;
  currency: string;
  availability: string;
  /** 掉落/服务类商品需先提交申请并通过人工审批（C28） */
  requires_approval: boolean;
  images: MediaImage[];
  tags: string[];
}

export interface ProductDetail extends ProductSummary {
  description_html: string | null;
  skus: Sku[];
  /** 运费说明：以服务端与后台配置为准，缺失时不补默认文案 */
  shipping_note: string | null;
  /** 售后说明：区分定制与现货，禁止一概“不退不换”（03 文档） */
  after_sales_note: string | null;
  /** 审批说明：由运营填写，前端不加工 */
  approval_note: string | null;
  updated_at: string | null;
  seo: SeoMeta | null;
}

export type ProductQuery = {
  kind?: string;
  category?: string;
  q?: string;
  page?: number;
  limit?: number;
};

export async function fetchProducts(query: ProductQuery = {}): Promise<Page<ProductSummary>> {
  return api.get<Page<ProductSummary>>("/products", {
    query: {
      kind: query.kind,
      category: query.category,
      q: query.q,
      page: query.page,
      limit: query.limit,
    },
  });
}

export async function fetchProduct(slug: string): Promise<ProductDetail> {
  return api.get<ProductDetail>(`/products/${encodeURIComponent(slug)}`);
}

export interface CatalogOption {
  value: string;
  label: string;
}

/**
 * 商城分类（03 文档：现货/配件/周边；服务按 C28 启用）。
 * 取值同时用于 URL query 与 API 的 `category` 参数，后端按同一份清单实现。
 */
export const PRODUCT_CATEGORY_OPTIONS: CatalogOption[] = [
  { value: "stock", label: "现货" },
  { value: "accessory", label: "配件" },
  { value: "merch", label: "周边" },
  { value: "service", label: "服务" },
];

const PRODUCT_KIND_LABELS: Record<string, string> = {
  stock_goods: "现货商品",
  exclusive_character: "独占角色",
  commission: "定制单",
  service: "服务",
};

export function productCategoryLabel(value: string | null | undefined): string {
  if (!value) return "";
  return PRODUCT_CATEGORY_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

/** 未登记的类型直接显示原始值，不猜含义、不隐藏后台新增类型。 */
export function productKindLabel(value: string | null | undefined): string {
  if (!value) return "";
  return PRODUCT_KIND_LABELS[value] ?? value;
}

export interface AvailabilityMeta {
  label: string;
  tone: BadgeTone;
  /** 与页面徽章同源，保证结构化数据里的库存状态和页面一致 */
  schemaOrg: "InStock" | "OutOfStock" | "PreOrder";
  /** 售罄仍可浏览，但不能加购 */
  purchasable: boolean;
}

export function productAvailabilityMeta(
  availability: string | null | undefined,
): AvailabilityMeta {
  if (availability === "available") {
    return { label: "有货", tone: "jade", schemaOrg: "InStock", purchasable: true };
  }
  if (availability === "sold_out") {
    return { label: "已售罄", tone: "neutral", schemaOrg: "OutOfStock", purchasable: false };
  }
  return { label: "待上架", tone: "neutral", schemaOrg: "PreOrder", purchasable: false };
}

// ---------------------------------------------------------------------------
// 角色（领养池）
// ---------------------------------------------------------------------------

export type CharacterStatus = "available" | "reserved" | "adopted" | "kept";

export interface CharacterSummary {
  id: string;
  slug: string;
  name: string;
  species: string | null;
  designer_credit: string | null;
  /** available/reserved/adopted/kept；未知值按服务端原样处理 */
  status: string;
  /** 独占角色库存固定为 1（C12） */
  exclusive: boolean;
  price_cents: number | null;
  currency: string;
  /** 服务端判定的可购买性；为 false 时前端不加购 */
  purchasable: boolean;
  product_slug: string | null;
  summary?: string | null;
  cover?: MediaImage | null;
  images?: MediaImage[];
  tags?: string[];
}

export interface CharacterDetail extends CharacterSummary {
  story: string | null;
  rights_summary: string | null;
  adoption_note: string | null;
  /** 领养/购买关联的商品；申请表单需要 product_id */
  product_id?: string | null;
}

export type CharacterQuery = {
  status?: string;
  species?: string;
  q?: string;
  page?: number;
  limit?: number;
};

export async function fetchCharacters(
  query: CharacterQuery = {},
): Promise<Page<CharacterSummary>> {
  return api.get<Page<CharacterSummary>>("/characters", {
    query: {
      status: query.status,
      species: query.species,
      q: query.q,
      page: query.page,
      limit: query.limit,
    },
  });
}

export async function fetchCharacter(slug: string): Promise<CharacterDetail> {
  return api.get<CharacterDetail>(`/characters/${encodeURIComponent(slug)}`);
}

export interface CharacterStatusMeta {
  label: string;
  tone: BadgeTone;
  /** 不可购买时的可见原因：不靠颜色单独表达（11 文档） */
  blockedReason: string;
  /** 是否计入“待领养” */
  open: boolean;
}

const CHARACTER_STATUS_META: Record<string, CharacterStatusMeta> = {
  available: {
    label: "待领养",
    tone: "jade",
    blockedReason: "",
    open: true,
  },
  reserved: {
    label: "已保留·未成交",
    tone: "amber",
    blockedReason: "该角色已被保留，保留不等于成交；若保留释放会重新开放。",
    open: false,
  },
  adopted: {
    label: "已领养",
    tone: "neutral",
    blockedReason: "该角色已完成领养并交付，不再重复出售。",
    open: false,
  },
  kept: {
    label: "工作室自留",
    tone: "neutral",
    blockedReason: "该角色由工作室自留，不对外出售。",
    open: false,
  },
};

export function characterStatusMeta(status: string | null | undefined): CharacterStatusMeta {
  if (status && CHARACTER_STATUS_META[status]) return CHARACTER_STATUS_META[status];
  return {
    label: status ? status : "状态待确认",
    tone: "neutral",
    blockedReason: "该角色当前状态以服务端为准，暂时无法购买。",
    open: false,
  };
}

/** 角色筛选选项；`value` 直接进 URL query 与 API 参数。 */
export const CHARACTER_STATUS_OPTIONS: CatalogOption[] = [
  { value: "available", label: "待领养" },
  { value: "reserved", label: "保留中" },
  { value: "adopted", label: "已领养" },
  { value: "kept", label: "工作室自留" },
];

// ---------------------------------------------------------------------------
// 购物车（需登录；服务端是权威源，PUT 为绝对数量）
// ---------------------------------------------------------------------------

export interface CartItem {
  id: string;
  sku_id: string;
  quantity: number;
  product_slug: string | null;
  product_title: string;
  sku_label: string;
  sku_code: string;
  unit_price_cents: number;
  currency: string;
  /** 服务端复检结果：false 时购物车行不可结算，页面需给出说明 */
  available: boolean;
  in_stock: boolean;
  max_per_order: number | null;
  image: MediaImage | null;
  note?: string | null;
}

export interface Cart {
  items: CartItem[];
  currency: string;
  updated_at?: string | null;
}

export async function fetchCart(): Promise<Cart> {
  return api.get<Cart>("/cart");
}

export async function setCartItemQuantity(skuId: string, quantity: number): Promise<Cart> {
  return api.put<Cart>(`/cart/items/${encodeURIComponent(skuId)}`, { quantity });
}

export async function removeCartItem(skuId: string): Promise<Cart> {
  return api.delete<Cart>(`/cart/items/${encodeURIComponent(skuId)}`);
}

// ---------------------------------------------------------------------------
// 掉落与服务申请（需登录；C28：审批通过后才能购买）
// ---------------------------------------------------------------------------

export type StockApplicationKind = "drop" | "service";

export interface StockApplicationInput {
  product_id: string;
  sku_id?: string | null;
  kind: string;
  reason: string;
  measurement_note?: string | null;
}

export interface StockApplication {
  id: string;
  kind: string;
  status: string;
  product_id: string;
  product_slug: string | null;
  product_title: string;
  sku_id: string | null;
  sku_label: string | null;
  reason: string;
  measurement_note: string | null;
  review_note: string | null;
  created_at: string;
  reviewed_at: string | null;
  /** 审批通过后的购买截止时间；有值且未到期才显示剩余购买时间 */
  purchase_expires_at: string | null;
  /** 审批通过列表中的优先级（C28） */
  purchase_priority: number | null;
}

export type StockApplicationQuery = {
  product_id?: string;
  status?: string;
  page?: number;
  limit?: number;
};

export async function submitStockApplication(
  input: StockApplicationInput,
): Promise<StockApplication> {
  return api.post<StockApplication>("/stock-applications", input);
}

export async function fetchStockApplications(
  query: StockApplicationQuery = {},
): Promise<StockApplication[]> {
  // 服务端 `GET /stock-applications` 按 06 文档返回**本人申请的数组**（不是分页对象）。
  // 这里曾经按 `Page<T>` 解析，导致 `payload.items` 为 undefined，
  // 页面在渲染时对 undefined 调 `.filter()` 直接抛错并永久停在加载态。
  return api.get<StockApplication[]>("/stock-applications", {
    query: {
      product_id: query.product_id,
      status: query.status,
      limit: query.limit,
    },
  });
}

export async function fetchStockApplication(id: string): Promise<StockApplication> {
  return api.get<StockApplication>(`/stock-applications/${encodeURIComponent(id)}`);
}

export const STOCK_APPLICATION_KIND_OPTIONS: CatalogOption[] = [
  { value: "drop", label: "掉落商品" },
  { value: "service", label: "服务" },
];

export function stockApplicationKindLabel(value: string | null | undefined): string {
  if (!value) return "";
  return STOCK_APPLICATION_KIND_OPTIONS.find((option) => option.value === value)?.label ?? value;
}

export interface StockApplicationStatusMeta {
  label: string;
  tone: BadgeTone;
  /** 用户可读的下一步说明，不承诺未核准结果 */
  description: string;
}

const STOCK_APPLICATION_STATUS_META: Record<string, StockApplicationStatusMeta> = {
  pending: {
    label: "待人工审批",
    tone: "amber",
    description: "申请已提交，工作人员会逐条人工审批；结果以站内通知与状态为准。",
  },
  approved: {
    label: "审批已通过",
    tone: "jade",
    description: "可在购买时间内按审批通过列表购买；过时需重新申请。",
  },
  rejected: {
    label: "未通过",
    tone: "wine",
    description: "本次申请未通过，理由见审核说明；可以补充信息后重新申请。",
  },
  withdrawn: {
    label: "已撤回",
    tone: "neutral",
    description: "申请已由你撤回，不再进入审批队列。",
  },
  expired: {
    label: "已过期",
    tone: "neutral",
    description: "购买时间已过，库存回到掉落池；可以重新提交申请。",
  },
};

export function stockApplicationStatusMeta(
  status: string | null | undefined,
): StockApplicationStatusMeta {
  if (status && STOCK_APPLICATION_STATUS_META[status]) {
    return STOCK_APPLICATION_STATUS_META[status];
  }
  return {
    label: status ? status : "状态待确认",
    tone: "neutral",
    description: "状态以服务端返回为准。",
  };
}

// ---------------------------------------------------------------------------
// 规格 schema 渲染（系列）：动态取键值，不硬编码字段是否存在
// ---------------------------------------------------------------------------

export interface SpecEntry {
  key: string;
  label: string;
  value: string;
}

/**
 * 常见规格键的中文label。未登记的键显示原始键名（下划线转空格），
 * 既不隐藏后台新增字段，也不猜测它的业务含义。
 */
const SPEC_KEY_LABELS: Record<string, string> = {
  applicable: "适用场景",
  after_sales: "售后",
  body: "身体",
  cleaning: "清洁",
  cooling: "散热",
  customization: "可定制项",
  delivery: "交付形态",
  delivery_items: "交付内容",
  excludes: "不含",
  eyes: "眼部与视野",
  feet: "脚部",
  fit: "适配体型",
  frame: "支撑结构",
  fur: "毛料",
  hands: "手部",
  head: "头部",
  includes: "包含",
  lead_time: "交期",
  lead_time_note: "交期说明",
  lining: "内衬",
  material: "材质",
  notes: "说明",
  options: "选配",
  packaging: "包装",
  padding: "填充",
  scenario: "适用场景",
  size: "尺寸",
  storage: "存放",
  structure: "结构",
  tail: "尾部",
  ventilation: "透气",
  vision: "视野",
  warranty: "保修",
  weight: "重量",
};

export function specKeyLabel(key: string): string {
  const known = SPEC_KEY_LABELS[key];
  if (known) return known;
  return key.replace(/[_-]+/g, " ").trim();
}

/** 只渲染可确定的标量值；嵌套结构不猜测渲染方式，直接跳过。 */
function specValueText(value: unknown): string | null {
  if (typeof value === "string") return value.trim().length > 0 ? value.trim() : null;
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : null;
  if (typeof value === "boolean") return value ? "是" : "否";
  if (Array.isArray(value)) {
    const parts = value
      .filter((item): item is string | number => typeof item === "string" || typeof item === "number")
      .map((item) => String(item).trim())
      .filter((item) => item.length > 0);
    return parts.length > 0 ? parts.join("、") : null;
  }
  return null;
}

export function specEntries(schema: Record<string, unknown> | null | undefined): SpecEntry[] {
  if (!schema) return [];
  const entries: SpecEntry[] = [];
  for (const [key, value] of Object.entries(schema)) {
    const text = specValueText(value);
    if (text === null) continue;
    entries.push({ key, label: specKeyLabel(key), value: text });
  }
  return entries;
}

/** 规格对比表的行：所有系列出现过的键并集，缺值显示“未提供”。 */
export function specComparison(
  schemas: Array<Record<string, unknown> | null | undefined>,
): { key: string; label: string }[] {
  const seen = new Map<string, string>();
  for (const schema of schemas) {
    for (const entry of specEntries(schema)) {
      if (!seen.has(entry.key)) seen.set(entry.key, entry.label);
    }
  }
  return Array.from(seen, ([key, label]) => ({ key, label }));
}

export function specValueOf(
  schema: Record<string, unknown> | null | undefined,
  key: string,
): string {
  if (!schema) return "";
  const text = specValueText(schema[key]);
  return text ?? "";
}

// ---------------------------------------------------------------------------
// 图片属性（11 文档：固定 width/height、srcset/sizes、alt 描述角色与角度）
// ---------------------------------------------------------------------------

export function mediaSrc(image: MediaImage | null | undefined): string {
  if (!image) return "";
  return image.thumb ?? image.src;
}

export function mediaSrcset(image: MediaImage | null | undefined): string {
  if (!image || !Array.isArray(image.srcset) || image.srcset.length === 0) return "";
  return image.srcset.map((item) => `${item.url} ${item.width}w`).join(", ");
}

/** 列表图统一尺寸提示；详情主图另用更宽的 sizes。 */
export const GRID_IMAGE_SIZES = "(min-width: 1024px) 25vw, (min-width: 640px) 50vw, 100vw";
export const DETAIL_IMAGE_SIZES = "(min-width: 1024px) 50vw, 100vw";

// ---------------------------------------------------------------------------
// 路由 query 读取（筛选条件进 URL，刷新与返回都能恢复）
// ---------------------------------------------------------------------------

export function readQueryString(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return "";
}

export function readQueryInt(value: unknown, fallback: number): number {
  const raw = readQueryString(value);
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * 拼接站内相对地址。空值不写入 query；
 * `AppButton` 的 `to` 只接受字符串，因此站内链接统一用本函数生成。
 */
export function withQuery(
  path: string,
  params: Record<string, string | number | null | undefined>,
): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    search.set(key, String(value));
  }
  const query = search.toString();
  return query ? `${path}?${query}` : path;
}

// ---------------------------------------------------------------------------
// 页面 meta 与 Product 结构化数据
// ---------------------------------------------------------------------------

/** 与 app/router.ts 的 SITE_NAME 保持一致（路由 afterEach 设置的标题格式）。 */
const SITE_NAME = "弈星纪元兽装工作室";

export interface PageMetaInput {
  /** 页面标题，不含站点名 */
  title: string;
  description: string;
  /** 站内相对路径；私密页面只传 path，不带 query */
  canonicalPath: string;
}

export function applyPageMeta(meta: PageMetaInput): void {
  if (typeof document === "undefined") return;
  document.title = `${meta.title} · ${SITE_NAME}`;

  let description = document.head.querySelector<HTMLMetaElement>('meta[name="description"]');
  if (!description) {
    description = document.createElement("meta");
    description.name = "description";
    document.head.appendChild(description);
  }
  description.content = meta.description;

  let canonical = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]');
  if (!canonical) {
    canonical = document.createElement("link");
    canonical.rel = "canonical";
    document.head.appendChild(canonical);
  }
  const origin = typeof location === "undefined" ? "" : location.origin;
  canonical.href = `${origin}${meta.canonicalPath}`;
}

const JSONLD_ATTRIBUTE = "data-catalog-jsonld";

/** 更新 Product 结构化数据；传 null 清除（详情页卸载时调用）。 */
export function setProductJsonLd(data: Record<string, unknown> | null): void {
  if (typeof document === "undefined") return;
  for (const node of Array.from(document.head.querySelectorAll(`script[${JSONLD_ATTRIBUTE}]`))) {
    node.remove();
  }
  if (!data) return;
  const script = document.createElement("script");
  script.type = "application/ld+json";
  script.setAttribute(JSONLD_ATTRIBUTE, "true");
  script.textContent = JSON.stringify(data);
  document.head.appendChild(script);
}

export interface ProductJsonLdInput {
  product: ProductDetail;
  /** 页面展示的价格区间，来自同一份 SKU 数据 */
  priceLowCents: number | null;
  priceHighCents: number | null;
  skuCount: number;
  url: string;
}

/**
 * Product 结构化数据：价格与库存与页面展示同源；
 * 不写 aggregateRating/review（没有真实评价就不虚构，11 文档）。
 */
export function buildProductJsonLd(input: ProductJsonLdInput): Record<string, unknown> {
  const availability = productAvailabilityMeta(input.product.availability);
  const data: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "Product",
    name: input.product.title,
    url: input.url,
    brand: { "@type": "Brand", name: SITE_NAME },
  };

  const description = input.product.subtitle?.trim();
  if (description) data.description = description;

  const origin = typeof location === "undefined" ? "" : location.origin;
  const images = (input.product.images ?? [])
    .map((image) => image.src)
    .filter((src) => typeof src === "string" && src.length > 0)
    .map((src) => (origin ? `${origin}${src.startsWith("/") ? src : `/${src}`}` : src));
  if (images.length > 0) data.image = images;

  if (input.priceLowCents !== null && input.priceLowCents !== undefined) {
    const currency = toCurrency(input.product.currency);
    const low = (input.priceLowCents / 100).toFixed(2);
    const high =
      input.priceHighCents !== null && input.priceHighCents !== undefined
        ? (input.priceHighCents / 100).toFixed(2)
        : low;
    data.offers = {
      "@type": "AggregateOffer",
      priceCurrency: currency,
      lowPrice: low,
      highPrice: high,
      offerCount: input.skuCount,
      availability: `https://schema.org/${availability.schemaOrg}`,
      url: input.url,
    };
  }

  return data;
}
