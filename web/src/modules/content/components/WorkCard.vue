<script setup lang="ts">
/**
 * 作品卡片：作品集网格、相关作品与详情页推荐共用同一张卡。
 *
 * 图片规则（11 文档）：固定比例容器 + 固定 width/height，srcset/sizes 选派生图，
 * 折叠下懒加载；alt 描述角色与角度，装饰性缺失时给出可读描述而不是空字符串。
 * 只有拿到公开展示授权的作品才会出现在列表里（FR-002），卡片不做任何状态猜测。
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import type { ContentSummary } from "@/modules/content/api/content";
import { truncate } from "@/shared/lib/format";
import { StatusBadge } from "@/shared/ui/base";

const props = withDefaults(
  defineProps<{
    work: ContentSummary;
    /** 与所在网格一致，避免浏览器选到过大的派生图 */
    sizes?: string;
    /** 首屏可见的第一张卡片才用 eager */
    eager?: boolean;
  }>(),
  {
    sizes: "(min-width: 1280px) 25vw, (min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw",
    eager: false,
  },
);

const cover = computed(() => props.work.cover);

const coverAlt = computed(() => {
  const description = cover.value?.alt?.trim();
  if (description) return description;
  // 服务端未提供描述时，用作品名与类型生成可读替代文本，而不是留空
  const parts = [props.work.title, props.work.work_type, props.work.species].filter(
    (value): value is string => Boolean(value),
  );
  return `${parts.join(" · ")} 成品展示`;
});

const srcset = computed(() =>
  (cover.value?.srcset ?? []).map((item) => `${item.url} ${item.width}w`).join(", "),
);
</script>

<template>
  <RouterLink
    :to="work.route"
    class="group flex flex-col overflow-hidden rounded-lg border border-line bg-surface transition-shadow hover:shadow-lift"
  >
    <div class="aspect-[4/5] overflow-hidden bg-canvas-sunk">
      <img
        v-if="cover"
        :src="cover.thumb ?? cover.src"
        :srcset="srcset || undefined"
        :sizes="sizes"
        :width="cover.width"
        :height="cover.height"
        :alt="coverAlt"
        :loading="eager ? 'eager' : 'lazy'"
        decoding="async"
        class="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
      />
      <div v-else class="flex size-full items-center justify-center px-4 text-center text-xs text-ink-faint">
        图片准备中
      </div>
    </div>

    <div class="flex flex-1 flex-col p-5">
      <div class="flex flex-wrap items-center gap-2">
        <StatusBadge v-if="work.work_type" tone="amber" :label="work.work_type" />
        <StatusBadge v-if="work.species" tone="neutral" :label="work.species" />
        <StatusBadge v-if="work.is_featured" tone="jade" label="代表作品" />
      </div>
      <h3 class="mt-3 font-display text-lg leading-snug group-hover:text-amber-strong">{{ work.title }}</h3>
      <p v-if="work.summary" class="mt-2 text-sm leading-relaxed text-ink-muted">
        {{ truncate(work.summary, 76) }}
      </p>
      <p class="mt-auto pt-4 text-xs text-ink-faint">
        {{ work.published_at ? `${work.published_at.slice(0, 10)} 发布` : "发布时间以详情页为准" }}
      </p>
    </div>
  </RouterLink>
</template>
