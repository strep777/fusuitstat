<template>
  <div>
    <section class="border-b-2 border-ink/5 bg-canvas-sunk">
      <div class="container-page py-8 lg:py-12">
        <Breadcrumbs :items="[{ label: t('nav.home'), to: '/' }, { label: t('shop.crumb') }]" />
        <div class="mt-6 grid gap-8 lg:grid-cols-[1.2fr_0.8fr] lg:items-end">
          <SectionHeading
            :level="1"
            :eyebrow="t('shop.eyebrow')"
            :title="t('shop.title')"
            :description="t('shop.description')"
          >
            <template #actions>
              <AppButton to="/characters" size="sm" variant="secondary">{{ t("shop.characters") }}</AppButton>
              <AppButton to="/account/applications" size="sm">{{ t("shop.applications") }}</AppButton>
            </template>
          </SectionHeading>

          <div class="flex flex-wrap gap-2">
            <RouterLink to="/wash" class="rounded-full bg-jade-soft px-4 py-2 text-sm font-bold text-jade hover:bg-jade hover:text-white">
              洗毛护理
            </RouterLink>
            <RouterLink to="/props" class="rounded-full bg-grape-soft px-4 py-2 text-sm font-bold text-grape hover:bg-grape hover:text-white">
              道具工坊
            </RouterLink>
            <RouterLink to="/merch" class="rounded-full bg-lemon-soft px-4 py-2 text-sm font-bold text-ink hover:bg-lemon">
              周边商店
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <div class="container-page py-8 lg:py-10">
      <!-- 分类筛选：分类进 URL -->
      <section class="rounded-2xl border-2 border-ink/10 bg-surface p-4 sm:p-5" :aria-label="t('shop.filterLabel')">
        <div class="-mx-1 flex gap-2 overflow-x-auto px-1 pb-1">
          <button
            type="button"
            class="min-h-[44px] shrink-0 rounded-full border-2 px-4 text-sm font-bold transition-colors"
            :class="
              filters.category === ''
                ? 'border-brand bg-brand text-white'
                : 'border-ink/10 bg-surface text-ink-soft hover:border-brand hover:text-brand-strong'
            "
            :aria-pressed="filters.category === ''"
            @click="pushQuery({ category: '' })"
          >
            {{ t("shop.all") }}
          </button>
          <button
            v-for="option in PRODUCT_CATEGORY_OPTIONS"
            :key="option.value"
            type="button"
            class="min-h-[44px] shrink-0 rounded-full border-2 px-4 text-sm font-bold transition-colors"
            :class="
              filters.category === option.value
                ? 'border-brand bg-brand text-white'
                : 'border-ink/10 bg-surface text-ink-soft hover:border-brand hover:text-brand-strong'
            "
            :aria-pressed="filters.category === option.value"
            @click="pushQuery({ category: option.value })"
          >
            {{ productCategoryLabel(option.value) }}
          </button>
        </div>

        <div class="mt-4 flex flex-wrap items-center gap-3 border-t-2 border-ink/5 pt-4">
          <form class="flex min-w-[260px] flex-1 gap-2" @submit.prevent="submitSearch()">
            <label class="sr-only" for="shop-q">{{ t("shop.searchLabel") }}</label>
            <input
              id="shop-q"
              v-model="draftQuery"
              class="field-base"
              type="search"
              :placeholder="t('shop.searchPlaceholder')"
              autocomplete="off"
            />
            <AppButton type="submit" size="sm">{{ t("shop.search") }}</AppButton>
          </form>

          <span
            v-if="filters.kind"
            class="inline-flex min-h-[44px] items-center gap-2 rounded-full bg-canvas-sunk px-3 text-sm text-ink-soft"
          >
            {{ productKindLabel(filters.kind) }}
            <button type="button" class="text-xs font-bold text-brand-strong link-underline" @click="pushQuery({ kind: '' })">
              {{ t("shop.cancel") }}
            </button>
          </span>

          <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
            {{ t("shop.clearFilters") }}
          </AppButton>
        </div>

        <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-muted">
          <p>{{ t("shop.urlNote") }}</p>
          <p>{{ t("shop.zoneNote", { currency: site.browsingCurrency }) }}</p>
          <p v-if="filters.category">{{ t("shop.currentCategory") }}：{{ productCategoryLabel(filters.category) }}</p>
        </div>
      </section>

      <div v-if="loading" class="mt-8 rounded-2xl border-2 border-ink/10 bg-surface p-6">
        <SkeletonBlock :lines="4" :aria-label="t('shop.loading')" />
      </div>

      <EmptyState
        v-else-if="forbidden"
        class="mt-8"
        tone="forbidden"
        :title="t('shop.forbiddenTitle')"
        :description="t('shop.forbiddenBody')"
      >
        <AppButton to="/login" size="sm">{{ t("nav.login") }}</AppButton>
        <AppButton size="sm" variant="secondary" to="/contact">{{ t("shop.contact") }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        class="mt-8"
        tone="error"
        :title="t('shop.errorTitle')"
        :description="`${error} ${t('shop.errorNote')}`"
      >
        <AppButton size="sm" @click="load()">{{ t("shop.reload") }}</AppButton>
        <AppButton size="sm" variant="secondary" to="/contact">{{ t("shop.contact") }}</AppButton>
      </EmptyState>

      <template v-else-if="items.length">
        <p class="mt-6 text-sm text-ink-muted">
          <template v-if="activeFilterCount">{{ t("shop.matchedCount", { count: total }) }}</template>
          <template v-else>{{ t("shop.totalCount", { count: total }) }}</template>
          <span v-if="filters.page > 1" class="ml-2 text-ink-faint">
            {{ t("shop.page", { page: filters.page }) }}
          </span>
        </p>

        <div class="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-4">
          <ProductCard v-for="product in items" :key="product.id" :product="product" />
        </div>

        <div class="mt-10 flex flex-wrap items-center justify-between gap-4">
          <AppButton
            v-if="filters.page > 1"
            size="sm"
            variant="secondary"
            @click="pushQuery({ page: filters.page - 1 })"
          >
            {{ t("shop.previous") }}
          </AppButton>
          <span v-else class="text-xs text-ink-faint">{{ t("shop.firstPage") }}</span>

          <AppButton
            v-if="hasMore"
            size="sm"
            variant="secondary"
            @click="pushQuery({ page: filters.page + 1 })"
          >
            {{ t("shop.next") }}
          </AppButton>
          <span v-else class="text-xs text-ink-faint">{{ t("shop.allShown") }}</span>
        </div>
      </template>

      <EmptyState
        v-else-if="activeFilterCount"
        class="mt-8"
        :title="t('shop.emptyFilteredTitle')"
        :description="t('shop.emptyFilteredBody')"
      >
        <AppButton size="sm" variant="secondary" @click="resetFilters()">{{ t("shop.clearFilters") }}</AppButton>
      </EmptyState>

      <EmptyState
        v-else
        class="mt-8"
        :title="t('shop.emptyTitle')"
        :description="t('shop.emptyBody')"
      >
        <AppButton to="/characters" size="sm" variant="secondary">{{ t("shop.lookCharacters") }}</AppButton>
        <AppButton to="/contact" size="sm">{{ t("shop.askStock") }}</AppButton>
      </EmptyState>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 商城列表（/shop）：分类、搜索与库存状态，售罄商品仍可打开详情。
 *
 * 约束（03/11 文档）：分类进 URL（刷新与分享可恢复）；库存状态用文字徽章表达；
 * 不显示占位商品，空态说明上架前需要完成的核对。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import ProductCard from "@/modules/catalog/components/ProductCard.vue";
import {
  PRODUCT_CATEGORY_OPTIONS,
  applyPageMeta,
  fetchProducts,
  productCategoryLabel,
  productKindLabel,
  readQueryInt,
  readQueryString,
  type ProductSummary,
} from "@/modules/catalog/api/catalog";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock } from "@/shared/ui/base";
import { useSiteStore } from "@/modules/content/stores/site";
import { useI18n } from "@/shared/lib/i18n";
import { ApiError } from "@/shared/lib/http";

const route = useRoute();
const router = useRouter();
const site = useSiteStore();
const { t } = useI18n();

onMounted(() => {
  void site.load();
});

const items = ref<ProductSummary[]>([]);
const total = ref(0);
const hasMore = ref(false);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);

const filters = computed(() => ({
  category: readQueryString(route.query.category),
  kind: readQueryString(route.query.kind),
  q: readQueryString(route.query.q),
  page: readQueryInt(route.query.page, 1),
}));

const draftQuery = ref(filters.value.q);

watch(filters, (value) => {
  draftQuery.value = value.q;
});

const activeFilterCount = computed(
  () => [filters.value.category, filters.value.kind, filters.value.q].filter(Boolean).length,
);

let loadVersion = 0;
async function load(): Promise<void> {
  const version = ++loadVersion;
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  try {
    const page = await fetchProducts({
      currency: site.browsingCurrency,
      category: filters.value.category || undefined,
      kind: filters.value.kind || undefined,
      q: filters.value.q || undefined,
      page: filters.value.page,
      limit: 12,
    });
    if (version !== loadVersion) return;
    items.value = page.items;
    total.value = page.total;
    hasMore.value = page.has_more;
  } catch (caught) {
    if (version !== loadVersion) return;
    if (caught instanceof ApiError && (caught.status === 403 || caught.needsLogin)) {
      forbidden.value = true;
    } else {
      error.value = caught instanceof ApiError ? caught.message : t("shop.errorTitle");
    }
    items.value = [];
    total.value = 0;
    hasMore.value = false;
  } finally {
    if (version === loadVersion) loading.value = false;
  }
}

function pushQuery(patch: {
  category?: string;
  kind?: string;
  q?: string;
  page?: number;
}): void {
  const merged = {
    category: patch.category ?? filters.value.category,
    kind: patch.kind ?? filters.value.kind,
    q: patch.q ?? filters.value.q,
    page: patch.page ?? 1,
  };
  const query: Record<string, string> = {};
  if (merged.category) query.category = merged.category;
  if (merged.kind) query.kind = merged.kind;
  if (merged.q) query.q = merged.q;
  if (merged.page > 1) query.page = String(merged.page);
  void router.push({ path: route.path, query });
}

function submitSearch(): void {
  pushQuery({ q: draftQuery.value.trim() });
}

function resetFilters(): void {
  draftQuery.value = "";
  void router.push({ path: route.path });
}

onMounted(() => {
  applyPageMeta({
    title: t("shop.title"),
    description: t("shop.metaDescription"),
    canonicalPath: "/shop",
  });
  void load();
});

watch([() => route.fullPath, () => site.browsingCurrency], () => void load());
</script>
