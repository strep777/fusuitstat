<script setup lang="ts">
/**
 * 角色卡：领养池列表使用。
 *
 * 约束：保留不等于成交（03 文档），状态必须用文字说明；
 * 独占角色标注“库存 1”；售价与授权范围在详情页可见，卡片给出状态与价格入口。
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

const props = defineProps<{ character: CharacterSummary }>();

const cover = computed(
  () => props.character.cover ?? props.character.images?.[0] ?? null,
);
const status = computed(() => characterStatusMeta(props.character.status));
</script>

<template>
  <RouterLink
    :to="`/characters/${character.slug}`"
    class="group flex flex-col overflow-hidden rounded-lg border border-line bg-surface transition-shadow hover:shadow-lift"
  >
    <div class="aspect-[4/5] overflow-hidden bg-canvas-sunk">
      <img
        v-if="cover"
        :src="mediaSrc(cover)"
        :srcset="mediaSrcset(cover) || undefined"
        :sizes="GRID_IMAGE_SIZES"
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

    <div class="flex flex-1 flex-col p-4">
      <div class="flex flex-wrap items-center gap-1.5">
        <StatusBadge :tone="status.tone" :label="status.label" />
        <StatusBadge v-if="character.exclusive" tone="night" label="独占 · 库存 1" />
      </div>

      <h3 class="mt-3 font-display text-base">{{ character.name }}</h3>
      <p class="mt-1 text-xs text-ink-faint">
        <span v-if="character.species">{{ character.species }}</span>
        <span v-if="character.species && character.designer_credit"> · </span>
        <span v-if="character.designer_credit">设定 {{ character.designer_credit }}</span>
      </p>
      <p v-if="character.summary" class="mt-2 text-xs text-ink-muted">
        {{ truncate(character.summary, 56) }}
      </p>

      <p class="mt-auto pt-4 font-display text-lg">
        <template v-if="character.price_cents !== null">
          {{ formatMoney(character.price_cents, toCurrency(character.currency)) }}
        </template>
        <span v-else class="text-sm text-ink-muted">售价待公布</span>
      </p>
      <p v-if="!status.open" class="mt-1 text-xs text-ink-faint">不可购买：{{ status.label }}</p>
    </div>
  </RouterLink>
</template>
