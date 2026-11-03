import { createPinia, setActivePinia } from "pinia";
import { afterEach, expect, it, vi } from "vitest";
import { fetchContentList, fetchContentDetail, fetchSeriesList, fetchSeries, fetchWorkFacets, fetchHome } from "@/modules/content/api/content";
import { usePreferencesStore } from "@/shared/stores/preferences";

afterEach(() => vi.unstubAllGlobals());

it("所有公开内容读取携带当前语言，查询条件保留，切换语言后使用新值", async () => {
  setActivePinia(createPinia());
  const preferences = usePreferencesStore();
  const urls: URL[] = [];
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    urls.push(new URL(input, "http://localhost"));
    return new Response("{}", { status: 200, headers: { "Content-Type": "application/json" } });
  }));
  preferences.setLocale("en-US");
  await fetchContentList("news", { q: "fur", page: 2 });
  await fetchContentDetail("faq", "care");
  await fetchSeriesList();
  await fetchSeries("full-suit");
  await fetchWorkFacets();
  await fetchHome();
  expect(urls).toHaveLength(6);
  expect(urls.every((url) => url.searchParams.get("locale") === "en-US")).toBe(true);
  expect(urls[0]?.searchParams.get("q")).toBe("fur");
  expect(urls[0]?.searchParams.get("page")).toBe("2");
  preferences.setLocale("zh-CN");
  await fetchContentDetail("faq", "care");
  expect(urls.at(-1)?.searchParams.get("locale")).toBe("zh-CN");
});
