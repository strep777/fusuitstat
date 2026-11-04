<script setup lang="ts">
/**
 * 分类专题页骨架（道具 / 周边）：与 /shop?category=... 共用同一份目录数据。
 * 不复制商品与库存逻辑，只做展示与入口。
 */
import { computed, onMounted, ref } from "vue";

import ProductCard from "@/modules/catalog/components/ProductCard.vue";
import { fetchProducts, type ProductSummary } from "@/modules/catalog/api/catalog";
import { useI18n } from "@/shared/lib/i18n";
import { ApiError } from "@/shared/lib/http";
import { AppButton, EmptyState, SectionHeading } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";

const props = defineProps<{
  category: "props" | "merch";
  eyebrow: string;
  title: string;
  intro: string;
  gridLabel: string;
  gridNote: string;
  emptyLabel: string;
  tone: "grape" | "lemon";
  faqs: { q: string; a: string }[];
}>();

const { t } = useI18n();
const products = ref<ProductSummary[]>([]);
const loading = ref(true);
const error = ref("");

const toneClass = computed(() =>
  props.tone === "grape" ? "bg-[#f0ebf2]" : "bg-[#f8eedf]",
);

async function load() {
  loading.value = true;
  error.value = "";
  try {
    const page = await fetchProducts({ category: props.category, limit: 48 });
    products.value = page.items;
  } catch (cause) {
    const apiError = cause instanceof ApiError ? cause : null;
    error.value = apiError?.message ?? "加载失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <section :class="[toneClass, 'text-[#2E2923]']">
      <div class="container-page grid gap-10 py-12 lg:grid-cols-[1.15fr_0.85fr] lg:items-end lg:py-16">
        <div>
          <p class="text-[11px] uppercase tracking-[0.18em] text-[#7D7369]">{{ eyebrow }}</p>
          <h1 class="mt-4 text-balance font-display text-4xl font-medium leading-[1.08] tracking-[-0.04em] sm:text-6xl">
            {{ title }}
          </h1>
          <p class="mt-5 max-w-2xl text-base leading-relaxed text-[#57504A] sm:text-lg">{{ intro }}</p>
          <div class="mt-7 flex flex-wrap gap-3">
            <AppButton :to="`/shop?category=${category}`" variant="primary">在商城中筛选</AppButton>
            <AppButton to="/contact" variant="secondary" class="!border-[#2E2923]/15 !bg-white/70 !text-[#2E2923] hover:!bg-white">想要定制</AppButton>
          </div>
        </div>
        <div class="rounded-2xl bg-surface/85 p-6 text-ink shadow-soft backdrop-blur">
          <p class="text-[10px] uppercase tracking-[0.22em] text-ink-faint">在售</p>
          <p class="mt-3 font-display text-5xl font-light leading-none">
            {{ loading ? "…" : products.length }}
            <span class="text-sm font-normal text-ink-muted">件</span>
          </p>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">{{ gridNote }}</p>
        </div>
      </div>
    </section>

    <section class="container-page py-14 sm:py-20">
      <SectionHeading :eyebrow="category.toUpperCase()" :title="gridLabel" :description="gridNote" />

      <StarLoader v-if="loading" :message="t('common.loading')" class="py-10" />
      <EmptyState v-else-if="error" tone="error" title="加载失败" :description="error">
        <AppButton variant="secondary" @click="load">重试</AppButton>
      </EmptyState>
      <EmptyState v-else-if="!products.length" tone="empty" :title="emptyLabel">
        <AppButton :to="`/shop?category=${category}`" variant="secondary">去商城看看</AppButton>
      </EmptyState>

      <div v-else class="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
        <ProductCard v-for="product in products" :key="product.id" :product="product" />
      </div>
    </section>

    <slot />

    <section class="border-t border-line bg-[#f8f3ec] py-14 sm:py-20">
      <div class="container-page grid gap-10 lg:grid-cols-[0.8fr_1.2fr]">
        <SectionHeading eyebrow="FAQ" title="购买前确认" description="限量与定制件以服务端审批结果为准。" />
        <div class="divide-y divide-line">
          <details v-for="item in faqs" :key="item.q" class="group py-4">
            <summary class="cursor-pointer list-none font-medium marker:hidden">
              <span class="flex items-center justify-between gap-3">
                {{ item.q }}
                <span class="text-brand-strong transition-transform group-open:rotate-45" aria-hidden="true">+</span>
              </span>
            </summary>
            <p class="mt-2 text-sm leading-relaxed text-ink-muted">{{ item.a }}</p>
          </details>
        </div>
      </div>
    </section>
  </div>
</template>
