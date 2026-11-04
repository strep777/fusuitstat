<script setup lang="ts">
/**
 * 角色领养池（/characters）。
 *
 * 约束（03 文档）：待领养/保留/已领养/自留可筛选，状态进 URL（刷新与返回都能恢复）；
 * 保留不等于成交；售价与授权范围可见（卡片给状态与价格，授权说明在详情页）；
 * 没有数据时显示诚实空态，不用占位角色填充。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CharacterCard from "@/modules/catalog/components/CharacterCard.vue";
import {
  CHARACTER_STATUS_OPTIONS,
  applyPageMeta,
  fetchCharacters,
  readQueryInt,
  readQueryString,
  type CharacterSummary,
} from "@/modules/catalog/api/catalog";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock } from "@/shared/ui/base";
import { ApiError } from "@/shared/lib/http";

const route = useRoute();
const router = useRouter();

const items = ref<CharacterSummary[]>([]);
const total = ref(0);
const hasMore = ref(false);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);

const filters = computed(() => ({
  status: readQueryString(route.query.status),
  species: readQueryString(route.query.species),
  q: readQueryString(route.query.q),
  page: readQueryInt(route.query.page, 1),
}));

/** 输入框草稿：只有提交表单时才写回 URL，地址栏不会随每次按键抖动。 */
const draftSpecies = ref(filters.value.species);
const draftQuery = ref(filters.value.q);

watch(filters, (value) => {
  draftSpecies.value = value.species;
  draftQuery.value = value.q;
});

const activeFilterCount = computed(
  () => [filters.value.status, filters.value.species, filters.value.q].filter(Boolean).length,
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  try {
    const page = await fetchCharacters({
      status: filters.value.status || undefined,
      species: filters.value.species || undefined,
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
      error.value = caught instanceof ApiError ? caught.message : "角色列表加载失败。";
    }
    items.value = [];
    total.value = 0;
    hasMore.value = false;
  } finally {
    loading.value = false;
  }
}

/** 筛选与分页都写进 URL；空值不写入，页码 1 不写入。 */
function pushQuery(patch: {
  status?: string;
  species?: string;
  q?: string;
  page?: number;
}): void {
  const merged = {
    status: patch.status ?? filters.value.status,
    species: patch.species ?? filters.value.species,
    q: patch.q ?? filters.value.q,
    page: patch.page ?? 1,
  };
  const query: Record<string, string> = {};
  if (merged.status) query.status = merged.status;
  if (merged.species) query.species = merged.species;
  if (merged.q) query.q = merged.q;
  if (merged.page > 1) query.page = String(merged.page);
  void router.push({ path: "/characters", query });
}

function submitFilters(): void {
  pushQuery({ species: draftSpecies.value.trim(), q: draftQuery.value.trim() });
}

function resetFilters(): void {
  draftSpecies.value = "";
  draftQuery.value = "";
  void router.push({ path: "/characters" });
}

onMounted(() => {
  applyPageMeta({
    title: "角色领养",
    description:
      "角色领养池：待领养、保留中、已领养与工作室自留状态可筛选；保留不等于成交，售价与授权范围在角色详情页说明。",
    canonicalPath: "/characters",
  });
  void load();
});

watch(() => route.fullPath, () => void load());
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '角色领养' }]" />

    <div class="mt-6">
      <SectionHeading
        :level="1"
        eyebrow="Adoption"
        title="角色领养"
        description="每个角色都是唯一设定：独占角色库存固定为 1，售出后不再复制。保留状态不等于成交，只有已完成领养的记录才代表角色归属变更。"
      >
        <template #actions>
          <AppButton to="/fursuits" size="sm" variant="secondary">查看系列规格</AppButton>
          <AppButton to="/estimate" size="sm">按自己的设定估价</AppButton>
        </template>
      </SectionHeading>
    </div>

    <!-- 筛选：状态进 URL -->
    <section class="mt-10 rounded-lg border border-line bg-surface p-5" aria-label="角色筛选">
      <div class="flex flex-wrap items-center gap-2">
        <span class="text-xs text-ink-faint">状态</span>
        <button
          type="button"
          class="min-h-[44px] rounded-sm border px-3 text-sm transition-colors"
          :class="
            filters.status === ''
              ? 'border-amber bg-amber-soft text-amber-strong'
              : 'border-line-strong bg-surface text-ink-soft hover:border-amber'
          "
          :aria-pressed="filters.status === ''"
          @click="pushQuery({ status: '' })"
        >
          全部
        </button>
        <button
          v-for="option in CHARACTER_STATUS_OPTIONS"
          :key="option.value"
          type="button"
          class="min-h-[44px] rounded-sm border px-3 text-sm transition-colors"
          :class="
            filters.status === option.value
              ? 'border-amber bg-amber-soft text-amber-strong'
              : 'border-line-strong bg-surface text-ink-soft hover:border-amber'
          "
          :aria-pressed="filters.status === option.value"
          @click="pushQuery({ status: option.value })"
        >
          {{ option.label }}
        </button>
      </div>

      <form class="mt-5 grid gap-4 sm:grid-cols-[1fr_1fr_auto]" @submit.prevent="submitFilters()">
        <div>
          <label class="text-sm font-medium text-ink-soft" for="character-species">物种</label>
          <input
            id="character-species"
            v-model="draftSpecies"
            class="field-base mt-1.5"
            type="search"
            placeholder="输入物种名称筛选"
            autocomplete="off"
          />
        </div>
        <div>
          <label class="text-sm font-medium text-ink-soft" for="character-q">关键词</label>
          <input
            id="character-q"
            v-model="draftQuery"
            class="field-base mt-1.5"
            type="search"
            placeholder="角色名或设定关键词"
            autocomplete="off"
          />
        </div>
        <div class="flex items-end gap-3">
          <AppButton type="submit" size="sm">筛选</AppButton>
          <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
            清除
          </AppButton>
        </div>
      </form>
      <p class="mt-3 text-xs text-ink-faint">筛选条件会写进地址栏，刷新或分享链接都能恢复同样的结果。</p>
    </section>

    <div v-if="loading" class="mt-10 rounded-lg border border-line bg-surface p-6">
      <SkeletonBlock :lines="4" aria-label="正在加载角色列表" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="没有访问角色池的权限"
      description="角色池对访客开放；如果这里被拒绝，可能是账号状态受限。登录后可确认账号状态，公开角色无需登录即可查看。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      title="角色列表暂时无法加载"
      :description="`${error} 已选择的筛选条件仍保留在地址栏，可直接重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="items.length">
      <p class="mt-8 text-sm text-ink-muted">
        <template v-if="activeFilterCount">符合条件 {{ total }} 位角色</template>
        <template v-else>共 {{ total }} 位角色</template>
        <span v-if="filters.page > 1" class="ml-2 text-ink-faint">第 {{ filters.page }} 页</span>
      </p>

      <div class="mt-6 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <CharacterCard v-for="character in items" :key="character.id" :character="character" />
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
      title="没有符合条件的角色"
      description="当前筛选条件下没有已发布的角色。可以放宽状态或物种条件；保留中的角色不会因为筛选而变成可购买。"
    >
      <AppButton size="sm" variant="secondary" @click="resetFilters()">清除筛选</AppButton>
    </EmptyState>

    <EmptyState
      v-else
      class="mt-10"
      title="当前没有已发布的角色"
      description="角色由运营整理设定图与授权说明后发布；没有取得展示授权的设定不会出现在这里。"
    >
      <AppButton to="/estimate" size="sm">按自己的设定估价</AppButton>
      <AppButton to="/works" size="sm" variant="secondary">浏览作品集</AppButton>
    </EmptyState>
  </div>
</template>
