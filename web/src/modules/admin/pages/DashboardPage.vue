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
  key: keyof DashboardPayload;
  label: string;
  to: string;
  permission: string;
  hint: string;
}

const METRICS: Metric[] = [
  { key: "pending_payment_orders", label: "待付款订单", to: "/admin/orders", permission: "order.read", hint: "超过付款期限的订单由定时任务关闭" },
  { key: "pending_refunds", label: "待审退款", to: "/admin/refunds", permission: "refund.request", hint: "申请与批准必须由不同员工完成" },
  { key: "reconciliation_differences", label: "对账差异", to: "/admin/reconciliation", permission: "finance.reconcile", hint: "差异必须归因并留下处置记录" },
  { key: "open_inquiries", label: "待处理询价", to: "/admin/quotes", permission: "quote.review", hint: "人工首响时间以经营设置为准" },
  { key: "pending_feedback", label: "待审反馈", to: "/admin/feedback", permission: "feedback.review", hint: "先查重再决定奖励等级" },
  { key: "low_stock_skus", label: "低库存 SKU", to: "/admin/products", permission: "catalog.write", hint: "按阈值提示补货，不自动改库存" },
  { key: "dead_letter_events", label: "死信事件", to: "/admin/jobs", permission: "ops.read", hint: "人工重试前先看失败原因" },
];

const metrics = computed(() => METRICS.filter((item) => auth.can(item.permission)));

const featureFlags = computed(() => Object.entries(payload.value?.feature_flags ?? {}));

function metricValue(metric: Metric): number | null {
  const value = payload.value?.[metric.key];
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
      eyebrow="Overview"
      title="运营概览"
      description="只显示脱敏汇总数字：不显示手机号、地址、验证码或签名 URL。数字来自服务端实时统计，点击卡片进入对应模块。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="loading" @click="load">刷新汇总</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      v-if="payload"
      tone="info"
      :title="`统计时间 ${formatDateTime(payload.generated_at)}，当前员工 ${auth.displayName}（${auth.roleLabel}）`"
      description="概览数字只用于排优先级；具体权限仍由服务端逐动作校验，缺少权限码的模块不会出现在下面。"
    />

    <div v-if="loading" class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <div v-for="index in 6" :key="index" class="rounded-lg border border-line bg-surface p-5">
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
      <div v-if="!metrics.length" class="rounded-lg border border-dashed border-line-strong bg-surface-muted px-6 py-10 text-center">
        <p class="eyebrow">无权访问</p>
        <p class="mt-2 font-display text-lg">当前角色没有任何概览权限</p>
        <p class="mt-2 text-sm text-ink-muted">
          需要的权限码是 <span class="font-mono">dashboard.read</span>。
          请联系管理员在「员工与权限」里授权；服务端仍会逐动作校验权限。
        </p>
      </div>

      <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <SurfaceCard v-for="metric in metrics" :key="metric.key">
          <div class="flex items-start justify-between gap-3">
            <p class="eyebrow">{{ metric.label }}</p>
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

          <p v-if="metricMissing(metric)" class="mt-4 text-sm text-ink-muted">
            该模块的后台统计接口还没有上线，所以这里显示「未就绪」而不是 0。
          </p>
          <p v-else class="mt-4 font-display text-4xl tabular-nums">{{ metricValue(metric) }}</p>

          <p class="mt-3 text-xs text-ink-faint">{{ metric.hint }}</p>
          <RouterLink
            :to="metric.to"
            class="link-underline mt-4 inline-block text-sm text-amber-strong hover:text-amber"
          >
            进入{{ metric.label }}模块
          </RouterLink>
        </SurfaceCard>
      </div>

      <section v-if="metrics.length" class="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
        <SurfaceCard>
          <SectionHeading level="2" title="功能开关" description="未启用或未达商业门槛的能力保持关闭：入口隐藏且后端拒绝，不存在绕过开关的接口。" />
          <div v-if="featureFlags.length" class="mt-5 space-y-2">
            <div
              v-for="[flag, enabled] in featureFlags"
              :key="flag"
              class="flex items-center justify-between gap-4 rounded-sm border border-line px-4 py-3 text-sm"
            >
              <span class="font-mono text-xs">{{ flag }}</span>
              <StatusBadge :tone="enabled ? 'jade' : 'neutral'" :label="enabled ? '已启用' : '未启用'" />
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
              class="rounded-sm border border-line px-4 py-3"
            >
              <p class="font-medium">{{ metric.label }}</p>
              <p class="mt-1 text-xs text-ink-faint">
                统计字段返回空值。对应模块上线后这里会显示真实数字。
              </p>
            </li>
          </ul>
          <p v-else class="mt-5 text-sm text-ink-muted">
            所有已授权模块都返回了统计数字，没有未就绪项。
          </p>
        </SurfaceCard>
      </section>
    </template>
  </div>
</template>
