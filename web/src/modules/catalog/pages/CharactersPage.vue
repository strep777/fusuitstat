<script setup lang="ts">
/**
 * 角色汤（/characters）：领养工作室原创角色设定。
 *
 * 每个角色都是唯一设定；状态、价格与授权范围公开可见。
 * 保留不等于成交，只有已完成领养的记录才代表角色归属变更。
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
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock } from "@/shared/ui/base";
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

const featured = computed(() => (items.value.length === 1 ? items.value[0] ?? null : null));

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
      error.value = caught instanceof ApiError ? caught.message : "角色汤加载失败。";
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
    title: "角色汤",
    description:
      "角色汤：领养工作室原创角色设定。每个角色都是唯一设定，状态、售价与授权范围公开可见；保留状态不等于成交。",
    canonicalPath: "/characters",
  });
  void load();
});

watch(() => route.fullPath, () => void load());
</script>

<template>
  <div class="container-page pb-10 pt-8 lg:pb-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '角色汤' }]" />

    <section class="relative mt-6 overflow-hidden rounded-[2rem] bg-[#f4efe8]">
      <div class="relative z-10 grid gap-8 px-7 py-10 sm:px-10 sm:py-14 lg:grid-cols-[1.05fr_0.95fr] lg:items-end">
        <div>
          <p class="text-[11px] uppercase tracking-[0.22em] text-ink-faint">CHARACTER SOUP</p>
          <h1 class="mt-4 font-display text-5xl font-medium tracking-[-0.045em] text-ink sm:text-7xl">
            角色汤
          </h1>
          <p class="mt-5 max-w-lg text-sm leading-relaxed text-ink-muted sm:text-base">
            一个设定，只属于一个委托人。领养后获得角色使用权，可以按它定制整装，也可以只带走这份设定。
          </p>
          <div class="mt-7 flex flex-wrap gap-3">
            <AppButton to="/estimate" size="lg">按设定估价</AppButton>
            <AppButton to="/fursuits" size="lg" variant="secondary">查看系列规格</AppButton>
          </div>
        </div>

        <div class="flex items-end justify-between gap-6">
          <p class="text-[11px] leading-relaxed text-ink-faint">
            保留状态不等于成交<br />
            以完成领养的记录为准
          </p>
          <div class="rounded-2xl bg-white/75 px-6 py-5 text-right">
            <p class="text-[10px] uppercase tracking-[0.2em] text-ink-faint">AVAILABLE</p>
            <p class="mt-2 font-display text-4xl font-medium text-ink">{{ total }}</p>
            <p class="mt-1 text-[11px] text-ink-muted">位角色可查看</p>
          </div>
        </div>
      </div>
      <span
        class="pointer-events-none absolute -bottom-16 right-4 select-none font-display text-[18rem] leading-none text-ink/[0.04]"
        aria-hidden="true"
      >汤</span>
    </section>

    <section class="mt-8 border-y border-line py-5" aria-label="角色筛选">
      <div class="flex flex-col gap-3 lg:flex-row lg:items-center">
        <div class="flex items-center gap-1.5 overflow-x-auto pb-1">
          <button
            type="button"
            class="min-h-[44px] shrink-0 rounded-full px-4 text-xs transition-colors"
            :class="
              filters.status === ''
                ? 'bg-brand text-white'
                : 'bg-canvas-sunk text-ink-soft hover:text-ink'
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
            class="min-h-[44px] shrink-0 rounded-full px-4 text-xs transition-colors"
            :class="
              filters.status === option.value
                ? 'bg-brand text-white'
                : 'bg-canvas-sunk text-ink-soft hover:text-ink'
            "
            :aria-pressed="filters.status === option.value"
            @click="pushQuery({ status: option.value })"
          >
            {{ option.label }}
          </button>
        </div>

        <form class="flex min-w-0 flex-wrap gap-2 lg:ml-auto lg:max-w-2xl lg:flex-1" @submit.prevent="submitFilters()">
          <label class="sr-only" for="character-species">物种</label>
          <input
            id="character-species"
            v-model="draftSpecies"
            class="field-base min-w-[130px] flex-1"
            type="search"
            placeholder="物种"
            autocomplete="off"
          />
          <label class="sr-only" for="character-q">关键词</label>
          <input
            id="character-q"
            v-model="draftQuery"
            class="field-base min-w-[160px] flex-[1.4]"
            type="search"
            placeholder="角色名或设定关键词"
            autocomplete="off"
          />
          <AppButton type="submit" size="sm">筛选</AppButton>
          <AppButton v-if="activeFilterCount" size="sm" variant="ghost" @click="resetFilters()">
            清除
          </AppButton>
        </form>
      </div>
      <p class="mt-3 text-[11px] text-ink-faint">筛选条件写进地址栏，刷新或分享链接都能恢复。</p>
    </section>

    <div v-if="loading" class="mt-8 rounded-2xl bg-surface p-6 shadow-soft">
      <SkeletonBlock :lines="4" aria-label="正在加载角色汤" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="没有访问角色汤的权限"
      description="角色汤对访客开放；如果这里被拒绝，可能是账号状态受限。登录后可确认账号状态，公开角色无需登录即可查看。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="角色汤暂时无法加载"
      :description="`${error} 已选择的筛选条件仍保留在地址栏，可直接重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="items.length">
      <p class="mt-6 text-sm text-ink-muted">
        <template v-if="activeFilterCount">符合条件 {{ total }} 位角色</template>
        <template v-else>共 {{ total }} 位角色</template>
        <span v-if="filters.page > 1" class="ml-2 text-ink-faint">第 {{ filters.page }} 页</span>
      </p>

      <div v-if="featured" class="mt-5">
        <CharacterCard :character="featured" featured />
      </div>
      <div v-else class="mt-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
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
      class="mt-8"
      title="没有符合条件的角色"
      description="当前筛选条件下没有已发布的角色。可以放宽状态或物种条件；保留中的角色不会因为筛选而变成可购买。"
    >
      <AppButton size="sm" variant="secondary" @click="resetFilters()">清除筛选</AppButton>
    </EmptyState>

    <EmptyState
      v-else
      class="mt-8"
      title="角色汤正在熬制"
      description="角色由运营整理设定图与授权说明后发布；没有取得展示授权的设定不会出现在这里。"
    >
      <AppButton to="/estimate" size="sm">按自己的设定估价</AppButton>
      <AppButton to="/works" size="sm" variant="secondary">浏览作品集</AppButton>
    </EmptyState>
  </div>
</template>
