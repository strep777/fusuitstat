/**
 * 购物车交互状态（04 文档：服务端是权威源）。
 *
 * 这里只保存服务端返回的购物车快照、加载/提交状态与导航角标所需的件数；
 * 价格、库存与可售性都以下单时服务端校验为准，前端不做乐观改价，也不计算金额。
 *
 * PUT /cart/items/{skuId} 的 body 是绝对数量：加购 = 已有数量 + 本次数量，
 * 由本 store 依据服务端快照计算后提交，写成功后用服务端返回覆盖本地快照。
 */

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import {
  fetchCart,
  removeCartItem,
  setCartItemQuantity,
  type Cart,
  type CartItem,
} from "@/modules/catalog/api/catalog";
import { ApiError } from "@/shared/lib/http";
import type { CurrencyCode } from "@/shared/lib/format";

function isCartPayload(value: unknown): value is Cart {
  return (
    typeof value === "object" &&
    value !== null &&
    Array.isArray((value as { items?: unknown }).items)
  );
}

export const useCartStore = defineStore("cart", () => {
  const cart = ref<Cart | null>(null);
  const loading = ref(false);
  const loaded = ref(false);
  const error = ref<string | null>(null);
  /** 需要登录：写操作返回 401 时置位，页面据此给出登录入口 */
  const requiresLogin = ref(false);
  /** 正在提交的 SKU，用于禁用重复点击（服务端另有幂等与校验） */
  const pendingSkuIds = ref<string[]>([]);

  const items = computed<CartItem[]>(() => cart.value?.items ?? []);
  const itemCount = computed(() =>
    items.value.reduce((total, item) => total + (item.quantity > 0 ? item.quantity : 0), 0),
  );
  const lineCount = computed(() => items.value.length);
  const hasItems = computed(() => items.value.length > 0);
  const currency = computed<CurrencyCode>(() => (cart.value?.currency === "USD" ? "USD" : "CNY"));
  /** 仅用于展示的参考金额；实际应付由服务端结算 */
  const goodsCents = computed(() =>
    items.value.reduce((total, item) => total + item.unit_price_cents * item.quantity, 0),
  );
  const unavailableCount = computed(
    () => items.value.filter((item) => !item.available || !item.in_stock).length,
  );

  function applyServerCart(payload: unknown): void {
    if (isCartPayload(payload)) {
      cart.value = payload;
      return;
    }
    // 写接口未返回整车时，重新读取服务端快照，避免本地拼出一份假购物车。
    void load(true);
  }

  function quantityOf(skuId: string): number {
    return items.value.find((item) => item.sku_id === skuId)?.quantity ?? 0;
  }

  function isPending(skuId: string): boolean {
    return pendingSkuIds.value.includes(skuId);
  }

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    try {
      cart.value = await fetchCart();
      error.value = null;
      requiresLogin.value = false;
    } catch (caught) {
      if (caught instanceof ApiError && caught.needsLogin) {
        // 未登录不是错误：购物车为空，页面显示登录入口。
        cart.value = null;
        requiresLogin.value = true;
        error.value = null;
      } else {
        error.value = caught instanceof ApiError ? caught.message : "购物车加载失败，请稍后重试。";
      }
    } finally {
      loaded.value = true;
      loading.value = false;
    }
  }

  async function setQuantity(skuId: string, quantity: number): Promise<boolean> {
    if (quantity <= 0) return remove(skuId);
    if (isPending(skuId)) return false;
    pendingSkuIds.value = [...pendingSkuIds.value, skuId];
    try {
      const payload = await setCartItemQuantity(skuId, quantity);
      applyServerCart(payload);
      error.value = null;
      requiresLogin.value = false;
      return true;
    } catch (caught) {
      if (caught instanceof ApiError && caught.needsLogin) {
        requiresLogin.value = true;
        error.value = null;
      } else {
        error.value = caught instanceof ApiError ? caught.message : "购物车更新失败，请稍后重试。";
      }
      return false;
    } finally {
      pendingSkuIds.value = pendingSkuIds.value.filter((id) => id !== skuId);
    }
  }

  /** 加购：以服务端快照为基础累加，最终数量由服务端校验上限与库存。 */
  async function addItem(skuId: string, quantity = 1): Promise<boolean> {
    if (!loaded.value) await load();
    const next = quantityOf(skuId) + Math.max(1, quantity);
    return setQuantity(skuId, next);
  }

  async function remove(skuId: string): Promise<boolean> {
    if (isPending(skuId)) return false;
    pendingSkuIds.value = [...pendingSkuIds.value, skuId];
    try {
      const payload = await removeCartItem(skuId);
      applyServerCart(payload);
      error.value = null;
      return true;
    } catch (caught) {
      if (caught instanceof ApiError && caught.needsLogin) {
        requiresLogin.value = true;
        error.value = null;
      } else {
        error.value = caught instanceof ApiError ? caught.message : "移除失败，请稍后重试。";
      }
      return false;
    } finally {
      pendingSkuIds.value = pendingSkuIds.value.filter((id) => id !== skuId);
    }
  }

  /** 退出登录或切换账号时清空本地快照。 */
  function reset(): void {
    cart.value = null;
    loaded.value = false;
    error.value = null;
    requiresLogin.value = false;
    pendingSkuIds.value = [];
  }

  return {
    cart,
    items,
    loading,
    loaded,
    error,
    requiresLogin,
    pendingSkuIds,
    itemCount,
    lineCount,
    hasItems,
    currency,
    goodsCents,
    unavailableCount,
    quantityOf,
    isPending,
    load,
    setQuantity,
    addItem,
    remove,
    reset,
  };
});
