<script setup lang="ts">
/**
 * 报价版本卡片。
 *
 * 规则源：文档/07（报价版本）、文档/11（报价详情信息顺序）。
 *
 * 信息顺序：当前版本/有效期 → 明细与总额 → 首期/尾款 → 包含及例外 → 条款 → 接受或重新评估。
 * 过期或旧版本报价不能转订单：禁用接受并给出明确原因与“查看最新版本”入口，
 * 绝不静默按新价格处理。
 */
import { computed } from "vue";

import {
  quoteBalanceCents,
  type Quote,
} from "@/modules/commissions/api/commissions";
import { AppButton, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatCountdown, formatDateTime, formatMoneyExact } from "@/shared/lib/format";
import { percentOf } from "@/shared/lib/format";

const props = defineProps<{
  quote: Quote;
  /** 是否为当前可接受的版本（服务端 QUOTED 状态下发布的最新版本）。 */
  current?: boolean;
  /** 是否存在更新的版本；有值时提供“查看最新版本”。 */
  hasNewerVersion?: boolean;
  /** 已经转成订单：不再提供接受入口。 */
  accepted?: boolean;
  accepting?: boolean;
}>();

const emit = defineEmits<{
  (event: "accept", payload: { quoteId: string; quoteVersion: number; termsVersion: string }): void;
  (event: "view-latest"): void;
}>();

const expired = computed(() => {
  const target = new Date(props.quote.expires_at).getTime();
  return !Number.isNaN(target) && target <= Date.now();
});

const balanceCents = computed(() => quoteBalanceCents(props.quote));

const isSuperseded = computed(
  () => props.quote.status === "SUPERSEDED" || (!props.current && !props.accepted),
);

/** 接受入口的可用性：任何一条不满足都说明原因，而不是只把按钮置灰。 */
const blockedReason = computed(() => {
  if (props.accepted || props.quote.status === "ACCEPTED") return "这份报价已经转成订单，不能重复接受。";
  if (expired.value || props.quote.status === "EXPIRED") {
    return "这份报价已过期，不能转到订单。";
  }
  if (isSuperseded.value) return "这份报价已被更新版本替代，不能按旧版本下单。";
  if (!props.current) return "这份报价不是当前可接受的版本。";
  return "";
});

const canAccept = computed(() => blockedReason.value === "");

const statusLabel = computed(() => {
  if (props.accepted || props.quote.status === "ACCEPTED") return "已接受";
  if (expired.value || props.quote.status === "EXPIRED") return "已过期";
  if (isSuperseded.value) return "已被新版本替代";
  return "当前版本";
});

const statusTone = computed(() => {
  if (props.accepted || props.quote.status === "ACCEPTED") return "jade" as const;
  if (expired.value || props.quote.status === "EXPIRED") return "wine" as const;
  if (isSuperseded.value) return "neutral" as const;
  return "amber" as const;
});

function accept(): void {
  if (!canAccept.value) return;
  emit("accept", {
    quoteId: props.quote.id,
    quoteVersion: props.quote.version,
    termsVersion: props.quote.terms_version,
  });
}
</script>

<template>
  <SurfaceCard>
    <!-- 1. 当前版本与有效期 -->
    <div class="flex flex-wrap items-start justify-between gap-3">
      <div>
        <p class="eyebrow">Quote v{{ quote.version }}</p>
        <h3 class="mt-2 font-display text-lg">报价版本 {{ quote.version }}</h3>
      </div>
      <StatusBadge :tone="statusTone" :label="statusLabel" />
    </div>

    <dl class="mt-4 space-y-2 text-sm">
      <div class="flex flex-wrap justify-between gap-4">
        <dt class="text-ink-faint">有效期至</dt>
        <dd class="tabular-nums text-ink-soft">
          {{ formatDateTime(quote.expires_at) }}
          <span class="ml-2 text-xs" :class="expired ? 'text-wine' : 'text-ink-faint'">
            {{ expired ? "已到期" : `剩余 ${formatCountdown(quote.expires_at)}` }}
          </span>
        </dd>
      </div>
      <div class="flex flex-wrap justify-between gap-4">
        <dt class="text-ink-faint">发布时间</dt>
        <dd class="tabular-nums text-ink-soft">{{ formatDateTime(quote.created_at) }}</dd>
      </div>
      <div v-if="quote.estimated_start" class="flex flex-wrap justify-between gap-4">
        <dt class="text-ink-faint">预计开始</dt>
        <dd class="tabular-nums text-ink-soft">{{ formatDateTime(quote.estimated_start) }}</dd>
      </div>
      <div v-if="quote.estimated_delivery" class="flex flex-wrap justify-between gap-4">
        <dt class="text-ink-faint">预计交付</dt>
        <dd class="tabular-nums text-ink-soft">{{ formatDateTime(quote.estimated_delivery) }}</dd>
      </div>
      <div class="flex flex-wrap justify-between gap-4">
        <dt class="text-ink-faint">发布人</dt>
        <dd class="text-ink-soft">{{ quote.issuer_name }}</dd>
      </div>
    </dl>

    <p class="mt-3 text-xs text-ink-faint">
      预计时间属于计划值；实际进度以制作页面的阶段记录为准。
    </p>

    <!-- 2. 明细与总额 -->
    <div class="mt-6">
      <h4 class="text-sm font-medium text-ink-soft">明细</h4>
      <ul class="mt-3 divide-y divide-line border-y border-line text-sm">
        <li v-for="line in quote.line_items" :key="line.code" class="flex flex-wrap justify-between gap-3 py-2.5">
          <span>
            <span class="text-ink-soft">{{ line.label }}</span>
            <span v-if="line.note" class="mt-0.5 block text-xs text-ink-faint">{{ line.note }}</span>
          </span>
          <span
            class="tabular-nums"
            :class="line.amount_cents < 0 ? 'text-jade' : 'text-ink'"
          >
            {{ formatMoneyExact(line.amount_cents, quote.currency === "USD" ? "USD" : "CNY") }}
          </span>
        </li>
        <li v-if="quote.line_items.length === 0" class="py-3 text-sm text-ink-faint">
          这份报价没有明细行，请联系工作人员确认。
        </li>
      </ul>
      <div class="mt-3 flex flex-wrap items-baseline justify-between gap-3">
        <span class="text-sm text-ink-muted">总额</span>
        <span class="font-display text-2xl tabular-nums">
          {{ formatMoneyExact(quote.total_cents, quote.currency === "USD" ? "USD" : "CNY") }}
        </span>
      </div>
    </div>

    <!-- 3. 首期与尾款 -->
    <div class="mt-5 grid gap-4 sm:grid-cols-2">
      <div class="rounded-sm bg-canvas-sunk p-4">
        <p class="text-xs text-ink-faint">首期预付款</p>
        <p class="mt-1 font-display text-lg tabular-nums">
          {{ formatMoneyExact(quote.prepayment_cents, quote.currency === "USD" ? "USD" : "CNY") }}
        </p>
        <p class="mt-1 text-xs text-ink-faint">
          占总额 {{ percentOf(quote.prepayment_cents, quote.total_cents) }}%
        </p>
      </div>
      <div class="rounded-sm bg-canvas-sunk p-4">
        <p class="text-xs text-ink-faint">尾款</p>
        <p class="mt-1 font-display text-lg tabular-nums">
          {{ formatMoneyExact(balanceCents, quote.currency === "USD" ? "USD" : "CNY") }}
        </p>
        <p class="mt-1 text-xs text-ink-faint">与首期相加等于总额，差额照实展示。</p>
      </div>
    </div>
    <p class="mt-2 text-xs text-ink-faint">首期款项在合同说明中称为“预付款”，不称为“定金”。</p>

    <!-- 4. 包含及例外 -->
    <div class="mt-6 grid gap-4 sm:grid-cols-2">
      <div>
        <h4 class="text-sm font-medium text-ink-soft">包含</h4>
        <ul class="mt-2 space-y-1.5 text-sm text-ink-muted">
          <li class="flex gap-2">
            <span class="mt-2 size-1.5 shrink-0 rounded-full bg-jade" aria-hidden="true" />
            <span>免费修改 {{ quote.included_revisions }} 次（阶段确认时提出）</span>
          </li>
          <li class="flex gap-2">
            <span class="mt-2 size-1.5 shrink-0 rounded-full bg-jade" aria-hidden="true" />
            <span>出厂记录与阶段图片留存</span>
          </li>
        </ul>
      </div>
      <div>
        <h4 class="text-sm font-medium text-ink-soft">不含 / 例外</h4>
        <ul v-if="quote.exclusions.length" class="mt-2 space-y-1.5 text-sm text-ink-muted">
          <li v-for="line in quote.exclusions" :key="line" class="flex gap-2">
            <span class="mt-2 size-1.5 shrink-0 rounded-full bg-line-strong" aria-hidden="true" />
            <span>{{ line }}</span>
          </li>
        </ul>
        <p v-else class="mt-2 text-sm text-ink-faint">这份报价没有列出例外项。</p>
      </div>
    </div>

    <!-- 5. 条款 -->
    <div class="mt-6 rounded-sm border border-line px-4 py-3 text-sm">
      <div class="flex flex-wrap justify-between gap-3">
        <span class="text-ink-faint">条款版本</span>
        <span class="font-mono text-ink-soft">{{ quote.terms_version }}</span>
      </div>
      <p class="mt-2 text-xs text-ink-faint">
        接受报价时会同时提交这个条款版本；网站勾选留证不等同具有可靠电子签名能力。
      </p>
      <div class="mt-2">
        <AppButton to="/commission/contract" variant="ghost" size="sm">阅读条款说明与示例</AppButton>
      </div>
    </div>

    <!-- 6. 接受或重新评估 -->
    <div class="mt-6">
      <div v-if="blockedReason" class="rounded-lg border border-wine bg-wine-soft p-4 text-sm text-wine" role="status">
        <p class="font-medium">{{ blockedReason }}</p>
        <p v-if="expired" class="mt-2 text-xs">
          过期报价不能转到订单，也不会被静默按新价格处理。你可以查看最新版本，或要求重新评估。
        </p>
        <div v-if="hasNewerVersion" class="mt-3">
          <AppButton size="sm" variant="secondary" @click="emit('view-latest')">查看最新版本</AppButton>
        </div>
      </div>
      <div v-else class="flex flex-wrap items-center gap-3">
        <AppButton :loading="accepting" @click="accept">
          接受这份报价（v{{ quote.version }}）
        </AppButton>
        <AppButton to="/commission/contract" variant="secondary">先看条款</AppButton>
      </div>
      <p class="mt-3 text-xs text-ink-faint">
        接受时会一并提交报价版本 v{{ quote.version }} 与条款版本 {{ quote.terms_version }}；
        同一份报价重复提交只会生成一个订单。
      </p>
    </div>
  </SurfaceCard>
</template>
