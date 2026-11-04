<template>
  <div>
    <section class="container-page pt-8">
      <Breadcrumbs :items="[{ label: t('nav.home'), to: '/' }, { label: '商城' }]" />

      <div class="mt-6 overflow-hidden rounded-[2rem] bg-[#f6f1ea]">
        <div class="grid gap-8 px-7 py-10 sm:px-10 sm:py-14 lg:grid-cols-[1.05fr_0.95fr] lg:items-end">
          <div>
            <p class="text-[11px] uppercase tracking-[0.22em] text-ink-faint">STORE / 物件</p>
            <h1 class="mt-4 font-display text-5xl font-medium tracking-[-0.04em] text-ink sm:text-6xl">
              商城
            </h1>
            <p class="mt-5 max-w-lg text-sm leading-relaxed text-ink-muted sm:text-base">
              道具、配件与周边。现货直接下单；周边目前只展示，暂不售卖。限量结构件仍需先申请再报价。
            </p>
            <p class="mt-2 text-xs text-ink-faint">角色汤与保养服务各有独立入口，不在这里重复售卖。</p>
          </div>

          <div class="grid gap-2 text-sm">
            <RouterLink
              to="/characters"
              class="group flex items-center justify-between rounded-2xl bg-white/70 px-5 py-4 text-ink transition-colors hover:bg-white"
            >
              <span>
                <span class="block text-[10px] uppercase tracking-[0.18em] text-ink-faint">CHARACTER SOUP</span>
                <span class="mt-1 block font-medium">角色汤 · 领养一个设定</span>
              </span>
              <span class="text-ink-faint transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
            </RouterLink>
            <RouterLink
              to="/wash"
              class="group flex items-center justify-between rounded-2xl bg-white/70 px-5 py-4 text-ink transition-colors hover:bg-white"
            >
              <span>
                <span class="block text-[10px] uppercase tracking-[0.18em] text-ink-faint">CARE</span>
                <span class="mt-1 block font-medium">保养服务 · 清洗与养护</span>
              </span>
              <span class="text-ink-faint transition-transform group-hover:translate-x-1" aria-hidden="true">→</span>
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <div class="container-page pb-8 pt-9 lg:pb-12">
      <section class="border-b border-line pb-5" :aria-label="t('shop.filterLabel')">
        <div class="-mx-1 flex flex-wrap items-center gap-1.5 px-1">
          <button
            type="button"
            class="min-h-[44px] shrink-0 rounded-full px-4 text-xs transition-colors"
            :class="
              filters.category === ''
                ? 'bg-brand text-white'
                : 'bg-canvas-sunk text-ink-soft hover:text-ink'
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
            class="min-h-[44px] shrink-0 rounded-full px-4 text-xs transition-colors"
            :class="
              filters.category === option.value
                ? 'bg-brand text-white'
                : 'bg-canvas-sunk text-ink-soft hover:text-ink'
            "
            :aria-pressed="filters.category === option.value"
            @click="pushQuery({ category: option.value })"
          >
            {{ productCategoryLabel(option.value) }}
          </button>

          <form class="ml-auto flex min-w-[240px] flex-1 gap-2 sm:max-w-md" @submit.prevent="submitSearch()">
            <label class="sr-only" for="shop-q">{{ t("shop.searchLabel") }}</label>
            <input
              id="shop-q"
              v-model="draftQuery"
              class="field-base"
              type="search"
              :placeholder="t('shop.searchPlaceholder')"
              autocomplete="off"
            />
            <AppButton type="submit" size="sm" class="shrink-0 whitespace-nowrap">{{ t("shop.search") }}</AppButton>
          </form>

          <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
            {{ t("shop.clearFilters") }}
          </AppButton>
        </div>

        <div class="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-ink-faint">
          <p>{{ t("shop.urlNote") }}</p>
          <p>{{ t("shop.zoneNote", { currency: site.browsingCurrency }) }}</p>
        </div>
      </section>

      <div v-if="loading" class="mt-8 rounded-2xl border border-line bg-surface p-6 shadow-soft">
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

        <div class="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
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
        <AppButton to="/characters" size="sm" variant="secondary">去角色汤看看</AppButton>
        <AppButton to="/contact" size="sm">{{ t("shop.askStock") }}</AppButton>
      </EmptyState>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 商城列表（/shop）：道具、配件与周边的合并目录。
 *
 * 角色领养在 /characters（角色汤），洗毛/养护在 /wash（保养服务）；
 * 服务与独占角色通过 exclude_kinds 从商城排除，避免同一内容出现两次。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import ProductCard from "@/modules/catalog/components/ProductCard.vue";
import {
  PRODUCT_CATEGORY_OPTIONS,
  applyPageMeta,
  fetchProducts,
  productCategoryLabel,
  readQueryInt,
  readQueryString,
  type ProductSummary,
} from "@/modules/catalog/api/catalog";
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock } from "@/shared/ui/base";
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
  q: readQueryString(route.query.q),
  page: readQueryInt(route.query.page, 1),
}));

const draftQuery = ref(filters.value.q);

watch(filters, (value) => {
  draftQuery.value = value.q;
});

const activeFilterCount = computed(
  () => [filters.value.category, filters.value.q].filter(Boolean).length,
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
      excludeKinds: ["service", "exclusive_character"],
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

function pushQuery(patch: { category?: string; q?: string; page?: number }): void {
  const merged = {
    category: patch.category ?? filters.value.category,
    q: patch.q ?? filters.value.q,
    page: patch.page ?? 1,
  };
  const query: Record<string, string> = {};
  if (merged.category) query.category = merged.category;
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
    title: "商城",
    description: "道具、配件与周边现货目录；角色汤与保养服务各有独立入口。",
    canonicalPath: "/shop",
  });
  void load();
});

watch([() => route.fullPath, () => site.browsingCurrency], () => void load());
</script>
