/**
 * locale 前缀路由（R15b / C02、C24）。
 *
 * 约束：
 * 1. 公开页面在 `/en/...` 下**同一份组件、同一份路径**可访问（不存在"英文站功能少一半"）；
 * 2. **URL 决定语言**：进入 `/en/...` 界面切英文，回到中文地址切回简中，刷新与直达一致；
 * 3. 账户/结算等尚未双语的页面**没有对位地址**，`counterpartPath()` 返回 `null`，
 *    语言切换只切界面语言、不跳到"英文 URL 里的中文页"；
 * 4. 公开页面输出 `hreflang`（zh-CN / en-US / x-default）与指向简中的 canonical；
 *    私密页面不输出 alternate、也不写 canonical（双语范围待 N15 决定）。
 */

import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it } from "vitest";

import { ENGLISH_PREFIX, counterpartPath, isEnglishPath, router } from "@/app/router";
import { usePreferencesStore } from "@/shared/stores/preferences";

async function visit(path: string) {
  await router.push(path);
  await router.isReady();
  return router.currentRoute.value;
}

describe("locale 前缀路由", () => {
  beforeEach(async () => {
    setActivePinia(createPinia());
    localStorage.clear();
  });

  it("公开页面在 /en 下解析到同一组件，并带上英文 locale 标记", async () => {
    const zh = await visit("/shop");
    expect(String(zh.name)).toBe("shop");
    expect(zh.meta.locale).toBe("zh-CN");

    const en = await visit(`${ENGLISH_PREFIX}/shop`);
    expect(String(en.name)).toBe("shop-en");
    expect(en.meta.locale).toBe("en-US");
    // 同一份组件：路由记录指向同一个懒加载入口（不是另写一套页面）
    const zhRecord = router.resolve("/shop").matched.at(-1);
    const enRecord = router.resolve(`${ENGLISH_PREFIX}/shop`).matched.at(-1);
    expect(enRecord?.components).toEqual(zhRecord?.components);
  });

  it("带参数的公开页面同样有英文对位（作品/商品详情）", async () => {
    const work = await visit(`${ENGLISH_PREFIX}/works/star-tail`);
    expect(String(work.name)).toBe("work-detail-en");
    expect(work.params.slug).toBe("star-tail");

    const product = await visit(`${ENGLISH_PREFIX}/shop/suit-1`);
    expect(String(product.name)).toBe("product-detail-en");
    expect(product.params.slug).toBe("suit-1");
  });

  it("URL 决定语言：进入英文前缀切到英文，回到中文地址切回简中", async () => {
    const preferences = usePreferencesStore();
    expect(preferences.locale).toBe("zh-CN");

    await visit(`${ENGLISH_PREFIX}/works`);
    expect(preferences.locale).toBe("en-US");

    await visit("/works");
    expect(preferences.locale).toBe("zh-CN");
  });

  it("英文前缀下没有对应条目时返回 404，不静默回落到中文页", async () => {
    const missing = await visit(`${ENGLISH_PREFIX}/account/orders`);
    expect(String(missing.name)).toBe("not-found-en");
    expect(missing.meta.locale).toBe("en-US");
  });

  it("counterpartPath 给出对位地址，且对未双语页面返回 null", async () => {
    await visit("/shop");
    expect(counterpartPath("/shop")).toBe(`${ENGLISH_PREFIX}/shop`);
    expect(counterpartPath("/works/star-tail?tag=recent")).toBe(
      `${ENGLISH_PREFIX}/works/star-tail?tag=recent`,
    );
    expect(counterpartPath("/")).toBe(ENGLISH_PREFIX);
    expect(counterpartPath(`${ENGLISH_PREFIX}/`)).toBe("/");

    // 账户与结算：没有英文对位 → null（不允许造半成品英文 URL）
    expect(counterpartPath("/account/orders")).toBeNull();
    expect(counterpartPath("/checkout")).toBeNull();
    expect(counterpartPath("/pay/inst_1")).toBeNull();
  });

  it("isEnglishPath 只认 /en 前缀本身与它的子路径", async () => {
    expect(isEnglishPath("/en")).toBe(true);
    expect(isEnglishPath("/en/works")).toBe(true);
    // `/energy` 这类同前缀单词不能被误判
    expect(isEnglishPath("/energy")).toBe(false);
    expect(isEnglishPath("/works")).toBe(false);
  });

  it("公开页面输出 hreflang 与 canonical，私密页面不输出", async () => {
    await visit("/shop");
    const alternates = Array.from(
      document.head.querySelectorAll<HTMLLinkElement>('link[data-dsh-alternate="1"]'),
    );
    const languages = alternates.map((link) => link.hreflang).sort();
    expect(languages).toEqual(["en-US", "x-default", "zh-CN"]);
    expect(alternates.find((link) => link.hreflang === "en-US")?.href).toContain("/en/shop");
    expect(alternates.find((link) => link.hreflang === "zh-CN")?.href).toContain("/shop");

    // 私密页面：清掉 alternate 且不写 canonical
    await visit("/checkout");
    expect(document.head.querySelectorAll('link[data-dsh-alternate="1"]').length).toBe(0);
  });
});
