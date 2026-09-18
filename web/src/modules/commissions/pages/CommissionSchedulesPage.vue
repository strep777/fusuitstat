<script setup lang="ts">
/**
 * 档期与名额。
 *
 * 规则源：文档/07（名额与档期）。必须分开表述四件事：
 * 提交申请 ≠ 入选 ≠ 预留名额 ≠ 正式占用；公开排期只展示匿名余量。
 * 开放时间与余量由服务端判定；页面不做本地“抢名额”判断。
 */
import { computed, onMounted, ref } from "vue";

import {
  SCHEDULE_STATUS_LABEL,
  fetchScheduleRounds,
  useDocumentTitle,
  type ScheduleRound,
} from "@/modules/commissions/api/commissions";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const site = useSiteStore();
const setTitle = useDocumentTitle();

const rounds = ref<ScheduleRound[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const errorCode = ref("");

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  errorCode.value = "";
  try {
    const payload = await fetchScheduleRounds();
    rounds.value = payload.items;
    const current = payload.items.find((item) => item.status === "open") ?? payload.items[0] ?? null;
    site.setScheduleSummary(
      current
        ? {
            id: current.id,
            title: current.title,
            mode: current.mode,
            status: current.status,
            start_at: current.start_at,
            end_at: current.end_at,
            capacity: current.capacity,
            remaining: current.remaining,
            timezone: current.timezone,
          }
        : null,
    );
  } catch (caught) {
    errorCode.value = caught instanceof ApiError ? caught.code : "";
    error.value = caught instanceof ApiError ? caught.message : "档期数据加载失败。";
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  setTitle("档期与名额");
  void load();
  void site.load();
});

type Group = { key: "open" | "upcoming" | "closed"; title: string; description: string; items: ScheduleRound[] };

const groups = computed<Group[]>(() => [
  {
    key: "open",
    title: "开放中",
    description: "现在可以提交询价与申请，服务端按提交时间与人工筛选处理。",
    items: rounds.value.filter((item) => item.status === "open"),
  },
  {
    key: "upcoming",
    title: "即将开放",
    description: "还没到开始时间，提前了解排期即可；开放前提交的申请不会自动顺延。",
    items: rounds.value.filter((item) => item.status === "upcoming"),
  },
  {
    key: "closed",
    title: "已结束",
    description: "本轮申请与预留都已关闭，仅作为记录保留。",
    items: rounds.value.filter((item) => item.status === "closed"),
  },
]);

const openRounds = computed(() => rounds.value.filter((item) => item.status === "open"));

function statusTone(status: ScheduleRound["status"]) {
  if (status === "open") return "jade" as const;
  if (status === "upcoming") return "amber" as const;
  return "neutral" as const;
}

/** 余量说明：只给匿名数量，不披露申请人信息，也不把余量等同于你能拿到名额。 */
function capacityNote(round: ScheduleRound): string {
  if (round.capacity <= 0) return "名额由工作人员按轮次配置，当前未公开具体数量。";
  if (round.remaining <= 0) return "本轮匿名余量为 0，可以等下一轮开放或先提交询价沟通需求。";
  return `本轮匿名余量 ${round.remaining} / ${round.capacity}，余量只说明还能收到多少申请，不代表已为你保留。`;
}

/** 时区与时间：显示服务端给出的时区，避免把 UTC 当本地时间读。 */
function windowText(round: ScheduleRound): string {
  return `${formatDateTime(round.start_at)} 至 ${formatDateTime(round.end_at)}（${round.timezone}）`;
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '委托', to: '/commission/guide' },
        { label: '档期与名额' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Schedule</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">档期与名额</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        每轮档期有时区、开始时间与截止时间。公开排期只展示匿名余量，不含手机号、姓名、地址、
        量体数据或订单流水号。
      </p>
    </header>

    <!-- 四阶段口径：必须分开表述，避免把“提交申请”说成“已获得名额” -->
    <SurfaceCard class="mt-8">
      <h2 class="font-display text-lg">四个阶段是分开的</h2>
      <ol class="mt-4 grid gap-4 text-sm text-ink-muted sm:grid-cols-2 lg:grid-cols-4">
        <li>
          <p class="font-medium text-ink-soft">1. 提交询价</p>
          <p class="mt-1">你提交需求记录，工作室尚未评估。此时没有名额，也没有付款义务。</p>
        </li>
        <li>
          <p class="font-medium text-ink-soft">2. 入选</p>
          <p class="mt-1">人工筛选通过，可以进入报价流程。入选仍然不等于占用名额。</p>
        </li>
        <li>
          <p class="font-medium text-ink-soft">3. 预留</p>
          <p class="mt-1">
            接受报价后进入预留，预留
            {{ site.commissionPolicy.slot_hold_minutes }} 分钟用于付款；超时未完成收款会进入关闭核对。
          </p>
        </li>
        <li>
          <p class="font-medium text-ink-soft">4. 正式占用</p>
          <p class="mt-1">收到首期款项后才转为正式占用，制作队列按此顺序排期。</p>
        </li>
      </ol>
      <p class="mt-5 rounded-sm bg-canvas-sunk px-4 py-3 text-xs text-ink-muted">
        预留超时进入关闭核对时，工作室会先确认支付渠道没有在途付款，再释放名额；
        不会因为页面刷新或网络延迟直接判定你放弃。
      </p>
    </SurfaceCard>

    <!-- 筛选状态 -->
    <div v-if="loading" class="mt-12">
      <StarLoader message="正在读取档期" hint="需要一点时间" />
    </div>

    <div v-else-if="error" class="mt-12">
      <EmptyState
        tone="error"
        title="档期数据暂时无法加载"
        :description="
          errorCode === 'PROVIDER_UNAVAILABLE'
            ? '网络连接失败或请求超时。恢复后重新加载即可，不会影响已提交的询价。'
            : `${error} 可以重试，也可以先阅读委托指南。`
        "
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
        <AppButton size="sm" variant="secondary" to="/commission/guide">阅读委托指南</AppButton>
      </EmptyState>
    </div>

    <div v-else-if="rounds.length === 0" class="mt-12">
      <EmptyState
        title="当前没有公布的档期"
        description="档期由运营在后台配置并发布。没有公布排期时，可以先提交询价进入沟通，开放后工作人员会按顺序处理。"
      >
        <AppButton size="sm" to="/commission/new">先提交询价</AppButton>
        <AppButton size="sm" variant="secondary" to="/news">查看公告</AppButton>
      </EmptyState>
    </div>

    <template v-else>
      <section v-for="group in groups" :key="group.key" class="mt-12">
        <SectionHeading
          :eyebrow="`${group.items.length} 轮`"
          :title="group.title"
          :description="group.description"
        />
        <div v-if="group.items.length" class="mt-6 grid gap-6 lg:grid-cols-2">
          <SurfaceCard v-for="round in group.items" :key="round.id">
            <div class="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p class="eyebrow">{{ round.mode === 'rush' ? '限时申请' : '普通申请' }}</p>
                <h3 class="mt-2 font-display text-xl">{{ round.title }}</h3>
              </div>
              <StatusBadge :tone="statusTone(round.status)" :label="SCHEDULE_STATUS_LABEL[round.status]" />
            </div>

            <dl class="mt-5 space-y-2 text-sm">
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
                <dt class="text-ink-faint">总名额</dt>
                <dd class="tabular-nums text-ink-soft">
                  {{ round.capacity > 0 ? round.capacity : "未公开" }}
                </dd>
              </div>
              <div class="flex justify-between gap-4">
                <dt class="text-ink-faint">匿名余量</dt>
                <dd class="tabular-nums text-ink-soft">
                  {{ round.capacity > 0 ? round.remaining : "未公开" }}
                </dd>
              </div>
            </dl>

            <p class="mt-4 text-xs leading-relaxed text-ink-faint">{{ capacityNote(round) }}</p>
            <p class="mt-1 text-xs text-ink-faint">申请时间窗口：{{ windowText(round) }}</p>

            <div class="mt-5 flex flex-wrap items-center gap-3">
              <AppButton to="/account/quotes" variant="secondary" size="sm">在账户内查看申请与报价</AppButton>
              <AppButton to="/commission/guide" variant="ghost" size="sm">阅读规则</AppButton>
            </div>
          </SurfaceCard>
        </div>
        <EmptyState
          v-else
          class="mt-6"
          :title="`没有${group.title}的档期`"
          :description="
            group.key === 'closed'
              ? '还没有已结束的档期记录。'
              : '运营发布排期后会出现在这里；没有公布的内容不会用占位信息填充。'
          "
        />
      </section>

      <div class="mt-12 rounded-lg border border-line bg-surface-muted p-5 text-xs leading-relaxed text-ink-muted">
        <p>
          名额容量由服务端用条件更新维护（预留数 + 正式占用数不超过容量），重复提交同一轮申请不会占用第二个位置。
        </p>
        <p class="mt-2">
          公开排期是匿名摘要：它不会显示申请人身份，也不会因为余量充足就承诺你能入选。
          <template v-if="openRounds.length > 0">当前有 {{ openRounds.length }} 轮开放中。</template>
        </p>
      </div>
    </template>
  </div>
</template>
