<template>
  <div class="space-y-5">
    <!-- 审批制商品：先说明规则，再给申请入口（C28） -->
    <div
      v-if="requiresApproval && !approvalWindowLabel"
      class="rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong"
    >
      <p class="font-medium">{{ t('skuPicker.approvalTitle') }}</p>
      <p class="mt-1 leading-relaxed">
        {{ t('skuPicker.approvalNote') }}
      </p>
      <AppButton
        v-if="approvalUrl"
        :to="approvalUrl"
        size="sm"
        variant="secondary"
        class="mt-3"
      >
        {{ t('skuPicker.apply') }}
      </AppButton>
    </div>

    <p
      v-else-if="requiresApproval && approvalWindowLabel"
      class="rounded-lg border border-jade bg-jade-soft p-4 text-sm text-jade"
    >
      {{ t('skuPicker.approved', { window: approvalWindowLabel }) }}
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
          <span v-if="option.soldOut" class="text-xs text-ink-faint">{{ t('skuPicker.outOfStock') }}</span>
        </button>
      </div>
    </fieldset>

    <!-- 当前规格：价格、库存、重量 -->
    <div v-if="selectedSku" class="rounded-lg border border-line bg-surface-muted p-4">
      <div class="flex flex-wrap items-center justify-between gap-2">
        <p class="text-sm text-ink-soft">
          <span class="font-medium">{{ selectedSku.label }}</span>
          <span class="ml-2 text-xs text-ink-faint">{{ t('skuPicker.code', { code: selectedSku.sku_code }) }}</span>
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
        <span class="text-xs text-ink-faint">{{ t('skuPicker.shippingExcluded') }}</span>
      </p>

      <p v-if="selectedSku.weight_grams" class="mt-2 text-xs text-ink-faint">
        {{ t('skuPicker.weight', { grams: selectedSku.weight_grams }) }}
      </p>
    </div>
    <p v-else class="rounded-lg border border-dashed border-line-strong p-4 text-sm text-ink-muted">
      {{ t('skuPicker.empty') }}
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
        <label class="text-sm font-medium text-ink-soft" for="sku-quantity">{{ t('skuPicker.quantity') }}</label>
        <div class="mt-1.5 flex items-center gap-2">
          <button
            type="button"
            class="size-11 rounded-sm border border-line-strong text-lg text-ink-soft transition-colors hover:border-amber disabled:cursor-not-allowed disabled:opacity-50"
            :disabled="quantity <= 1"
            :aria-label="t('skuPicker.decrease')"
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
            :aria-label="t('skuPicker.increase')"
            @click="stepQuantity(1)"
          >
            +
          </button>
        </div>
        <p id="sku-quantity-hint" class="mt-1 text-xs text-ink-faint">
          <template v-if="selectedSku.max_per_order">
            {{ t('skuPicker.max', { count: maxQuantity }) }}
          </template>
          <template v-else>{{ t('skuPicker.quantityNote') }}</template>
        </p>
      </div>

      <AppButton
        size="lg"
        :disabled="!canAdd"
        :loading="submitting"
        :disabled-reason="blockedReason"
        @click="submit()"
      >
        {{ addLabel || t('skuPicker.add') }}
      </AppButton>
    </div>

    <p v-if="blockedReason" class="text-xs text-ink-muted" role="status">{{ blockedReason }}</p>
  </div>
</template>

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
import { useI18n } from "@/shared/lib/i18n";
import { formatMoney } from "@/shared/lib/format";
import { AppButton, StatusBadge } from "@/shared/ui/base";

const { t } = useI18n();

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
    addLabel: "",
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
  if (!sku) return { tone: "neutral", label: t('skuPicker.noSku') };
  if (!sku.available) return { tone: "neutral", label: t('skuPicker.unavailable') };
  if (!sku.in_stock) return { tone: "neutral", label: t('skuPicker.soldOut') };
  if (sku.low_stock) return { tone: "amber", label: t('skuPicker.lowStock') };
  return { tone: "jade", label: t('skuPicker.inStock') };
});

/** 不可加购的可见原因；空字符串表示可以加购。 */
const blockedReason = computed(() => {
  if (!props.purchasable) {
    return props.unavailableReason || t('skuPicker.blocked');
  }
  if (props.requiresApproval && !props.approvalWindowLabel) {
    return t('skuPicker.approvalRequired');
  }
  const sku = selectedSku.value;
  if (!sku) return t('skuPicker.noVariants');
  if (!sku.available) {
    return t('skuPicker.notAvailable', { label: sku.label });
  }
  if (!sku.in_stock) {
    return t('skuPicker.variantSoldOut', { label: sku.label });
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
