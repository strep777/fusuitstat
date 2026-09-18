<script setup lang="ts">
/**
 * 内容详情页：公告正文（/news/:slug）与自定义页面（/pages/:slug）。
 *
 * 正文 `body_html` 由服务端白名单清洗后返回，前端直接渲染；前端不自行拼接或改写正文。
 * 下架内容返回 404 → 真实 not-found 语义，不重定向、不用其它内容顶替。
 * SEO：公开内容使用条目自带的 title/description/canonical 与 OG 图。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  fetchContentList,
  type ContentDetail,
  type ContentKind,
  type ContentSummary,
} from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { truncate } from "@/shared/lib/format";
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock, StatusBadge } from "@/shared/ui/base";

const route = useRoute();
const site = useSiteStore();

const entry = ref<ContentDetail | null>(null);
const related = ref<ContentSummary[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);

const KIND_LABEL: Record<string, { list: string; listPath: string; eyebrow: string }> = {
  news: { list: "公告", listPath: "/news", eyebrow: "News" },
  page: { list: "页面", listPath: "", eyebrow: "Page" },
  story: { list: "客户故事", listPath: "/stories", eyebrow: "Story" },
  event: { list: "活动", listPath: "/events", eyebrow: "Event" },
};

const kind = computed<ContentKind>(() => {
  const meta = route.meta.kind;
  return typeof meta === "string" ? (meta as ContentKind) : "news";
});

const label = computed(() => KIND_LABEL[kind.value] ?? KIND_LABEL.news!);

const slug = computed(() => {
  const value = route.params.slug;
  const raw = Array.isArray(value) ? value[0] : value;
  return raw ?? "";
});

const breadcrumbs = computed<Array<{ label: string; to?: string }>>(() => {
  const items: Array<{ label: string; to?: string }> = [{ label: "首页", to: "/" }];
  if (label.value.listPath) items.push({ label: label.value.list, to: label.value.listPath });
  items.push({ label: entry.value?.title ?? "内容详情" });
  return items;
});

const publishedText = computed(() => {
  const raw = entry.value?.published_at ?? entry.value?.updated_at ?? null;
  return raw ? raw.slice(0, 10) : null;
});

async function loadRelated(currentSlug: string): Promise<void> {
  related.value = [];
  try {
    const page = await fetchContentList(kind.value, { limit: 6, page: 1 });
    related.value = page.items.filter((item) => item.slug !== currentSlug).slice(0, 5);
  } catch {
    // 相关阅读是附加区块：失败时静默留空，不影响正文
    related.value = [];
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  entry.value = null;
  related.value = [];

  if (!slug.value) {
    missing.value = true;
    loading.value = false;
    return;
  }

  try {
    const detail = await fetchContentDetail(kind.value, slug.value);
    entry.value = detail;
    applyPageHead({
      title: detail.seo.title || detail.title,
      description: detail.seo.description || detail.summary,
      canonical: detail.seo.canonical,
      ogType: "article",
      ogImage: detail.cover?.src,
    });
    void loadRelated(detail.slug);
  } catch (caught) {
    const state = classifyPublicError(caught, "这条内容加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
    applyPageHead({
      title: label.value.list,
      robots: "noindex, follow",
    });
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => route.fullPath, load);
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <div class="container-page pb-16 pt-6">
      <div v-if="loading" class="space-y-4">
        <SkeletonBlock :lines="3" aria-label="正在加载正文" />
        <SkeletonBlock :lines="6" aria-label="正在加载正文" />
      </div>

      <EmptyState
        v-else-if="missing"
        tone="not-found"
        title="这条内容不存在或已下架"
        description="地址可能拼写有误，也可能是内容下架后旧链接失效。下架内容不再公开，也不会用其它内容顶替。"
      >
        <AppButton v-if="label.listPath" size="sm" :to="label.listPath">返回{{ label.list }}</AppButton>
        <AppButton size="sm" to="/">返回首页</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="blocked"
        tone="forbidden"
        title="这条内容当前不可见"
        description="内容需要相应权限才能查看。访客可以稍后重试，或联系客服了解情况。"
      >
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        tone="error"
        :title="error"
        description="网络恢复后可以重试；重试只重新读取这一条内容。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <article v-else-if="entry" class="grid gap-10 lg:grid-cols-[1fr_280px]">
        <div class="min-w-0">
          <p class="eyebrow">{{ label.eyebrow }}</p>
          <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">{{ entry.title }}</h1>

          <div class="mt-4 flex flex-wrap items-center gap-3 text-xs text-ink-faint">
            <time v-if="publishedText" class="tabular-nums" :datetime="entry.published_at ?? undefined">
              发布于 {{ publishedText }}
            </time>
            <span v-if="entry.updated_at && entry.published_at !== entry.updated_at" class="tabular-nums">
              最近更新 {{ entry.updated_at.slice(0, 10) }}
            </span>
            <StatusBadge v-if="entry.is_featured" tone="amber" label="置顶" />
            <StatusBadge v-for="tag in entry.tags" :key="tag" tone="neutral" :label="tag" />
          </div>

          <p v-if="entry.summary" class="mt-6 max-w-[720px] text-base leading-relaxed text-ink-muted">
            {{ entry.summary }}
          </p>

          <div v-if="entry.cover" class="mt-8 overflow-hidden rounded-lg border border-line bg-canvas-sunk">
            <img
              :src="entry.cover.src"
              :srcset="entry.cover.srcset.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
              sizes="(min-width: 1024px) 820px, 100vw"
              :width="entry.cover.width"
              :height="entry.cover.height"
              :alt="entry.cover.alt || `${entry.title} 配图`"
              loading="lazy"
              decoding="async"
              class="w-full object-cover"
            />
          </div>

          <div class="prose-article mt-8 max-w-[720px] text-base leading-relaxed text-ink-soft" v-html="entry.body_html" />

          <div class="mt-10 flex flex-wrap gap-3 border-t border-line pt-8">
            <AppButton v-if="label.listPath" :to="label.listPath" variant="secondary" size="sm">
              返回{{ label.list }}
            </AppButton>
            <AppButton to="/contact" variant="secondary" size="sm">联系与反馈</AppButton>
            <AppButton to="/faq" variant="secondary" size="sm">常见问题</AppButton>
            <AppButton to="/feedback/new" size="sm">提交反馈</AppButton>
          </div>
        </div>

        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <section v-if="related.length" class="rounded-lg border border-line bg-surface p-5">
            <h2 class="font-display text-base">更多{{ label.list }}</h2>
            <ul class="mt-4 space-y-4">
              <li v-for="item in related" :key="item.id">
                <RouterLink class="group block" :to="item.route">
                  <time class="text-xs tabular-nums text-ink-faint">
                    {{ (item.published_at ?? item.updated_at ?? "").slice(0, 10) }}
                  </time>
                  <p class="mt-1 text-sm font-medium group-hover:text-amber-strong">{{ item.title }}</p>
                  <p v-if="item.summary" class="mt-1 text-xs text-ink-muted">{{ truncate(item.summary, 56) }}</p>
                </RouterLink>
              </li>
            </ul>
          </section>

          <section
            class="rounded-lg border border-night-line bg-night p-5 text-canvas"
            aria-labelledby="content-help-title"
          >
            <h2 id="content-help-title" class="font-display text-base">需要人工确认？</h2>
            <p class="mt-3 text-sm leading-relaxed text-canvas/70">
              公告与页面由运营维护；涉及具体订单、报价或交期的问题请带上编号联系客服。反馈首响
              {{ site.brand.response_sla.feedback_workdays }} 个工作日。
            </p>
            <p class="mt-4 text-sm">
              <a class="link-underline text-canvas/85 hover:text-amber-ink" :href="`mailto:${site.brand.support_email}`">
                {{ site.brand.support_email }}
              </a>
            </p>
          </section>
        </aside>
      </article>
    </div>
  </div>
</template>

<style scoped>
.prose-article :deep(h2) {
  margin-top: 2rem;
  margin-bottom: 0.75rem;
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 600;
}
.prose-article :deep(h2:first-child) {
  margin-top: 0;
}
.prose-article :deep(h3) {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.0625rem;
  font-weight: 600;
}
.prose-article :deep(p) {
  margin-bottom: 0.875rem;
}
.prose-article :deep(ul),
.prose-article :deep(ol) {
  margin-bottom: 0.875rem;
  padding-left: 1.25rem;
}
.prose-article :deep(ul) {
  list-style: disc;
}
.prose-article :deep(ol) {
  list-style: decimal;
}
.prose-article :deep(li) {
  margin-bottom: 0.375rem;
}
.prose-article :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.prose-article :deep(img) {
  margin: 1rem 0;
  border-radius: var(--radius-md);
}
.prose-article :deep(blockquote) {
  margin: 1rem 0;
  border-left: 3px solid var(--color-amber);
  padding-left: 1rem;
  color: var(--color-ink-muted);
}
.prose-article :deep(table) {
  width: 100%;
  margin: 1rem 0;
  border-collapse: collapse;
  font-size: 0.9375rem;
}
.prose-article :deep(th),
.prose-article :deep(td) {
  border: 1px solid var(--color-line);
  padding: 0.5rem 0.75rem;
  text-align: left;
}
</style>
