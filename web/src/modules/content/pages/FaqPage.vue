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
const bodyFailureCount = ref(0);

const keyword = ref("");

const breadcrumbs = [
  { label: "首页", to: "/" },
  { label: "常见问题" },
];

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
    const label = item.tags[0]?.trim() || "其他问题";
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
    const state = classifyPublicError(caught, "这条问题的正文载入失败。");
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
  bodyFailureCount.value = Object.keys(bodyErrors.value).length;
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
  bodyFailureCount.value = 0;
  try {
    const page = await fetchContentList("faq", { limit: 50, page: 1 });
    items.value = page.items;
    truncated.value = page.total > page.items.length;
    loading.value = false;
    await loadBodies(page.items);
  } catch (caught) {
    const state = classifyPublicError(caught, "常见问题加载失败。");
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
    title: "常见问题",
    description: "委托报价、修改次数、交期、支付与售后相关的常见问题，按分类折叠，可搜索。",
    canonical: "/faq",
    ogType: "website",
  });
  void load();
});
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-8 pt-6">
      <SectionHeading
        eyebrow="FAQ"
        title="常见问题"
        description="这里只写已经核准的规则与做法。涉及具体订单的问题，请带上订单号联系客服；没有答案时不要按猜测操作。"
        level="1"
      />
    </section>

    <!-- 搜索：前端过滤已加载的条目并显示命中数 -->
    <section class="container-page pb-10">
      <SurfaceCard as="div">
        <form class="flex flex-col gap-3 sm:flex-row sm:items-end" @submit.prevent>
          <div class="flex-1">
            <label class="block text-sm font-medium text-ink-soft" for="faq-keyword">搜索问题</label>
            <input
              id="faq-keyword"
              v-model="keyword"
              class="field-base mt-1.5"
              type="search"
              placeholder="例如：报价有效期、修改次数、清洗"
              autocomplete="off"
            />
          </div>
          <AppButton v-if="searching" size="sm" variant="secondary" @click="keyword = ''">清除关键词</AppButton>
        </form>
        <p class="mt-3 text-xs text-ink-faint" aria-live="polite">
          <template v-if="loading">正在读取问题列表…</template>
          <template v-else-if="searching">
            命中 {{ filtered.length }} / {{ items.length }} 条问题。
          </template>
          <template v-else>共 {{ items.length }} 条问题，按分类折叠；搜索只过滤已加载的条目。</template>
        </p>
        <p v-if="truncated" class="mt-2 text-xs text-amber-strong" role="status">
          条目较多，这里只列出前 50 条；需要更多内容请用搜索或联系客服。
        </p>
      </SurfaceCard>
    </section>

    <section class="container-page pb-14">
      <div v-if="loading" class="space-y-4">
        <SkeletonBlock :lines="4" aria-label="正在加载常见问题" />
        <SkeletonBlock :lines="4" aria-label="正在加载常见问题" />
      </div>

      <EmptyState
        v-else-if="listBlocked"
        tone="forbidden"
        title="常见问题当前不可见"
        description="内容需要权限才能读取。访客可以稍后重试，或直接联系客服询问。"
      >
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="listMissing"
        tone="not-found"
        title="没有这个 FAQ 列表"
        description="地址可能已失效。可以回到首页，或从页脚进入其他帮助页面。"
      >
        <AppButton size="sm" to="/">返回首页</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="listError"
        tone="error"
        :title="listError"
        description="网络恢复后可以重试；重试只重新读取问题列表。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!items.length"
        title="FAQ 尚未发布"
        description="问题与答案由运营在后台整理并预览后发布；没有数据时不会用示例问题占位。你也可以直接联系客服提问。"
      >
        <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!filtered.length"
        title="没有匹配的问题"
        :description="`关键词“${keyword.trim()}”没有命中任何条目。可以换个说法，或直接联系客服。`"
      >
        <AppButton size="sm" variant="secondary" @click="keyword = ''">清除关键词</AppButton>
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <template v-else>
        <div class="space-y-6">
          <details
            v-for="group in groups"
            :key="group.label"
            class="rounded-lg border border-line bg-surface"
            :open="searching"
          >
            <summary
              class="flex min-h-[44px] cursor-pointer list-none items-center justify-between gap-4 px-5 py-3 marker:hidden"
            >
              <span class="font-display text-lg">{{ group.label }}</span>
              <span class="shrink-0 text-xs text-ink-faint tabular-nums">{{ group.items.length }} 条</span>
            </summary>

            <div class="divide-y divide-line border-t border-line px-5">
              <details v-for="item in group.items" :key="item.id" class="group py-4">
                <summary class="flex cursor-pointer list-none items-start justify-between gap-4 marker:hidden">
                  <span class="font-medium text-ink-soft">{{ item.title }}</span>
                  <span class="mt-0.5 shrink-0 text-xs text-ink-faint">
                    <span class="group-open:hidden" aria-hidden="true">展开</span>
                    <span class="hidden group-open:inline" aria-hidden="true">收起</span>
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
                  <SkeletonBlock v-else :lines="2" aria-label="正在加载这条问题的正文" />
                  <div v-if="bodyErrorOf(item.slug)" class="mt-3">
                    <AppButton size="sm" variant="secondary" @click="loadBody(item)">重新加载这条</AppButton>
                  </div>
                </div>
              </details>
            </div>
          </details>
        </div>

        <div
          v-if="bodyFailureCount > 0"
          class="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber bg-amber-soft px-4 py-3 text-sm text-amber-strong"
          role="status"
        >
          <span>有 {{ bodyFailureCount }} 条问题的正文没能加载，问题本身仍然可见。</span>
          <AppButton size="sm" variant="secondary" @click="retryFailedBodies()">重新加载失败项</AppButton>
        </div>

        <p v-else-if="bodyLoading" class="mt-6 text-xs text-ink-faint" role="status">正在补齐问题正文…</p>
      </template>
    </section>

    <section class="border-t border-line bg-surface-muted">
      <div class="container-page flex flex-col gap-4 py-12 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h2 class="font-display text-xl">没有找到答案？</h2>
          <p class="mt-2 max-w-xl text-sm text-ink-muted">
            把问题发给我们：说明涉及的页面、订单号或询价编号，以及你期望的处理结果。也可以直接发邮件，不需要登录。
          </p>
        </div>
        <div class="flex flex-wrap gap-3">
          <AppButton to="/contact" size="sm">联系与反馈</AppButton>
          <AppButton to="/commission/guide" size="sm" variant="secondary">阅读委托指南</AppButton>
        </div>
      </div>
    </section>
  </div>
</template>

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
