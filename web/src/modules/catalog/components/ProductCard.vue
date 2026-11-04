<template>
  <RouterLink
    :to="`${locale === 'en-US' ? '/en' : ''}/shop/${product.slug}`"
    class="group flex flex-col overflow-hidden rounded-2xl bg-surface shadow-soft transition-all duration-300 hover:-translate-y-1 hover:shadow-lift"
  >
    <div class="relative aspect-square overflow-hidden bg-[#f2e5d8]">
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
        class="size-full object-cover transition-transform duration-700 group-hover:scale-[1.03]"
      />
      <div v-else class="flex size-full flex-col items-center justify-center gap-2 text-ink-faint">
        <span class="text-4xl font-light text-brand/40" aria-hidden="true">✦</span>
        <span class="text-[11px]">{{ t("productCard.imagePending") }}</span>
      </div>

      <div class="absolute left-3 top-3 flex flex-wrap gap-1.5">
        <StatusBadge :tone="availability.tone" :label="availability.label" />
        <StatusBadge v-if="product.requires_approval" tone="grape" :label="t('productCard.approval')" />
        <span
          v-if="isDemo"
          class="rounded-full bg-canvas-sunk px-2.5 py-1 text-[10px] text-ink-muted"
        >
          演示
        </span>
      </div>
      <span
        v-if="categoryLabel"
        class="absolute right-3 top-3 rounded-full bg-surface/85 px-2.5 py-1 text-[10px] text-ink-muted backdrop-blur"
      >
        {{ categoryLabel }}
      </span>
    </div>

    <div class="flex flex-1 flex-col p-4">
      <p v-if="product.subtitle" class="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
        {{ truncate(product.subtitle, 28) }}
      </p>
      <h3 class="mt-1.5 line-clamp-2 font-display text-base font-medium leading-snug">
        {{ displayTitle }}
      </h3>
      <p v-if="kindLabel && !categoryLabel" class="mt-1 text-xs text-ink-faint">{{ kindLabel }}</p>

      <div class="mt-auto flex items-end justify-between gap-2 pt-5">
        <p class="font-display text-base font-medium text-brand-strong">
          <template v-if="product.price_from_cents !== null">
            {{ formatMoney(product.price_from_cents, toCurrency(product.currency)) }}
            <span class="text-[11px] font-normal text-ink-muted">{{ t("productCard.from") }}</span>
          </template>
          <span v-else class="text-sm font-normal text-ink-muted">{{ t("productCard.pricePending") }}</span>
        </p>
        <span
          class="inline-flex size-7 shrink-0 items-center justify-center rounded-full bg-canvas-sunk text-xs text-ink-muted transition-colors group-hover:bg-brand group-hover:text-white"
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
