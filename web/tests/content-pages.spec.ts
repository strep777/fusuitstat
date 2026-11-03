/**
 * 内容类页面测试。
 *
 * 覆盖范围（任务要求的四项 + 与实现直接相关的补充）：
 * 1. 作品集筛选条件与 URL 双向同步（刷新恢复、切换筛选重置页码）；
 * 2. 空态（作品集在筛选条件下无结果、FAQ 无内容）；
 * 3. 404 态（全局 not-found 路由与内容详情接口 404）；
 * 4. 灯箱 Esc 关闭、焦点归还触发控件、←/→ 切换；
 * 5. 补充：公告分页进 URL、FAQ 前端搜索命中数、公开读取错误分支。
 *
 * 只使用已安装的 vitest + @vue/test-utils + happy-dom，不引入新依赖；
 * 网络层用 vi.mock 替换 `@/modules/content/api/content` 的取数函数，
 * 其余导出（applyPageHead、classifyPublicError 等）保留真实实现。
 */
import { enableAutoUnmount, flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick, type Component } from "vue";
import { createMemoryHistory, createRouter, type Router } from "vue-router";

const api = vi.hoisted(() => ({
  fetchContentList: vi.fn(),
  fetchContentDetail: vi.fn(),
  fetchWorkFacets: vi.fn(),
  fetchPublicSettings: vi.fn(),
  fetchHome: vi.fn(),
}));

vi.mock("@/modules/content/api/content", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/modules/content/api/content")>();
  return { ...actual, ...api };
});

import ContentDetailPage from "@/modules/content/pages/ContentDetailPage.vue";
import ContentPage from "@/modules/content/pages/ContentPage.vue";
import FaqPage from "@/modules/content/pages/FaqPage.vue";
import NotFoundPage from "@/modules/content/pages/NotFoundPage.vue";
import WorksPage from "@/modules/content/pages/WorksPage.vue";
import ImageLightbox from "@/modules/content/components/ImageLightbox.vue";
import { classifyPublicError, type ContentDetail, type ContentSummary, type MediaImage } from "@/modules/content/api/content";
import { ApiError } from "@/shared/lib/http";
import { usePreferencesStore } from "@/shared/stores/preferences";

enableAutoUnmount(afterEach);

/** 占位路由组件：不使用 template，避免依赖运行时模板编译。 */
const Blank = defineComponent({ name: "Blank", render: () => h("div") });

const WORK_FACETS = {
  work_types: [
    { value: "全装", count: 3 },
    { value: "半装", count: 1 },
  ],
  species: [
    { value: "龙", count: 2 },
    { value: "狼", count: 2 },
  ],
  series: [{ value: "full-suit", count: 0, label: "全集系列" }],
};

function image(id: string, alt = ""): MediaImage {
  return {
    id,
    alt,
    width: 1200,
    height: 1500,
    src: `/media/${id}.webp`,
    thumb: `/media/${id}-thumb.webp`,
    srcset: [
      { url: `/media/${id}-320.webp`, width: 320 },
      { url: `/media/${id}-640.webp`, width: 640 },
    ],
  };
}

function summary(overrides: Partial<ContentSummary> = {}): ContentSummary {
  return {
    id: "content_demo",
    kind: "work",
    slug: "demo",
    locale: "zh-CN",
    title: "示例作品",
    summary: "示例摘要",
    route: "/works/demo",
    cover_asset_id: null,
    cover: null,
    tags: [],
    species: null,
    work_type: null,
    published_at: "2025-01-02T10:00:00+08:00",
    updated_at: "2025-01-02T10:00:00+08:00",
    is_featured: false,
    seo: { title: "示例作品", description: "示例摘要", canonical: "/works/demo" },
    ...overrides,
  };
}

function detail(overrides: Partial<ContentDetail> = {}): ContentDetail {
  return {
    ...summary(overrides),
    body_html: "<p>正文</p>",
    gallery: [],
    ...overrides,
  };
}

function listPage<T>(items: T[], total: number, page = 1, limit = 12) {
  return {
    items,
    total,
    page,
    limit,
    has_more: page * limit < total,
    next_cursor: null,
  };
}

function makeRouter(): Router {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", name: "home", component: Blank },
      { path: "/works", name: "works", component: WorksPage, meta: { title: "作品集" } },
      { path: "/works/:slug", name: "work-detail", component: Blank },
      { path: "/news", name: "news", component: ContentPage, meta: { title: "公告", kind: "news" } },
      {
        path: "/news/:slug",
        name: "news-detail",
        component: ContentDetailPage,
        meta: { title: "公告", kind: "news" },
      },
      { path: "/faq", name: "faq", component: FaqPage, meta: { title: "常见问题" } },
      { path: "/contact", name: "contact", component: Blank },
      { path: "/commission/new", name: "inquiry-new", component: Blank },
      { path: "/feedback/new", name: "feedback-new", component: Blank },
      { path: "/payment/return", name: "payment-return", component: Blank },
      { path: "/login", name: "login", component: Blank },
      { path: "/:pathMatch(.*)*", name: "not-found", component: NotFoundPage },
    ],
  });
}

interface Mounted {
  wrapper: VueWrapper;
  router: Router;
}

async function mountAt(component: Component, path: string, locale: "zh-CN" | "en-US" = "zh-CN"): Promise<Mounted> {
  const router = makeRouter();
  await router.push(path);
  await router.isReady();
  const pinia = createPinia();
  setActivePinia(pinia);
  usePreferencesStore().setLocale(locale);
  const wrapper = mount(component, { global: { plugins: [router, pinia] } });
  await flushPromises();
  await nextTick();
  return { wrapper, router };
}

beforeEach(() => {
  api.fetchContentList.mockReset();
  api.fetchContentDetail.mockReset();
  api.fetchWorkFacets.mockReset();
  api.fetchPublicSettings.mockReset();
  api.fetchHome.mockReset();
  api.fetchPublicSettings.mockResolvedValue({});
});

describe("作品集筛选与 URL 同步", () => {
  it("按 URL 恢复筛选与页码，并把新的筛选条件写回 URL", async () => {
    api.fetchWorkFacets.mockResolvedValue(WORK_FACETS);
    api.fetchContentList.mockResolvedValue(listPage([], 0));

    const { wrapper, router } = await mountAt(WorksPage, "/works?species=龙&page=2");

    // 刷新恢复：URL 上的条件直接进入接口查询
    expect(api.fetchContentList).toHaveBeenCalledWith(
      "work",
      expect.objectContaining({ species: "龙", page: 2, limit: 12 }),
    );

    // 点击类型筛选：写入 URL，并回到第 1 页，保留其它条件
    const chip = wrapper.findAll("button").find((node) => node.text().includes("全装"));
    expect(chip).toBeTruthy();
    await chip!.trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.query.category).toBe("全装");
    expect(router.currentRoute.value.query.species).toBe("龙");
    expect(router.currentRoute.value.query.page).toBeUndefined();
    expect(api.fetchContentList).toHaveBeenLastCalledWith(
      "work",
      expect.objectContaining({ work_type: "全装", species: "龙", page: 1 }),
    );
  });

  it("搜索关键词进入 URL 的 q 参数", async () => {
    api.fetchWorkFacets.mockResolvedValue(WORK_FACETS);
    api.fetchContentList.mockResolvedValue(listPage([], 0));

    const { wrapper, router } = await mountAt(WorksPage, "/works");
    await wrapper.find("#works-search").setValue("逐星绒龙");
    await wrapper.find("form").trigger("submit");
    await flushPromises();

    expect(router.currentRoute.value.query.q).toBe("逐星绒龙");
    expect(api.fetchContentList).toHaveBeenLastCalledWith(
      "work",
      expect.objectContaining({ q: "逐星绒龙" }),
    );
  });
});

describe("空态渲染", () => {
  it("作品集无结果时说明当前筛选条件并提供清除筛选", async () => {
    api.fetchWorkFacets.mockResolvedValue(WORK_FACETS);
    api.fetchContentList.mockResolvedValue(listPage([], 0));

    const { wrapper, router } = await mountAt(WorksPage, "/works?species=龙&q=不存在");

    expect(wrapper.text()).toContain("没有符合当前筛选条件的作品");
    expect(wrapper.text()).toContain("物种=龙");
    expect(wrapper.text()).toContain("关键词=不存在");
    expect(wrapper.findAll("button").some((node) => node.text() === "清除筛选")).toBe(true);

    // 清除筛选后地址恢复为无参数
    const clear = wrapper.findAll("button").find((node) => node.text() === "清除筛选")!;
    await clear.trigger("click");
    await flushPromises();
    expect(router.currentRoute.value.query).toEqual({});
  });

  it("FAQ 没有已发布内容时显示空态，而不是示例问题", async () => {
    api.fetchContentList.mockResolvedValue(listPage([], 0, 1, 50));

    const { wrapper } = await mountAt(FaqPage, "/faq");

    expect(wrapper.text()).toContain("FAQ 尚未发布");
    expect(wrapper.text()).toContain("问题与答案由运营在后台整理并预览后发布");
    expect(wrapper.findAll("details")).toHaveLength(0);
  });

  it("英文 FAQ 显示英文搜索与空态，帮助链接保留语言前缀", async () => {
    api.fetchContentList.mockResolvedValue(listPage([], 0, 1, 50));
    const { wrapper } = await mountAt(FaqPage, "/faq", "en-US");
    expect(wrapper.text()).toContain("Frequently asked questions");
    expect(wrapper.text()).toContain("No FAQs published yet");
    expect(wrapper.find('a[href="/en/contact"]').exists()).toBe(true);
    expect(wrapper.find("#faq-keyword").attributes("placeholder")).toContain("quote validity");
    expect(wrapper.text()).not.toContain("常见问题");
  });

  it("单条 FAQ 正文重试成功后清除失败计数", async () => {
    api.fetchContentList.mockResolvedValue(listPage([summary({ kind: "faq", slug: "retry" })], 1));
    api.fetchContentDetail.mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce(detail({ kind: "faq", slug: "retry", body_html: "<p>Recovered answer</p>" }));
    const { wrapper } = await mountAt(FaqPage, "/faq", "en-US");
    expect(wrapper.text()).toContain("1 answers could not be loaded");
    const retry = wrapper.findAll("button").find((button) => button.text() === "Retry this answer");
    expect(retry).toBeTruthy();
    await retry!.trigger("click");
    await flushPromises();
    expect(wrapper.text()).toContain("Recovered answer");
    expect(wrapper.text()).not.toContain("answers could not be loaded");
  });

  it("FAQ 搜索在同一批数据上过滤并显示命中数", async () => {
    api.fetchContentList.mockResolvedValue(
      listPage(
        [
          summary({ id: "faq_1", kind: "faq", slug: "quote-valid", title: "报价有效期多久？", tags: ["报价"] }),
          summary({ id: "faq_2", kind: "faq", slug: "clean-care", title: "怎么清洁毛面？", tags: ["养护"] }),
        ],
        2,
        1,
        50,
      ),
    );
    api.fetchContentDetail.mockImplementation(async (_kind, slug) =>
      detail({ kind: "faq", slug, title: slug, body_html: `<p>${slug} 的正文</p>` }),
    );

    const { wrapper } = await mountAt(FaqPage, "/faq");
    expect(wrapper.text()).toContain("共 2 条问题");

    await wrapper.find("#faq-keyword").setValue("清洁");
    await flushPromises();
    expect(wrapper.text()).toContain("命中 1 / 2 条问题");
    expect(wrapper.text()).toContain("怎么清洁毛面？");
    expect(wrapper.text()).not.toContain("报价有效期多久？");
  });
});

describe("404 与错误态", () => {
  it("全局 404 页面给出三条出口且不自动跳转", async () => {
    const { wrapper, router } = await mountAt(NotFoundPage, "/nope/deep");

    expect(wrapper.text()).toContain("这个地址没有对应页面");
    expect(wrapper.text()).toContain("/nope/deep");
    expect(wrapper.text()).toContain("返回首页");
    expect(wrapper.text()).toContain("浏览作品集");
    expect(wrapper.text()).toContain("联系与反馈");
    expect(router.currentRoute.value.fullPath).toBe("/nope/deep");
  });

  it("内容详情 404 渲染 not-found 态并提供返回列表入口", async () => {
    api.fetchContentDetail.mockRejectedValue(
      new ApiError(404, { code: "RESOURCE_NOT_FOUND", message: "内容不存在。", fields: {}, trace_id: "" }),
    );

    const { wrapper } = await mountAt(ContentDetailPage, "/news/removed");

    expect(wrapper.text()).toContain("这条内容不存在或已下架");
    expect(wrapper.text()).toContain("返回公告");
    expect(wrapper.find("article").exists()).toBe(false);
  });

  it("公开读取的错误码分成 not-found / forbidden / login / error 四类", () => {
    const notFound = classifyPublicError(
      new ApiError(404, { code: "RESOURCE_NOT_FOUND", message: "无", fields: {}, trace_id: "" }),
    );
    const forbidden = classifyPublicError(
      new ApiError(403, { code: "FORBIDDEN", message: "无", fields: {}, trace_id: "" }),
    );
    const login = classifyPublicError(
      new ApiError(401, { code: "AUTH_REQUIRED", message: "无", fields: {}, trace_id: "" }),
    );
    const failed = classifyPublicError(new Error("boom"), "加载失败。");

    expect(notFound.kind).toBe("not-found");
    expect(forbidden.kind).toBe("forbidden");
    expect(login.kind).toBe("login");
    expect(failed.kind).toBe("error");
    expect(failed.message).toBe("加载失败。");
  });
});

describe("公告列表分页", () => {
  it("分页状态进 URL，并据此请求对应页", async () => {
    api.fetchContentList.mockResolvedValue(
      listPage([summary({ id: "news_1", kind: "news", slug: "n1", title: "公告一", route: "/news/n1" })], 30, 2, 10),
    );

    const { wrapper, router } = await mountAt(ContentPage, "/news?page=2");
    expect(api.fetchContentList).toHaveBeenCalledWith("news", expect.objectContaining({ page: 2, limit: 10 }));
    expect(wrapper.text()).toContain("第 2 / 3 页");

    const next = wrapper.findAll("button").find((node) => node.text() === "下一页");
    expect(next).toBeTruthy();
    await next!.trigger("click");
    await flushPromises();

    expect(router.currentRoute.value.query.page).toBe("3");
    expect(api.fetchContentList).toHaveBeenLastCalledWith("news", expect.objectContaining({ page: 3 }));
  });
});

describe("作品相册灯箱", () => {
  it("Esc 关闭灯箱并把焦点还给触发控件", async () => {
    const trigger = document.createElement("button");
    trigger.textContent = "查看完整图";
    document.body.appendChild(trigger);
    trigger.focus();

    const wrapper = mount(ImageLightbox, {
      props: { images: [image("a", "龙形全装正面"), image("b", "龙形全装背面")], open: false, index: 0, title: "逐星绒龙" },
      attachTo: document.body,
    });

    expect(wrapper.find('[role="dialog"]').exists()).toBe(false);

    await wrapper.setProps({ open: true });
    await flushPromises();
    const dialog = wrapper.find('[role="dialog"]');
    expect(dialog.exists()).toBe(true);
    expect(dialog.attributes("aria-modal")).toBe("true");

    // Esc 关闭（useFocusTrap 的键盘处理）
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("close")).toBeTruthy();

    // 关闭后焦点回到触发控件
    await wrapper.setProps({ open: false });
    await flushPromises();
    expect(document.activeElement).toBe(trigger);

    trigger.remove();
  });

  it("方向键在相册内切换图片", async () => {
    const wrapper = mount(ImageLightbox, {
      props: { images: [image("a"), image("b"), image("c")], open: true, index: 0, title: "逐星绒龙" },
      attachTo: document.body,
    });
    await flushPromises();

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("update:index")?.[0]).toEqual([1]);

    document.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    await flushPromises();
    expect(wrapper.emitted("update:index")?.[1]).toEqual([2]);
  });
});
