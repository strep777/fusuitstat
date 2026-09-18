<script setup lang="ts">
/**
 * 商品卡：商城列表与相关推荐共用。
 *
 * 约束（11 文档）：图片固定 width/height + srcset/sizes + alt；售罄仍可打开详情，
 * 库存状态用文字徽章表达，不靠颜色单独区分；需要人工审批的商品在卡片上就能看到。
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import {
  GRID_IMAGE_SIZES,
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

const props = defineProps<{ product: ProductSummary }>();

const cover = computed(() => props.product.images?.[0] ?? null);
const availability = computed(() => productAvailabilityMeta(props.product.availability));
const categoryLabel = computed(() => productCategoryLabel(props.product.category));
const kindLabel = computed(() => productKindLabel(props.product.kind));
</script>

<template>
  <RouterLink
    :to="`/shop/${product.slug}`"
    class="group flex flex-col overflow-hidden rounded-lg border border-line bg-surface transition-shadow hover:shadow-lift"
  >
    <div class="aspect-square overflow-hidden bg-canvas-sunk">
      <img
        v-if="cover"
        :src="mediaSrc(cover)"
        :srcset="mediaSrcset(cover) || undefined"
        :sizes="GRID_IMAGE_SIZES"
        :width="cover.width"
        :height="cover.height"
        :alt="cover.alt || `${product.title} 商品图`"
        loading="lazy"
        decoding="async"
        class="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
      />
      <div v-else class="flex size-full items-center justify-center text-xs text-ink-faint">
        图片准备中
      </div>
    </div>

    <div class="flex flex-1 flex-col p-4">
      <div class="flex flex-wrap items-center gap-1.5">
        <StatusBadge :tone="availability.tone" :label="availability.label" />
        <StatusBadge v-if="categoryLabel" tone="neutral" :label="categoryLabel" />
        <StatusBadge v-if="product.requires_approval" tone="amber" label="需先申请审批" />
      </div>

      <h3 class="mt-3 text-sm font-medium">{{ product.title }}</h3>
      <p v-if="product.subtitle" class="mt-1 text-xs text-ink-faint">
        {{ truncate(product.subtitle, 40) }}
      </p>
      <p v-if="kindLabel && !categoryLabel" class="mt-1 text-xs text-ink-faint">{{ kindLabel }}</p>

      <p class="mt-auto pt-4 font-display text-lg">
        <template v-if="product.price_from_cents !== null">
          {{ formatMoney(product.price_from_cents, toCurrency(product.currency)) }}
          <span class="text-xs text-ink-faint">起</span>
        </template>
        <span v-else class="text-sm text-ink-muted">价格待公布</span>
      </p>
    </div>
  </RouterLink>
</template>
