<script setup lang="ts">
/**
 * 商城列表（/shop）：分类、搜索与库存状态，售罄商品仍可打开详情。
 *
 * 约束（03/11 文档）：分类进 URL（刷新与分享可恢复）；库存状态用文字徽章表达；
 * 不显示占位商品，空态说明上架前需要完成的核对。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

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
import { useI18n } from "@/shared/lib/i18n";
import { ApiError } from "@/shared/lib/http";

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

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

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  try {
    const page = await fetchProducts({
      category: filters.value.category || undefined,
      kind: filters.value.kind || undefined,
      q: filters.value.q || undefined,
      page: filters.value.page,
      limit: 12,
    });
    items.value = page.items;
    total.value = page.total;
    hasMore.value = page.has_more;
  } catch (caught) {
    if (caught instanceof ApiError && (caught.status === 403 || caught.needsLogin)) {
      forbidden.value = true;
    } else {
      error.value = caught instanceof ApiError ? caught.message : t("shop.errorTitle");
    }
    items.value = [];
    total.value = 0;
    hasMore.value = false;
  } finally {
    loading.value = false;
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
  void router.push({ path: "/shop", query });
}

function submitSearch(): void {
  pushQuery({ q: draftQuery.value.trim() });
}

function resetFilters(): void {
  draftQuery.value = "";
  void router.push({ path: "/shop" });
}

onMounted(() => {
  applyPageMeta({
    title: t("shop.title"),
    description: t("shop.metaDescription"),
    canonicalPath: "/shop",
  });
  void load();
});

watch(() => route.fullPath, () => void load());
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: t('nav.home'), to: '/' }, { label: t('shop.crumb') }]" />

    <div class="mt-6">
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
    </div>

    <!-- 分类筛选：分类进 URL -->
    <section class="mt-10 rounded-lg border border-line bg-surface p-5" :aria-label="t('shop.filterLabel')">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-ink-faint">{{ t("shop.category") }}</span>
        <button
          type="button"
          class="min-h-[44px] rounded-sm border px-3 text-sm transition-colors"
          :class="
            filters.category === ''
              ? 'border-amber bg-amber-soft text-amber-strong'
              : 'border-line-strong bg-surface text-ink-soft hover:border-amber'
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
          class="min-h-[44px] rounded-sm border px-3 text-sm transition-colors"
          :class="
            filters.category === option.value
              ? 'border-amber bg-amber-soft text-amber-strong'
              : 'border-line-strong bg-surface text-ink-soft hover:border-amber'
          "
          :aria-pressed="filters.category === option.value"
          @click="pushQuery({ category: option.value })"
        >
          {{ option.label }}
        </button>
        <span
          v-if="filters.kind"
          class="inline-flex min-h-[44px] items-center gap-2 rounded-sm border border-line-strong bg-surface-muted px-3 text-sm text-ink-soft"
        >
          {{ t("shop.kindPrefix") }}：{{ productKindLabel(filters.kind) }}
          <button type="button" class="text-xs text-ink-faint link-underline" @click="pushQuery({ kind: '' })">
            {{ t("shop.cancel") }}
          </button>
        </span>
      </div>

      <form class="mt-5 flex flex-wrap items-end gap-3" @submit.prevent="submitSearch()">
        <div class="min-w-[240px] flex-1">
          <label class="text-sm font-medium text-ink-soft" for="shop-q">{{ t("shop.searchLabel") }}</label>
          <input
            id="shop-q"
            v-model="draftQuery"
            class="field-base mt-1.5"
            type="search"
            :placeholder="t('shop.searchPlaceholder')"
            autocomplete="off"
          />
        </div>
        <AppButton type="submit" size="sm">{{ t("shop.search") }}</AppButton>
        <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
          {{ t("shop.clearFilters") }}
        </AppButton>
      </form>
      <p class="mt-3 text-xs text-ink-faint">{{ t("shop.urlNote") }}</p>

      <p v-if="filters.category" class="mt-3 text-xs text-ink-muted">
        {{ t("shop.currentCategory") }}：{{ productCategoryLabel(filters.category) }}
      </p>
    </section>

    <div v-if="loading" class="mt-10 rounded-lg border border-line bg-surface p-6">
      <SkeletonBlock :lines="4" :aria-label="t('shop.loading')" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      :title="t('shop.forbiddenTitle')"
      :description="t('shop.forbiddenBody')"
    >
      <AppButton to="/login" size="sm">{{ t("nav.login") }}</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">{{ t("shop.contact") }}</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      :title="t('shop.errorTitle')"
      :description="`${error} ${t('shop.errorNote')}`"
    >
      <AppButton size="sm" @click="load()">{{ t("shop.reload") }}</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">{{ t("shop.contact") }}</AppButton>
    </EmptyState>

    <template v-else-if="items.length">
      <p class="mt-8 text-sm text-ink-muted">
        <template v-if="activeFilterCount">{{ t("shop.matchedCount", { count: total }) }}</template>
        <template v-else>{{ t("shop.totalCount", { count: total }) }}</template>
        <span v-if="filters.page > 1" class="ml-2 text-ink-faint">
          {{ t("shop.page", { page: filters.page }) }}
        </span>
      </p>

      <div class="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
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
      class="mt-10"
      :title="t('shop.emptyFilteredTitle')"
      :description="t('shop.emptyFilteredBody')"
    >
      <AppButton size="sm" variant="secondary" @click="resetFilters()">{{ t("shop.clearFilters") }}</AppButton>
    </EmptyState>

    <EmptyState
      v-else
      class="mt-10"
      :title="t('shop.emptyTitle')"
      :description="t('shop.emptyBody')"
    >
      <AppButton to="/characters" size="sm" variant="secondary">{{ t("shop.lookCharacters") }}</AppButton>
      <AppButton to="/contact" size="sm">{{ t("shop.askStock") }}</AppButton>
    </EmptyState>
  </div>
</template>
