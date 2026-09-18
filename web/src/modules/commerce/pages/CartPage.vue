<script setup lang="ts">
/**
 * 购物车。
 *
 * 规则：
 * - 数量修改是绝对数量（`PUT /cart/items/{skuId}`），不是增量；服务端返回的车是新权威值。
 * - 购物车价格只作展示，结算金额由 `/checkout/preview` 决定（08 文档）。
 * - 本地车不存隐私：未登录时不缓存地址、手机号等任何个人信息，只提示登录后由服务端合并。
 * - 库存与可售状态展示服务端返回值，前端不自行判定可售，也不用颜色单独表达售罄。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import {
  fetchCart,
  removeCartItem,
  setCartItemQuantity,
  type CartLine,
  type CartResponse,
} from "@/modules/commerce/api/commerce";
import MoneySummary from "@/modules/commerce/components/MoneySummary.vue";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { ApiError } from "@/shared/lib/http";
import { formatMoneyExact } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, StatusBadge } from "@/shared/ui/base";

const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();

const cart = ref<CartResponse | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const errorCode = ref<string | null>(null);
const forbidden = ref(false);
const savingSku = ref<string | null>(null);
const removingSku = ref<string | null>(null);
/** 每个 SKU 的串行队列：请求在途时再次点击，合并为最后想要的数量，避免乱序覆盖 */
const queued = new Map<string, number>();
const lineErrors = ref<Record<string, string>>({});

const lines = computed<CartLine[]>(() => cart.value?.items ?? []);
const totalQuantity = computed(() => lines.value.reduce((sum, line) => sum + line.quantity, 0));
const hasUnavailable = computed(() => lines.value.some((line) => line.availability !== "available"));
const mergeNote = computed(() => cart.value?.merge?.note ?? "");

function applyCart(next: CartResponse): void {
  cart.value = next;
  const valid = new Set(next.items.map((item) => item.sku_id));
  for (const sku of Object.keys(lineErrors.value)) {
    if (!valid.has(sku)) delete lineErrors.value[sku];
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  errorCode.value = null;
  forbidden.value = false;
  try {
    applyCart(await fetchCart());
  } catch (caught) {
    if (caught instanceof ApiError) {
      errorCode.value = caught.code;
      forbidden.value = caught.code === "FORBIDDEN" || caught.status === 403;
      error.value = caught.needsLogin
        ? "登录后才能读取服务端购物车。"
        : caught.message || "购物车加载失败。";
    } else {
      error.value = "购物车加载失败，请稍后重试。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await Promise.allSettled([site.load(), load()]);
});

function availabilityBadge(line: CartLine): { tone: "jade" | "amber" | "wine" | "neutral"; label: string } {
  switch (line.availability) {
    case "available":
      return { tone: "jade", label: "可结算" };
    case "insufficient":
      return { tone: "amber", label: "库存不足" };
    case "sold_out":
      return { tone: "wine", label: "已售罄" };
    default:
      return { tone: "neutral", label: "暂不可售" };
  }
}

function maxQuantity(line: CartLine): number | null {
  return line.available_quantity;
}

function canIncrease(line: CartLine): boolean {
  if (line.availability !== "available") return false;
  const max = maxQuantity(line);
  return max === null ? true : line.quantity < max;
}

function canDecrease(line: CartLine): boolean {
  return line.quantity > 1;
}

function findLine(skuId: string): CartLine | null {
  return lines.value.find((item) => item.sku_id === skuId) ?? null;
}

async function commitQuantity(skuId: string, firstQuantity: number): Promise<void> {
  if (savingSku.value === skuId) return;

  savingSku.value = skuId;
  try {
    let next = firstQuantity;
    applyCart(await setCartItemQuantity(skuId, next));

    // 在途期间的点击合并为最后想要的数量
    while (queued.has(skuId)) {
      next = queued.get(skuId) as number;
      queued.delete(skuId);
      applyCart(await setCartItemQuantity(skuId, next));
    }
    delete lineErrors.value[skuId];
  } catch (caught) {
    queued.delete(skuId);
    const fallback = "数量未能保存，请稍后重试。";
    const title = findLine(skuId)?.product_title ?? "该商品";
    if (caught instanceof ApiError) {
      lineErrors.value[skuId] = caught.needsLogin ? "登录状态已过期，请重新登录后再修改。" : caught.message || fallback;
      if (caught.code === "STOCK_NOT_ENOUGH") {
        toast.error("库存不足", `${title} 的可售数量不足，已刷新为服务端当前值。`);
      } else {
        toast.error("数量未保存", caught.message || fallback);
      }
      await load();
    } else {
      lineErrors.value[skuId] = fallback;
      toast.error("数量未保存", fallback);
    }
  } finally {
    savingSku.value = null;
  }
}

function changeQuantity(line: CartLine, delta: number): void {
  const target = line.quantity + delta;
  if (target < 1) return;
  const max = maxQuantity(line);
  if (max !== null && target > max) {
    lineErrors.value[line.sku_id] = `当前可售上限为 ${max} 件，请调整数量。`;
    return;
  }
  delete lineErrors.value[line.sku_id];
  line.quantity = target;
  if (savingSku.value === line.sku_id) {
    queued.set(line.sku_id, target);
    return;
  }
  void commitQuantity(line.sku_id, target);
}

async function remove(line: CartLine): Promise<void> {
  if (removingSku.value) return;
  removingSku.value = line.sku_id;
  try {
    applyCart(await removeCartItem(line.sku_id));
    queued.delete(line.sku_id);
    toast.success("已从购物车移除", `${line.product_title} ${line.sku_name}`);
  } catch (caught) {
    const message = caught instanceof ApiError ? caught.message : "移除失败，请稍后重试。";
    toast.error("移除失败", message, { label: "重试", run: () => void load() });
  } finally {
    removingSku.value = null;
  }
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '购物车' }]" />

    <SectionHeading
      class="mt-6"
      :level="1"
      eyebrow="Cart"
      title="购物车"
      description="数量与可售状态以服务端返回为准。结算时会重新校验价格与库存，发生变化会先让你确认。"
    >
      <template #actions>
        <AppButton to="/shop" variant="secondary" size="sm">继续挑选</AppButton>
      </template>
    </SectionHeading>

    <!-- 加载 -->
    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="5" aria-label="正在读取购物车" />
    </div>

    <!-- 错误 / 未登录 / 无权限 -->
    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : 'error'"
      :title="forbidden ? '无法访问该购物车' : '购物车暂时无法加载'"
      :description="error"
    >
      <AppButton v-if="errorCode === 'AUTH_REQUIRED' || errorCode === 'SESSION_EXPIRED'" size="sm" to="/login?returnTo=/cart">
        去登录
      </AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="cart">
      <!-- 登录合并结果 -->
      <div
        v-if="mergeNote"
        class="mt-8 rounded-lg border border-jade bg-jade-soft p-4 text-sm text-jade"
        role="status"
      >
        {{ mergeNote }}
      </div>

      <div v-if="!auth.isAuthenticated" class="mt-8 rounded-lg border border-line bg-surface-muted p-5 text-sm text-ink-muted">
        <p class="font-medium text-ink-soft">当前未登录</p>
        <p class="mt-1">
          购物车保存在服务端，登录后才能同步到你的账号。本站不会在本地保存收货地址、手机号等个人信息。
        </p>
        <div class="mt-4">
          <AppButton size="sm" to="/login?returnTo=/cart">登录后查看购物车</AppButton>
        </div>
      </div>

      <EmptyState
        v-else-if="lines.length === 0"
        class="mt-8"
        title="购物车是空的"
        description="现货、配件与已通过审批的掉落都可以加入购物车。定制委托请先提交询价，不进入购物车。"
      >
        <AppButton size="sm" to="/shop">去商城看看</AppButton>
        <AppButton size="sm" variant="secondary" to="/commission/new">提交询价</AppButton>
      </EmptyState>

      <div v-else class="mt-8 grid gap-8 lg:grid-cols-[1.6fr_1fr] lg:items-start">
        <ul class="divide-y divide-line rounded-lg border border-line bg-surface">
          <li v-for="line in lines" :key="line.sku_id" class="grid gap-4 p-5 sm:grid-cols-[96px_1fr]">
            <RouterLink
              :to="`/shop/${line.product_slug}`"
              class="block overflow-hidden rounded-sm bg-canvas-sunk"
            >
              <img
                v-if="line.image"
                :src="line.image.thumb ?? line.image.src"
                :alt="line.image.alt || `${line.product_title} 商品图`"
                :width="line.image.width"
                :height="line.image.height"
                loading="lazy"
                decoding="async"
                class="aspect-square size-full object-cover"
              />
              <span v-else class="flex aspect-square items-center justify-center text-xs text-ink-faint">
                图片准备中
              </span>
            </RouterLink>

            <div class="min-w-0">
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <RouterLink :to="`/shop/${line.product_slug}`" class="font-medium hover:text-amber-strong">
                    {{ line.product_title }}
                  </RouterLink>
                  <p class="mt-1 text-xs text-ink-faint">{{ line.sku_name }}</p>
                </div>
                <StatusBadge :tone="availabilityBadge(line).tone" :label="availabilityBadge(line).label" />
              </div>

              <p v-if="line.availability_note" class="mt-2 text-xs text-ink-muted">
                {{ line.availability_note }}
              </p>

              <div class="mt-4 flex flex-wrap items-center justify-between gap-4">
                <div class="flex items-center gap-2">
                  <span class="text-xs text-ink-faint" :id="`qty-label-${line.sku_id}`">数量</span>
                  <div
                    class="flex items-center rounded-sm border border-line-strong"
                    role="group"
                    :aria-labelledby="`qty-label-${line.sku_id}`"
                  >
                    <button
                      type="button"
                      class="min-h-[44px] min-w-[44px] px-3 text-lg text-ink-soft hover:bg-surface-muted disabled:opacity-50"
                      :disabled="!canDecrease(line) || savingSku === line.sku_id"
                      :aria-label="`减少 ${line.product_title} 的数量`"
                      @click="changeQuantity(line, -1)"
                    >
                      −
                    </button>
                    <span class="min-w-[3rem] text-center text-sm tabular-nums">{{ line.quantity }}</span>
                    <button
                      type="button"
                      class="min-h-[44px] min-w-[44px] px-3 text-lg text-ink-soft hover:bg-surface-muted disabled:opacity-50"
                      :disabled="!canIncrease(line) || savingSku === line.sku_id"
                      :aria-label="`增加 ${line.product_title} 的数量`"
                      @click="changeQuantity(line, 1)"
                    >
                      +
                    </button>
                  </div>
                  <span v-if="savingSku === line.sku_id" class="text-xs text-ink-faint" role="status">保存中</span>
                  <span
                    v-else-if="line.available_quantity !== null && line.availability === 'available'"
                    class="text-xs text-ink-faint"
                  >
                    可售上限 {{ line.available_quantity }} 件
                  </span>
                </div>

                <div class="text-right">
                  <p class="text-sm tabular-nums">{{ formatMoneyExact(line.line_total_cents, line.currency) }}</p>
                  <p class="text-xs text-ink-faint">
                    单价 {{ formatMoneyExact(line.unit_price_cents, line.currency) }}
                  </p>
                </div>
              </div>

              <p v-if="lineErrors[line.sku_id]" class="mt-3 text-xs font-medium text-wine" role="alert">
                {{ lineErrors[line.sku_id] }}
              </p>

              <div class="mt-4 flex flex-wrap gap-3">
                <AppButton
                  size="sm"
                  variant="quiet"
                  :loading="removingSku === line.sku_id"
                  @click="remove(line)"
                >
                  移除
                </AppButton>
                <AppButton size="sm" variant="ghost" :to="`/shop/${line.product_slug}`">查看商品</AppButton>
              </div>
            </div>
          </li>
        </ul>

        <div class="space-y-4 lg:sticky lg:top-24">
          <MoneySummary
            :goods-cents="cart.summary.goods_cents"
            :discount-cents="cart.summary.discount_cents"
            :shipping-cents="cart.summary.shipping_cents"
            :total-cents="cart.summary.total_cents"
            :currency="cart.summary.currency"
            title="购物车金额摘要"
            total-label="合计（未含结算校验）"
            :note="`${totalQuantity} 件商品。购物车金额仅作展示，运费与优惠在结算时由服务端重新计算。`"
          />

          <div class="rounded-lg border border-line bg-surface-muted p-4 text-xs text-ink-muted">
            {{ site.commercePolicy.shipping_template_note }}
          </div>

          <div v-if="hasUnavailable" class="rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong" role="status">
            购物车中存在库存不足或已售罄的商品。结算时服务端会再次校验，可能返回具体到 SKU 的提示。
          </div>

          <AppButton block size="lg" to="/checkout">去结算</AppButton>
          <p class="text-center text-xs text-ink-faint">结算需要登录，以便保存订单、地址快照与发票信息。</p>
        </div>
      </div>
    </template>
  </div>
</template>
