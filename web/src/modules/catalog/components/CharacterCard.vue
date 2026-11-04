<script setup lang="ts">
/**
 * 角色卡：角色汤列表使用。
 *
 * 约束：保留不等于成交（03 文档），状态必须用文字说明；
 * 独占角色标注“库存 1”；售价与授权范围在详情页可见。
 * featured 用于只有一位角色时占满内容区的横向卡。
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import {
  GRID_IMAGE_SIZES,
  characterStatusMeta,
  mediaSrc,
  mediaSrcset,
  toCurrency,
  type CharacterSummary,
} from "@/modules/catalog/api/catalog";
import { formatMoney, truncate } from "@/shared/lib/format";
import { StatusBadge } from "@/shared/ui/base";

const props = withDefaults(defineProps<{ character: CharacterSummary; featured?: boolean }>(), {
  featured: false,
});

const cover = computed(
  () => props.character.cover ?? props.character.images?.[0] ?? null,
);
const status = computed(() => characterStatusMeta(props.character.status));
const imageSizes = computed(() =>
  props.featured ? "(min-width: 768px) 52vw, 100vw" : GRID_IMAGE_SIZES,
);
</script>

<template>
  <RouterLink
    :to="`/characters/${character.slug}`"
    class="group overflow-hidden rounded-2xl bg-surface shadow-soft transition-shadow hover:shadow-lift"
    :class="featured ? 'grid md:grid-cols-[1.08fr_0.92fr]' : 'flex flex-col'"
  >
    <div
      class="overflow-hidden bg-[#f2efe9]"
      :class="featured ? 'aspect-[4/3] md:aspect-auto md:min-h-[420px]' : 'aspect-[4/5]'"
    >
      <img
        v-if="cover"
        :src="mediaSrc(cover)"
        :srcset="mediaSrcset(cover) || undefined"
        :sizes="imageSizes"
        :width="cover.width"
        :height="cover.height"
        :alt="cover.alt || `${character.name} 设定图`"
        loading="lazy"
        decoding="async"
        class="size-full object-cover"
      />
      <div v-else class="flex size-full items-center justify-center text-xs text-ink-faint">
        设定图准备中
      </div>
    </div>

    <div :class="featured ? 'flex flex-col justify-center p-7 sm:p-10 lg:p-12' : 'flex flex-1 flex-col p-4'">
      <div class="flex flex-wrap items-center gap-1.5">
        <StatusBadge :tone="status.tone" :label="status.label" />
        <StatusBadge v-if="character.exclusive" tone="neutral" label="独占 · 库存 1" />
      </div>

      <h3
        class="mt-3 font-display font-medium tracking-tight text-ink"
        :class="featured ? 'text-3xl sm:text-4xl' : 'text-lg'"
      >
        {{ character.name }}
      </h3>
      <p class="mt-1.5 text-xs text-ink-faint">
        <span v-if="character.species">{{ character.species }}</span>
        <span v-if="character.species && character.designer_credit"> · </span>
        <span v-if="character.designer_credit">设定 {{ character.designer_credit }}</span>
      </p>
      <p
        v-if="character.summary"
        class="mt-3 text-ink-muted"
        :class="featured ? 'max-w-md text-sm leading-relaxed' : 'text-xs'"
      >
        {{ truncate(character.summary, featured ? 140 : 56) }}
      </p>

      <p class="font-display text-ink" :class="featured ? 'mt-8 text-3xl' : 'mt-auto pt-4 text-lg'">
        <template v-if="character.price_cents !== null">
          {{ formatMoney(character.price_cents, toCurrency(character.currency)) }}
        </template>
        <span v-else class="text-sm text-ink-muted">售价待公布</span>
      </p>
      <p v-if="featured" class="mt-5 inline-flex items-center gap-2 text-xs text-ink-soft">
        <template v-if="status.open">查看设定与领养 <span aria-hidden="true">→</span></template>
        <span v-else>不可购买：{{ status.label }}</span>
      </p>
      <p v-else-if="!status.open" class="mt-1 text-xs text-ink-faint">不可购买：{{ status.label }}</p>
    </div>
  </RouterLink>
</template>
