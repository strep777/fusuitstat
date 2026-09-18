/**
 * 目录类页面测试：筛选与 URL 同步、SKU 选择与数量上限、售罄不可加购、审批制商品需先申请。
 *
 * 说明：
 * - 通过 stub 全局 fetch 走真实的 `@/shared/lib/http` 客户端，因此同时覆盖了路径、query 拼装、
 *   CSRF 获取与错误形状解析，而不是把 api 模块整个替换掉。
 * - 服务端是价格、库存与可售性的权威源，测试里所有断言都基于服务端返回字段。
 */
import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { createMemoryHistory, createRouter, type Router } from "vue-router";
import { afterEach, describe, expect, it, vi, type Mock } from "vitest";

import SkuPicker from "@/modules/catalog/components/SkuPicker.vue";
import AccountApplicationsPage from "@/modules/catalog/pages/AccountApplicationsPage.vue";
import CharacterDetailPage from "@/modules/catalog/pages/CharacterDetailPage.vue";
import CharactersPage from "@/modules/catalog/pages/CharactersPage.vue";
import ProductDetailPage from "@/modules/catalog/pages/ProductDetailPage.vue";
import SeriesDetailPage from "@/modules/catalog/pages/SeriesDetailPage.vue";
import SeriesListPage from "@/modules/catalog/pages/SeriesListPage.vue";
import ShopPage from "@/modules/catalog/pages/ShopPage.vue";
import type {
  CharacterDetail,
  CharacterSummary,
  MediaImage,
  Page,
  ProductDetail,
  ProductSummary,
  SeriesDetail,
  Sku,
  StockApplication,
} from "@/modules/catalog/api/catalog";

// ---------------------------------------------------------------------------
// 测试替身
// ---------------------------------------------------------------------------

interface StubResult {
  status?: number;
  body?: unknown;
}

type FetchHandler = (url: string, init: RequestInit | undefined) => StubResult;

/** duck-typed Response：只实现 http 客户端实际读取的字段。 */
function fakeResponse(result: StubResult): Response {
  const status = result.status ?? 200;
  const text = JSON.stringify(result.body ?? {});
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    text: async () => text,
  } as unknown as Response;
}

function installFetch(handler: FetchHandler): { calls: string[]; fetchMock: Mock } {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: unknown, init?: RequestInit) => {
    const url = typeof input === "string" ? input : String(input);
    calls.push(url);
    if (url.includes("/session/csrf")) {
      return fakeResponse({ body: { csrf_token: "csrf-test", expires_in: 1800 } });
    }
    return fakeResponse(handler(url, init));
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

function jsonPage<T>(items: T[], extra: Partial<Page<T>> = {}): Page<T> {
  return {
    items,
    next_cursor: null,
    has_more: false,
    total: items.length,
    page: 1,
    limit: 12,
    ...extra,
  };
}

function createTestRouter(): Router {
  const stub = { template: "<div />" };
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: stub },
      { path: "/shop", component: stub },
      { path: "/shop/:slug", component: stub },
      { path: "/characters", component: stub },
      { path: "/characters/:slug", component: stub },
      { path: "/fursuits", component: stub },
      { path: "/fursuits/:slug", component: stub },
      { path: "/commission/new", component: stub },
      { path: "/account/applications", component: stub },
      { path: "/login", component: stub },
      { path: "/contact", component: stub },
    ],
  });
}

async function settle(): Promise<void> {
  await flushPromises();
  await flushPromises();
}

async function mountAt(
  component: Parameters<typeof mount>[0],
  path: string,
): Promise<{ wrapper: VueWrapper; router: Router }> {
  const router = createTestRouter();
  await router.push(path);
  await router.isReady();
  const pinia = createPinia();
  setActivePinia(pinia);
  const wrapper = mount(component, { global: { plugins: [pinia, router] } });
  await settle();
  return { wrapper, router };
}

function byText(wrapper: VueWrapper, selector: string, text: string) {
  const found = wrapper.findAll(selector).find((node) => node.text().includes(text));
  if (!found) throw new Error(`没有找到包含「${text}」的 ${selector}`);
  return found;
}

/** 单独挂载 SKU 选择器：它不依赖路由状态，只需要 pinia 与 RouterLink 的运行环境。 */
interface PickerProps {
  skus: Sku[];
  requiresApproval?: boolean;
  approvalUrl?: string;
  approvalWindowLabel?: string;
  purchasable?: boolean;
  unavailableReason?: string;
  submitting?: boolean;
  addLabel?: string;
}

function mountPicker(props: PickerProps): VueWrapper {
  const pinia = createPinia();
  setActivePinia(pinia);
  return mount(SkuPicker, {
    props,
    global: { plugins: [pinia, createTestRouter()] },
  });
}

function isDisabled(wrapper: VueWrapper, selector: string, text: string): boolean {
  const element = byText(wrapper, selector, text).element as HTMLButtonElement;
  return element.disabled === true || element.getAttribute("aria-disabled") === "true";
}

afterEach(() => {
  vi.unstubAllGlobals();
  document.head.querySelectorAll('script[type="application/ld+json"]').forEach((node) => node.remove());
});

// ---------------------------------------------------------------------------
// 固件
// ---------------------------------------------------------------------------

const IMAGE: MediaImage = {
  id: "img-1",
  alt: "示例商品正面图",
  width: 1200,
  height: 1200,
  src: "/assets/demo/p1.webp",
  thumb: "/assets/demo/p1-thumb.webp",
  srcset: [
    { url: "/assets/demo/p1-640.webp", width: 640 },
    { url: "/assets/demo/p1-1200.webp", width: 1200 },
  ],
};

function makeSku(overrides: Partial<Sku> = {}): Sku {
  return {
    id: "sku-standard",
    sku_code: "FS-STD-M",
    label: "标准版 M",
    attributes: { 版本: "标准版", 尺寸: "M" },
    price_cents: 120000,
    currency: "CNY",
    compare_at_cents: null,
    available: true,
    in_stock: true,
    low_stock: false,
    max_per_order: 3,
    weight_grams: 3200,
    ...overrides,
  };
}

const STANDARD_SKU = makeSku();
const THICK_SKU = makeSku({
  id: "sku-thick",
  sku_code: "FS-THK-M",
  label: "加厚版 M",
  attributes: { 版本: "加厚版", 尺寸: "M" },
  price_cents: 158000,
  max_per_order: 2,
});

function makeProductSummary(overrides: Partial<ProductSummary> = {}): ProductSummary {
  return {
    id: "product-1",
    slug: "fursuit-basic",
    title: "基础款兽装",
    subtitle: "现货，含可拆尾巴",
    kind: "stock_goods",
    category: "stock",
    price_from_cents: 120000,
    currency: "CNY",
    availability: "available",
    requires_approval: false,
    images: [IMAGE],
    tags: [],
    ...overrides,
  };
}

function makeProductDetail(overrides: Partial<ProductDetail> = {}): ProductDetail {
  return {
    ...makeProductSummary(),
    description_html: null,
    skus: [STANDARD_SKU, THICK_SKU],
    shipping_note: null,
    after_sales_note: null,
    approval_note: null,
    updated_at: null,
    seo: null,
    ...overrides,
  };
}

function makeCharacter(overrides: Partial<CharacterSummary> = {}): CharacterSummary {
  return {
    id: "character-1",
    slug: "star-dragon",
    name: "逐星绒龙",
    species: "龙",
    designer_credit: "设定：阿澈",
    status: "available",
    exclusive: true,
    price_cents: 680000,
    currency: "CNY",
    purchasable: true,
    product_slug: "fursuit-basic",
    cover: IMAGE,
    images: [IMAGE],
    tags: [],
    ...overrides,
  };
}

function makeCharacterDetail(overrides: Partial<CharacterDetail> = {}): CharacterDetail {
  return {
    ...makeCharacter(),
    story: "在星绒纪元里守护坠落陨石的绒龙。",
    rights_summary: "允许用于个人展示与同人创作，商业用途需另行授权。",
    adoption_note: "领养后不再复制同一设定。",
    product_id: "product-1",
    ...overrides,
  };
}

function makeSeries(overrides: Partial<SeriesDetail> = {}): SeriesDetail {
  return {
    id: "series-1",
    kind: "series",
    slug: "star-dragon-fursuit",
    locale: "zh-CN",
    title: "星绒龙系列",
    summary: "面向漫展与外拍的全装系列，起价含可拆尾巴。",
    route: "/fursuits/star-dragon-fursuit",
    cover_asset_id: null,
    cover: IMAGE,
    tags: ["漫展", "室内活动"],
    species: null,
    work_type: null,
    published_at: null,
    updated_at: null,
    is_featured: false,
    seo: { title: "星绒龙系列", description: "系列说明", canonical: "/fursuits/star-dragon-fursuit" },
    body_html: "",
    gallery: [],
    spec: {
      code: "SD-01",
      base_price_cents: 2680000,
      currency: "CNY",
      price_includes: ["头部", "身体", "可拆尾巴"],
      price_excludes: ["手爪升级", "运费"],
      lead_time_note: "按报价记录排队",
      // 含一个未登记的中文标签键，用来验证规格表是动态渲染而不是硬编码字段名
      spec_schema: { material: "仿生绒", fur_density: "高密度", cooling: ["风扇", "透气网"] },
      display_order: 1,
      is_active: true,
    },
    faqs: [
      {
        id: "faq-1",
        kind: "faq",
        slug: "color-change",
        locale: "zh-CN",
        title: "可以改颜色吗",
        summary: "颜色在报价阶段逐项确认。",
        route: "/faq",
        cover_asset_id: null,
        cover: null,
        tags: [],
        species: null,
        work_type: null,
        published_at: null,
        updated_at: null,
        is_featured: false,
        seo: { title: "可以改颜色吗", description: "颜色在报价阶段确认", canonical: "/faq" },
      },
    ],
    ...overrides,
  };
}

const CREATED_APPLICATION: StockApplication = {
  id: "application-1",
  kind: "drop",
  status: "pending",
  product_id: "product-1",
  product_slug: "fursuit-basic",
  product_title: "基础款兽装",
  sku_id: null,
  sku_label: null,
  reason: "用于线下漫展，需要可拆尾巴",
  measurement_note: null,
  review_note: null,
  created_at: "2026-01-05T10:00:00Z",
  reviewed_at: null,
  purchase_expires_at: null,
  purchase_priority: null,
};

// ---------------------------------------------------------------------------
// 商城列表：筛选 query 同步
// ---------------------------------------------------------------------------

describe("ShopPage 筛选与 URL 同步", () => {
  it("按 URL 上的分类请求服务端，点击分类后把条件写回 URL", async () => {
    const { calls } = installFetch((url) => {
      if (url.includes("/products")) {
        return { body: jsonPage([makeProductSummary({ category: "accessory", title: "兽爪手套" })]) };
      }
      return { body: {} };
    });

    const { wrapper, router } = await mountAt(ShopPage, "/shop?category=accessory");
    expect(calls.some((url) => url.includes("/products") && url.includes("category=accessory"))).toBe(true);

    const accessoryChip = byText(wrapper, "button", "配件");
    expect(accessoryChip.attributes("aria-pressed")).toBe("true");

    await byText(wrapper, "button", "周边").trigger("click");
    await settle();

    expect(router.currentRoute.value.query.category).toBe("merch");
    expect(calls.some((url) => url.includes("category=merch"))).toBe(true);

    // 分类可以取消，URL 与请求都回到不带分类的状态
    await byText(wrapper, "button", "全部").trigger("click");
    await settle();
    expect(router.currentRoute.value.query.category).toBeUndefined();
    expect(wrapper.text()).toContain("共 1 件商品");
  });

  it("搜索词提交后进入 URL，并让服务端做检索", async () => {
    const { calls } = installFetch((url) => {
      if (url.includes("/products")) return { body: jsonPage([]) };
      return { body: {} };
    });
    const { wrapper, router } = await mountAt(ShopPage, "/shop");

    await wrapper.get("#shop-q").setValue("爪套");
    await wrapper.get("form").trigger("submit");
    await settle();

    expect(router.currentRoute.value.query.q).toBe("爪套");
    expect(calls.some((url) => url.includes("q=") && url.includes(encodeURIComponent("爪套")))).toBe(true);
    expect(wrapper.text()).toContain("没有符合条件的商品");
  });

  it("空结果显示诚实空态，不展示占位商品", async () => {
    installFetch(() => ({ body: jsonPage([], { total: 0 }) }));
    const { wrapper } = await mountAt(ShopPage, "/shop");
    expect(wrapper.text()).toContain("商城商品正在上架");
    expect(wrapper.findAll("img")).toHaveLength(0);
  });

  it("区域定价：标注没有本区币种定价的商品，且不做汇率折算", async () => {
    // 站点配置声明区域币种：CN=CNY、US=USD（只有币种，没有汇率字段）
    installFetch((url) => {
      if (url.includes("/content/settings")) {
        return {
          body: {
            "commerce.policy": {
              returns_reference: "参照平台规则",
              cross_border_note: "",
              invoice_enabled: true,
              shipping_template_note: "",
              regions: { CN: { currency: "CNY" }, US: { currency: "USD" } },
            },
          },
        };
      }
      if (url.includes("/products")) {
        return {
          body: jsonPage([
            makeProductSummary({ id: "p-cny", slug: "cny-item", title: "人民币定价商品", currency: "CNY" }),
            makeProductSummary({ id: "p-usd", slug: "usd-item", title: "美元定价商品", currency: "USD" }),
          ]),
        };
      }
      return { body: {} };
    });

    const { wrapper } = await mountAt(ShopPage, "/shop");
    // 简中浏览区 → 人民币结算；美元定价的商品被如实标注为"没有本区定价"
    expect(wrapper.text()).toContain("本区结算币种：CNY");
    expect(wrapper.text()).toContain("该商品没有 CNY 定价");
    expect(wrapper.text()).toContain("1 件商品没有 CNY 定价");
    // 价格来自服务端整数分与币种，页面不做任何换算（两种币种各自按原币显示）
    expect(wrapper.text()).toContain("¥1,200 起");
    expect(wrapper.text()).toContain("$1,200 起");
  });
});

// ---------------------------------------------------------------------------
// SKU 选择器
// ---------------------------------------------------------------------------

describe("SkuPicker SKU 选择与数量上限", () => {
  it("默认选中可售组合，数量受 max_per_order 限制并可加购", async () => {
    const picker = mountPicker({ skus: [STANDARD_SKU, THICK_SKU] });

    expect(picker.text()).toContain("标准版 M");
    expect(picker.text()).toContain("该规格每单最多 3 件");

    const plus = picker.get('button[aria-label="增加数量"]');
    for (let index = 0; index < 5; index += 1) {
      await plus.trigger("click");
    }
    expect((picker.get("#sku-quantity").element as HTMLInputElement).value).toBe("3");
    expect((plus.element as HTMLButtonElement).disabled).toBe(true);

    await byText(picker, "button", "加厚版").trigger("click");
    expect(picker.text()).toContain("加厚版 M");
    expect((picker.get("#sku-quantity").element as HTMLInputElement).value).toBe("1");
    expect(picker.text()).toContain("该规格每单最多 2 件");

    await byText(picker, "button", "加入购物车").trigger("click");
    expect(picker.emitted("add")?.[0]).toEqual([
      { sku: expect.objectContaining({ id: "sku-thick" }), quantity: 1 },
    ]);
  });

  it("售罄规格禁用加购并说明原因，仍可浏览价格", async () => {
    const soldOut = makeSku({ id: "sku-sold", label: "标准版 M", in_stock: false, available: true });
    const wrapper = mountPicker({ skus: [soldOut] });

    expect(wrapper.text()).toContain("已售罄");
    expect(wrapper.text()).toContain("¥1,200");
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(true);
    expect(wrapper.text()).toContain("已售罄；售罄仍可浏览");
    expect(wrapper.emitted("add")).toBeUndefined();
  });

  it("服务端标记不可售时禁用加购", async () => {
    const unavailable = makeSku({ available: false, in_stock: false });
    const wrapper = mountPicker({ skus: [unavailable] });
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(true);
    expect(wrapper.text()).toContain("当前不可售");
  });
});

describe("SkuPicker 审批制商品", () => {
  it("未获审批时说明规则、给出申请入口并禁用加购", async () => {
    const wrapper = mountPicker({
      skus: [STANDARD_SKU],
      requiresApproval: true,
      approvalUrl: "/account/applications?product_id=product-1&kind=drop",
    });

    expect(wrapper.text()).toContain("该商品需先申请，人工审批通过后才能购买");
    expect(wrapper.text()).toContain("未通过审批前不能加入购物车");
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(true);
    expect(wrapper.text()).toContain("需先提交申请并通过人工审批后才能购买");

    const applyLink = wrapper
      .findAll("a")
      .find((node) => (node.attributes("href") ?? "").includes("/account/applications"));
    expect(applyLink).toBeTruthy();
    expect(applyLink?.attributes("href")).toContain("product_id=product-1");
  });

  it("已有审批通过记录时显示剩余购买时间并允许加购", async () => {
    const wrapper = mountPicker({
      skus: [STANDARD_SKU],
      requiresApproval: true,
      approvalWindowLabel: "剩余购买时间 2 天 3 小时",
    });

    expect(wrapper.text()).toContain("审批已通过");
    expect(wrapper.text()).toContain("剩余购买时间 2 天 3 小时");
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// 角色列表：状态进 URL
// ---------------------------------------------------------------------------

describe("CharactersPage 状态筛选", () => {
  it("状态筛选进入 URL，保留状态明确标注未成交", async () => {
    const { calls } = installFetch((url) => {
      if (url.includes("/characters")) {
        const reserved = url.includes("status=reserved");
        return {
          body: jsonPage(
            [
              makeCharacter({
                status: reserved ? "reserved" : "available",
                name: reserved ? "守夜雪狐" : "逐星绒龙",
              }),
            ],
            { total: 1 },
          ),
        };
      }
      return { body: {} };
    });

    const { wrapper, router } = await mountAt(CharactersPage, "/characters");
    expect(wrapper.text()).toContain("逐星绒龙");
    expect(wrapper.text()).toContain("独占 · 库存 1");

    await byText(wrapper, "button", "保留中").trigger("click");
    await settle();

    expect(router.currentRoute.value.query.status).toBe("reserved");
    expect(calls.some((url) => url.includes("/characters") && url.includes("status=reserved"))).toBe(true);
    expect(wrapper.text()).toContain("已保留·未成交");
    expect(wrapper.text()).toContain("不可购买：已保留·未成交");
  });

  it("筛选无结果时不伪造角色，提示可放宽条件", async () => {
    installFetch(() => ({ body: jsonPage([], { total: 0 }) }));
    const { wrapper } = await mountAt(CharactersPage, "/characters?status=kept");
    expect(wrapper.text()).toContain("没有符合条件的角色");
    expect(wrapper.text()).toContain("保留中的角色不会因为筛选而变成可购买");
    expect(wrapper.findAll("img")).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// 商品详情：审批引导与结构化数据
// ---------------------------------------------------------------------------

describe("ProductDetailPage", () => {
  it("审批制商品显示申请引导，JSON-LD 的价格与库存与页面一致", async () => {
    installFetch((url) => {
      if (url.includes("/products/fursuit-basic")) {
        return { body: makeProductDetail({ requires_approval: true }) };
      }
      // GET /stock-applications 返回裸数组（见 06_API接口契约），不是分页对象
      if (url.includes("/stock-applications")) return { body: [] };
      return { body: {} };
    });

    const { wrapper } = await mountAt(ProductDetailPage, "/shop/fursuit-basic");

    expect(wrapper.text()).toContain("需先申请审批");
    expect(wrapper.text()).toContain("该商品需先申请，人工审批通过后才能购买");
    expect(wrapper.text()).toContain("¥1,200 – ¥1,580");
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(true);

    const script = document.head.querySelector('script[type="application/ld+json"]');
    expect(script).toBeTruthy();
    const data = JSON.parse(script?.textContent ?? "{}") as {
      offers?: { lowPrice?: string; highPrice?: string; availability?: string };
      aggregateRating?: unknown;
      name?: string;
    };
    expect(data.name).toBe("基础款兽装");
    expect(data.offers?.lowPrice).toBe("1200.00");
    expect(data.offers?.highPrice).toBe("1580.00");
    // 页面徽章显示“有货”，结构化数据必须一致；且不虚构评价
    expect(wrapper.text()).toContain("有货");
    expect(data.offers?.availability).toBe("https://schema.org/InStock");
    expect(data.aggregateRating).toBeUndefined();

    wrapper.unmount();
    expect(document.head.querySelector('script[type="application/ld+json"]')).toBeNull();
  });

  it("售罄商品仍可浏览，且加购被禁用并给出说明", async () => {
    installFetch((url) => {
      if (url.includes("/products/fursuit-basic")) {
        return {
          body: makeProductDetail({
            availability: "sold_out",
            skus: [makeSku({ in_stock: false, available: true })],
          }),
        };
      }
      return { body: {} };
    });

    const { wrapper } = await mountAt(ProductDetailPage, "/shop/fursuit-basic");

    expect(wrapper.text()).toContain("基础款兽装");
    expect(wrapper.text()).toContain("该商品当前全部规格售罄");
    expect(isDisabled(wrapper, "button", "加入购物车")).toBe(true);
    expect(wrapper.text()).toContain("售罄仍可浏览");

    const script = document.head.querySelector('script[type="application/ld+json"]');
    const data = JSON.parse(script?.textContent ?? "{}") as {
      offers?: { availability?: string };
    };
    expect(data.offers?.availability).toBe("https://schema.org/OutOfStock");
  });

  it("商品不存在时显示 not-found 状态而不是空白页", async () => {
    installFetch(() => ({
      status: 404,
      body: {
        error: {
          code: "RESOURCE_NOT_FOUND",
          message: "商品不存在。",
          fields: {},
          trace_id: "trace-1",
        },
      },
    }));

    const { wrapper } = await mountAt(ProductDetailPage, "/shop/missing");
    expect(wrapper.text()).toContain("找不到这个商品");
  });
});

// ---------------------------------------------------------------------------
// 掉落与服务申请表单
// ---------------------------------------------------------------------------
describe("AccountApplicationsPage 申请表单", () => {
  it("校验失败时贴字段提示且不发请求，填写后提交成功并显示已保存状态", async () => {
    const { fetchMock } = installFetch((url, init) => {
      if (url.includes("/stock-applications") && init?.method === "POST") {
        return { body: CREATED_APPLICATION };
      }
      // GET /stock-applications 返回裸数组（见 06_API接口契约），不是分页对象
      if (url.includes("/stock-applications")) return { body: [] };
      if (url.includes("/products/fursuit-basic")) {
        return { body: makeProductDetail({ requires_approval: true }) };
      }
      if (url.includes("/products")) {
        return { body: jsonPage([makeProductSummary({ requires_approval: true })]) };
      }
      return { body: {} };
    });

    const { wrapper } = await mountAt(AccountApplicationsPage, "/account/applications");
    expect(wrapper.text()).toContain("提交新申请");
    expect(wrapper.text()).toContain("还没有申请记录");

    // 未选择商品、理由过短 → 校验错误，不发 POST（按钮为 type=submit，这里直接提交表单）
    await wrapper.get("form").trigger("submit");
    await settle();
    expect(wrapper.text()).toContain("请选择要申请的商品或服务。");
    expect(wrapper.text()).toContain("至少 5 个字");
    expect(
      fetchMock.mock.calls.some((call) => (call[1] as RequestInit | undefined)?.method === "POST"),
    ).toBe(false);

    await wrapper.get("#application-product").setValue("product-1");
    await settle();
    expect(wrapper.text()).toContain("标准版 M");

    await wrapper.get("#application-reason").setValue("用于线下漫展，需要可拆尾巴");
    await wrapper.get("form").trigger("submit");
    await settle();

    const postCall = fetchMock.mock.calls.find(
      (call) => (call[1] as RequestInit | undefined)?.method === "POST",
    );
    expect(postCall).toBeTruthy();
    expect(String(postCall?.[0])).toContain("/api/v1/stock-applications");
    expect(JSON.parse(String((postCall?.[1] as RequestInit).body))).toMatchObject({
      product_id: "product-1",
      kind: "drop",
      reason: "用于线下漫展，需要可拆尾巴",
    });
    expect(wrapper.text()).toContain("申请已提交，编号 application-1");
    expect(wrapper.text()).toContain("待人工审批");
  });

  it("URL 带上商品参数时预选该商品", async () => {
    installFetch((url) => {
      // GET /stock-applications 返回裸数组（见 06_API接口契约），不是分页对象
      if (url.includes("/stock-applications")) return { body: [] };
      if (url.includes("/products/fursuit-basic")) {
        return { body: makeProductDetail({ requires_approval: true }) };
      }
      if (url.includes("/products")) {
        return { body: jsonPage([makeProductSummary({ requires_approval: true })]) };
      }
      return { body: {} };
    });

    const { wrapper } = await mountAt(
      AccountApplicationsPage,
      "/account/applications?product_id=product-1&kind=service",
    );

    const productSelect = wrapper.get("#application-product").element as HTMLSelectElement;
    expect(productSelect.value).toBe("product-1");
    expect(wrapper.text()).toContain("已选择商品「基础款兽装」");
    const serviceRadio = wrapper
      .findAll('input[name="application-kind"]')
      .find((node) => (node.element as HTMLInputElement).value === "service");
    expect((serviceRadio?.element as HTMLInputElement).checked).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// 系列列表与详情
// ---------------------------------------------------------------------------

describe("SeriesListPage", () => {
  it("起价同时标注包含项与不含项，交期只在有文案时出现并注明以报价为准", async () => {
    installFetch((url) => {
      if (url.includes("/content/series")) return { body: jsonPage([makeSeries()]) };
      return { body: {} };
    });

    const { wrapper } = await mountAt(SeriesListPage, "/fursuits");

    expect(wrapper.text()).toContain("星绒龙系列");
    expect(wrapper.text()).toContain("¥26,800 起");
    expect(wrapper.text()).toContain("起价，非最终报价");
    expect(wrapper.text()).toContain("起价包含");
    expect(wrapper.text()).toContain("可拆尾巴");
    expect(wrapper.text()).toContain("起价不含");
    expect(wrapper.text()).toContain("手爪升级");
    expect(wrapper.text()).toContain("实际交期以报价记录为准。");
  });

  it("没有 lead_time_note 时不显示交期文案", async () => {
    installFetch(() => ({
      body: jsonPage([
        makeSeries({
          spec: { ...makeSeries().spec!, lead_time_note: null },
          faqs: [],
        }),
      ]),
    }));

    const { wrapper } = await mountAt(SeriesListPage, "/fursuits");
    expect(wrapper.text()).toContain("星绒龙系列");
    expect(wrapper.text()).not.toContain("实际交期以报价记录为准。");
  });

  it("没有系列时显示空态而不是占位卡片", async () => {
    installFetch(() => ({ body: jsonPage([]) }));
    const { wrapper } = await mountAt(SeriesListPage, "/fursuits");
    expect(wrapper.text()).toContain("系列资料尚未发布");
    expect(wrapper.findAll("img")).toHaveLength(0);
  });
});

describe("SeriesDetailPage", () => {
  it("规格表按 spec_schema 动态生成，包含未登记键与数组值", async () => {
    installFetch((url) => {
      if (url.includes("/content/series/star-dragon-fursuit")) return { body: makeSeries() };
      return { body: {} };
    });

    const { wrapper } = await mountAt(SeriesDetailPage, "/fursuits/star-dragon-fursuit");

    expect(wrapper.text()).toContain("¥26,800 起");
    expect(wrapper.text()).toContain("材质");
    expect(wrapper.text()).toContain("仿生绒");
    // 未登记字段不隐藏：显示原始键名并把数组值连起来
    expect(wrapper.text()).toContain("fur density");
    expect(wrapper.text()).toContain("高密度");
    expect(wrapper.text()).toContain("风扇、透气网");
    // FAQ 与询价入口
    expect(wrapper.text()).toContain("可以改颜色吗");
    expect(
      wrapper.findAll("a").some((node) => (node.attributes("href") ?? "").includes("/commission/new")),
    ).toBe(true);
  });

  it("系列不存在时显示 not-found 状态", async () => {
    installFetch(() => ({
      status: 404,
      body: { error: { code: "RESOURCE_NOT_FOUND", message: "系列不存在。", fields: {}, trace_id: "t" } },
    }));
    const { wrapper } = await mountAt(SeriesDetailPage, "/fursuits/missing");
    expect(wrapper.text()).toContain("找不到这个系列");
  });
});

// ---------------------------------------------------------------------------
// 角色详情
// ---------------------------------------------------------------------------

describe("CharacterDetailPage", () => {
  it("待领养角色可前往购买，并显示独占库存 1", async () => {
    installFetch((url) => {
      if (url.includes("/characters/star-dragon")) return { body: makeCharacterDetail() };
      return { body: {} };
    });

    const { wrapper } = await mountAt(CharacterDetailPage, "/characters/star-dragon");

    expect(wrapper.text()).toContain("逐星绒龙");
    expect(wrapper.text()).toContain("独占 · 库存 1");
    expect(wrapper.text()).toContain("¥6,800");
    expect(wrapper.text()).toContain("设定作者：设定：阿澈");
    expect(wrapper.text()).toContain("允许用于个人展示与同人创作");
    expect(
      wrapper.findAll("a").some((node) => (node.attributes("href") === "/shop/fursuit-basic")),
    ).toBe(true);
    expect(wrapper.text()).not.toContain("暂不可购买");
  });

  it("已领养角色禁用购买并说明原因，不靠颜色表达", async () => {
    installFetch(() => ({
      body: makeCharacterDetail({ status: "adopted", purchasable: false }),
    }));

    const { wrapper } = await mountAt(CharacterDetailPage, "/characters/star-dragon");

    expect(wrapper.text()).toContain("已领养");
    expect(isDisabled(wrapper, "button", "暂不可购买")).toBe(true);
    expect(wrapper.text()).toContain("该角色已完成领养并交付，不再重复出售。");
    expect(
      wrapper.findAll("a").some((node) => (node.attributes("href") ?? "").startsWith("/shop/")),
    ).toBe(false);
  });

  it("授权摘要缺失时说明按合同执行，不自行扩写范围", async () => {
    installFetch(() => ({ body: makeCharacterDetail({ rights_summary: null, story: null }) }));

    const { wrapper } = await mountAt(CharacterDetailPage, "/characters/star-dragon");
    expect(wrapper.text()).toContain("该角色尚未发布授权摘要");
    expect(wrapper.text()).toContain("按照合同要求执行");
    expect(wrapper.text()).toContain("该角色的故事尚未发布");
  });
});
