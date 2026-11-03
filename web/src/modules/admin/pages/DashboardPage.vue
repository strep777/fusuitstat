<script setup lang="ts">
/**
 * 运营概览：脱敏汇总。
 *
 * 约束：某些项可能为 `null`，表示该模块尚未就绪——界面如实展示「模块未就绪」，
 * 不把它当成 0，也不伪造数字。加载失败要保留重试按钮。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { adminApi, describeFailure, type DashboardPayload, type FailureView } from "@/modules/admin/api/admin";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AdminPageHeader, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ApiError } from "@/shared/lib/http";
import { AppButton, EmptyState, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";

const auth = useAdminAuthStore();

const payload = ref<DashboardPayload | null>(null);
const loading = ref(true);
const error = ref<FailureView | null>(null);

interface Metric {
  key: string;
  group: "orders" | "finance" | "commissions" | "feedback" | "catalog" | "operations";
  field: string;
  label: string;
  to: string;
  permission: string;
  hint: string;
  tone: string;
  index: string;
}

const METRICS: Metric[] = [
  {
    key: "orders.awaiting_payment",
    group: "orders",
    field: "awaiting_payment",
    label: "待付款订单",
    to: "/admin/orders",
    permission: "order.read",
    hint: "超过付款期限由定时任务关闭",
    tone: "bg-[#FFF1C9]",
    index: "01",
  },
  {
    key: "finance.pending_refunds",
    group: "finance",
    field: "pending_refunds",
    label: "待审退款",
    to: "/admin/refunds",
    permission: "refund.request",
    hint: "申请与批准必须由不同员工完成",
    tone: "bg-[#FFE0D7]",
    index: "02",
  },
  {
    key: "finance.reconciliation_differences",
    group: "finance",
    field: "reconciliation_differences",
    label: "对账差异",
    to: "/admin/reconciliation",
    permission: "finance.reconcile",
    hint: "差异必须归因并留下处置记录",
    tone: "bg-[#EDE7FF]",
    index: "03",
  },
  {
    key: "commissions.pending_inquiries",
    group: "commissions",
    field: "pending_inquiries",
    label: "待处理询价",
    to: "/admin/quotes",
    permission: "quote.review",
    hint: "人工首响以经营设置为准",
    tone: "bg-[#DFF3EC]",
    index: "04",
  },
  {
    key: "feedback.pending_reviews",
    group: "feedback",
    field: "pending_reviews",
    label: "待审反馈",
    to: "/admin/feedback",
    permission: "feedback.review",
    hint: "先查重再决定奖励等级",
    tone: "bg-[#E4EEFF]",
    index: "05",
  },
  {
    key: "catalog.low_stock_skus",
    group: "catalog",
    field: "low_stock_skus",
    label: "低库存 SKU",
    to: "/admin/products",
    permission: "catalog.write",
    hint: "提示补货，不自动修改库存",
    tone: "bg-[#FFE9E1]",
    index: "06",
  },
  {
    key: "operations.dead_letter_events",
    group: "operations",
    field: "dead_letter_events",
    label: "死信事件",
    to: "/admin/jobs",
    permission: "ops.read",
    hint: "重试前先看失败原因",
    tone: "bg-[#F1E7D6]",
    index: "07",
  },
];

const QUICK_ACTIONS = [
  { label: "新建内容", to: "/admin/content", permission: "content.write", hint: "公告、页面、FAQ" },
  { label: "商品与库存", to: "/admin/products", permission: "catalog.write", hint: "上架与 SKU" },
  { label: "订单处理", to: "/admin/orders", permission: "order.read", hint: "付款与发货" },
  { label: "审计日志", to: "/admin/audit", permission: "audit.read", hint: "谁改了什么" },
];

const metrics = computed(() => METRICS.filter((item) => auth.can(item.permission)));
const quickActions = computed(() => QUICK_ACTIONS.filter((item) => auth.can(item.permission)));
const featureFlags = computed(() => Object.entries(payload.value?.feature_flags ?? {}));

function metricValue(metric: Metric): number | null {
  const group = payload.value?.[metric.group] as Record<string, unknown> | null | undefined;
  const value = group?.[metric.field];
  return typeof value === "number" ? value : null;
}

function metricMissing(metric: Metric): boolean {
  return payload.value !== null && metricValue(metric) === null;
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    payload.value = await adminApi.dashboard();
  } catch (error_) {
    if (error_ instanceof ApiError && error_.needsLogin) {
      auth.handleSessionExpired();
      return;
    }
    error.value = describeFailure(error_, "运营概览");
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="01 / Overview"
      title="运营概览"
      description="脱敏实时汇总：数字来自服务端统计，点击卡片进入对应模块。不显示手机号、地址、验证码或签名 URL。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="loading" @click="load">刷新汇总</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      v-if="payload"
      tone="info"
      :title="`统计时间 ${formatDateTime(payload.generated_at)} · 当前员工 ${auth.displayName}（${auth.roleLabel}）`"
      description="概览数字只用于排优先级；具体权限仍由服务端逐动作校验，无权限模块不会出现在下面。"
    />

    <div v-if="loading" class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <div v-for="index in 8" :key="index" class="border-2 border-ink bg-surface p-5 shadow-[4px_4px_0_var(--color-ink)]">
        <SkeletonBlock :lines="3" aria-label="正在加载概览数字" />
      </div>
    </div>

    <EmptyState
      v-else-if="error"
      tone="error"
      :title="error.title"
      :description="`${error.description} ${error.retryHint}`"
    >
      <AppButton size="sm" @click="load">重新加载</AppButton>
    </EmptyState>

    <template v-else>
      <div
        v-if="!metrics.length"
        class="border-2 border-dashed border-ink bg-surface-muted px-6 py-10 text-center"
      >
        <p class="eyebrow">NO ACCESS</p>
        <p class="mt-2 font-display text-lg font-bold">当前角色没有任何概览权限</p>
        <p class="mt-2 text-sm text-ink-muted">
          需要权限码 <span class="font-mono">dashboard.read</span>。请联系管理员在「员工与权限」授权。
        </p>
      </div>

      <section v-else aria-label="关键指标">
        <div class="mb-3 flex items-end justify-between gap-4">
          <h2 class="font-mono text-[11px] font-bold uppercase tracking-[0.18em] text-ink-muted">
            Key metrics / 待办
          </h2>
          <p class="text-xs text-ink-faint">共 {{ metrics.length }} 项</p>
        </div>
        <div class="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <RouterLink
            v-for="metric in metrics"
            :key="metric.key"
            :to="metric.to"
            class="group flex min-h-[178px] flex-col border-2 border-ink p-5 text-ink shadow-[4px_4px_0_var(--color-ink)] transition-all hover:-translate-y-1 hover:shadow-[7px_7px_0_var(--color-ink)]"
            :class="metric.tone"
          >
            <div class="flex items-start justify-between gap-3">
              <span class="font-mono text-[10px] font-bold tracking-[0.18em]">{{ metric.index }}</span>
              <StatusBadge
                v-if="metricMissing(metric)"
                tone="wine"
                label="模块未就绪"
              />
              <StatusBadge
                v-else-if="metricValue(metric) === 0"
                tone="jade"
                label="无待处理"
              />
            </div>

            <p class="mt-2 font-display text-lg font-bold">{{ metric.label }}</p>
            <p v-if="metricMissing(metric)" class="mt-2 text-sm leading-snug text-ink-soft">
              后台统计接口尚未上线，显示「未就绪」而不是 0。
            </p>
            <p v-else class="mt-1 font-display text-5xl font-bold tabular-nums leading-none">
              {{ metricValue(metric) }}
            </p>

            <div class="mt-auto flex items-end justify-between gap-2 pt-4">
              <span class="max-w-[15rem] text-xs leading-snug text-ink-soft">{{ metric.hint }}</span>
              <span class="font-mono text-lg transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
            </div>
          </RouterLink>
        </div>
      </section>

      <section v-if="quickActions.length" class="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <RouterLink
          v-for="action in quickActions"
          :key="action.to"
          :to="action.to"
          class="flex items-center justify-between gap-3 border-2 border-ink bg-surface px-4 py-3 text-sm font-bold shadow-[3px_3px_0_var(--color-ink)] transition-colors hover:bg-lemon"
        >
          <span>
            {{ action.label }}
            <span class="mt-0.5 block font-mono text-[10px] font-normal uppercase tracking-wide text-ink-muted">{{ action.hint }}</span>
          </span>
          <span aria-hidden="true">↗</span>
        </RouterLink>
      </section>

      <section v-if="metrics.length" class="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <SurfaceCard>
          <SectionHeading level="2" title="功能开关" description="未启用或未达商业门槛的能力保持关闭：入口隐藏且后端拒绝，不存在绕过开关的接口。" />
          <div v-if="featureFlags.length" class="mt-5 grid gap-2 sm:grid-cols-2">
            <div
              v-for="[flag, enabled] in featureFlags"
              :key="flag"
              class="flex items-center justify-between gap-3 border-2 border-ink bg-canvas-sunk px-3 py-2.5 text-sm"
            >
              <span class="font-mono text-[11px]">{{ flag }}</span>
              <StatusBadge :tone="enabled ? 'jade' : 'neutral'" :label="enabled ? 'ON' : 'OFF'" />
            </div>
          </div>
          <p v-else-if="payload?.feature_flags === null" class="mt-5 text-sm text-ink-muted">
            功能开关接口尚未就绪，这里不显示任何开关状态，也不假设它们是关闭的。
          </p>
          <p v-else class="mt-5 text-sm text-ink-muted">当前没有额外的功能开关。</p>
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading level="2" title="未就绪模块" description="没有数据的模块会显示在这里，避免把「接口未上线」误读成「业务为 0」。" />
          <ul v-if="metrics.some((metric) => metricMissing(metric))" class="mt-5 space-y-2 text-sm">
            <li
              v-for="metric in metrics.filter((item) => metricMissing(item))"
              :key="metric.key"
              class="border-2 border-ink bg-wine-soft px-4 py-3"
            >
              <p class="font-bold">{{ metric.label }}</p>
              <p class="mt-1 text-xs text-ink-soft">统计字段返回空值；模块上线后显示真实数字。</p>
            </li>
          </ul>
          <p v-else class="mt-5 text-sm text-ink-muted">所有已授权模块都返回了统计数字，没有未就绪项。</p>
        </SurfaceCard>
      </section>
    </template>
  </div>
</template>
