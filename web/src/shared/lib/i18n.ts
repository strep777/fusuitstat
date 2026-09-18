/**
 * 文案层（C02/C24、02 文档 FR-022）。
 *
 * 设计约束：
 * - **键安全**：`t()` 只接受词条树里真实存在的点号路径，写错键在 `pnpm typecheck` 阶段失败，
 *   不靠运行时才发现；键集一致性由 `scripts/check_i18n.py` 门禁保证。
 * - **单一语言来源**：当前语言只由 `usePreferencesStore().locale` 决定（已持久化、并写入
 *   `<html lang>`），本模块不另存一份 state，避免两处语言不一致。
 * - **缺失不回退成空白**：英文缺词条时回退到中文原文，再不行返回键名本身，并把缺失键记录到
 *   `missingMessageKeys()`；界面宁可显示中文原句，也不显示空字符串或 `undefined`。
 * - **未上线语言不给假入口**：语言切换入口是否出现由后台功能开关 `en_locale` 决定
 *   （见 `SiteHeader.vue`），本模块只负责翻译，不负责"假装已支持"。
 */

import { computed, type ComputedRef } from "vue";

import { enUS } from "@/shared/i18n/en-US";
import { zhCN, type MessageKey, type MessageTree } from "@/shared/i18n/zh-CN";
import { usePreferencesStore, type Locale } from "@/shared/stores/preferences";

export const SUPPORTED_LOCALES: readonly Locale[] = ["zh-CN", "en-US"];
export const DEFAULT_LOCALE: Locale = "zh-CN";

const CATALOGS: Record<Locale, MessageTree> = {
  "zh-CN": zhCN as unknown as MessageTree,
  "en-US": enUS as unknown as MessageTree,
};

const missing = new Set<string>();

export type MessageParams = Record<string, string | number>;

function lookup(catalog: MessageTree, key: string): string | null {
  let node: string | MessageTree | undefined = catalog;
  for (const part of key.split(".")) {
    if (typeof node !== "object" || node === null) return null;
    node = node[part];
  }
  return typeof node === "string" ? node : null;
}

/** `{name}` 形式插值；未提供参数时保留占位符原样，便于在页面上直接看出漏传。 */
function interpolate(template: string, params?: MessageParams): string {
  if (!params) return template;
  return template.replace(/\{(\w+)\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(params, name) ? String(params[name]) : match,
  );
}

/**
 * 取词条：按当前语言 → 中文 → 键名 的顺序回退。
 *
 * `locale` 省略时用当前偏好语言；显式传入用于非组件环境（路由标题、预渲染）与测试。
 */
export function translate(key: MessageKey, params?: MessageParams, locale?: Locale): string {
  const target = locale ?? currentLocale();
  const primary = lookup(CATALOGS[target], key);
  if (primary !== null && primary !== "") return interpolate(primary, params);
  const fallback = lookup(CATALOGS[DEFAULT_LOCALE], key);
  if (fallback !== null && fallback !== "") {
    if (target !== DEFAULT_LOCALE) missing.add(`${target}:${key}`);
    return interpolate(fallback, params);
  }
  missing.add(`${target}:${key}`);
  return key;
}

/** 当前语言：优先读偏好 store；在 store 尚未初始化时（如模块级调用）退回默认语言。 */
function currentLocale(): Locale {
  try {
    return usePreferencesStore().locale;
  } catch {
    return DEFAULT_LOCALE;
  }
}

/** 缺失词条（回退过中文或键名的键），用于测试与本地诊断。 */
export function missingMessageKeys(): string[] {
  return [...missing].sort();
}

export function clearMissingMessageKeys(): void {
  missing.clear();
}

/**
 * 面向用户的运营文案：可以是所有语言共用的一句话，也可以是 `{"zh-CN": …, "en-US": …}`。
 *
 * 服务端**不按请求语言裁剪**（06 文档「多语言文本」），由这里按当前语言解析：
 * 目标语言缺失时回退简中，再缺失返回空串——**不显示 `[object Object]`，也不编造文案**。
 */
export type LocalizedText = string | Record<string, string> | null | undefined;

export function resolveText(value: LocalizedText, locale?: Locale): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const target = locale ?? currentLocale();
    for (const candidate of [target, DEFAULT_LOCALE, ...SUPPORTED_LOCALES]) {
      const text = value[candidate];
      if (typeof text === "string" && text.trim() !== "") return text;
    }
  }
  return "";
}

export interface I18n {
  locale: ComputedRef<Locale>;
  /** 是否为默认语言（简中）；英文站特有文案用它分支时更明确。 */
  isDefaultLocale: ComputedRef<boolean>;
  t: (key: MessageKey, params?: MessageParams) => string;
  /** 解析服务端下发的运营文案（可能是按语言给出的对象）。 */
  text: (value: LocalizedText) => string;
  setLocale: (value: Locale) => void;
}

export function useI18n(): I18n {
  const preferences = usePreferencesStore();
  const locale = computed(() => preferences.locale);
  return {
    locale,
    isDefaultLocale: computed(() => locale.value === DEFAULT_LOCALE),
    t: (key, params) => translate(key, params, locale.value),
    text: (value) => resolveText(value, locale.value),
    setLocale: (value) => preferences.setLocale(value),
  };
}
