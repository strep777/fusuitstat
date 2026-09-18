<script setup lang="ts">
/**
 * 我的反馈（列表）。
 *
 * 规则源：文档/03（`/account/feedback`：反馈列表与奖励结果）。
 *
 * 处理状态与奖励状态分开显示：处理完成不等于一定奖励，奖励需要独立审核。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useDocumentTitle } from "@/modules/commissions/api/commissions";
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_CATEGORY_LABEL,
  FEEDBACK_STATUS_LABEL,
  REWARD_STATUS_LABEL,
  fetchFeedbackList,
  type FeedbackSummary,
} from "@/modules/feedback/api/feedback";
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const auth = useAuthStore();
const setTitle = useDocumentTitle();

type FilterKey = "all" | "open" | "resolved";
const filter = ref<FilterKey>("all");

const items = ref<FeedbackSummary[]>([]);
const total = ref(0);
const page = ref(1);
const limit = 20;
const loading = ref(true);
const loadingMore = ref(false);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const FILTERS: Array<{ key: FilterKey; label: string; description: string; statuses: string[] }> = [
  { key: "all", label: "全部", description: "所有本人提交的反馈。", statuses: [] },
  {
    key: "open",
    label: "处理中",
    description: "已提交、已受理、待补充或正在处理的反馈。",
    statuses: ["SUBMITTED", "TRIAGED", "NEEDS_INFO", "IN_PROGRESS"],
  },
  {
    key: "resolved",
    label: "已结束",
    description: "已解决、已关闭、重复或未采纳的反馈。",
    statuses: ["RESOLVED", "CLOSED", "DUPLICATE", "REJECTED"],
  },
];

const activeFilter = computed(() => FILTERS.find((item) => item.key === filter.value) ?? FILTERS[0]!);

async function load(targetPage = 1): Promise<void> {
  if (targetPage === 1) {
    loading.value = true;
    items.value = [];
  } else {
    loadingMore.value = true;
  }
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  try {
    const payload = await fetchFeedbackList({ page: targetPage, limit });
    const statuses = activeFilter.value.statuses;
    const filtered = statuses.length > 0 ? payload.items.filter((item) => statuses.includes(item.status)) : payload.items;
    items.value = targetPage === 1 ? filtered : [...items.value, ...filtered];
    total.value = payload.total;
    page.value = targetPage;
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) forbidden.value = true;
      else if (caught.status === 404) notFound.value = true;
      else error.value = caught.message;
    } else {
      error.value = "反馈列表加载失败。";
    }
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

onMounted(async () => {
  setTitle("我的反馈");
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
  if (!auth.isAuthenticated) {
    forbidden.value = true;
    loading.value = false;
    return;
  }
  await load(1);
});

function switchFilter(key: FilterKey): void {
  if (filter.value === key) return;
  filter.value = key;
  void load(1);
}

const hasMore = computed(() => items.value.length < total.value && items.value.length >= limit);

function categoryLabel(value: string): string {
  return FEEDBACK_CATEGORY_LABEL[value] ?? value;
}

/** 状态 → 语气；不靠颜色单独表达，标签文本本身自解释。 */
function statusTone(status: string): "neutral" | "amber" | "jade" | "wine" {
  if (status === "RESOLVED" || status === "CLOSED") return "jade";
  if (status === "REJECTED" || status === "DUPLICATE") return "wine";
  if (status === "IN_PROGRESS" || status === "TRIAGED") return "amber";
  return "neutral";
}

function rewardTone(status: string): "neutral" | "amber" | "jade" | "wine" {
  if (status === "GRANTED") return "jade";
  if (status === "NOT_ELIGIBLE" || status === "REVOKED") return "wine";
  if (status === "APPROVED") return "jade";
  return "neutral";
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '我的反馈' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">My feedback</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">我的反馈</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        处理状态与奖励状态分开显示。奖励需要人工审核；没有获得奖励时，详情页会给出可读理由，并保留申诉入口。
      </p>
      <div class="mt-6 flex flex-wrap gap-3">
        <AppButton to="/feedback/new">提交反馈</AppButton>
        <AppButton to="/account/points" variant="secondary">查看星绒币</AppButton>
      </div>
    </header>

    <nav class="mt-8" aria-label="反馈筛选">
      <ul class="flex flex-wrap gap-2">
        <li v-for="item in FILTERS" :key="item.key">
          <button
            type="button"
            class="min-h-[44px] rounded-sm border px-4 text-sm transition-colors"
            :class="
              filter === item.key
                ? 'border-amber bg-amber-soft font-medium text-amber-strong'
                : 'border-line bg-surface text-ink-soft hover:border-line-strong'
            "
            :aria-pressed="filter === item.key"
            @click="switchFilter(item.key)"
          >
            {{ item.label }}
          </button>
        </li>
      </ul>
      <p class="mt-3 text-xs text-ink-faint">{{ activeFilter.description }}</p>
    </nav>

    <EmptyState
      v-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="反馈记录只对本人开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到反馈记录"
      description="列表地址可能已变化。回到账户首页可以继续查看其他内容。"
    >
      <AppButton size="sm" to="/account">回到我的账户</AppButton>
    </EmptyState>

    <div v-else-if="loading" class="mt-8 space-y-4">
      <div v-for="index in 3" :key="index">
        <SurfaceCard>
          <SkeletonBlock :lines="3" aria-label="正在加载反馈列表" />
        </SurfaceCard>
      </div>
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="反馈列表加载失败"
      :description="`${error} 可以重试；已提交的反馈不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="load(1)">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="items.length === 0"
      class="mt-8"
      :title="filter === 'all' ? '还没有提交过反馈' : `没有${activeFilter.label}的反馈`"
      :description="
        filter === 'all'
          ? '提交后可以在这里跟进处理进度、奖励结果与申诉。'
          : '换个筛选条件看看，或者提交一条新的反馈。'
      "
    >
      <AppButton size="sm" to="/feedback/new">提交反馈</AppButton>
      <AppButton v-if="filter !== 'all'" size="sm" variant="secondary" @click="switchFilter('all')">查看全部</AppButton>
    </EmptyState>

    <template v-else>
      <ul class="mt-8 space-y-4">
        <li v-for="item in items" :key="item.id">
          <SurfaceCard interactive>
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge tone="neutral" :label="categoryLabel(item.category)" />
                  <StatusBadge :tone="statusTone(item.status)" :label="FEEDBACK_STATUS_LABEL[item.status] ?? item.status" />
                  <StatusBadge
                    :tone="rewardTone(item.reward_status)"
                    :label="`奖励：${REWARD_STATUS_LABEL[item.reward_status] ?? item.reward_status}`"
                  />
                </div>
                <h2 class="mt-3 font-display text-lg">{{ item.title }}</h2>
                <p class="mt-1 text-xs text-ink-faint">
                  编号 {{ item.id }} · 提交于 {{ formatDateTime(item.created_at) }} · 更新于
                  {{ formatDateTime(item.updated_at) }}
                </p>
                <p class="mt-2 text-xs text-ink-muted">
                  页面路径：<span class="font-mono">{{ truncate(item.page_path, 60) }}</span>
                </p>
                <p v-if="item.duplicate_of_summary" class="mt-2 rounded-sm bg-canvas-sunk px-3 py-2 text-xs text-ink-muted">
                  与已有反馈重复：{{ truncate(item.duplicate_of_summary, 80) }}
                </p>
              </div>
              <AppButton size="sm" :to="`/account/feedback/${item.id}`">查看详情</AppButton>
            </div>
          </SurfaceCard>
        </li>
      </ul>

      <div v-if="hasMore" class="mt-6">
        <AppButton variant="secondary" :loading="loadingMore" @click="load(page + 1)">加载更多</AppButton>
      </div>
    </template>

    <p class="mt-8 text-xs text-ink-faint">
      反馈分类包括
      <template v-for="(item, index) in FEEDBACK_CATEGORIES" :key="item.value">
        {{ item.label }}<template v-if="index < FEEDBACK_CATEGORIES.length - 1">、</template>
      </template>
      。涉及具体订单的售后会转到售后单处理，同时不影响你提交网站问题。
    </p>
    <p class="mt-2 text-xs text-ink-faint">
      需要补充信息或对结果有疑问时，可以
      <RouterLink to="/contact" class="link-underline hover:text-amber-strong">联系客服</RouterLink>
      ，或在详情页提交申诉。
    </p>
  </div>
</template>
