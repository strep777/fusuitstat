<script setup lang="ts">
/**
 * 作品集（FR-002 / 03 文档 /works）。
 *
 * 筛选条件全部进 URL：`category`（作品类型）、`series`、`species`、`q`、`page`。
 * 刷新、前进后退、分享链接都能恢复同一组结果；接口参数 `work_type` 由 URL 的
 * `category` 映射而来，`series` 传的是系列 code。
 * 只展示已取得公开展示授权的作品（服务端保证），筛选项来自 `/content/work/facets`。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentList,
  fetchWorkFacets,
  type ContentSummary,
  type Facet,
  type SeriesFacet,
  type WorkFacetSet,
} from "@/modules/content/api/content";
import WorkCard from "@/modules/content/components/WorkCard.vue";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock } from "@/shared/ui/base";

const route = useRoute();
const router = useRouter();

const LIMIT = 12;

const works = ref<ContentSummary[]>([]);
const total = ref(0);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);

const facets = ref<WorkFacetSet | null>(null);
const facetsLoading = ref(true);
const facetsError = ref<string | null>(null);

const searchInput = ref("");

function readQuery(key: string): string {
  const raw = route.query[key];
  return (typeof raw === "string" ? raw : "").trim();
}

const filters = computed(() => ({
  category: readQuery("category"),
  series: readQuery("series"),
  species: readQuery("species"),
  q: readQuery("q"),
}));

const page = computed(() => {
  const raw = Number(route.query.page ?? 1);
  return Number.isFinite(raw) && raw >= 1 ? Math.floor(raw) : 1;
});

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / LIMIT)));

const pageWindow = computed(() => {
  const last = totalPages.value;
  const start = Math.max(1, Math.min(page.value - 2, last - 4));
  const end = Math.min(last, start + 4);
  return Array.from({ length: Math.max(end - start + 1, 0) }, (_, index) => start + index);
});

const hasFilters = computed(
  () => Boolean(filters.value.category || filters.value.series || filters.value.species || filters.value.q),
);

const seriesLabel = computed(() => {
  const match = facets.value?.series.find((item) => item.value === filters.value.series);
  return match?.label ?? filters.value.series;
});

/** 空结果时明确说明当前生效的筛选条件，而不是只说“没有结果”。 */
const activeFilterText = computed(() => {
  const parts: string[] = [];
  if (filters.value.category) parts.push(`类型=${filters.value.category}`);
  if (filters.value.series) parts.push(`系列=${seriesLabel.value}`);
  if (filters.value.species) parts.push(`物种=${filters.value.species}`);
  if (filters.value.q) parts.push(`关键词=${filters.value.q}`);
  return parts.join("、");
});

const breadcrumbs = [
  { label: "首页", to: "/" },
  { label: "作品集" },
];

function queryFor(input: {
  category?: string;
  series?: string;
  species?: string;
  q?: string;
  page?: number,
}): Record<string, string> {
  const query: Record<string, string> = {};
  if (input.category) query.category = input.category;
  if (input.series) query.series = input.series;
  if (input.species) query.species = input.species;
  if (input.q) query.q = input.q;
  if (input.page && input.page > 1) query.page = String(input.page);
  return query;
}

/** 切换任一筛选条件都会回到第 1 页，避免停在越界页码上。 */
function setFilter(key: "category" | "series" | "species", value: string): void {
  const next = { ...filters.value, [key]: filters.value[key] === value ? "" : value, page: 1 };
  void router.push({ path: "/works", query: queryFor(next) });
}

function submitSearch(): void {
  void router.push({ path: "/works", query: queryFor({ ...filters.value, q: searchInput.value.trim(), page: 1 }) });
}

function clearFilters(): void {
  searchInput.value = "";
  void router.push({ path: "/works", query: {} });
}

function goToPage(target: number): void {
  const next = Math.min(Math.max(target, 1), totalPages.value);
  if (next === page.value) return;
  void router.push({ path: "/works", query: queryFor({ ...filters.value, page: next }) });
}

async function loadFacets(): Promise<void> {
  facetsLoading.value = true;
  facetsError.value = null;
  try {
    // 服务端返回的 series 维度带 label；类型收窄只影响前端展示，不改变响应
    facets.value = (await fetchWorkFacets()) as WorkFacetSet;
  } catch (caught) {
    facetsError.value = classifyPublicError(caught, "筛选项加载失败。").message;
  } finally {
    facetsLoading.value = false;
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  searchInput.value = filters.value.q;
  try {
    const result = await fetchContentList("work", {
      page: page.value,
      limit: LIMIT,
      q: filters.value.q || undefined,
      work_type: filters.value.category || undefined,
      series: filters.value.series || undefined,
      species: filters.value.species || undefined,
    });
    works.value = result.items;
    total.value = result.total;
  } catch (caught) {
    works.value = [];
    total.value = 0;
    const state = classifyPublicError(caught, "作品列表加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
  } finally {
    loading.value = false;
  }
}

watch(
  () => route.query,
  () => {
    // 只有路径真是 /works 时才按作品集查询，避免同组件复用时串数据
    if (route.path !== "/works") return;
    void load();
  },
);

onMounted(() => {
  applyPageHead({
    title: "作品集",
    description: "按类型、系列与物种浏览已取得公开展示授权的兽装成品，附装师与摄影署名。",
    canonical: "/works",
    ogType: "website",
  });
  void Promise.allSettled([load(), loadFacets()]);
});

const workTypeFacets = computed<Facet[]>(() => facets.value?.work_types ?? []);
const speciesFacets = computed<Facet[]>(() => facets.value?.species ?? []);
const seriesFacets = computed<SeriesFacet[]>(() => facets.value?.series ?? []);
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-8 pt-6">
      <SectionHeading
        eyebrow="Works"
        title="作品集"
        description="只展示已取得公开展示授权的成品。署名、工艺与授权状态都能在详情页核对；筛选条件会写进地址，刷新后仍然保留。"
        level="1"
      />
    </section>

    <!-- 筛选：类型 / 系列 / 物种 + 搜索，全部同步到 URL -->
    <section class="border-y border-line bg-surface-muted">
      <div class="container-page py-8">
        <div v-if="facetsLoading" class="space-y-4">
          <SkeletonBlock :lines="3" aria-label="正在加载筛选项" />
        </div>

        <template v-else>
          <p v-if="facetsError" class="mb-5 rounded-sm border border-amber bg-amber-soft px-4 py-3 text-sm text-amber-strong" role="status">
            {{ facetsError }} 仍然可以用关键词搜索，或直接翻页浏览。
            <button type="button" class="link-underline ml-1" @click="loadFacets()">重新加载筛选项</button>
          </p>

          <div class="grid gap-6 lg:grid-cols-3">
            <fieldset>
              <legend class="eyebrow">作品类型</legend>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  class="inline-flex min-h-[44px] items-center rounded-full border px-4 text-sm transition-colors"
                  :class="
                    !filters.category
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="!filters.category"
                  @click="setFilter('category', '')"
                >
                  全部类型
                </button>
                <button
                  v-for="facet in workTypeFacets"
                  :key="facet.value"
                  type="button"
                  class="inline-flex min-h-[44px] items-center gap-2 rounded-full border px-4 text-sm transition-colors"
                  :class="
                    filters.category === facet.value
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="filters.category === facet.value"
                  @click="setFilter('category', facet.value)"
                >
                  {{ facet.value }}
                  <span class="text-xs text-ink-faint tabular-nums">{{ facet.count }}</span>
                </button>
              </div>
            </fieldset>

            <fieldset>
              <legend class="eyebrow">物种</legend>
              <div class="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  class="inline-flex min-h-[44px] items-center rounded-full border px-4 text-sm transition-colors"
                  :class="
                    !filters.species
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="!filters.species"
                  @click="setFilter('species', '')"
                >
                  全部物种
                </button>
                <button
                  v-for="facet in speciesFacets"
                  :key="facet.value"
                  type="button"
                  class="inline-flex min-h-[44px] items-center gap-2 rounded-full border px-4 text-sm transition-colors"
                  :class="
                    filters.species === facet.value
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="filters.species === facet.value"
                  @click="setFilter('species', facet.value)"
                >
                  {{ facet.value }}
                  <span class="text-xs text-ink-faint tabular-nums">{{ facet.count }}</span>
                </button>
              </div>
            </fieldset>

            <fieldset>
              <legend class="eyebrow">系列</legend>
              <div v-if="seriesFacets.length" class="mt-3 flex flex-wrap gap-2">
                <button
                  type="button"
                  class="inline-flex min-h-[44px] items-center rounded-full border px-4 text-sm transition-colors"
                  :class="
                    !filters.series
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="!filters.series"
                  @click="setFilter('series', '')"
                >
                  全部系列
                </button>
                <button
                  v-for="facet in seriesFacets"
                  :key="facet.value"
                  type="button"
                  class="inline-flex min-h-[44px] items-center rounded-full border px-4 text-sm transition-colors"
                  :class="
                    filters.series === facet.value
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line-strong bg-surface text-ink-soft hover:border-amber hover:text-amber-strong'
                  "
                  :aria-pressed="filters.series === facet.value"
                  @click="setFilter('series', facet.value)"
                >
                  {{ facet.label }}
                </button>
              </div>
              <p v-else class="mt-3 text-sm text-ink-faint">还没有启用中的系列，系列筛选暂不可用。</p>
            </fieldset>
          </div>
        </template>

        <!-- 搜索不依赖筛选项加载：facets 失败时仍然可用 -->
        <form class="mt-6 flex flex-col gap-3 sm:flex-row sm:items-end" @submit.prevent="submitSearch">
          <div class="flex-1 sm:max-w-md">
            <label class="block text-sm font-medium text-ink-soft" for="works-search">搜索作品</label>
            <input
              id="works-search"
              v-model="searchInput"
              class="field-base mt-1.5"
              type="search"
              placeholder="角色名、物种或关键词"
              autocomplete="off"
            />
          </div>
          <AppButton type="submit" size="sm">搜索</AppButton>
          <AppButton v-if="hasFilters" size="sm" variant="secondary" @click="clearFilters()">清除筛选</AppButton>
        </form>
      </div>
    </section>

    <section class="container-page py-12">
      <div v-if="loading" class="grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <div v-for="index in 8" :key="index" class="space-y-3 rounded-lg border border-line bg-surface p-4">
          <div class="aspect-[4/5] animate-pulse rounded-md bg-canvas-sunk" />
          <SkeletonBlock :lines="3" aria-label="正在加载作品" />
        </div>
      </div>

      <EmptyState
        v-else-if="blocked"
        tone="forbidden"
        title="作品集当前不可见"
        description="内容需要相应权限才能查看。访客可以稍后重试，或直接联系客服。"
      >
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="missing"
        tone="not-found"
        title="没有这个作品列表"
        description="地址可能已失效。可以回到首页，或联系客服确认作品是否已下架。"
      >
        <AppButton size="sm" to="/">返回首页</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        tone="error"
        :title="error"
        description="网络恢复后可以重试；已选中的筛选条件会保留在地址里。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="!works.length"
        title="没有符合当前筛选条件的作品"
        :description="
          hasFilters
            ? `当前筛选条件：${activeFilterText}。可以逐个去掉条件，或清除后浏览全部已公开作品。`
            : '目前还没有已取得公开展示授权的作品。我们不会用他人作品充当案例。'
        "
      >
        <AppButton v-if="hasFilters" size="sm" @click="clearFilters()">清除筛选</AppButton>
        <AppButton size="sm" variant="secondary" to="/commission/new">提交询价</AppButton>
      </EmptyState>

      <template v-else>
        <div class="flex flex-wrap items-baseline justify-between gap-3">
          <p class="text-sm text-ink-muted" aria-live="polite">
            共 {{ total }} 件作品 · 第 {{ page }} / {{ totalPages }} 页
          </p>
          <p v-if="hasFilters" class="text-xs text-ink-faint">当前筛选：{{ activeFilterText }}</p>
        </div>

        <div class="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          <WorkCard
            v-for="(work, index) in works"
            :key="work.id"
            :work="work"
            :eager="index < 2"
            sizes="(min-width: 1280px) 22vw, (min-width: 1024px) 30vw, (min-width: 640px) 45vw, 92vw"
          />
        </div>

        <nav v-if="totalPages > 1" class="mt-10 flex flex-wrap items-center gap-2" aria-label="分页">
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
            :to="{ path: '/works', query: queryFor({ ...filters, page: candidate }) }"
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
