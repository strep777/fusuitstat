/**
 * Vitest 全局准备。
 *
 * happy-dom 已提供 document、crypto 与 localStorage；这里只补齐测试中
 * 一定会被访问、但 jsdom/happy-dom 未实现或行为不一致的浏览器 API。
 */

import { beforeEach, vi } from "vitest";

// 部分实现缺少 crypto.getRandomValues（幂等键生成使用它）。
if (typeof globalThis.crypto === "undefined") {
  Object.defineProperty(globalThis, "crypto", {
    value: { getRandomValues: (array: Uint8Array) => array.fill(1) },
    configurable: true,
  });
} else if (typeof globalThis.crypto.getRandomValues !== "function") {
  Object.defineProperty(globalThis.crypto, "getRandomValues", {
    value: (array: Uint8Array) => array.fill(1),
    configurable: true,
  });
}

// Vue Router 的 scrollBehavior 在测试里不需要真实滚动。
if (typeof window !== "undefined" && typeof window.scrollTo !== "function") {
  Object.defineProperty(window, "scrollTo", { value: () => undefined, configurable: true });
}

// 每个用例前清空会话偏好，避免用例之间通过 localStorage 互相影响。
beforeEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});
