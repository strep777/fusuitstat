<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-8 pt-6">
      <SectionHeading
        eyebrow="FAQ"
        :title="t('faq.title')"
        :description="t('faq.intro')"
        level="1"
      />
    </section>

    <!-- 搜索：前端过滤已加载的条目并显示命中数 -->
    <section class="container-page pb-10">
      <SurfaceCard as="div">
        <form class="flex flex-col gap-3 sm:flex-row sm:items-end" @submit.prevent>
          <div class="flex-1">
            <label class="block text-sm font-medium text-ink-soft" for="faq-keyword">{{ t('faq.search') }}</label>
            <input
              id="faq-keyword"
              v-model="keyword"
              class="field-base mt-1.5"
              type="search"
              :placeholder="t('faq.placeholder')"
              autocomplete="off"
            />
          </div>
          <AppButton v-if="searching" size="sm" variant="secondary" @click="keyword = ''">{{ t('faq.clear') }}</AppButton>
        </form>
        <p class="mt-3 text-xs text-ink-faint" aria-live="polite">
          <template v-if="loading">{{ t('faq.listLoading') }}</template>
          <template v-else-if="searching">
            {{ t('faq.matches', { count: filtered.length, total: items.length }) }}
          </template>
          <template v-else>{{ t('faq.count', { count: items.length }) }}</template>
        </p>
        <p v-if="truncated" class="mt-2 text-xs text-amber-strong" role="status">
          {{ t('faq.truncated') }}
        </p>
      </SurfaceCard>
    </section>

    <section class="container-page pb-14">
      <div v-if="loading" class="space-y-4">
        <SkeletonBlock :lines="4" :aria-label="t('faq.loading')" />
        <SkeletonBlock :lines="4" :aria-label="t('faq.loading')" />
      </div>

      <EmptyState
        v-else-if="listBlocked"
        tone="forbidden"
        :title="t('faq.blocked')"
        :description="t('faq.blockedNote')"
      >
        <AppButton size="sm" :to="publicPath('/contact')">{{ t('faq.contact') }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="listMissing"
        tone="not-found"
        :title="t('faq.missing')"
        :description="t('faq.missingNote')"
      >
        <AppButton size="sm" :to="publicPath('/')">{{ t('faq.home') }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="listError"
        tone="error"
        :title="listError"
        :description="t('faq.errorNote')"
      >
        <AppButton size="sm" @click="load()">{{ t('faq.reload') }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!items.length"
        :title="t('faq.empty')"
        :description="t('faq.emptyNote')"
      >
        <AppButton size="sm" variant="secondary" :to="publicPath('/contact')">{{ t('faq.contact') }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!filtered.length"
        :title="t('faq.noMatch')"
        :description="t('faq.noMatchNote', { keyword: keyword.trim() })"
      >
        <AppButton size="sm" variant="secondary" @click="keyword = ''">{{ t('faq.clear') }}</AppButton>
        <AppButton size="sm" :to="publicPath('/contact')">{{ t('faq.contact') }}</AppButton>
      </EmptyState>

      <template v-else>
        <div class="space-y-6">
          <section
            v-for="group in groups"
            :key="group.label"
            class="rounded-lg border border-line bg-surface"
          >
            <div
              class="flex min-h-[44px] items-center justify-between gap-4 px-5 py-3"
            >
              <span class="font-display text-lg">{{ group.label }}</span>
              <span class="shrink-0 text-xs text-ink-faint tabular-nums">{{ t('faq.groupCount', { count: group.items.length }) }}</span>
            </div>

            <div class="divide-y divide-line border-t border-line px-5">
              <details v-for="item in group.items" :key="item.id" class="group py-4">
                <summary class="flex cursor-pointer list-none items-start justify-between gap-4 marker:hidden">
                  <span class="font-medium text-ink-soft">{{ item.title }}</span>
                  <span class="mt-0.5 shrink-0 text-xs text-ink-faint">
                    <span class="group-open:hidden" aria-hidden="true">{{ t('faq.expand') }}</span>
                    <span class="hidden group-open:inline" aria-hidden="true">{{ t('faq.collapse') }}</span>
                  </span>
                </summary>
                <div class="mt-3">
                  <div
                    v-if="bodies[item.slug] !== undefined"
                    class="prose-quiet text-sm leading-relaxed text-ink-muted"
                    v-html="bodyOf(item.slug)"
                  />
                  <p v-else-if="bodyErrorOf(item.slug)" class="text-sm text-wine" role="alert">
                    {{ bodyErrorOf(item.slug) }}
                  </p>
                  <SkeletonBlock v-else :lines="2" :aria-label="t('faq.bodyLoading')" />
                  <div v-if="bodyErrorOf(item.slug)" class="mt-3">
                    <AppButton size="sm" variant="secondary" @click="loadBody(item)">{{ t('faq.retryBody') }}</AppButton>
                  </div>
                </div>
              </details>
            </div>
          </section>
        </div>

        <div
          v-if="bodyFailureCount > 0"
          class="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber bg-amber-soft px-4 py-3 text-sm text-amber-strong"
          role="status"
        >
          <span>{{ t('faq.bodyFailures', { count: bodyFailureCount }) }}</span>
          <AppButton size="sm" variant="secondary" @click="retryFailedBodies()">{{ t('faq.retryFailed') }}</AppButton>
        </div>

        <p v-else-if="bodyLoading" class="mt-6 text-xs text-ink-faint" role="status">{{ t('faq.completing') }}</p>
      </template>
    </section>

    <section class="border-t border-line bg-surface-muted">
      <div class="container-page flex flex-col gap-4 py-12 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 class="font-display text-xl">{{ t('faq.noAnswer') }}</h2>
          <p class="mt-2 max-w-xl text-sm text-ink-muted">
            {{ t('faq.contactNote') }}
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          <AppButton :to="publicPath('/contact')" size="sm">{{ t('faq.feedback') }}</AppButton>
          <AppButton :to="publicPath('/commission/guide')" size="sm" variant="secondary">{{ t('faq.guide') }}</AppButton>
        </div>
      </div>
    </section>
  </div>
</template>

<script setup lang="ts">
/**
 * 常见问题（FR-003）。
 *
 * 数据来源：`GET /content/faq` 返回条目摘要，正文在 `GET /content/faq/{slug}` 的
 * `body_html`（服务端已清洗，可直接 v-html）。列表接口不返回正文，所以这里按小并发补齐正文，
 * 单条正文失败只影响该条，不影响整页。
 * 交互：搜索在已加载的条目上前端过滤并显示命中数；分类用原生 `<details>`，键盘可直接展开。
 */
import { computed, onMounted, ref } from "vue";
import { useI18n } from "@/shared/lib/i18n";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  fetchContentList,
  type ContentSummary,
} from "@/modules/content/api/content";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  SkeletonBlock,
  SurfaceCard,
} from "@/shared/ui/base";

const { t, locale } = useI18n();
const publicPath = (path: string): string => locale.value === "en-US" ? `/en${path === "/" ? "" : path}` : path;

const items = ref<ContentSummary[]>([]);
const loading = ref(true);
const listError = ref<string | null>(null);
const listMissing = ref(false);
const listBlocked = ref(false);
const truncated = ref(false);

/** 正文按 slug 缓存；失败与在途状态分开记录，便于单条重试 */
const bodies = ref<Record<string, string>>({});
const bodyErrors = ref<Record<string, string>>({});
const bodyPending = ref<Record<string, boolean>>({});
const bodyLoading = ref(false);
const bodyFailureCount = computed(() => Object.keys(bodyErrors.value).length);

const keyword = ref("");

const breadcrumbs = computed(() => [
  { label: t("nav.home"), to: publicPath("/") },
  { label: t("faq.title") },
]);

const searching = computed(() => keyword.value.trim().length > 0);

function stripHtml(value: string): string {
  return value.replace(/<[^>]*>/g, " ");
}

function bodyOf(slug: string): string {
  return bodies.value[slug] ?? "";
}

function bodyErrorOf(slug: string): string {
  return bodyErrors.value[slug] ?? "";
}

const filtered = computed(() => {
  const needle = keyword.value.trim().toLowerCase();
  if (!needle) return items.value;
  return items.value.filter((item) => {
    const haystack = [item.title, item.summary, item.tags.join(" "), stripHtml(bodies.value[item.slug] ?? "")]
      .join(" ")
      .toLowerCase();
    return haystack.includes(needle);
  });
});

const groups = computed(() => {
  const map = new Map<string, ContentSummary[]>();
  for (const item of filtered.value) {
    const label = item.tags[0]?.trim() || t("faq.other");
    const bucket = map.get(label) ?? [];
    bucket.push(item);
    map.set(label, bucket);
  }
  return Array.from(map, ([label, list]) => ({ label, items: list }));
});

async function loadBody(item: ContentSummary): Promise<void> {
  if (bodies.value[item.slug] !== undefined || bodyPending.value[item.slug]) return;
  bodyPending.value = { ...bodyPending.value, [item.slug]: true };
  try {
    const detail = await fetchContentDetail("faq", item.slug);
    bodies.value = { ...bodies.value, [item.slug]: detail.body_html };
    const nextErrors = { ...bodyErrors.value };
    delete nextErrors[item.slug];
    bodyErrors.value = nextErrors;
  } catch (caught) {
    const state = classifyPublicError(caught, t("faq.bodyError"));
    bodyErrors.value = { ...bodyErrors.value, [item.slug]: state.message };
  } finally {
    const pending = { ...bodyPending.value };
    delete pending[item.slug];
    bodyPending.value = pending;
  }
}

/** 小并发补齐正文：一次打出几十个请求会拖慢首屏，也更容易触发限流。 */
async function loadBodies(queue: ContentSummary[], concurrency = 6): Promise<void> {
  bodyLoading.value = true;
  let cursor = 0;
  const workers = Array.from({ length: Math.min(concurrency, Math.max(queue.length, 1)) }, async () => {
    while (cursor < queue.length) {
      const item = queue[cursor];
      cursor += 1;
      if (item) await loadBody(item);
    }
  });
  await Promise.all(workers);
  bodyLoading.value = false;
}

async function load(): Promise<void> {
  loading.value = true;
  listError.value = null;
  listMissing.value = false;
  listBlocked.value = false;
  truncated.value = false;
  bodies.value = {};
  bodyErrors.value = {};
  try {
    const page = await fetchContentList("faq", { limit: 50, page: 1 });
    items.value = page.items;
    truncated.value = page.total > page.items.length;
    loading.value = false;
    await loadBodies(page.items);
  } catch (caught) {
    const state = classifyPublicError(caught, t("faq.listError"));
    if (state.kind === "not-found") listMissing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") listBlocked.value = true;
    else listError.value = state.message;
    loading.value = false;
  }
}

async function retryFailedBodies(): Promise<void> {
  await loadBodies(items.value.filter((item) => bodyErrors.value[item.slug] !== undefined));
}

onMounted(() => {
  applyPageHead({
    title: t("faq.title"),
    description: t("faq.meta"),
    canonical: publicPath("/faq"),
    ogType: "website",
  });
  void load();
});
</script>

<style scoped>
.prose-quiet :deep(p) {
  margin-bottom: 0.5rem;
}
.prose-quiet :deep(p:last-child) {
  margin-bottom: 0;
}
.prose-quiet :deep(ul),
.prose-quiet :deep(ol) {
  margin-bottom: 0.5rem;
  padding-left: 1.25rem;
}
.prose-quiet :deep(ul) {
  list-style: disc;
}
.prose-quiet :deep(ol) {
  list-style: decimal;
}
.prose-quiet :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
