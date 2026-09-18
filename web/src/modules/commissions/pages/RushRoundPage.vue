<script setup lang="ts">
/**
 * C11 限时拼手速（默认不开放）。
 *
 * 该路由只在功能开关 `rush_rounds` 打开时才被注册；本页是诚实占位页：
 * - 说明机制当前未开放，不伪造排队序号、排位或“成功锁定名额”。
 * - 明确按钮倒计时不具备裁决权，资格以服务端时间与数据库分配的单调序号判定。
 * - 不宣称网络请求先后等同用户实际点击先后。
 * 只有开关打开、且该轮次确实处于限时模式时，才展示服务端返回的时间窗口。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import {
  fetchScheduleRounds,
  useDocumentTitle,
  type ScheduleRound,
} from "@/modules/commissions/api/commissions";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";

const route = useRoute();
const site = useSiteStore();
const setTitle = useDocumentTitle();

const roundId = computed(() => String(route.params.roundId ?? ""));
const enabled = computed(() => site.featureEnabled("rush_rounds"));

const round = ref<ScheduleRound | null>(null);
const loading = ref(true);
const loadError = ref<string | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  try {
    const payload = await fetchScheduleRounds();
    round.value = payload.items.find((item) => item.id === roundId.value) ?? null;
  } catch {
    // 读取失败不影响本页的主要结论：机制未开放。这里只如实说明查不到轮次信息。
    loadError.value = "轮次信息暂时无法加载，不影响下面的说明。";
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  setTitle("限时委托（暂未开放）");
  void site.load();
  void load();
});

const isRushRound = computed(() => round.value?.mode === "rush");

/** 是否真的处于可参与的限时状态：开关 + 服务端判定开放 + 该轮次为限时模式。 */
const openForRush = computed(
  () => enabled.value && isRushRound.value && round.value?.status === "open",
);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '委托', to: '/commission/guide' },
        { label: '限时委托' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <div class="flex flex-wrap items-center gap-3">
        <p class="eyebrow">Rush round</p>
        <StatusBadge tone="wine" label="当前未开放" />
      </div>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">限时委托（拼手速）暂未开放</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        限时拼手速是可选扩展机制，目前没有上线。这个页面存在的意义是把规则和边界讲清楚，
        而不是让你在这里等待一个不存在的名额。普通委托请走「提交询价 → 人工报价 → 接受报价」的流程。
      </p>
    </header>

    <div class="mt-8 rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine">
      <p class="font-medium">这一轮不会放号，也不会有排队结果</p>
      <ul class="mt-3 space-y-2">
        <li>本页不会显示排队序号、排位进度或“你已锁定名额”，因为这些数据现在根本不存在。</li>
        <li>页面上的任何按钮不会开始计时生效；按钮倒计时只用于浏览提示，不具备裁决权。</li>
        <li>轮次编号：<span class="font-mono">{{ roundId || "未提供" }}</span>。</li>
      </ul>
    </div>

    <section class="mt-12">
      <SectionHeading
        eyebrow="Rules"
        title="如果将来启用，判定规则是这些"
        description="先说明清楚，避免上线时出现“谁点得快”的争议。下面描述的是机制约束，不是已经生效的承诺。"
      />

      <div class="mt-8 grid gap-6 lg:grid-cols-2">
        <SurfaceCard>
          <h3 class="font-display text-lg">资格与排位</h3>
          <ul class="mt-4 space-y-3 text-sm text-ink-muted">
            <li>资格以服务端时间判定，不使用你手机或电脑上的本地时间。</li>
            <li>排位由数据库分配的单调序号决定，同一个账号只分配一次序号；重复请求返回原结果。</li>
            <li>不宣称网络请求的先后等同于用户实际点击的先后。</li>
            <li>配套措施包括设计验证码预验证、入口限流、候补与取消释放。</li>
          </ul>
        </SurfaceCard>

        <SurfaceCard>
          <h3 class="font-display text-lg">不满足条件时保持关闭</h3>
          <ul class="mt-4 space-y-3 text-sm text-ink-muted">
            <li>只有公平性说明、限流与压测都通过，开关才会打开。</li>
            <li>压测不通过时保持关闭；关闭期间本页只提供说明，不提供参与入口。</li>
            <li>关闭时不会用“排队中”“即将开始”一类文案制造紧迫感。</li>
          </ul>
        </SurfaceCard>
      </div>
    </section>

    <section class="mt-12">
      <SurfaceCard>
        <h2 class="font-display text-lg">本轮的公开排期信息</h2>

        <div v-if="loading" class="mt-4 space-y-2" role="status" aria-live="polite">
          <p class="text-sm text-ink-muted">正在读取轮次信息…</p>
        </div>

        <div v-else-if="round" class="mt-4">
          <div class="flex flex-wrap items-center gap-3">
            <StatusBadge
              :tone="round.status === 'open' ? 'jade' : round.status === 'upcoming' ? 'amber' : 'neutral'"
              :label="round.status === 'open' ? '开放中' : round.status === 'upcoming' ? '即将开放' : '已结束'"
            />
            <StatusBadge tone="neutral" :label="round.mode === 'rush' ? '限时模式' : '普通申请模式'" />
          </div>
          <h3 class="mt-3 font-display text-lg">{{ round.title }}</h3>
          <dl class="mt-4 space-y-2 text-sm">
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">开始时间</dt>
              <dd class="tabular-nums text-ink-soft">{{ formatDateTime(round.start_at) }}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">截止时间</dt>
              <dd class="tabular-nums text-ink-soft">{{ formatDateTime(round.end_at) }}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">时区</dt>
              <dd class="text-ink-soft">{{ round.timezone }}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">匿名余量</dt>
              <dd class="tabular-nums text-ink-soft">
                {{ round.capacity > 0 ? `${round.remaining} / ${round.capacity}` : "未公开" }}
              </dd>
            </div>
          </dl>
          <p class="mt-4 text-xs leading-relaxed text-ink-faint">
            <template v-if="openForRush">
              这一轮在服务端标记为开放的限时模式，但本站当前仍未启用限时参与入口，请以公告为准。
            </template>
            <template v-else-if="enabled">
              功能开关已打开，但这一轮不是限时模式或尚未开放，因此没有限时参与入口。
            </template>
            <template v-else>
              功能开关 `rush_rounds` 当前为关闭状态，所以这里只有说明，没有参与入口。
              余量数字只是排期摘要，不代表可以抢到名额。
            </template>
          </p>
        </div>

        <EmptyState
          v-else
          class="mt-4"
          :title="loadError ? '轮次信息无法加载' : '没有找到这个轮次'"
          :description="
            loadError ??
            '轮次编号可能已变更或不在公开排期内。公开排期只展示匿名余量，不展示申请人信息。'
          "
        >
          <AppButton size="sm" variant="secondary" to="/commission/schedules">查看全部档期</AppButton>
        </EmptyState>
      </SurfaceCard>
    </section>

    <section class="mt-12 grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
      <SurfaceCard>
        <h2 class="font-display text-lg">想委托时请走普通流程</h2>
        <ol class="mt-4 space-y-3 text-sm text-ink-muted">
          <li>1. 阅读委托指南，确认可接范围与量体方式。</li>
          <li>2. 在档期页确认当前是否开放，然后提交询价。</li>
          <li>3. 收到报价后核对明细、有效期与条款版本，再决定是否接受。</li>
        </ol>
        <div class="mt-5 flex flex-wrap gap-3">
          <AppButton to="/commission/new" size="sm">提交询价</AppButton>
          <AppButton to="/commission/schedules" variant="secondary" size="sm">查看档期与名额</AppButton>
          <AppButton to="/commission/guide" variant="ghost" size="sm">阅读委托指南</AppButton>
        </div>
      </SurfaceCard>

      <SurfaceCard>
        <h2 class="font-display text-lg">有疑问时</h2>
        <p class="mt-3 text-sm text-ink-muted">
          限时机制的公平性说明、压测结果与上线计划会在公告中说明。对排期有疑问可以直接联系客服，
          工作时段为 {{ site.brand.service_hours }}，也可以提交反馈。
        </p>
        <div class="mt-5 flex flex-wrap gap-3">
          <AppButton to="/contact" variant="secondary" size="sm">联系客服</AppButton>
          <AppButton to="/feedback/new" variant="secondary" size="sm">提交反馈</AppButton>
        </div>
      </SurfaceCard>
    </section>
  </div>
</template>
