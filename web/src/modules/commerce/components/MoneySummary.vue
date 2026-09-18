<script setup lang="ts">
/**
 * 金额摘要。用于购物车、结算预览、订单详情与售后详情，付款摘要始终可见（11 文档）。
 *
 * 规则：
 * - 金额全部来自服务端，组件只做展示，不参与计算（08 文档：应付金额由服务端计算）。
 * - 已付金额不可手改，因此这里只读；差异行（已付/已退/待付）用文字标注口径，不只靠加粗或颜色。
 * - 使用 `formatMoneyExact`，避免四舍五入造成与账单不一致的显示。
 */
import { computed } from "vue";

import { formatMoneyExact, formatPoints, type CurrencyCode } from "@/shared/lib/format";
import { SurfaceCard } from "@/shared/ui/base";

const props = withDefaults(
  defineProps<{
    goodsCents?: number | null;
    discountCents?: number | null;
    /** 积分抵扣（C15）：与优惠分开显示，退款按原订单分摊返还时口径才清楚。 */
    pointsDiscountCents?: number | null;
    pointsName?: string;
    pointsRedeemed?: number | null;
    shippingCents?: number | null;
    totalCents: number;
    currency?: CurrencyCode;
    /** 已付金额：只计 APPLIED 分配，来自服务端。 */
    paidCents?: number | null;
    /** 已成功退款金额：只计已成功且指向 APPLIED 的分配。 */
    refundedCents?: number | null;
    /** 物流/优惠等附加说明行。 */
    extraLines?: Array<{ label: string; value: string }>;
    title?: string;
    note?: string;
    /** 强调金额（如退款金额）时的替换总额标签。 */
    totalLabel?: string;
    compact?: boolean;
  }>(),
  {
    goodsCents: null,
    discountCents: null,
    pointsDiscountCents: null,
    pointsName: "星绒币",
    pointsRedeemed: null,
    shippingCents: null,
    currency: "CNY",
    paidCents: null,
    refundedCents: null,
    extraLines: () => [],
    title: "金额摘要",
    note: "",
    totalLabel: "应付合计",
    compact: false,
  },
);

const rows = computed(() => {
  const list: Array<{ label: string; value: string; tone: "normal" | "discount" | "muted" }> = [];
  if (props.goodsCents !== null && props.goodsCents !== undefined) {
    list.push({
      label: "商品金额",
      value: formatMoneyExact(props.goodsCents, props.currency),
      tone: "normal",
    });
  }
  if (props.discountCents !== null && props.discountCents !== undefined && props.discountCents > 0) {
    list.push({
      label: "优惠抵扣",
      value: `-${formatMoneyExact(props.discountCents, props.currency)}`,
      tone: "discount",
    });
  }
  if (
    props.pointsDiscountCents !== null &&
    props.pointsDiscountCents !== undefined &&
    props.pointsDiscountCents > 0
  ) {
    const used =
      props.pointsRedeemed !== null && props.pointsRedeemed !== undefined && props.pointsRedeemed > 0
        ? `（${formatPoints(props.pointsRedeemed)} ${props.pointsName}）`
        : "";
    list.push({
      label: `${props.pointsName}抵扣${used}`,
      value: `-${formatMoneyExact(props.pointsDiscountCents, props.currency)}`,
      tone: "discount",
    });
  }
  if (props.shippingCents !== null && props.shippingCents !== undefined) {
    list.push({
      label: "运费",
      value: formatMoneyExact(props.shippingCents, props.currency),
      tone: "normal",
    });
  }
  if (props.paidCents !== null && props.paidCents !== undefined) {
    list.push({
      label: "已核实收款",
      value: formatMoneyExact(props.paidCents, props.currency),
      tone: "muted",
    });
  }
  if (props.refundedCents !== null && props.refundedCents !== undefined && props.refundedCents > 0) {
    list.push({
      label: "已成功退款",
      value: formatMoneyExact(props.refundedCents, props.currency),
      tone: "muted",
    });
  }
  return list;
});

const outstanding = computed(() => {
  if (props.paidCents === null || props.paidCents === undefined) return null;
  const refunded = props.refundedCents ?? 0;
  const value = props.totalCents - props.paidCents + refunded;
  if (value <= 0) return null;
  return value;
});
</script>

<template>
  <SurfaceCard :padded="!compact">
    <h3 class="font-display text-base">{{ title }}</h3>

    <dl class="mt-4 space-y-2 text-sm">
      <div v-for="row in rows" :key="row.label" class="flex items-baseline justify-between gap-4">
        <dt :class="row.tone === 'muted' ? 'text-ink-faint' : 'text-ink-muted'">{{ row.label }}</dt>
        <dd class="tabular-nums" :class="row.tone === 'muted' ? 'text-ink-muted' : 'text-ink-soft'">
          {{ row.value }}
        </dd>
      </div>
      <div
        v-for="row in extraLines"
        :key="row.label"
        class="flex items-baseline justify-between gap-4 text-ink-faint"
      >
        <dt>{{ row.label }}</dt>
        <dd class="tabular-nums">{{ row.value }}</dd>
      </div>
    </dl>

    <div class="mt-4 flex items-baseline justify-between gap-4 border-t border-line pt-4">
      <p class="text-sm font-medium text-ink-soft">{{ totalLabel }}</p>
      <p class="font-display text-xl tabular-nums">{{ formatMoneyExact(totalCents, currency) }}</p>
    </div>

    <p v-if="outstanding !== null" class="mt-3 rounded-sm bg-amber-soft px-3 py-2 text-xs text-amber-strong">
      待付金额 {{ formatMoneyExact(outstanding, currency) }}（按应付合计减已核实收款，再加已成功退款计算）。
    </p>

    <p v-if="note" class="mt-3 text-xs text-ink-faint">{{ note }}</p>
  </SurfaceCard>
</template>
