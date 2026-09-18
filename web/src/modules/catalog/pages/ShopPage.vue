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
import { ApiError } from "@/shared/lib/http";

const route = useRoute();
const router = useRouter();

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
      error.value = caught instanceof ApiError ? caught.message : "商品列表加载失败。";
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
    title: "商城",
    description:
      "现货、配件、周边与服务：按分类筛选并查看库存状态。售罄商品仍可浏览；可售性、价格与运费以下单时服务端校验为准。",
    canonicalPath: "/shop",
  });
  void load();
});

watch(() => route.fullPath, () => void load());
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '商城' }]" />

    <div class="mt-6">
      <SectionHeading
        :level="1"
        eyebrow="Shop"
        title="商城"
        description="现货与配件可直接下单；掉落与服务按“申请 → 人工审批 → 购买”执行。售罄商品仍可浏览，是否可售以服务端在下单时的校验为准。"
      >
        <template #actions>
          <AppButton to="/characters" size="sm" variant="secondary">角色领养</AppButton>
          <AppButton to="/account/applications" size="sm">掉落与服务申请</AppButton>
        </template>
      </SectionHeading>
    </div>

    <!-- 分类筛选：分类进 URL -->
    <section class="mt-10 rounded-lg border border-line bg-surface p-5" aria-label="商品筛选">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-ink-faint">分类</span>
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
          全部
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
          类型：{{ productKindLabel(filters.kind) }}
          <button type="button" class="text-xs text-ink-faint link-underline" @click="pushQuery({ kind: '' })">
            取消
          </button>
        </span>
      </div>

      <form class="mt-5 flex flex-wrap items-end gap-3" @submit.prevent="submitSearch()">
        <div class="min-w-[240px] flex-1">
          <label class="text-sm font-medium text-ink-soft" for="shop-q">搜索商品</label>
          <input
            id="shop-q"
            v-model="draftQuery"
            class="field-base mt-1.5"
            type="search"
            placeholder="商品名称或关键词"
            autocomplete="off"
          />
        </div>
        <AppButton type="submit" size="sm">搜索</AppButton>
        <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
          清除筛选
        </AppButton>
      </form>
      <p class="mt-3 text-xs text-ink-faint">分类与搜索词会写进地址栏，刷新后仍能看到同一批商品。</p>

      <p v-if="filters.category" class="mt-3 text-xs text-ink-muted">
        当前分类：{{ productCategoryLabel(filters.category) }}
      </p>
    </section>

    <div v-if="loading" class="mt-10 rounded-lg border border-line bg-surface p-6">
      <SkeletonBlock :lines="4" aria-label="正在加载商品列表" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="没有访问商城的权限"
      description="商城商品对访客开放；如果这里被拒绝，可能是账号状态受限。登录后可确认账号状态。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      title="商品列表暂时无法加载"
      :description="`${error} 筛选条件仍保留在地址栏，可直接重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="items.length">
      <p class="mt-8 text-sm text-ink-muted">
        <template v-if="activeFilterCount">符合条件的商品 {{ total }} 件</template>
        <template v-else>共 {{ total }} 件商品</template>
        <span v-if="filters.page > 1" class="ml-2 text-ink-faint">第 {{ filters.page }} 页</span>
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
          上一页
        </AppButton>
        <span v-else class="text-xs text-ink-faint">已是第一页</span>

        <AppButton
          v-if="hasMore"
          size="sm"
          variant="secondary"
          @click="pushQuery({ page: filters.page + 1 })"
        >
          下一页
        </AppButton>
        <span v-else class="text-xs text-ink-faint">已显示全部结果</span>
      </div>
    </template>

    <EmptyState
      v-else-if="activeFilterCount"
      class="mt-10"
      title="没有符合条件的商品"
      description="当前分类或搜索词下没有已上架商品。售罄商品仍会出现在列表里，因此这里确实是没有匹配结果。"
    >
      <AppButton size="sm" variant="secondary" @click="resetFilters()">清除筛选</AppButton>
    </EmptyState>

    <EmptyState
      v-else
      class="mt-10"
      title="商城商品正在上架"
      description="上架前需要完成图片授权、SKU 与库存核对；这里不会用占位商品填充页面。"
    >
      <AppButton to="/characters" size="sm" variant="secondary">先看看角色领养</AppButton>
      <AppButton to="/contact" size="sm">询问现货情况</AppButton>
    </EmptyState>
  </div>
</template>
