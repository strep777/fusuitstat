<script setup lang="ts">
/**
 * 系列列表（/fursuits）：系列概览、适用场景与起价包含项/不含项。
 *
 * 约束（03/11 文档）：起价必须同时标注包含项与不含项；只有 `lead_time_note` 有值才显示交期，
 * 并注明“实际交期以报价记录为准”；参数缺失时用空态，不放占位数据。
 */
import { computed, onMounted, ref } from "vue";

import {
  applyPageMeta,
  fetchSeriesList,
  specComparison,
  specValueOf,
  toCurrency,
  withQuery,
  type SeriesDetail,
} from "@/modules/catalog/api/catalog";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatPriceFrom, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const series = ref<SeriesDetail[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  try {
    const page = await fetchSeriesList();
    series.value = page.items;
  } catch (caught) {
    if (caught instanceof ApiError && (caught.status === 403 || caught.needsLogin)) {
      forbidden.value = true;
      series.value = [];
    } else {
      error.value = caught instanceof ApiError ? caught.message : "系列资料加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  applyPageMeta({
    title: "系列与规格",
    description:
      "兽装系列概览：材质、结构、交付内容与起价包含项、不含项。起价只作参考，最终金额与交期以工作人员发布的报价记录为准。",
    canonicalPath: "/fursuits",
  });
  void load();
});

/** 规格对比：行来自所有系列 spec_schema 的键并集，缺值显示“未提供”。 */
const withSpec = computed(() => series.value.filter((item) => item.spec !== null));
const comparisonRows = computed(() =>
  specComparison(withSpec.value.map((item) => item.spec?.spec_schema)),
);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '系列与规格' }]" />

    <div class="mt-6">
      <SectionHeading
        :level="1"
        eyebrow="Series"
        title="系列与规格"
        description="不同系列在材质、结构与交付内容上差别明确。每张卡片分开列出起价的包含项与不含项；起价不等于最终报价，报价与交期以工作人员发布的报价记录为准。"
      >
        <template #actions>
          <AppButton to="/commission/new" size="sm">按需求询价</AppButton>
          <AppButton to="/characters" size="sm" variant="secondary">角色领养</AppButton>
        </template>
      </SectionHeading>
    </div>

    <div v-if="loading" class="py-16">
      <StarLoader message="正在读取系列资料" hint="首次加载可能需要一点时间" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-12"
      tone="forbidden"
      title="没有访问系列资料的权限"
      description="系列资料可能仅对特定合作方开放。登录后可确认自己的账号状态，公开系列无需登录即可查看。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-12"
      tone="error"
      title="系列资料暂时无法加载"
      :description="`${error} 网络恢复后可以重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="series.length">
      <div class="mt-10 grid gap-6 md:grid-cols-2">
        <SurfaceCard v-for="item in series" :key="item.id" interactive class="flex flex-col">
          <div class="flex items-start justify-between gap-4">
            <div>
              <p class="eyebrow">{{ item.spec?.code ?? item.slug }}</p>
              <h2 class="mt-2 font-display text-xl">{{ item.title }}</h2>
            </div>
            <p class="shrink-0 text-right">
              <span class="block font-display text-2xl">
                {{ formatPriceFrom(item.spec?.base_price_cents ?? null, toCurrency(item.spec?.currency)) }}
              </span>
              <span class="text-xs text-ink-faint">起价，非最终报价</span>
            </p>
          </div>

          <p class="mt-4 text-sm text-ink-muted">{{ truncate(item.summary, 120) }}</p>

          <dl class="mt-5 grid gap-3 text-sm">
            <div v-if="item.spec?.price_includes?.length">
              <dt class="text-xs text-ink-faint">起价包含</dt>
              <dd class="mt-1 flex flex-wrap gap-1.5">
                <span
                  v-for="line in item.spec.price_includes.slice(0, 6)"
                  :key="line"
                  class="rounded-xs bg-jade-soft px-2 py-0.5 text-xs text-jade"
                >
                  {{ line }}
                </span>
              </dd>
            </div>
            <div v-else>
              <dt class="text-xs text-ink-faint">起价包含</dt>
              <dd class="mt-1 text-xs text-ink-muted">运营尚未公布包含项，暂不展示推测内容。</dd>
            </div>

            <div v-if="item.spec?.price_excludes?.length">
              <dt class="text-xs text-ink-faint">起价不含</dt>
              <dd class="mt-1 flex flex-wrap gap-1.5">
                <span
                  v-for="line in item.spec.price_excludes.slice(0, 6)"
                  :key="line"
                  class="rounded-xs bg-canvas-sunk px-2 py-0.5 text-xs text-ink-muted"
                >
                  {{ line }}
                </span>
              </dd>
            </div>

            <div v-if="item.spec?.lead_time_note">
              <dt class="text-xs text-ink-faint">交期说明</dt>
              <dd class="mt-1 text-ink-soft">
                {{ item.spec.lead_time_note }}
                <span class="mt-1 block text-xs text-ink-faint">实际交期以报价记录为准。</span>
              </dd>
            </div>
          </dl>

          <div v-if="item.tags?.length" class="mt-5">
            <p class="text-xs text-ink-faint">适用场景</p>
            <p class="mt-1 flex flex-wrap gap-1.5">
              <StatusBadge v-for="tag in item.tags.slice(0, 5)" :key="tag" tone="neutral" :label="tag" />
            </p>
          </div>

          <div class="mt-auto flex flex-wrap gap-3 pt-6">
            <AppButton :to="`/fursuits/${item.slug}`" size="sm" variant="secondary">查看规格</AppButton>
            <AppButton :to="withQuery('/commission/new', { series: item.slug })" size="sm">
              按此系列询价
            </AppButton>
          </div>
        </SurfaceCard>
      </div>

      <!-- 规格对比：只有确实有 spec_schema 的系列参与 -->
      <section v-if="comparisonRows.length && withSpec.length > 1" class="mt-16">
        <SectionHeading
          eyebrow="Compare"
          title="规格对比"
          description="表格行由各系列已发布的规格字段自动汇总；未填写的单元格显示“未提供”，不做推测性补齐。"
        />
        <div class="mt-6 overflow-x-auto rounded-lg border border-line bg-surface">
          <table class="w-full min-w-[640px] border-collapse text-sm">
            <caption class="sr-only">各系列已发布规格对比</caption>
            <thead>
              <tr class="border-b border-line bg-surface-muted text-left">
                <th scope="col" class="px-4 py-3 font-medium text-ink-soft">规格项</th>
                <th
                  v-for="item in withSpec"
                  :key="item.id"
                  scope="col"
                  class="px-4 py-3 font-medium text-ink-soft"
                >
                  {{ item.title }}
                </th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in comparisonRows" :key="row.key" class="border-b border-line last:border-0">
                <th scope="row" class="px-4 py-3 text-left font-normal text-ink-muted">
                  {{ row.label }}
                </th>
                <td v-for="item in withSpec" :key="`${row.key}-${item.id}`" class="px-4 py-3 align-top">
                  <template v-if="specValueOf(item.spec?.spec_schema, row.key)">
                    {{ specValueOf(item.spec?.spec_schema, row.key) }}
                  </template>
                  <span v-else class="text-ink-faint">未提供</span>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="mt-3 text-xs text-ink-faint">
          对比表只反映已发布参数，不含未核准的交期与性能承诺。
        </p>
      </section>
    </template>

    <EmptyState
      v-else
      class="mt-12"
      title="系列资料尚未发布"
      description="系列参数由运营在后台维护并预览后发布；没有核准的参数不会显示。"
    >
      <AppButton to="/commission/new" size="sm">先提交询价</AppButton>
      <AppButton to="/works" size="sm" variant="secondary">浏览作品集</AppButton>
    </EmptyState>
  </div>
</template>
