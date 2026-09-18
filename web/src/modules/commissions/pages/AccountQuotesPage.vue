<script setup lang="ts">
/**
 * 我的询价与报价（列表）。
 *
 * 规则源：文档/03（`/account/quotes`：草稿 / 处理中 / 已报价 / 过期筛选）。
 *
 * 状态：loading / empty / error / forbidden / not-found 齐备。
 * 过期由服务端状态判定；页面不根据本地时间自行把报价说成过期。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import {
  INQUIRY_FILTER_GROUPS,
  INQUIRY_STATUS_LABEL,
  INQUIRY_STATUS_TONE,
  SUIT_TYPE_OPTIONS,
  fetchInquiries,
  useDocumentTitle,
  type Inquiry,
} from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const auth = useAuthStore();
const setTitle = useDocumentTitle();

type FilterKey = "all" | "draft" | "processing" | "quoted" | "expired";
const filter = ref<FilterKey>("all");

const items = ref<Inquiry[]>([]);
const total = ref(0);
const page = ref(1);
const limit = 20;
const loading = ref(true);
const loadingMore = ref(false);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const activeGroup = computed(
  () => INQUIRY_FILTER_GROUPS.find((group) => group.key === filter.value) ?? INQUIRY_FILTER_GROUPS[0]!,
);

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
    const statuses = activeGroup.value.statuses;
    const payload = await fetchInquiries({
      // 单一状态交给服务端过滤；多状态分组一次取回后在前端筛选，避免依赖未约定的多值语义。
      status: statuses.length === 1 ? statuses[0] : "",
      page: targetPage,
      limit,
    });
    const filtered =
      statuses.length > 1 ? payload.items.filter((item) => statuses.includes(item.status)) : payload.items;
    items.value = targetPage === 1 ? filtered : [...items.value, ...filtered];
    total.value = payload.total;
    page.value = targetPage;
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        forbidden.value = true;
      } else if (caught.status === 403) {
        forbidden.value = true;
      } else if (caught.status === 404) {
        notFound.value = true;
      } else {
        error.value = caught.message;
      }
    } else {
      error.value = "询价列表加载失败。";
    }
  } finally {
    loading.value = false;
    loadingMore.value = false;
  }
}

onMounted(async () => {
  setTitle("我的询价与报价");
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

function suitTypeLabel(value: string): string {
  return SUIT_TYPE_OPTIONS.find((item) => item.value === value)?.label ?? value;
}

function budgetText(inquiry: Inquiry): string {
  if (inquiry.budget_min_cents === null && inquiry.budget_max_cents === null) return "未填写预算";
  const min = inquiry.budget_min_cents === null ? "未填" : formatMoneyExact(inquiry.budget_min_cents);
  const max = inquiry.budget_max_cents === null ? "未填" : formatMoneyExact(inquiry.budget_max_cents);
  return `${min} – ${max}`;
}

/** 每个状态给出下一步动作，避免用户停在列表上不知道做什么。 */
function nextAction(inquiry: Inquiry): string {
  switch (inquiry.status) {
    case "DRAFT":
      return "继续填写并提交，或直接删除草稿（撤回）。";
    case "SUBMITTED":
      return "等待工作人员评估；首响为工作日内的 3 个工作日。";
    case "NEEDS_INFO":
      return "需要你补件，进入详情查看说明。";
    case "QUOTED":
      return "报价有有效期，过期后不能转订单。";
    case "ACCEPTED":
      return "已接受并生成订单，转到制作进度查看。";
    case "EXPIRED":
      return "报价已过期。查看最新版本或要求重新评估。";
    case "REJECTED":
      return "本次未承接，详情内会写明原因。";
    case "WITHDRAWN":
      return "已撤回。可以重新提交新的询价。";
    default:
      return "";
  }
}

const counts = computed(() => ({
  all: total.value,
}));
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '询价与报价' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Inquiries & quotes</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">我的询价与报价</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        草稿、处理中、已报价与过期分开筛选。报价有有效期，只有当前有效版本才能转成订单。
      </p>
      <div class="mt-6 flex flex-wrap gap-3">
        <AppButton to="/commission/new">新建询价</AppButton>
        <AppButton to="/commission/guide" variant="secondary">阅读委托指南</AppButton>
      </div>
    </header>

    <!-- 筛选 -->
    <nav class="mt-8" aria-label="询价筛选">
      <ul class="flex flex-wrap gap-2">
        <li v-for="group in INQUIRY_FILTER_GROUPS" :key="group.key">
          <button
            type="button"
            class="min-h-[44px] rounded-sm border px-4 text-sm transition-colors"
            :class="
              filter === group.key
                ? 'border-amber bg-amber-soft font-medium text-amber-strong'
                : 'border-line bg-surface text-ink-soft hover:border-line-strong'
            "
            :aria-pressed="filter === group.key"
            @click="switchFilter(group.key)"
          >
            {{ group.label }}
            <span v-if="group.key === 'all' && total > 0" class="ml-1 tabular-nums text-xs text-ink-faint">
              {{ counts.all }}
            </span>
          </button>
        </li>
      </ul>
      <p class="mt-3 text-xs text-ink-faint">{{ activeGroup.description }}</p>
    </nav>

    <!-- 无权访问 -->
    <EmptyState
      v-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="询价与报价只对本人开放。登录后会回到这一页，草稿仍在你的账户里。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/commission/guide">阅读委托指南</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到这部分内容"
      description="列表地址可能已变化。回到账户首页可以继续查看其他内容。"
    >
      <AppButton size="sm" to="/account">回到我的账户</AppButton>
    </EmptyState>

    <div v-else-if="loading" class="mt-8 space-y-4">
      <div v-for="index in 3" :key="index">
        <SurfaceCard>
          <SkeletonBlock :lines="4" aria-label="正在加载询价列表" />
        </SurfaceCard>
      </div>
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="询价列表加载失败"
      :description="`${error} 网络恢复后可以重试，已提交的询价不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="load(1)">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="items.length === 0"
      class="mt-8"
      :title="filter === 'all' ? '还没有询价记录' : `没有${activeGroup.label}的询价`"
      :description="
        filter === 'all'
          ? '提交询价后会在这里看到状态、报价版本与有效期。'
          : '换个筛选条件看看，或者新建一条询价。'
      "
    >
      <AppButton size="sm" to="/commission/new">提交询价</AppButton>
      <AppButton v-if="filter !== 'all'" size="sm" variant="secondary" @click="switchFilter('all')">
        查看全部
      </AppButton>
    </EmptyState>

    <template v-else>
      <ul class="mt-8 space-y-4">
        <li v-for="inquiry in items" :key="inquiry.id">
          <SurfaceCard interactive>
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div class="min-w-0">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    :tone="INQUIRY_STATUS_TONE[inquiry.status]"
                    :label="INQUIRY_STATUS_LABEL[inquiry.status]"
                  />
                  <StatusBadge tone="neutral" :label="suitTypeLabel(inquiry.suit_type)" />
                </div>
                <h2 class="mt-3 font-display text-lg">
                  {{ inquiry.character_name || "未命名角色" }}
                  <span class="text-sm text-ink-faint">· {{ inquiry.species || "未填物种" }}</span>
                </h2>
                <p class="mt-1 text-xs text-ink-faint">
                  编号 {{ inquiry.id }} · 版本 {{ inquiry.version }} · 更新于
                  {{ formatDateTime(inquiry.updated_at) }}
                </p>
              </div>
              <div class="text-right">
                <p class="text-xs text-ink-faint">预算区间</p>
                <p class="mt-1 text-sm tabular-nums text-ink-soft">{{ budgetText(inquiry) }}</p>
              </div>
            </div>

            <dl class="mt-4 grid gap-2 text-xs text-ink-muted sm:grid-cols-2">
              <div v-if="inquiry.design_asset_ids" class="flex justify-between gap-3">
                <dt>设定图</dt>
                <dd class="tabular-nums">{{ inquiry.design_asset_ids.length }} 张</dd>
              </div>
              <div v-else class="flex justify-between gap-3">
                <dt>设定图</dt>
                <dd>见详情</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt>收货区域</dt>
                <dd>{{ inquiry.delivery_region || "未填写" }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt>提交时间</dt>
                <dd class="tabular-nums">{{ inquiry.submitted_at ? formatDateTime(inquiry.submitted_at) : "未提交" }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt>创建时间</dt>
                <dd class="tabular-nums">{{ formatDateTime(inquiry.created_at) }}</dd>
              </div>
            </dl>

            <p v-if="inquiry.needs_info_note" class="mt-4 rounded-sm bg-wine-soft px-3 py-2 text-xs text-wine">
              待补件：{{ inquiry.needs_info_note }}
            </p>
            <p v-if="inquiry.reject_reason" class="mt-4 rounded-sm bg-canvas-sunk px-3 py-2 text-xs text-ink-muted">
              未承接原因：{{ inquiry.reject_reason }}
            </p>

            <p class="mt-4 text-xs text-ink-faint">{{ nextAction(inquiry) }}</p>

            <div class="mt-5 flex flex-wrap gap-3">
              <AppButton size="sm" :to="`/account/quotes/${inquiry.id}`">查看详情</AppButton>
              <AppButton
                v-if="inquiry.status === 'DRAFT'"
                size="sm"
                variant="secondary"
                :to="`/commission/new?draft=${inquiry.id}`"
              >
                继续填写
              </AppButton>
              <AppButton
                v-if="inquiry.status === 'EXPIRED'"
                size="sm"
                variant="secondary"
                :to="`/account/quotes/${inquiry.id}`"
              >
                查看最新版本
              </AppButton>
            </div>
          </SurfaceCard>
        </li>
      </ul>

      <div v-if="hasMore" class="mt-6">
        <AppButton variant="secondary" :loading="loadingMore" @click="load(page + 1)">加载更多</AppButton>
      </div>

      <p class="mt-6 text-xs text-ink-faint">
        已接受询价不能直接撤回；如需取消，请按订单阶段处理或进入售后。终态记录不会删除，
        重新申请会建立新记录并关联原单。
      </p>
      <p class="mt-2 text-xs text-ink-faint">
        需要帮助？
        <RouterLink to="/contact" class="link-underline hover:text-amber-strong">联系客服</RouterLink>
        或
        <RouterLink to="/feedback/new" class="link-underline hover:text-amber-strong">提交反馈</RouterLink>。
      </p>
    </template>
  </div>
</template>
