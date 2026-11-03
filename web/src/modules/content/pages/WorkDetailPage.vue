<script setup lang="ts">
/**
 * 作品详情（FR-002 / 03 文档 /works/:slug）。
 *
 * 信息顺序按 11 文档的页面布局基线：主图与相册 → 名称及类型 → 工艺与署名 →
 * 关联系列 → 询价入口。
 * 缩略图是裁切预览，点开始终显示完整图（灯箱支持 Esc 关闭并把焦点还给触发控件）。
 * 署名与工艺来自条目数据，没有登记的字段不显示，不用“佚名”之类占位词。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  fetchContentList,
  fetchSeriesList,
  type ContentDetail,
  type ContentSummary,
  type SeriesDetail,
} from "@/modules/content/api/content";
import ImageLightbox from "@/modules/content/components/ImageLightbox.vue";
import WorkCard from "@/modules/content/components/WorkCard.vue";
import { useSiteStore } from "@/modules/content/stores/site";
import { displayTitle, isDemoTitle } from "@/shared/lib/demo-title";
import { formatPriceFrom } from "@/shared/lib/format";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";

const route = useRoute();
const site = useSiteStore();

/**
 * `/content/series` 列表接口返回的是扁平系列字段（code、base_price_cents…），
 * 而 `SeriesDetail` 类型把规格声明在 `spec` 里（详情接口才是嵌套的）。
 * 这里两种形状都读，避免因为接口形状差异把关联系列丢掉。
 */
type SeriesListItem = SeriesDetail & {
  code?: string;
  base_price_cents?: number;
  currency?: "CNY" | "USD";
};

const work = ref<ContentDetail | null>(null);
const series = ref<SeriesListItem[]>([]);
const related = ref<ContentSummary[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);

const lightboxOpen = ref(false);
const lightboxIndex = ref(0);

const displayName = computed(() => displayTitle(work.value?.title));
const demo = computed(() => isDemoTitle(work.value?.title));

const slug = computed(() => {
  const value = route.params.slug;
  const raw = Array.isArray(value) ? value[0] : value;
  return raw ?? "";
});

/** 相册：优先多图，没有附图时退回主图，保证任何作品都能打开完整图。 */
const gallery = computed(() => {
  const entry = work.value;
  if (!entry) return [];
  if (entry.gallery.length) return entry.gallery;
  return entry.cover ? [entry.cover] : [];
});

const mainImage = computed(() => gallery.value[0] ?? null);

const breadcrumbs = computed<Array<{ label: string; to?: string }>>(() => [
  { label: "首页", to: "/" },
  { label: "作品集", to: "/works" },
  { label: work.value?.title ? displayName.value : "作品详情" },
]);

/** 关联系列：作品的 tags 里存放系列 code，与服务端筛选口径一致。 */
const relatedSeries = computed(() => {
  const tags = work.value?.tags ?? [];
  return series.value.filter((item) => {
    const code = item.code ?? item.spec?.code ?? "";
    return code !== "" && tags.includes(code);
  });
});

/** 关联系列的展示数据：把扁平与嵌套两种系列形状归一。 */
const relatedSeriesView = computed(() =>
  relatedSeries.value.map((item) => ({
    id: item.id,
    route: item.route,
    title: item.title,
    summary: item.summary,
    code: item.code ?? item.spec?.code ?? "",
    priceCents: item.spec?.base_price_cents ?? item.base_price_cents ?? null,
    currency: item.spec?.currency ?? item.currency ?? "CNY",
  })),
);

const creditRows = computed(() => {
  const credits = work.value?.credits;
  if (!credits) return [];
  return [
    { label: "设计", value: credits.designer },
    { label: "装师", value: credits.maker },
    { label: "摄影", value: credits.photographer },
    { label: "委托方署名", value: credits.client_display_name },
  ].filter((row): row is { label: string; value: string } => Boolean(row.value && row.value.trim()));
});

async function loadSecondary(species: string | null): Promise<void> {
  const tasks: Array<Promise<void>> = [
    fetchSeriesList()
      .then((page) => {
        series.value = page.items;
      })
      .catch(() => {
        series.value = [];
      }),
  ];
  if (species) {
    tasks.push(
      fetchContentList("work", { species, limit: 5, page: 1 })
        .then((page) => {
          related.value = page.items.filter((item) => item.slug !== slug.value).slice(0, 4);
        })
        .catch(() => {
          related.value = [];
        }),
    );
  }
  await Promise.allSettled(tasks);
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  work.value = null;
  related.value = [];

  if (!slug.value) {
    missing.value = true;
    loading.value = false;
    return;
  }

  try {
    const detail = await fetchContentDetail("work", slug.value);
    work.value = detail;
    applyPageHead({
      title: displayTitle(detail.seo.title || detail.title),
      description: detail.seo.description || detail.summary,
      canonical: detail.seo.canonical,
      ogType: "article",
      ogImage: detail.cover?.src,
    });
    // 附加区块（关联系列、同物种作品）不阻塞正文渲染
    void loadSecondary(detail.species);
  } catch (caught) {
    const state = classifyPublicError(caught, "作品加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
    applyPageHead({
      title: "作品详情",
      description: "作品详情需要公开授权后才会展示。",
      robots: "noindex, follow",
    });
  } finally {
    loading.value = false;
  }
}

function openLightbox(position: number): void {
  if (!gallery.value.length) return;
  lightboxIndex.value = position;
  lightboxOpen.value = true;
}

onMounted(() => {
  void site.load();
  void load();
});
watch(() => route.fullPath, load);
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <div class="container-page pb-16 pt-6">
      <div v-if="loading" class="grid gap-10 lg:grid-cols-[1.05fr_1fr]">
        <div class="aspect-[4/5] animate-pulse rounded-lg bg-canvas-sunk" />
        <div class="space-y-4">
          <SkeletonBlock :lines="2" aria-label="正在加载作品" />
          <SkeletonBlock :lines="4" aria-label="正在加载作品" />
          <SkeletonBlock :lines="3" aria-label="正在加载作品" />
        </div>
      </div>

      <EmptyState
        v-else-if="missing"
        tone="not-found"
        title="这件作品不存在或已下架"
        description="没有公开授权的委托作品不会出现在作品集里；如果这条链接来自分享，可能作品已经下架或改址。"
      >
        <AppButton size="sm" to="/works">返回作品集</AppButton>
        <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="blocked"
        tone="forbidden"
        title="这件作品当前不可见"
        description="内容需要相应权限才能查看。访客可以稍后重试，或联系客服了解情况。"
      >
        <AppButton size="sm" to="/works">返回作品集</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        tone="error"
        :title="error"
        description="网络恢复后可以重试；重试只重新读取这一件作品。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <template v-else-if="work">
        <!-- 主图与相册 -->
        <div class="grid gap-10 lg:grid-cols-[1.05fr_1fr]">
          <div>
            <button
              v-if="mainImage"
              type="button"
              class="block w-full overflow-hidden rounded-lg border border-line bg-canvas-sunk"
              :aria-label="`查看《${work.title}》第 1 张完整图`"
              @click="openLightbox(0)"
            >
              <img
                :src="mainImage.src"
                :srcset="mainImage.srcset.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
                sizes="(min-width: 1024px) 620px, 100vw"
                :width="mainImage.width"
                :height="mainImage.height"
                :alt="mainImage.alt || `${work.title} 成品展示`"
                loading="eager"
                decoding="async"
                class="w-full object-contain"
              />
            </button>
            <div v-else class="flex aspect-[4/5] items-center justify-center rounded-lg border border-dashed border-line-strong bg-surface-muted text-sm text-ink-faint">
              这件作品还没有可公开的图片
            </div>

            <ul v-if="gallery.length > 1" class="mt-4 flex flex-wrap gap-3">
              <li v-for="(image, position) in gallery" :key="image.id">
                <button
                  type="button"
                  class="block overflow-hidden rounded-sm border transition-colors"
                  :class="position === 0 ? 'border-amber' : 'border-line hover:border-amber'"
                  :aria-label="`查看《${work.title}》第 ${position + 1} 张完整图`"
                  @click="openLightbox(position)"
                >
                  <img
                    :src="image.thumb ?? image.src"
                    :width="image.width"
                    :height="image.height"
                    :alt="image.alt || `${work.title} 第 ${position + 1} 张缩略图`"
                    loading="lazy"
                    decoding="async"
                    class="size-20 object-cover"
                  />
                </button>
              </li>
            </ul>
            <p v-if="gallery.length > 1" class="mt-2 text-xs text-ink-faint">
              缩略图为裁切预览，点开显示完整图；灯箱里可以用 ← → 切换，Esc 关闭。
            </p>
          </div>

          <!-- 名称、类型、工艺与署名、询价入口 -->
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <StatusBadge v-if="work.work_type" tone="amber" :label="work.work_type" />
              <StatusBadge v-if="work.species" tone="neutral" :label="work.species" />
              <StatusBadge v-if="work.is_featured" tone="jade" label="代表作品" />
              <StatusBadge v-if="demo" tone="neutral" label="演示" />
            </div>

            <h1 class="mt-4 font-display text-3xl leading-tight sm:text-4xl">{{ displayName }}</h1>

            <p v-if="work.summary" class="mt-4 max-w-xl text-base leading-relaxed text-ink-muted">
              {{ work.summary }}
            </p>

            <dl class="mt-6 grid gap-4 border-y border-line py-5 text-sm sm:grid-cols-2">
              <div>
                <dt class="text-ink-faint">作品类型</dt>
                <dd class="mt-1">{{ work.work_type ?? "未登记" }}</dd>
              </div>
              <div>
                <dt class="text-ink-faint">物种</dt>
                <dd class="mt-1">{{ work.species ?? "未登记" }}</dd>
              </div>
              <div v-if="work.published_at">
                <dt class="text-ink-faint">发布时间</dt>
                <dd class="mt-1 tabular-nums">{{ work.published_at.slice(0, 10) }}</dd>
              </div>
              <div v-if="work.updated_at && work.updated_at !== work.published_at">
                <dt class="text-ink-faint">最近更新</dt>
                <dd class="mt-1 tabular-nums">{{ work.updated_at.slice(0, 10) }}</dd>
              </div>
            </dl>

            <!-- 工艺 -->
            <section class="mt-6" aria-labelledby="work-craft">
              <h2 id="work-craft" class="font-display text-lg">工艺说明</h2>
              <p v-if="work.craft_notes" class="mt-3 text-sm leading-relaxed text-ink-muted">
                {{ work.craft_notes }}
              </p>
              <p v-else class="mt-3 text-sm text-ink-faint">
                这件作品还没有登记工艺说明；具体材质与结构以询价时的报价单为准。
              </p>
            </section>

            <!-- 署名 -->
            <section class="mt-6" aria-labelledby="work-credits">
              <h2 id="work-credits" class="font-display text-lg">署名</h2>
              <dl v-if="creditRows.length" class="mt-3 grid gap-3 text-sm sm:grid-cols-2">
                <div v-for="row in creditRows" :key="row.label">
                  <dt class="text-ink-faint">{{ row.label }}</dt>
                  <dd class="mt-1">{{ row.value }}</dd>
                </div>
              </dl>
              <p v-else class="mt-3 text-sm text-ink-faint">
                署名信息还没有登记；我们不会用“佚名”或他人名义补位。
              </p>
              <p class="mt-3 text-xs text-ink-faint">
                本页作品已取得公开展示授权（FR-002）；未授权的委托不会出现在作品集里。
              </p>
            </section>

            <!-- 询价入口 -->
            <SurfaceCard class="mt-8" as="div">
              <h2 class="font-display text-lg">按这件作品询价</h2>
              <p class="mt-3 text-sm leading-relaxed text-ink-muted">
                提交询价后由人工核对可接范围并给出报价。报价有效期
                {{ site.commissionPolicy.quote_valid_days }} 天、预付款
                {{ site.commissionPolicy.prepayment_percent }}%、尾款
                {{ site.commissionPolicy.balance_due_days }} 天、免费修改
                {{ site.commissionPolicy.free_revision_count }} 次；这些数值来自后台配置。
              </p>
              <div class="mt-5 flex flex-wrap gap-3">
                <AppButton to="/commission/new" size="md">提交询价</AppButton>
                <AppButton to="/commission/guide" size="md" variant="secondary">先读委托指南</AppButton>
              </div>
              <p class="mt-3 text-xs text-ink-faint">
                询价需要手机号登录，便于保存草稿与查看报价；也可以先发邮件到
                <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
                说明需求。
              </p>
            </SurfaceCard>
          </div>
        </div>

        <!-- 关联系列 -->
        <section class="mt-16">
          <SectionHeading
            eyebrow="Series"
            title="关联系列"
            description="这件作品的标签里登记了以下系列；系列规格、包含项与不含项都在系列页列出。"
          />
          <div v-if="relatedSeriesView.length" class="mt-6 grid gap-6 md:grid-cols-2">
            <SurfaceCard v-for="item in relatedSeriesView" :key="item.id" interactive class="flex flex-col">
              <div class="flex items-start justify-between gap-4">
                <div>
                  <p class="eyebrow">{{ item.code }}</p>
                  <h3 class="mt-2 font-display text-xl">{{ item.title }}</h3>
                </div>
                <p class="shrink-0 text-right">
                  <span class="block font-display text-2xl">
                    {{ formatPriceFrom(item.priceCents, item.currency) }}
                  </span>
                  <span class="text-xs text-ink-faint">仅展示起价</span>
                </p>
              </div>
              <p class="mt-4 text-sm text-ink-muted">{{ item.summary }}</p>
              <div class="mt-6">
                <AppButton :to="item.route" size="sm" variant="secondary">查看系列规格</AppButton>
              </div>
            </SurfaceCard>
          </div>
          <EmptyState
            v-else
            class="mt-6"
            title="这件作品还没有关联系列"
            description="系列由运营在作品标签中登记；没有登记时不推断所属系列。可以先浏览全部系列规格。"
          >
            <AppButton size="sm" variant="secondary" to="/fursuits">浏览系列规格</AppButton>
          </EmptyState>
        </section>

        <!-- 同物种的其他作品：附加区块，失败或为空时不显示 -->
        <section v-if="related.length" class="mt-16">
          <SectionHeading
            eyebrow="More"
            :title="`同为${work.species ?? ''}的其他作品`"
            description="只列出已公开授权的作品。"
          />
          <div class="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <WorkCard v-for="item in related" :key="item.id" :work="item" />
          </div>
        </section>

        <ImageLightbox
          :images="gallery"
          :open="lightboxOpen"
          :index="lightboxIndex"
          :title="displayName"
          @close="lightboxOpen = false"
          @update:index="lightboxIndex = $event"
        />
      </template>
    </div>
  </div>
</template>
