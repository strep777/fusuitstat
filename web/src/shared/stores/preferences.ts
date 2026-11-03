/**
 * 站点偏好：主题（浅色默认，深色候选）、减弱动效、语言。
 * 记录在 localStorage，只保存偏好，不保存任何隐私数据。
 */

import { defineStore } from "pinia";
import { computed, ref, watch } from "vue";

export type ThemeMode = "light" | "dark" | "system";
export type Locale = "zh-CN" | "en-US";

const THEME_KEY = "starium.theme";
const MOTION_KEY = "starium.motion";
const LOCALE_KEY = "starium.locale";

function readStored<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  if (typeof localStorage === "undefined") return fallback;
  const value = localStorage.getItem(key);
  return value && (allowed as readonly string[]).includes(value) ? (value as T) : fallback;
}

export const usePreferencesStore = defineStore("preferences", () => {
  const theme = ref<ThemeMode>(readStored(THEME_KEY, ["light", "dark", "system"] as const, "light"));
  const reducedMotion = ref(
    typeof localStorage !== "undefined" && localStorage.getItem(MOTION_KEY) === "reduced",
  );
  const locale = ref<Locale>(readStored(LOCALE_KEY, ["zh-CN", "en-US"] as const, "zh-CN"));

  /** 解析 system 后的实际主题，供需要换图的组件（如深色底 Logo）使用。 */
  const effectiveTheme = computed<"light" | "dark">(() => {
    if (theme.value !== "system") return theme.value;
    const prefersDark =
      typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
    return prefersDark ? "dark" : "light";
  });

  function apply(): void {
    if (typeof document === "undefined") return;
    const root = document.documentElement;
    root.dataset.theme = effectiveTheme.value;
    root.dataset.motion = reducedMotion.value ? "reduced" : "full";
    root.lang = locale.value;
  }

  function setTheme(value: ThemeMode): void {
    theme.value = value;
    if (typeof localStorage !== "undefined") localStorage.setItem(THEME_KEY, value);
    apply();
  }

  function toggleTheme(): void {
    setTheme(effectiveTheme.value === "dark" ? "light" : "dark");
  }

  function setReducedMotion(value: boolean): void {
    reducedMotion.value = value;
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(MOTION_KEY, value ? "reduced" : "full");
    }
    apply();
  }

  function setLocale(value: Locale): void {
    locale.value = value;
    if (typeof localStorage !== "undefined") localStorage.setItem(LOCALE_KEY, value);
    apply();
  }

  watch([theme, reducedMotion, locale], apply, { immediate: true });

  return {
    theme,
    effectiveTheme,
    reducedMotion,
    locale,
    apply,
    setTheme,
    toggleTheme,
    setReducedMotion,
    setLocale,
  };
});
