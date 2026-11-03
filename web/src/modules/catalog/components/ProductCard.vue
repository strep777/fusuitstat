<template>
  <RouterLink
    :to="`${locale === 'en-US' ? '/en' : ''}/shop/${product.slug}`"
    class="group flex flex-col overflow-hidden rounded-sm border-2 border-ink bg-surface shadow-[4px_4px_0_var(--color-ink)] transition duration-200 hover:-translate-y-1 hover:shadow-[7px_7px_0_var(--color-ink)]"
  >
    <div class="relative aspect-square overflow-hidden border-b-2 border-ink bg-canvas-sunk">
      <img
        v-if="cover"
        :src="mediaSrc(cover)"
        :srcset="mediaSrcset(cover) || undefined"
        :sizes="GRID_IMAGE_SIZES"
        :width="cover.width"
        :height="cover.height"
        :alt="cover.alt || t('productCard.imageAlt', { title: product.title })"
        loading="lazy"
        decoding="async"
        class="size-full object-cover transition-transform duration-500 group-hover:scale-[1.04]"
      />
      <div v-else class="flex size-full flex-col items-center justify-center gap-2 bg-[#FFE3DB] text-[#6B5D74]">
        <span class="text-4xl font-black text-brand/50" aria-hidden="true">✦</span>
        <span class="text-xs">{{ t("productCard.imagePending") }}</span>
      </div>

      <div class="absolute left-3 top-3 flex flex-wrap gap-1.5">
        <StatusBadge :tone="availability.tone" :label="availability.label" />
        <StatusBadge v-if="product.requires_approval" tone="grape" :label="t('productCard.approval')" />
        <span
          v-if="isDemo"
          class="rounded-full border-2 border-ink bg-surface px-2 py-0.5 font-mono text-[10px] font-bold text-ink"
        >
          演示
        </span>
      </div>
      <span
        v-if="categoryLabel"
        class="absolute right-0 top-3 border-2 border-r-0 border-ink bg-lemon px-2.5 py-1 font-mono text-[10px] font-bold uppercase tracking-wide text-[#17121C]"
      >
        {{ categoryLabel }}
      </span>
    </div>

    <div class="flex flex-1 flex-col p-4">
      <p v-if="product.subtitle" class="font-mono text-[10px] font-bold uppercase tracking-wide text-brand-strong">
        {{ truncate(product.subtitle, 28) }}
      </p>
      <h3 class="mt-1 line-clamp-2 font-display text-lg font-bold leading-snug">
        {{ displayTitle }}
      </h3>
      <p v-if="kindLabel && !categoryLabel" class="mt-1 text-xs text-ink-faint">{{ kindLabel }}</p>

      <div class="mt-auto flex items-end justify-between gap-2 pt-4">
        <p class="font-display text-xl font-bold text-brand-strong">
          <template v-if="product.price_from_cents !== null">
            {{ formatMoney(product.price_from_cents, toCurrency(product.currency)) }}
            <span class="text-xs font-semibold text-ink-muted">{{ t("productCard.from") }}</span>
          </template>
          <span v-else class="text-sm font-medium text-ink-muted">{{ t("productCard.pricePending") }}</span>
        </p>
        <span
          class="inline-flex size-8 shrink-0 items-center justify-center rounded-full border-2 border-ink bg-surface text-sm font-bold transition-colors group-hover:bg-brand group-hover:text-white"
          aria-hidden="true"
        >
          →
        </span>
      </div>
    </div>
  </RouterLink>
</template>

<script setup lang="ts">
/**
 * 商品卡：商城列表与专题页共用。
 *
 * 约束（11 文档）：图片固定 width/height + srcset/sizes + alt；售罄仍可打开详情，
 * 库存状态用文字徽章表达，不靠颜色单独区分；需要人工审批的商品在卡片上就能看到。
 */
import { computed } from "vue";
import { useI18n } from "@/shared/lib/i18n";
import { RouterLink } from "vue-router";

import {
  DEMO_TITLE_MARK,
  GRID_IMAGE_SIZES,
  displayProductTitle,
  mediaSrc,
  mediaSrcset,
  productAvailabilityMeta,
  productCategoryLabel,
  productKindLabel,
  toCurrency,
  type ProductSummary,
} from "@/modules/catalog/api/catalog";
import { formatMoney, truncate } from "@/shared/lib/format";
import { StatusBadge } from "@/shared/ui/base";

const { t, locale } = useI18n();
const props = defineProps<{ product: ProductSummary }>();

const cover = computed(() => props.product.images?.[0] ?? null);
const availability = computed(() => productAvailabilityMeta(props.product.availability));
const categoryLabel = computed(() => productCategoryLabel(props.product.category));
const kindLabel = computed(() => productKindLabel(props.product.kind));
const DEMO_SUFFIX = DEMO_TITLE_MARK;
const isDemo = computed(() => props.product.title.includes(DEMO_SUFFIX));
const displayTitle = computed(() => displayProductTitle(props.product.title));
</script>
