<script setup lang="ts">
/**
 * SKU 选择器：属性组合、数量上限与库存状态。
 *
 * 规则（03/08/11 文档）：
 * - 可售性只读服务端字段 `skus[].available` / `in_stock`，前端不自行判定可售，也不推测库存数量。
 * - 库存不足或售罄时禁用加购并在按钮旁写明原因，不靠颜色单独表达。
 * - 需要人工审批的商品（C28）在审批通过前不能加购，并给出申请入口。
 * - 数量上限取 SKU 的 `max_per_order`；未配置时不假装知道库存，只提示以下单校验为准。
 * - 加购动作交给父级处理（父级负责提示与购物车 store），本组件不直接发请求。
 */
import { computed, ref, watch } from "vue";

import {
  specKeyLabel,
  toCurrency,
  type Sku,
} from "@/modules/catalog/api/catalog";
import { formatMoney } from "@/shared/lib/format";
import { AppButton, StatusBadge } from "@/shared/ui/base";

const props = withDefaults(
  defineProps<{
    skus: Sku[];
    /** 商品需要先提交申请并通过人工审批才能购买 */
    requiresApproval?: boolean;
    /** 申请入口（站内路径，已带商品参数） */
    approvalUrl?: string;
    /** 审批已通过且仍在购买时间内时的说明；非空即表示已获准购买 */
    approvalWindowLabel?: string;
    /** 服务端给出的整体可售性（如角色 purchasable）；false 时禁用并显示原因 */
    purchasable?: boolean;
    unavailableReason?: string;
    submitting?: boolean;
    addLabel?: string;
  }>(),
  {
    requiresApproval: false,
    approvalUrl: "",
    approvalWindowLabel: "",
    purchasable: true,
    unavailableReason: "",
    submitting: false,
    addLabel: "加入购物车",
  },
);

const emit = defineEmits<{ add: [payload: { sku: Sku; quantity: number }] }>();

/** 未配置 max_per_order 时的输入上界，仅用于阻止明显异常输入，不代表库存。 */
const FALLBACK_MAX = 99;

const filters = ref<Record<string, string>>({});
const quantity = ref(1);

const attributeKeys = computed(() => {
  const keys: string[] = [];
  for (const sku of props.skus) {
    for (const key of Object.keys(sku.attributes ?? {})) {
      if (!keys.includes(key)) keys.push(key);
    }
  }
  return keys;
});

interface AttributeOption {
  value: string;
  soldOut: boolean;
}

function optionsOf(key: string): AttributeOption[] {
  const values: string[] = [];
  for (const sku of props.skus) {
    const value = sku.attributes?.[key];
    if (value && !values.includes(value)) values.push(value);
  }
  return values.map((value) => ({
    value,
    soldOut: !props.skus.some(
      (sku) => sku.attributes?.[key] === value && sku.available && sku.in_stock,
    ),
  }));
}

const candidates = computed(() =>
  props.skus.filter((sku) =>
    Object.entries(filters.value).every(
      ([key, value]) => !value || sku.attributes?.[key] === value,
    ),
  ),
);

const selectedSku = computed<Sku | null>(() => {
  const list = candidates.value;
  return list.find((sku) => sku.available && sku.in_stock) ?? list[0] ?? null;
});

const maxQuantity = computed(() => {
  const limit = selectedSku.value?.max_per_order ?? 0;
  return limit > 0 ? limit : FALLBACK_MAX;
});

/** 切换规格时数量回到 1；规格不变但上限变小时只做收敛，不重置用户输入。 */
watch(selectedSku, () => {
  quantity.value = 1;
});

watch(maxQuantity, () => {
  if (quantity.value > maxQuantity.value) quantity.value = maxQuantity.value;
  if (quantity.value < 1) quantity.value = 1;
});

const currency = computed(() => toCurrency(selectedSku.value?.currency));
const compareAtCents = computed(() => {
  const sku = selectedSku.value;
  if (!sku || sku.compare_at_cents === null) return null;
  return sku.compare_at_cents > sku.price_cents ? sku.compare_at_cents : null;
});

const stockBadge = computed<{ tone: "neutral" | "amber" | "jade"; label: string }>(() => {
  const sku = selectedSku.value;
  if (!sku) return { tone: "neutral", label: "无可用规格" };
  if (!sku.available) return { tone: "neutral", label: "不可售" };
  if (!sku.in_stock) return { tone: "neutral", label: "已售罄" };
  if (sku.low_stock) return { tone: "amber", label: "库存偏低" };
  return { tone: "jade", label: "有货" };
});

/** 不可加购的可见原因；空字符串表示可以加购。 */
const blockedReason = computed(() => {
  if (!props.purchasable) {
    return props.unavailableReason || "该商品当前不可购买，状态以服务端为准。";
  }
  if (props.requiresApproval && !props.approvalWindowLabel) {
    return "该商品需先提交申请并通过人工审批后才能购买。";
  }
  const sku = selectedSku.value;
  if (!sku) return "该商品暂未配置可售规格，暂时不能加入购物车。";
  if (!sku.available) {
    return `规格「${sku.label}」当前不可售；可售性以服务端下单校验为准。`;
  }
  if (!sku.in_stock) {
    return `规格「${sku.label}」已售罄；售罄仍可浏览，补货后需重新确认价格与库存。`;
  }
  return "";
});

const canAdd = computed(() => blockedReason.value === "" && !props.submitting);

function toggle(key: string, value: string): void {
  const current = filters.value[key] ?? "";
  filters.value = { ...filters.value, [key]: current === value ? "" : value };
}

function stepQuantity(delta: number): void {
  const next = quantity.value + delta;
  if (next < 1 || next > maxQuantity.value) return;
  quantity.value = next;
}

function onQuantityInput(event: Event): void {
  const raw = Number.parseInt((event.target as HTMLInputElement).value, 10);
  if (!Number.isFinite(raw)) {
    quantity.value = 1;
    return;
  }
  quantity.value = Math.min(Math.max(raw, 1), maxQuantity.value);
}

function submit(): void {
  const sku = selectedSku.value;
  if (!canAdd.value || !sku) return;
  emit("add", { sku, quantity: quantity.value });
}
</script>

<template>
  <div class="space-y-5">
    <!-- 审批制商品：先说明规则，再给申请入口（C28） -->
    <div
      v-if="requiresApproval && !approvalWindowLabel"
      class="rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong"
    >
      <p class="font-medium">该商品需先申请，人工审批通过后才能购买</p>
      <p class="mt-1 leading-relaxed">
        掉落商品与服务按“申请 → 人工审批 → 生成审批通过列表 → 按优先级购买”执行；
        未通过审批前不能加入购物车，也不会预留库存。
      </p>
      <AppButton
        v-if="approvalUrl"
        :to="approvalUrl"
        size="sm"
        variant="secondary"
        class="mt-3"
      >
        填写申请表
      </AppButton>
    </div>

    <p
      v-else-if="requiresApproval && approvalWindowLabel"
      class="rounded-lg border border-jade bg-jade-soft p-4 text-sm text-jade"
    >
      审批已通过 · {{ approvalWindowLabel }}；过时后需重新申请。
    </p>

    <!-- 属性组合 -->
    <fieldset v-for="key in attributeKeys" :key="key" class="space-y-2">
      <legend class="text-sm font-medium text-ink-soft">{{ specKeyLabel(key) }}</legend>
      <div class="flex flex-wrap gap-2">
        <button
          v-for="option in optionsOf(key)"
          :key="option.value"
          type="button"
          class="min-h-[44px] rounded-sm border px-3 text-sm transition-colors"
          :class="
            (filters[key] ?? '') === option.value
              ? 'border-amber bg-amber-soft text-amber-strong'
              : 'border-line-strong bg-surface text-ink-soft hover:border-amber'
          "
          :aria-pressed="(filters[key] ?? '') === option.value"
          @click="toggle(key, option.value)"
        >
          {{ option.value }}
          <span v-if="option.soldOut" class="text-xs text-ink-faint">（缺货）</span>
        </button>
      </div>
    </fieldset>

    <!-- 当前规格：价格、库存、重量 -->
    <div v-if="selectedSku" class="rounded-lg border border-line bg-surface-muted p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-sm text-ink-soft">
          <span class="font-medium">{{ selectedSku.label }}</span>
          <span class="ml-2 text-xs text-ink-faint">编码 {{ selectedSku.sku_code }}</span>
        </p>
        <StatusBadge :tone="stockBadge.tone" :label="stockBadge.label" />
      </div>

      <p class="mt-3 flex flex-wrap items-baseline gap-2">
        <span class="font-display text-2xl">
          {{ formatMoney(selectedSku.price_cents, currency) }}
        </span>
        <span v-if="compareAtCents !== null" class="text-sm text-ink-faint line-through">
          {{ formatMoney(compareAtCents, currency) }}
        </span>
        <span class="text-xs text-ink-faint">单价不含运费，最终金额以结算页为准</span>
      </p>

      <p v-if="selectedSku.weight_grams" class="mt-2 text-xs text-ink-faint">
        单件约 {{ selectedSku.weight_grams }} g，用于估算运费；实际以结算页为准。
      </p>
    </div>
    <p v-else class="rounded-lg border border-dashed border-line-strong p-4 text-sm text-ink-muted">
      该商品暂未配置可售规格。上架前需要先完成 SKU 与库存核对，这里不会显示占位规格。
    </p>

    <!-- 运费与售后由页面提供，位置固定在“价格之后、购买按钮之前”（11 文档商品购买顺序） -->
    <div v-if="$slots.shipping" class="rounded-lg border border-line bg-surface p-4">
      <slot name="shipping" />
    </div>
    <div v-if="$slots['after-sales']" class="rounded-lg border border-line bg-surface p-4">
      <slot name="after-sales" />
    </div>

    <!-- 购买按钮：数量与加购放在看完价格、运费与售后之后 -->
    <div v-if="selectedSku" class="flex flex-wrap items-end gap-4">
      <div>
        <label class="text-sm font-medium text-ink-soft" for="sku-quantity">购买数量</label>
        <div class="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            class="size-11 rounded-sm border border-line-strong text-lg text-ink-soft transition-colors hover:border-amber disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="quantity <= 1"
            aria-label="减少数量"
            @click="stepQuantity(-1)"
          >
            −
          </button>
          <input
            id="sku-quantity"
            class="field-base w-20 text-center tabular-nums"
            type="number"
            inputmode="numeric"
            :min="1"
            :max="maxQuantity"
            :value="quantity"
            aria-describedby="sku-quantity-hint"
            @change="onQuantityInput"
          />
          <button
            type="button"
            class="size-11 rounded-sm border border-line-strong text-lg text-ink-soft transition-colors hover:border-amber disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="quantity >= maxQuantity"
            aria-label="增加数量"
            @click="stepQuantity(1)"
          >
            +
          </button>
        </div>
        <p id="sku-quantity-hint" class="mt-1 text-xs text-ink-faint">
          <template v-if="selectedSku.max_per_order">
            该规格每单最多 {{ maxQuantity }} 件（上限由商品配置）。
          </template>
          <template v-else>数量上限与库存最终以服务端下单校验为准。</template>
        </p>
      </div>

      <AppButton
        size="lg"
        :disabled="!canAdd"
        :loading="submitting"
        :disabled-reason="blockedReason"
        @click="submit()"
      >
        {{ addLabel }}
      </AppButton>
    </div>

    <p v-if="blockedReason" class="text-xs text-ink-muted" role="status">{{ blockedReason }}</p>
  </div>
</template>
