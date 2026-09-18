<script setup lang="ts">
/**
 * 售后与退款列表。
 *
 * 规则：
 * - 售后流程状态（OPEN/NEEDS_INFO/APPROVED/RETURNING/RESOLVED/REJECTED）与退款状态
 *   （REQUESTED/APPROVED/PROCESSING/UNKNOWN/SUCCEEDED/FAILED/REJECTED）分开展示。
 * - 退款失败或未知时不能说钱已退，因此这两个状态使用“未确认到账/未成功”的直白文案。
 * - 状态用文字表达，不依赖颜色；筛选进 URL。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import {
  fetchAfterSales,
  type AfterSaleStatus,
  type AfterSaleSummary,
  type RefundStatus,
} from "@/modules/commerce/api/commerce";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
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

const STATUS_FILTERS: Array<{ value: AfterSaleStatus | "ALL"; label: string }> = [
  { value: "ALL", label: "全部" },
  { value: "OPEN", label: "已受理" },
  { value: "NEEDS_INFO", label: "待补件" },
  { value: "APPROVED", label: "已通过" },
  { value: "RETURNING", label: "退货寄回中" },
  { value: "RESOLVED", label: "已处理完成" },
  { value: "REJECTED", label: "未通过" },
];

const AFTER_SALE_LABEL: Record<AfterSaleStatus, string> = {
  OPEN: "已受理，等待客服核查",
  NEEDS_INFO: "需要补充信息",
  APPROVED: "已通过，等待寄回商品",
  RETURNING: "退货寄回中",
  RESOLVED: "已处理完成",
  REJECTED: "未通过，可在售后详情查看说明",
};

const REFUND_LABEL: Record<RefundStatus, string> = {
  REQUESTED: "退款已申请，等待审核",
  APPROVED: "退款已批准，等待执行",
  PROCESSING: "退款处理中，尚未确认到账",
  UNKNOWN: "退款结果未知，尚未确认到账",
  SUCCEEDED: "退款已成功",
  FAILED: "退款未成功",
  REJECTED: "退款被拒绝",
};

const items = ref<AfterSaleSummary[]>([]);
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
const statusFilter = computed<AfterSaleStatus | "ALL">(() => {
  const raw = String(route.query["status"] ?? "ALL");
  const found = STATUS_FILTERS.find((item) => item.value === raw);
  return found ? found.value : "ALL";
});
const totalPages = computed(() => Math.max(1, Math.ceil(total.value / 10)));

let requestSeq = 0;

async function load(): Promise<void> {
  const seq = ++requestSeq;
  loading.value = true;
  error.value = null;
  errorCode.value = null;
  forbidden.value = false;
  try {
    const payload = await fetchAfterSales({ status: statusFilter.value, page: page.value, limit: 10 });
    if (seq !== requestSeq) return;
    items.value = payload.items;
    total.value = payload.total;
    hasMore.value = payload.has_more;
  } catch (caught) {
    if (seq !== requestSeq) return;
    if (caught instanceof ApiError) {
      errorCode.value = caught.code;
      forbidden.value = caught.status === 403;
      error.value = caught.needsLogin
        ? "登录状态已过期，请重新登录后查看售后记录。"
        : caught.message || "售后列表加载失败。";
    } else {
      error.value = "售后列表加载失败，请稍后重试。";
    }
  } finally {
    if (seq === requestSeq) loading.value = false;
  }
}

onMounted(load);
watch(() => [route.query["status"], route.query["page"]], () => void load());

function setStatus(value: AfterSaleStatus | "ALL"): void {
  void router.push({
    name: "account-after-sales",
    query: { status: value === "ALL" ? undefined : value, page: undefined },
  });
}

function setPage(next: number): void {
  void router.push({
    name: "account-after-sales",
    query: {
      status: statusFilter.value === "ALL" ? undefined : statusFilter.value,
      page: next <= 1 ? undefined : String(next),
    },
  });
}

function saleTone(status: AfterSaleStatus): "jade" | "amber" | "wine" | "neutral" {
  if (status === "RESOLVED") return "jade";
  if (status === "NEEDS_INFO" || status === "RETURNING") return "amber";
  if (status === "REJECTED") return "wine";
  return "neutral";
}

function refundTone(status: RefundStatus): "jade" | "amber" | "wine" | "neutral" {
  if (status === "SUCCEEDED") return "jade";
  if (status === "FAILED" || status === "REJECTED") return "wine";
  if (status === "UNKNOWN" || status === "PROCESSING" || status === "REQUESTED") return "amber";
  return "neutral";
}

function fulfillmentLabel(type: string): string {
  if (type === "commission" || type === "custom") return "定制";
  if (type === "exclusive_character") return "独占角色";
  if (type === "service") return "服务";
  return "现货/配件";
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[{ label: '首页', to: '/' }, { label: '我的账户', to: '/account' }, { label: '售后与退款' }]"
    />

    <SectionHeading
      class="mt-6"
      :level="1"
      eyebrow="After sales"
      title="售后与退款"
      description="售后流程与退款渠道结果分开记录。退款是否到账以服务端查证结果为准，处理中的退款不计为已经退钱。"
    >
      <template #actions>
        <AppButton to="/account/orders" variant="secondary" size="sm">我的订单</AppButton>
        <AppButton to="/contact" variant="secondary" size="sm">联系客服</AppButton>
      </template>
    </SectionHeading>

    <div class="mt-6 flex flex-wrap gap-2" role="group" aria-label="售后状态筛选">
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
      <SkeletonBlock :lines="5" aria-label="正在读取售后记录" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : 'error'"
      :title="forbidden ? '无法访问售后记录' : '售后记录暂时无法加载'"
      :description="error"
    >
      <AppButton
        v-if="errorCode === 'AUTH_REQUIRED' || errorCode === 'SESSION_EXPIRED'"
        size="sm"
        to="/login?returnTo=/account/after-sales"
      >
        重新登录
      </AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="items.length === 0"
      class="mt-8"
      :title="statusFilter === 'ALL' ? '还没有售后申请' : '该状态下没有售后记录'"
      :description="
        statusFilter === 'ALL'
          ? '需要退货、维修或退款时，请在订单详情页提交售后申请；申请后可以在这里跟踪处理记录与退款结果。'
          : '换个状态筛选，或查看全部售后记录。'
      "
    >
      <AppButton size="sm" to="/account/orders">去订单列表申请售后</AppButton>
      <AppButton v-if="statusFilter !== 'ALL'" size="sm" variant="secondary" @click="setStatus('ALL')">
        查看全部
      </AppButton>
    </EmptyState>

    <template v-else>
      <p class="mt-6 text-xs text-ink-faint">
        共 {{ total }} 条售后记录，第 {{ page }} / {{ totalPages }} 页。
      </p>

      <ul class="mt-4 space-y-4">
        <li v-for="item in items" :key="item.id">
          <SurfaceCard interactive>
            <div class="flex flex-wrap items-start justify-between gap-4">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge :tone="saleTone(item.status)" :label="AFTER_SALE_LABEL[item.status]" />
                  <StatusBadge tone="neutral" :label="fulfillmentLabel(item.fulfillment_type)" />
                </div>
                <p class="mt-2 font-display text-lg">
                  <RouterLink :to="`/account/after-sales/${item.id}`" class="hover:text-amber-strong">
                    {{ item.reason }}
                  </RouterLink>
                </p>
                <p class="mt-1 text-xs text-ink-faint">
                  订单 <span class="tabular-nums">{{ item.order_no }}</span> ·
                  提交于 <span class="tabular-nums">{{ formatDateTime(item.created_at) }}</span>
                </p>
              </div>

              <div class="text-right text-sm">
                <p v-if="item.item" class="text-ink-soft">{{ item.item.product_title }}</p>
                <p v-if="item.item" class="text-xs text-ink-faint">
                  {{ item.item.sku_name }} × {{ item.item.quantity }} ·
                  <span class="tabular-nums">
                    {{ formatMoneyExact(item.item.line_total_cents, item.item.currency) }}
                  </span>
                </p>
              </div>
            </div>

            <div v-if="item.refund_status" class="mt-4 rounded-sm bg-surface-muted p-3 text-sm">
              <div class="flex flex-wrap items-center justify-between gap-3">
                <p class="text-ink-soft">
                  退款状态：<span class="font-medium">{{ REFUND_LABEL[item.refund_status] }}</span>
                </p>
                <StatusBadge :tone="refundTone(item.refund_status)" :label="REFUND_LABEL[item.refund_status]" />
              </div>
              <p
                v-if="item.refund_status === 'UNKNOWN' || item.refund_status === 'FAILED' || item.refund_status === 'PROCESSING'"
                class="mt-2 text-xs text-ink-muted"
              >
                这笔退款尚未确认成功到账。失败或未知都不是“钱已退”，服务端会继续查单或按原渠道重试。
              </p>
            </div>
            <p v-else class="mt-4 text-xs text-ink-faint">尚未生成退款记录：售后审批通过并执行退款后才会产生。</p>

            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton size="sm" :to="`/account/after-sales/${item.id}`">查看处理记录</AppButton>
              <AppButton size="sm" variant="secondary" :to="`/account/orders/${item.order_id}`">查看订单</AppButton>
            </div>
          </SurfaceCard>
        </li>
      </ul>

      <nav class="mt-8 flex flex-wrap items-center justify-between gap-3" aria-label="售后分页">
        <AppButton size="sm" variant="secondary" :disabled="page <= 1" disabled-reason="已经是第一页" @click="setPage(page - 1)">
          上一页
        </AppButton>
        <p class="text-xs text-ink-faint tabular-nums">第 {{ page }} / {{ totalPages }} 页</p>
        <AppButton
          size="sm"
          variant="secondary"
          :disabled="!hasMore"
          disabled-reason="没有更多记录了"
          @click="setPage(page + 1)"
        >
          下一页
        </AppButton>
      </nav>

      <p class="mt-8 text-xs text-ink-faint">
        退款金额只统计已成功且指向已分配收款的退款；多付款与异常资金单独记录，不冲减正常订单实收。
      </p>
    </template>
  </div>
</template>
