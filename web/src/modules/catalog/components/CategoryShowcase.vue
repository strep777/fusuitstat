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
  props.tone === "grape" ? "bg-[#EDE7FF] text-[#17121C]" : "bg-[#FFF1C9] text-[#17121C]",
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
    <section class="border-b-2 border-ink bg-dots" :class="toneClass">
      <div class="container-page grid gap-8 py-12 lg:grid-cols-[1.2fr_0.8fr] lg:items-end lg:py-16">
        <div>
          <p class="font-mono text-[11px] font-bold uppercase tracking-[0.18em] text-[#17121C]/60">{{ eyebrow }}</p>
          <h1 class="mt-3 text-balance font-display text-4xl font-bold leading-[0.98] tracking-[-0.045em] sm:text-6xl">
            {{ title }}
          </h1>
          <p class="mt-4 max-w-2xl text-lg leading-relaxed text-[#4A3D52]">{{ intro }}</p>
          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton :to="`/shop?category=${category}`" variant="primary">在商城中筛选</AppButton>
            <AppButton to="/contact" variant="secondary">想要定制</AppButton>
          </div>
        </div>
        <div class="rotate-1 border-2 border-ink bg-surface p-6 text-[#17121C] shadow-[8px_8px_0_var(--color-ink)]">
          <p class="font-mono text-[10px] font-bold uppercase tracking-[0.22em]" :class="tone === 'grape' ? 'text-[#4A2ED0]' : 'text-[#8A5A00]'">
            在售
          </p>
          <p class="mt-3 font-display text-6xl font-bold leading-none">
            {{ loading ? "…" : products.length }}
            <span class="text-base font-bold text-[#6B5D74]">件</span>
          </p>
          <p class="mt-2 text-sm text-[#4A3D52]">{{ gridNote }}</p>
        </div>
      </div>
    </section>

    <section class="container-page py-12 sm:py-16">
      <SectionHeading :eyebrow="category.toUpperCase()" :title="gridLabel" :description="gridNote" />

      <StarLoader v-if="loading" :message="t('common.loading')" class="py-10" />
      <EmptyState v-else-if="error" tone="error" title="加载失败" :description="error">
        <AppButton variant="secondary" @click="load">重试</AppButton>
      </EmptyState>
      <EmptyState v-else-if="!products.length" tone="empty" :title="emptyLabel">
        <AppButton :to="`/shop?category=${category}`" variant="secondary">去商城看看</AppButton>
      </EmptyState>

      <div v-else class="mt-8 grid gap-4 [grid-template-columns:repeat(auto-fit,minmax(230px,1fr))]">
        <ProductCard v-for="product in products" :key="product.id" :product="product" />
      </div>
    </section>

    <slot />

    <section class="border-t-2 border-ink bg-surface-muted py-12">
      <div class="container-page grid gap-8 lg:grid-cols-[0.8fr_1.2fr]">
        <SectionHeading eyebrow="FAQ" title="购买前确认" description="限量与定制件以服务端审批结果为准。" />
        <div class="space-y-3">
          <details v-for="item in faqs" :key="item.q" class="group rounded-sm border-2 border-ink bg-surface p-4">
            <summary class="cursor-pointer list-none font-bold marker:hidden">
              <span class="flex items-center justify-between gap-3">
                {{ item.q }}
                <span class="font-mono text-brand-strong transition-transform group-open:rotate-45" aria-hidden="true">+</span>
              </span>
            </summary>
            <p class="mt-2 text-sm leading-relaxed text-ink-muted">{{ item.a }}</p>
          </details>
        </div>
      </div>
    </section>
  </div>
</template>
