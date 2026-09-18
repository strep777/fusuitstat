<script setup lang="ts">
/**
 * 我的订单列表。
 *
 * 规则：
 * - 状态筛选与分页进 URL（03/11 文档：过滤条件进 URL，便于分享与刷新保持）。
 * - 待付款订单显示付款剩余时间；到期不等于订单取消，文案不能说成已取消。
 * - 状态与金额必须用文字表达，不能只靠颜色（11 文档）；这里状态用 `StatusBadge` 的中文标签，
 *   金额用 `formatMoneyExact` 的完整格式。
 * - UNKNOWN 类状态由订单详情承载，列表只展示服务端返回的履约状态。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { fetchOrders, type OrderStatus, type OrderSummary } from "@/modules/commerce/api/commerce";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { formatCountdown, formatDateTime, formatMoneyExact, isExpired } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  SkeletonBlock,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();

const STATUS_FILTERS: Array<{ value: OrderStatus | "ALL"; label: string }> = [
  { value: "ALL", label: "全部" },
  { value: "PENDING_PAYMENT", label: "待付款" },
  { value: "ACTIVE", label: "已付款待履约" },
  { value: "FULFILLING", label: "制作中" },
  { value: "SHIPPED", label: "已发货" },
  { value: "COMPLETED", label: "已完成" },
  { value: "CANCELLED", label: "已取消" },
];

const STATUS_LABEL: Record<OrderStatus, string> = {
  PENDING_PAYMENT: "待付款",
  ACTIVE: "已付款，等待履约",
  FULFILLING: "制作/备货中",
  SHIPPED: "已发货",
  COMPLETED: "已完成",
  CANCELLED: "已取消",
};

const orders = ref<OrderSummary[]>([]);
const total = ref(0);
const hasMore = ref(false);
const loading = ref(true);
const error = ref<string | null>(null);
const errorCode = ref<string | null>(null);
const forbidden = ref(false);

const page = computed(() => {
  const raw = Number(route.query["page"] ?? 1);
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
});
const statusFilter = computed<OrderStatus | "ALL">(() => {
  const raw = String(route.query["status"] ?? "ALL");
  const found = STATUS_FILTERS.find((item) => item.value === raw);
  return found ? found.value : "ALL";
});

let requestSeq = 0;

async function load(): Promise<void> {
  const seq = ++requestSeq;
  loading.value = true;
  error.value = null;
  errorCode.value = null;
  forbidden.value = false;
  try {
    const payload = await fetchOrders({
      status: statusFilter.value,
      page: page.value,
      limit: 10,
    });
    if (seq !== requestSeq) return;
    orders.value = payload.items;
    total.value = payload.total;
    hasMore.value = payload.has_more;
  } catch (caught) {
    if (seq !== requestSeq) return;
    if (caught instanceof ApiError) {
      errorCode.value = caught.code;
      forbidden.value = caught.status === 403;
      error.value = caught.needsLogin
        ? "登录状态已过期，请重新登录后查看订单。"
        : caught.message || "订单列表加载失败。";
    } else {
      error.value = "订单列表加载失败，请稍后重试。";
    }
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
}

onMounted(load);
watch(() => [route.query["status"], route.query["page"]], () => void load());

function setStatus(value: OrderStatus | "ALL"): void {
  void router.push({ name: "account-orders", query: { status: value === "ALL" ? undefined : value, page: undefined } });
}

function setPage(next: number): void {
  void router.push({
    name: "account-orders",
    query: {
      status: statusFilter.value === "ALL" ? undefined : statusFilter.value,
      page: next <= 1 ? undefined : String(next),
    },
  });
}

function statusTone(status: OrderStatus): "amber" | "jade" | "wine" | "neutral" {
  if (status === "PENDING_PAYMENT") return "amber";
  if (status === "CANCELLED") return "neutral";
  if (status === "COMPLETED") return "jade";
  return "neutral";
}

function isPendingPayment(order: OrderSummary): boolean {
  return order.status === "PENDING_PAYMENT" && Boolean(order.expires_at);
}

function pendingExpired(order: OrderSummary): boolean {
  return isPendingPayment(order) && isExpired(order.expires_at);
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / 10)));
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[{ label: '首页', to: '/' }, { label: '我的账户', to: '/account' }, { label: '我的订单' }]"
    />

    <SectionHeading
      class="mt-6"
      :level="1"
      eyebrow="Orders"
      title="我的订单"
      description="订单状态、金额与付款结果都以服务端记录为准。待付款订单请在到期前完成付款，到期后可在订单详情重新查询付款结果。"
    >
      <template #actions>
        <AppButton to="/cart" variant="secondary" size="sm">购物车</AppButton>
        <AppButton to="/account/after-sales" variant="secondary" size="sm">售后与退款</AppButton>
      </template>
    </SectionHeading>

    <div class="mt-6 flex flex-wrap gap-2" role="group" aria-label="订单状态筛选">
      <button
        v-for="filter in STATUS_FILTERS"
        :key="filter.value"
        type="button"
        class="min-h-[44px] rounded-sm border px-4 text-sm transition-colors"
        :class="
          statusFilter === filter.value
            ? 'border-amber bg-amber-soft text-amber-strong'
            : 'border-line bg-surface text-ink-soft hover:border-line-strong'
        "
        :aria-pressed="statusFilter === filter.value"
        @click="setStatus(filter.value)"
      >
        {{ filter.label }}
      </button>
    </div>

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="5" aria-label="正在读取订单列表" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : 'error'"
      :title="forbidden ? '无法访问订单列表' : '订单列表暂时无法加载'"
      :description="error"
    >
      <AppButton v-if="errorCode === 'AUTH_REQUIRED' || errorCode === 'SESSION_EXPIRED'" size="sm" to="/login?returnTo=/account/orders">
        重新登录
      </AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="orders.length === 0"
      class="mt-8"
      :title="statusFilter === 'ALL' ? '还没有订单' : '该状态下没有订单'"
      :description="
        statusFilter === 'ALL'
          ? '现货、配件与已通过审批的掉落购买后会生成订单；委托订单在接受报价后创建。'
          : '换个状态筛选，或查看全部订单。'
      "
    >
      <AppButton size="sm" to="/shop">去商城</AppButton>
      <AppButton v-if="statusFilter !== 'ALL'" size="sm" variant="secondary" @click="setStatus('ALL')">
        查看全部订单
      </AppButton>
    </EmptyState>

    <template v-else>
      <p class="mt-6 text-xs text-ink-faint">
        共 {{ total }} 笔订单，第 {{ page }} / {{ totalPages }} 页。列表每页 10 笔。
      </p>

      <ul class="mt-4 space-y-4">
        <li v-for="order in orders" :key="order.id">
          <SurfaceCard interactive>
            <div class="flex flex-wrap items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge :tone="statusTone(order.status)" :label="STATUS_LABEL[order.status]" />
                  <span v-if="order.refund_summary" class="text-xs text-ink-muted">
                    退款状态：{{ order.refund_summary.status }}
                  </span>
                </div>
                <p class="mt-2 font-display text-lg">
                  <RouterLink :to="`/account/orders/${order.id}`" class="hover:text-amber-strong">
                    订单 {{ order.order_no }}
                  </RouterLink>
                </p>
                <p class="mt-1 text-xs text-ink-faint">
                  下单时间 <span class="tabular-nums">{{ formatDateTime(order.created_at) }}</span> ·
                  共 {{ order.item_count }} 件商品
                </p>
              </div>

              <div class="text-right">
                <p class="font-display text-xl tabular-nums">{{ formatMoneyExact(order.total_cents, order.currency) }}</p>
                <p class="text-xs text-ink-faint tabular-nums">
                  已核实收款 {{ formatMoneyExact(order.paid_cents, order.currency) }}
                </p>
                <p v-if="order.refunded_cents > 0" class="text-xs text-ink-faint tabular-nums">
                  已成功退款 {{ formatMoneyExact(order.refunded_cents, order.currency) }}
                </p>
              </div>
            </div>

            <div v-if="order.items.length" class="mt-4 flex flex-wrap gap-3">
              <div
                v-for="item in order.items.slice(0, 3)"
                :key="item.id"
                class="flex items-center gap-3 rounded-sm border border-line bg-surface-muted p-2 pr-4"
              >
                <img
                  v-if="item.image"
                  :src="item.image.thumb ?? item.image.src"
                  :alt="item.image.alt || `${item.product_title} 商品图`"
                  :width="48"
                  :height="48"
                  loading="lazy"
                  decoding="async"
                  class="size-12 rounded-xs object-cover"
                />
                <div class="min-w-0 text-xs">
                  <p class="truncate font-medium text-ink-soft">{{ item.product_title }}</p>
                  <p class="text-ink-faint">{{ item.sku_name }} × {{ item.quantity }}</p>
                </div>
              </div>
              <p v-if="order.items.length > 3" class="self-center text-xs text-ink-faint">
                另有 {{ order.items.length - 3 }} 种商品
              </p>
            </div>

            <!-- 待付款提醒：到期不等于取消 -->
            <div
              v-if="isPendingPayment(order)"
              class="mt-4 rounded-sm px-3 py-2 text-xs"
              :class="pendingExpired(order) ? 'bg-wine-soft text-wine' : 'bg-amber-soft text-amber-strong'"
              role="status"
            >
              <template v-if="pendingExpired(order)">
                付款窗口已到期。付款结果以服务端查单为准，请进入订单详情重新查询；此时订单不一定是已取消。
              </template>
              <template v-else>
                请在 <span class="tabular-nums">{{ formatCountdown(order.expires_at) }}</span> 内完成付款
                （到期时间 {{ formatDateTime(order.expires_at) }}）。
              </template>
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton size="sm" :to="`/account/orders/${order.id}`">查看订单详情</AppButton>
              <AppButton
                v-if="order.status === 'PENDING_PAYMENT'"
                size="sm"
                variant="secondary"
                :to="`/account/orders/${order.id}`"
              >
                继续付款
              </AppButton>
              <AppButton
                v-if="order.status === 'COMPLETED' || order.status === 'SHIPPED'"
                size="sm"
                variant="secondary"
                to="/account/after-sales"
              >
                申请售后
              </AppButton>
            </div>
          </SurfaceCard>
        </li>
      </ul>

      <nav class="mt-8 flex flex-wrap items-center justify-between gap-3" aria-label="订单分页">
        <AppButton size="sm" variant="secondary" :disabled="page <= 1" disabled-reason="已经是第一页" @click="setPage(page - 1)">
          上一页
        </AppButton>
        <p class="text-xs text-ink-faint tabular-nums">第 {{ page }} / {{ totalPages }} 页</p>
        <AppButton
          size="sm"
          variant="secondary"
          :disabled="!hasMore"
          disabled-reason="没有更多订单了"
          @click="setPage(page + 1)"
        >
          下一页
        </AppButton>
      </nav>
    </template>

    <p class="mt-10 text-xs text-ink-faint">
      登录账号：{{ auth.displayName }}。订单列表不缓存到本地，每次进入都会重新读取服务端状态。
    </p>
  </div>
</template>
