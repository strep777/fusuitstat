<script setup lang="ts">
/**
 * 系列详情（/fursuits/:slug）：规格、选配、交付内容、FAQ 与询价入口。
 *
 * 约束（03/11 文档）：
 * - 规格表由 `spec_schema` 动态渲染键值，不硬编码字段名，缺值不编造。
 * - 起价必须同时标注包含项与不含项，并说明起价不等于最终报价。
 * - 只有 `lead_time_note` 有值才显示交期，且注明“实际交期以报价记录为准”；
 *   不承诺未经业务核准的性能。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  applyPageMeta,
  fetchSeries,
  specEntries,
  toCurrency,
  withQuery,
  type SeriesDetail,
} from "@/modules/catalog/api/catalog";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatPriceFrom, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

/** FAQ 列表项：正文由后台清洗后以 HTML 提供，缺失时退回摘要。 */
interface FaqItem {
  id: string;
  title: string;
  summary?: string;
  body_html?: string | null;
}

const route = useRoute();
const detail = ref<SeriesDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const notFound = ref(false);
const forbidden = ref(false);

const slug = computed(() => String(route.params.slug ?? ""));

async function load(): Promise<void> {
  if (!slug.value) {
    notFound.value = true;
    loading.value = false;
    return;
  }
  loading.value = true;
  error.value = null;
  notFound.value = false;
  forbidden.value = false;
  try {
    const payload = await fetchSeries(slug.value);
    detail.value = payload;
    applyPageMeta({
      title: payload.title,
      description:
        payload.summary ||
        `${payload.title} 系列的规格、起价包含项与不含项、交付内容与常见问题。最终金额与交期以报价记录为准。`,
      canonicalPath: `/fursuits/${payload.slug}`,
    });
  } catch (caught) {
    if (caught instanceof ApiError && (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND")) {
      notFound.value = true;
    } else if (caught instanceof ApiError && (caught.status === 403 || caught.needsLogin)) {
      forbidden.value = true;
    } else {
      error.value = caught instanceof ApiError ? caught.message : "系列详情加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  applyPageMeta({
    title: "系列详情",
    description: "系列规格、起价包含项与不含项、交付内容与常见问题。",
    canonicalPath: `/fursuits/${slug.value}`,
  });
  void load();
});

watch(slug, () => void load());

const spec = computed(() => detail.value?.spec ?? null);
const specRows = computed(() => specEntries(spec.value?.spec_schema));
const includes = computed(() => spec.value?.price_includes ?? []);
const excludes = computed(() => spec.value?.price_excludes ?? []);
const faqs = computed<FaqItem[]>(() => detail.value?.faqs ?? []);
const cover = computed(() => detail.value?.cover ?? null);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '系列与规格', to: '/fursuits' },
        { label: detail?.title ?? '系列详情' },
      ]"
    />

    <div v-if="loading" class="py-16">
      <StarLoader message="正在读取系列规格" />
    </div>

    <EmptyState
      v-else-if="notFound"
      class="mt-10"
      tone="not-found"
      title="找不到这个系列"
      description="该系列可能已下架或地址有误；下架内容不会通过公开地址继续提供。"
    >
      <AppButton to="/fursuits" size="sm">返回系列列表</AppButton>
      <AppButton to="/contact" size="sm" variant="secondary">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="没有访问该系列的权限"
      description="该系列可能仅对特定合作方开放。公开系列无需登录即可查看，登录后可确认账号状态。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/fursuits">返回系列列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      title="系列详情暂时无法加载"
      :description="`${error} 网络恢复后可以重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/fursuits">返回系列列表</AppButton>
    </EmptyState>

    <template v-else-if="detail">
      <div class="mt-6 grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <div>
          <p class="eyebrow">{{ spec?.code ?? detail.slug }}</p>
          <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">{{ detail.title }}</h1>
          <p v-if="detail.summary" class="mt-5 max-w-2xl text-base leading-relaxed text-ink-muted">
            {{ detail.summary }}
          </p>

          <div v-if="detail.tags?.length" class="mt-5 flex flex-wrap gap-2">
            <StatusBadge v-for="tag in detail.tags" :key="tag" tone="neutral" :label="tag" />
          </div>

          <div v-if="cover" class="mt-8 overflow-hidden rounded-lg border border-line bg-canvas-sunk">
            <img
              :src="cover.thumb ?? cover.src"
              :srcset="cover.srcset.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
              sizes="(min-width: 1024px) 55vw, 100vw"
              :width="cover.width"
              :height="cover.height"
              :alt="cover.alt || `${detail.title} 系列成品图`"
              loading="lazy"
              decoding="async"
              class="w-full object-cover"
            />
          </div>
        </div>

        <!-- 价格与交付：起价的包含项与不含项始终成对出现 -->
        <SurfaceCard class="h-fit">
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="eyebrow">Starting price</p>
              <p class="mt-2 font-display text-3xl">
                {{ formatPriceFrom(spec?.base_price_cents ?? null, toCurrency(spec?.currency)) }}
              </p>
              <p class="mt-1 text-xs text-ink-faint">起价只作参考，不等于最终报价。</p>
            </div>
          </div>

          <dl class="mt-6 grid gap-4 text-sm">
            <div>
              <dt class="text-xs text-ink-faint">包含</dt>
              <dd v-if="includes.length" class="mt-1.5 flex flex-wrap gap-1.5">
                <span
                  v-for="line in includes"
                  :key="line"
                  class="rounded-xs bg-jade-soft px-2 py-0.5 text-xs text-jade"
                >
                  {{ line }}
                </span>
              </dd>
              <dd v-else class="mt-1 text-xs text-ink-muted">运营尚未公布包含项。</dd>
            </div>

            <div>
              <dt class="text-xs text-ink-faint">不含</dt>
              <dd v-if="excludes.length" class="mt-1.5 flex flex-wrap gap-1.5">
                <span
                  v-for="line in excludes"
                  :key="line"
                  class="rounded-xs bg-canvas-sunk px-2 py-0.5 text-xs text-ink-muted"
                >
                  {{ line }}
                </span>
              </dd>
              <dd v-else class="mt-1 text-xs text-ink-muted">
                未公布的不含项不做推测；加价项在报价单中逐条列出。
              </dd>
            </div>

            <div v-if="spec?.lead_time_note">
              <dt class="text-xs text-ink-faint">交期说明</dt>
              <dd class="mt-1 text-ink-soft">{{ spec.lead_time_note }}</dd>
              <dd class="mt-1 text-xs text-ink-faint">实际交期以报价记录为准。</dd>
            </div>
            <div v-else>
              <dt class="text-xs text-ink-faint">交期说明</dt>
              <dd class="mt-1 text-xs text-ink-muted">
                尚未公布经核准的交期；排期与交期在报价记录中确认。
              </dd>
            </div>
          </dl>

          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton :to="withQuery('/commission/new', { series: detail.slug })" size="sm">
              按此系列询价
            </AppButton>
            <AppButton to="/commission/guide" size="sm" variant="secondary">阅读委托指南</AppButton>
          </div>
        </SurfaceCard>
      </div>

      <!-- 规格表：完全由 spec_schema 生成 -->
      <section class="mt-16">
        <SectionHeading
          eyebrow="Specification"
          title="规格参数"
          description="下表由该系列已发布的参数自动生成，未填写的字段不会出现在这里，也不会用推测值补齐。"
        />
        <div v-if="specRows.length" class="mt-6 overflow-hidden rounded-lg border border-line bg-surface">
          <table class="w-full border-collapse text-sm">
            <caption class="sr-only">{{ detail.title }} 已发布规格参数</caption>
            <tbody>
              <tr v-for="row in specRows" :key="row.key" class="border-b border-line last:border-0">
                <th scope="row" class="w-40 bg-surface-muted px-4 py-3 text-left align-top font-normal text-ink-muted">
                  {{ row.label }}
                </th>
                <td class="px-4 py-3 align-top">{{ row.value }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <EmptyState
          v-else
          class="mt-6"
          title="规格参数尚未发布"
          description="参数由运营在后台维护并预览后发布；没有核准的数据不会显示。"
        >
          <AppButton to="/contact" size="sm" variant="secondary">直接询问参数</AppButton>
        </EmptyState>
      </section>

      <!-- 选配与交付内容 -->
      <section class="mt-16 grid gap-6 md:grid-cols-2">
        <SurfaceCard>
          <h2 class="font-display text-xl">选配与加价</h2>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">
            起价不含的部件、材质与结构升级属于选配，逐项加价；是否可做与具体金额由工作人员在报价中确认，
            页面不预先承诺可实现范围。
          </p>
          <ul v-if="excludes.length" class="mt-4 space-y-2 text-sm text-ink-soft">
            <li v-for="line in excludes" :key="line" class="flex gap-2">
              <span aria-hidden="true">·</span>
              <span>{{ line }}</span>
            </li>
          </ul>
          <p v-else class="mt-4 text-xs text-ink-muted">不含项尚未公布。</p>
        </SurfaceCard>

        <SurfaceCard>
          <h2 class="font-display text-xl">交付内容</h2>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">
            起价包含以下交付内容；阶段确认与出厂记录按委托流程执行，运费、关税与偏远地区附加费单独说明。
          </p>
          <ul v-if="includes.length" class="mt-4 space-y-2 text-sm text-ink-soft">
            <li v-for="line in includes" :key="line" class="flex gap-2">
              <span aria-hidden="true">·</span>
              <span>{{ line }}</span>
            </li>
          </ul>
          <p v-else class="mt-4 text-xs text-ink-muted">包含项尚未公布。</p>
        </SurfaceCard>
      </section>

      <!-- FAQ -->
      <section class="mt-16 grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
        <div>
          <SectionHeading eyebrow="FAQ" title="该系列常见问题" description="只显示与该系列相关的已发布问答。" />
          <div v-if="faqs.length" class="mt-6 space-y-3">
            <details
              v-for="item in faqs"
              :key="item.id"
              class="group rounded-lg border border-line bg-surface px-5 py-4 open:shadow-quiet"
            >
              <summary class="cursor-pointer list-none font-medium marker:hidden">
                <span class="flex items-start justify-between gap-4">
                  {{ item.title }}
                  <span class="mt-1 shrink-0 text-xs text-ink-faint group-open:hidden" aria-hidden="true">展开</span>
                  <span class="mt-1 hidden shrink-0 text-xs text-ink-faint group-open:inline" aria-hidden="true">收起</span>
                </span>
              </summary>
              <div v-if="item.body_html" class="prose-quiet mt-3 text-sm leading-relaxed text-ink-muted" v-html="item.body_html" />
              <p v-else-if="item.summary" class="mt-3 text-sm leading-relaxed text-ink-muted">
                {{ truncate(item.summary, 200) }}
              </p>
            </details>
          </div>
          <EmptyState
            v-else
            class="mt-6"
            title="该系列暂无问答"
            description="问答由运营整理并发布；可以先查看完整 FAQ 或直接询价。"
          >
            <AppButton to="/faq" size="sm" variant="secondary">查看完整 FAQ</AppButton>
          </EmptyState>
        </div>

        <SurfaceCard class="h-fit !bg-[#f1e4d7] !text-ink">
          <p class="eyebrow">Next step</p>
          <h2 class="mt-3 font-display text-2xl font-medium">按这个系列开始沟通</h2>
          <p class="mt-4 text-sm leading-relaxed text-ink-muted">
            询价是需求沟通记录，不是订单；工作人员会先确认可接范围与参数，再发布有期限的报价。
          </p>
          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton :to="withQuery('/commission/new', { series: detail.slug })" size="sm">
              提交询价
            </AppButton>
            <AppButton to="/fursuits" size="sm" variant="secondary">
              对比其它系列
            </AppButton>
          </div>
        </SurfaceCard>
      </section>
    </template>
  </div>
</template>

<style scoped>
.prose-quiet :deep(p) {
  margin-bottom: 0.5rem;
}
.prose-quiet :deep(p:last-child) {
  margin-bottom: 0;
}
.prose-quiet :deep(ul) {
  list-style: disc;
  padding-left: 1.25rem;
}
</style>
