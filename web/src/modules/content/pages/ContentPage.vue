<script setup lang="ts">
/**
 * 内容列表页：公告（/news）与可选的自定义列表（/stories、/events、/pages）。
 *
 * 类型来自 `route.meta.kind`，缺失时按路径推断；分页与搜索状态全部进 URL
 * （`?page=2`、`?q=…`），刷新和前进后退都能恢复。
 * 分类：条目上的标签直接显示为标签徽章；接口没有提供标签过滤参数，
 * 因此这里不做会在翻页后失真的“前端分类过滤”，也不输出假分类入口。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentList,
  type ContentKind,
  type ContentSummary,
} from "@/modules/content/api/content";
import { truncate } from "@/shared/lib/format";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, StatusBadge } from "@/shared/ui/base";

const route = useRoute();
const router = useRouter();

const LIMIT = 10;

interface KindCopy {
  eyebrow: string;
  title: string;
  description: string;
  emptyTitle: string;
  emptyDescription: string;
  searchPlaceholder: string;
  basePath: string;
  searchable: boolean;
}

const KIND_COPY: Record<string, KindCopy> = {
  news: {
    eyebrow: "News",
    title: "工作室公告",
    description: "档期开放、新系列上线、展会安排与规则变更都发布在这里；每条公告有独立的发布时间。",
    emptyTitle: "暂无公告",
    emptyDescription: "有新的档期或活动时会在后台发布，发布前的草稿不会出现在这里。",
    searchPlaceholder: "搜索公告标题或正文摘要",
    basePath: "/news",
    searchable: true,
  },
  story: {
    eyebrow: "Stories",
    title: "客户故事",
    description: "经委托方同意后公开的委托记录；没有拿到授权的委托不会出现在这里。",
    emptyTitle: "暂无公开的故事",
    emptyDescription: "客户故事需要委托方书面同意后才会发布。",
    searchPlaceholder: "搜索故事",
    basePath: "/stories",
    searchable: true,
  },
  event: {
    eyebrow: "Events",
    title: "活动与展会",
    description: "参展、线下试穿与开放日安排。活动时间与地点以条目正文为准。",
    emptyTitle: "暂无活动",
    emptyDescription: "有确认的活动安排时会在这里发布。",
    searchPlaceholder: "搜索活动",
    basePath: "/events",
    searchable: true,
  },
  page: {
    eyebrow: "Pages",
    title: "页面列表",
    description: "运营在后台发布的自定义页面。",
    emptyTitle: "暂无页面",
    emptyDescription: "页面由运营在后台创建并发布。",
    searchPlaceholder: "搜索页面",
    basePath: "/pages",
    searchable: false,
  },
};

const KIND_VALUES: ContentKind[] = [
  "page",
  "work",
  "news",
  "series",
  "faq",
  "team",
  "legal",
  "story",
  "designer",
  "event",
];

const kind = computed<ContentKind>(() => {
  const meta = route.meta.kind;
  if (typeof meta === "string" && (KIND_VALUES as string[]).includes(meta)) return meta as ContentKind;
  if (route.path.startsWith("/stories")) return "story";
  if (route.path.startsWith("/events")) return "event";
  if (route.path.startsWith("/pages")) return "page";
  return "news";
});

const copy = computed<KindCopy>(() => KIND_COPY[kind.value] ?? KIND_COPY.news!);

const items = ref<ContentSummary[]>([]);
const total = ref(0);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);

const page = computed(() => {
  const raw = Number(route.query.page ?? 1);
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
});

const keyword = computed(() => {
  const raw = route.query.q;
  return (typeof raw === "string" ? raw : "").trim();
});

const searchInput = ref(keyword.value);

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / LIMIT)));

const pageWindow = computed(() => {
  const last = totalPages.value;
  const start = Math.max(1, Math.min(page.value - 2, last - 4));
  const end = Math.min(last, start + 4);
  return Array.from({ length: Math.max(end - start + 1, 0) }, (_, index) => start + index);
});

const breadcrumbs = computed(() => [{ label: "首页", to: "/" }, { label: copy.value.title }]);

function queryFor(targetPage: number, q: string): Record<string, string> {
  const query: Record<string, string> = {};
  if (q) query.q = q;
  if (targetPage > 1) query.page = String(targetPage);
  return query;
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  try {
    const result = await fetchContentList(kind.value, {
      page: page.value,
      limit: LIMIT,
      q: keyword.value || undefined,
    });
    items.value = result.items;
    total.value = result.total;
  } catch (caught) {
    items.value = [];
    total.value = 0;
    const state = classifyPublicError(caught, "列表加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
  } finally {
    loading.value = false;
  }
}

function goToPage(target: number): void {
  const next = Math.min(Math.max(target, 1), totalPages.value);
  if (next === page.value) return;
  void router.push({ path: route.path, query: queryFor(next, keyword.value) });
}

function submitSearch(): void {
  void router.push({ path: route.path, query: queryFor(1, searchInput.value.trim()) });
}

function clearSearch(): void {
  searchInput.value = "";
  void router.push({ path: route.path, query: queryFor(1, "") });
}

watch(
  () => route.query,
  () => {
    searchInput.value = keyword.value;
    void load();
  },
);

onMounted(() => {
  applyPageHead({
    title: copy.value.title,
    description: copy.value.description,
    canonical: copy.value.basePath,
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
        :eyebrow="copy.eyebrow"
        level="1"
        :title="copy.title"
        :description="copy.description"
      />
    </section>

    <section v-if="copy.searchable" class="container-page pb-8">
      <form class="flex flex-col gap-3 sm:flex-row sm:items-end" @submit.prevent="submitSearch">
        <div class="flex-1">
          <label class="block text-sm font-medium text-ink-soft" for="content-search">搜索</label>
          <input
            id="content-search"
            v-model="searchInput"
            class="field-base mt-1.5"
            type="search"
            :placeholder="copy.searchPlaceholder"
            autocomplete="off"
          />
        </div>
        <AppButton type="submit" size="sm">搜索</AppButton>
        <AppButton v-if="keyword" size="sm" variant="secondary" @click="clearSearch()">清除搜索</AppButton>
      </form>
      <p v-if="keyword" class="mt-3 text-xs text-ink-faint" aria-live="polite">
        当前关键词：“{{ keyword }}”，共 {{ total }} 条结果。
      </p>
    </section>

    <section class="container-page pb-14">
      <div v-if="loading" class="space-y-4">
        <SkeletonBlock :lines="3" aria-label="正在加载列表" />
        <SkeletonBlock :lines="3" aria-label="正在加载列表" />
        <SkeletonBlock :lines="3" aria-label="正在加载列表" />
      </div>

      <EmptyState
        v-else-if="blocked"
        tone="forbidden"
        title="这个列表当前不可见"
        description="内容需要相应权限才能读取。访客可以稍后重试，或直接联系客服。"
      >
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="missing"
        tone="not-found"
        title="没有这个列表"
        description="地址可能已失效。可以回到首页，或从导航进入公告与作品集。"
      >
        <AppButton size="sm" to="/">返回首页</AppButton>
        <AppButton size="sm" variant="secondary" to="/works">浏览作品集</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        tone="error"
        :title="error"
        description="网络恢复后可以重试；重试只重新读取这一页。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!items.length && keyword"
        title="没有匹配的内容"
        :description="`关键词“${keyword}”没有命中任何条目。可以换个说法，或清除搜索查看全部。`"
      >
        <AppButton size="sm" variant="secondary" @click="clearSearch()">清除搜索</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!items.length"
        :title="copy.emptyTitle"
        :description="copy.emptyDescription"
      >
        <AppButton size="sm" variant="secondary" to="/contact">有消息想先知道？联系客服</AppButton>
      </EmptyState>

      <template v-else>
        <p class="text-xs text-ink-faint" aria-live="polite">
          共 {{ total }} 条 · 第 {{ page }} / {{ totalPages }} 页
        </p>

        <ul class="mt-6 divide-y divide-line border-y border-line">
          <li v-for="item in items" :key="item.id">
            <article class="py-6">
              <div class="flex flex-wrap items-center gap-3 text-xs text-ink-faint">
                <time class="tabular-nums" :datetime="item.published_at ?? undefined">
                  {{ (item.published_at ?? item.updated_at ?? "").slice(0, 10) || "发布时间以详情页为准" }}
                </time>
                <StatusBadge v-if="item.is_featured" tone="amber" label="置顶" />
                <StatusBadge v-for="tag in item.tags.slice(0, 3)" :key="tag" tone="neutral" :label="tag" />
              </div>
              <h2 class="mt-3 font-display text-xl">
                <RouterLink class="hover:text-amber-strong" :to="item.route">{{ item.title }}</RouterLink>
              </h2>
              <p v-if="item.summary" class="mt-2 max-w-3xl text-sm leading-relaxed text-ink-muted">
                {{ truncate(item.summary, 160) }}
              </p>
              <p class="mt-3">
                <RouterLink class="link-underline text-sm text-amber-strong hover:text-amber" :to="item.route">
                  阅读全文
                </RouterLink>
              </p>
            </article>
          </li>
        </ul>

        <nav v-if="totalPages > 1" class="mt-8 flex flex-wrap items-center gap-2" aria-label="分页">
          <AppButton
            size="sm"
            variant="secondary"
            :disabled="page <= 1"
            :disabled-reason="page <= 1 ? '已经是第一页' : ''"
            @click="goToPage(page - 1)"
          >
            上一页
          </AppButton>
          <RouterLink
            v-for="candidate in pageWindow"
            :key="candidate"
            class="inline-flex min-h-[44px] min-w-[44px] items-center justify-center rounded-sm border px-3 text-sm tabular-nums transition-colors"
            :class="
              candidate === page
                ? 'border-amber bg-amber-soft text-amber-strong'
                : 'border-line bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
            "
            :to="{ path: route.path, query: queryFor(candidate, keyword) }"
            :aria-current="candidate === page ? 'page' : undefined"
          >
            {{ candidate }}
          </RouterLink>
          <AppButton
            size="sm"
            variant="secondary"
            :disabled="page >= totalPages"
            :disabled-reason="page >= totalPages ? '已经是最后一页' : ''"
            @click="goToPage(page + 1)"
          >
            下一页
          </AppButton>
          <span class="ml-2 text-xs text-ink-faint">第 {{ page }} / {{ totalPages }} 页</span>
        </nav>
      </template>
    </section>
  </div>
</template>
