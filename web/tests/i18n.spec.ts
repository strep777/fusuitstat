/**
 * 文案层测试（C02/C24、02 文档 FR-022）。
 *
 * 覆盖：
 * 1. 简中与英文词条都能按点号路径取到，且插值正确；
 * 2. 英文缺词条时**回退到中文原文**（不是空白、不是 undefined、不是键名）；
 * 3. 中文也缺时返回键名并把缺失键记录下来，方便定位；
 * 4. 词条键集与占位符一致（与 `scripts/check_i18n.py` 同一判定口径的运行时副本）；
 * 5. 语言切换通过偏好 store 生效，并同步 `<html lang>`；
 * 6. 语言切换入口只在后台启用 `en_locale` 后出现（未上线语言不给假入口）。
 */

import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

import SiteFooter from "@/app/components/SiteFooter.vue";
import SiteHeader from "@/app/components/SiteHeader.vue";
import { useSiteStore } from "@/modules/content/stores/site";
import { enUS } from "@/shared/i18n/en-US";
import { zhCN } from "@/shared/i18n/zh-CN";
import {
  clearMissingMessageKeys,
  missingMessageKeys,
  resolveText,
  translate,
  useI18n,
} from "@/shared/lib/i18n";
import { usePreferencesStore } from "@/shared/stores/preferences";

function flatten(tree: unknown, prefix = ""): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [key, value] of Object.entries(tree as Record<string, unknown>)) {
    const next = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") out[next] = value;
    else Object.assign(out, flatten(value, next));
  }
  return out;
}

const ZH = flatten(zhCN);
const EN = flatten(enUS);

function headerMocks() {
  vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.toString() : input.url;
    const body = url.includes("/content/settings")
      ? { "platform.feature_flags": { en_locale: true } }
      : {};
    return {
      ok: true,
      status: 200,
      headers: { get: () => null },
      text: async () => JSON.stringify(body),
      json: async () => body,
    } as unknown as Response;
  });
}

async function mountHeader() {
  headerMocks();
  const pinia = createPinia();
  setActivePinia(pinia);
  // 站点头部自己不加载站点配置（由布局负责）：这里显式给出一份启用 en_locale 的配置。
  const site = useSiteStore();
  site.settings = { "platform.feature_flags": { en_locale: true } };
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/works", component: { template: "<div />" } },
      { path: "/fursuits", component: { template: "<div />" } },
      { path: "/commission/guide", component: { template: "<div />" } },
      { path: "/commission/schedules", component: { template: "<div />" } },
      { path: "/characters", component: { template: "<div />" } },
      { path: "/shop", component: { template: "<div />" } },
      { path: "/news", component: { template: "<div />" } },
      { path: "/about", component: { template: "<div />" } },
      { path: "/login", component: { template: "<div />" } },
      { path: "/contact", component: { template: "<div />" } },
      { path: "/account", component: { template: "<div />" } },
    ],
  });
  await router.push("/");
  await router.isReady();
  const wrapper = mount(SiteHeader, { global: { plugins: [pinia, router] } });
  for (let index = 0; index < 30; index += 1) await Promise.resolve();
  await nextTick();
  return { wrapper, pinia };
}

describe("文案层取词与回退", () => {
  beforeEach(() => {
    clearMissingMessageKeys();
  });

  it("按点号路径取词，并替换占位符", () => {
    expect(translate("nav.works", undefined, "zh-CN")).toBe("作品");
    expect(translate("nav.works", undefined, "en-US")).toBe("Works");
    expect(
      translate("footer.responseSlaValue", { inquiry: 3, feedback: 7 }, "en-US"),
    ).toContain("3 business days");
    expect(
      translate("footer.responseSlaValue", { inquiry: 3, feedback: 7 }, "en-US"),
    ).toContain("7");
  });

  it("未提供的占位符保持原样，便于直接看出漏传参数", () => {
    expect(translate("footer.responseSlaValue", { inquiry: 3 }, "zh-CN")).toContain("{feedback}");
  });

  it("缺失键返回键名并记录，不显示空白", () => {
    // 用类型断言模拟「键写错但被绕过类型检查」的运行时情形
    const value = translate("nav.doesNotExist" as never, undefined, "zh-CN");
    expect(value).toBe("nav.doesNotExist");
    expect(missingMessageKeys()).toContain("zh-CN:nav.doesNotExist");
  });

  it("英文缺词条时回退中文原文", () => {
    // 词条树是常量，这里直接验证回退分支的语义：先查英文，再查中文
    const missingInEnglish = { ...EN };
    delete missingInEnglish["nav.works"];
    const original = enUS.nav.works;
    // 运行时替换（词条对象为只读视图，这里用最小侵入的方式验证回退逻辑）
    (enUS.nav as { works: string }).works = "";
    try {
      expect(translate("nav.works", undefined, "en-US")).toBe(original ? "作品" : "作品");
      expect(missingMessageKeys()).toContain("en-US:nav.works");
    } finally {
      (enUS.nav as { works: string }).works = original;
    }
  });

  it("两种语言的键集与占位符完全一致", () => {
    expect(Object.keys(EN).sort()).toEqual(Object.keys(ZH).sort());
    for (const key of Object.keys(ZH)) {
      const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort();
      expect(placeholders(EN[key]!), `占位符不一致：${key}`).toEqual(placeholders(ZH[key]!));
      expect(ZH[key]!.trim()).not.toBe("");
      expect(EN[key]!.trim()).not.toBe("");
    }
  });
});

describe("服务端运营文案的解析（LocalizedText）", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("纯字符串对所有语言共用", () => {
    expect(resolveText("周一至周五", "en-US")).toBe("周一至周五");
  });

  it("按语言取值：命中目标语言，缺失回退简中，都没有则空串", () => {
    const value = { "zh-CN": "周一至周五", "en-US": "Mon–Fri" };
    expect(resolveText(value, "en-US")).toBe("Mon–Fri");
    expect(resolveText(value, "zh-CN")).toBe("周一至周五");
    expect(resolveText({ "zh-CN": "只有中文" }, "en-US")).toBe("只有中文");
    expect(resolveText({ "en-US": "   " }, "en-US")).toBe("");
    expect(resolveText(null, "en-US")).toBe("");
    expect(resolveText(undefined, "zh-CN")).toBe("");
  });

  it("空对象不会被渲染成 [object Object]", () => {
    expect(resolveText({}, "zh-CN")).toBe("");
    expect(String(resolveText({}, "zh-CN"))).not.toContain("[object Object]");
  });

  it("站点 store 按当前语言解析品牌与商务政策，并在切换语言后立即更新", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const site = useSiteStore();
    const preferences = usePreferencesStore();
    site.settings = {
      "brand.profile": {
        brand_name: "弈星纪元兽装工作室",
        legal_entity: "弈星纪元（上海）文化发展有限公司",
        domain: "starium.cn",
        support_email: "support@starium.cn",
        service_hours: { "zh-CN": "周一至周五 10:00–19:00", "en-US": "Mon–Fri 10:00–19:00" },
        response_sla: { inquiry_workdays: 3, feedback_workdays: 7 },
      },
      "commerce.policy": {
        returns_reference: { "zh-CN": "参照平台规则", "en-US": "Follows marketplace rules" },
        cross_border_note: "跨境单独说明",
        invoice_enabled: true,
        shipping_template_note: { "zh-CN": "运费模板由工作室配置", "en-US": "Rates configured by the studio" },
      },
    };

    expect(site.brand.service_hours).toBe("周一至周五 10:00–19:00");
    expect(site.commercePolicy.shipping_template_note).toBe("运费模板由工作室配置");
    // 纯字符串字段在任何语言下都是同一句
    expect(site.commercePolicy.cross_border_note).toBe("跨境单独说明");

    preferences.setLocale("en-US");
    await nextTick();
    expect(site.brand.service_hours).toBe("Mon–Fri 10:00–19:00");
    expect(site.commercePolicy.shipping_template_note).toBe("Rates configured by the studio");
    // 未配英文的字段回退中文，而不是显示空白
    expect(site.commercePolicy.cross_border_note).toBe("跨境单独说明");
  });

  it("页脚在工作时段只有一种语言时回退，不会渲染成对象", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const site = useSiteStore();
    const preferences = usePreferencesStore();
    site.settings = {
      "brand.profile": {
        brand_name: "弈星纪元兽装工作室",
        legal_entity: "弈星纪元（上海）文化发展有限公司",
        domain: "starium.cn",
        support_email: "support@starium.cn",
        service_hours: { "zh-CN": "周一至周五 10:00–19:00", "en-US": "Mon–Fri 10:00–19:00" },
        response_sla: { inquiry_workdays: 3, feedback_workdays: 7 },
      },
    };
    preferences.setLocale("en-US");
    await nextTick();

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    await router.push("/");
    await router.isReady();
    const wrapper = mount(SiteFooter, { global: { plugins: [pinia, router] } });
    expect(wrapper.text()).toContain("Mon–Fri 10:00–19:00");
    expect(wrapper.text()).not.toContain("[object Object]");
  });
});

describe("语言切换与入口门禁", () => {  beforeEach(() => {
    localStorage.clear();
    clearMissingMessageKeys();
  });

  it("切换语言写回偏好 store 与 <html lang>", async () => {
    const pinia = createPinia();
    setActivePinia(pinia);
    const preferences = usePreferencesStore();
    const i18n = useI18n();

    expect(i18n.locale.value).toBe("zh-CN");
    i18n.setLocale("en-US");
    await nextTick();
    expect(preferences.locale).toBe("en-US");
    expect(document.documentElement.lang).toBe("en-US");
    expect(i18n.t("nav.shop")).toBe("Shop");

    i18n.setLocale("zh-CN");
    await nextTick();
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(i18n.t("nav.shop")).toBe("商城");
  });

  it("en_locale 未启用时不出现语言切换入口", async () => {
    vi.stubGlobal("fetch", async () =>
      ({
        ok: true,
        status: 200,
        headers: { get: () => null },
        text: async () => JSON.stringify({ "platform.feature_flags": {} }),
        json: async () => ({ "platform.feature_flags": {} }),
      }) as unknown as Response,
    );
    const pinia = createPinia();
    setActivePinia(pinia);
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/", component: { template: "<div />" } }],
    });
    await router.push("/");
    await router.isReady();
    const wrapper = mount(SiteHeader, { global: { plugins: [pinia, router] } });
    for (let index = 0; index < 20; index += 1) await Promise.resolve();
    await nextTick();

    expect(wrapper.text()).not.toContain("English (US)");
    expect(wrapper.findAll("button").some((item) => item.text().includes("EN"))).toBe(false);
  });

  it("en_locale 启用后出现入口，点击后界面切到英文", async () => {
    const { wrapper } = await mountHeader();
    const toggle = wrapper.findAll("button").find((item) => item.text().includes("EN"));
    expect(toggle, "启用 en_locale 后应出现语言切换入口").toBeTruthy();

    await toggle!.trigger("click");
    await nextTick();
    expect(wrapper.text()).toContain("Works");
    expect(wrapper.text()).toContain("Commissions");
    expect(wrapper.text()).not.toContain("作品");
  });
});
