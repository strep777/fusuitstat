<script setup lang="ts">
/**
 * 首页：先给结论（做什么、现在能不能接），再给入口（委托/商城/洗毛/道具/周边）。
 * 文案规则见 11 文档：短句、先结论后条件、不堆形容词。
 */

import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { fetchHome, type HomeAggregate } from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { displayTitle } from "@/shared/lib/demo-title";
import { useI18n, resolveText } from "@/shared/lib/i18n";
import { BrandMark, StarLoader } from "@/shared/ui/BrandMark";
import { AppButton, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDate, formatMoney, formatPriceFrom, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const { locale, t } = useI18n();
const site = useSiteStore();

const home = ref<HomeAggregate | null>(null);
const loading = ref(true);
const error = ref<{ code: string; message: string } | null>(null);

const heroTagline = computed(() => (home.value ? resolveText(home.value.hero.tagline, locale.value) : ""));
const heroStatement = computed(() =>
  home.value ? resolveText(home.value.hero.statement, locale.value) : "",
);

const heroPrimary = computed(() => {
  const action = home.value?.hero.primary_action;
  return {
    label: (action && resolveText(action.label, locale.value)) || "提交询价",
    to: action?.to || "/commission/new",
  };
});
const heroSecondary = computed(() => {
  const action = home.value?.hero.secondary_action;
  return {
    label: (action && resolveText(action.label, locale.value)) || "看看作品",
    to: action?.to || "/works",
  };
});

const schedule = computed(() => home.value?.schedule ?? null);
const scheduleOpen = computed(() => schedule.value?.status === "open");
const scheduleLabel = computed(() => {
  const value = schedule.value;
  if (!value) return "";
  if (value.status === "open") return `本期余 ${value.remaining} 席`;
  if (value.status === "upcoming") return "下期未开放";
  return "档期已满";
});

const serviceCards = computed(() => [
  {
    label: "CARE",
    title: "洗毛护理",
    body: "到店或寄回，按套装深度清洁。",
    to: "/wash",
    tone: "bg-[#eaf2ee] text-[#557565]",
  },
  {
    label: "PROPS",
    title: "道具工坊",
    body: "发光角、可动耳、尾巴支架。",
    to: "/props",
    tone: "bg-[#f0ebf2] text-[#7c6b8a]",
  },
  {
    label: "MERCH",
    title: "周边商店",
    body: "徽章、挂件、托特包，少量现货。",
    to: "/merch",
    tone: "bg-[#f8eedf] text-[#96724d]",
  },
]);

const workImages = computed(() => home.value?.featured_works.items ?? []);
const seriesCards = computed(() => home.value?.series ?? []);
const products = computed(() => home.value?.products.items ?? []);
const news = computed(() => home.value?.news.items ?? []);
const faq = computed(() => home.value?.faq ?? []);

const steps = computed(() => home.value?.commission_steps ?? []);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    home.value = await fetchHome();
    site.setScheduleSummary(home.value.schedule);
  } catch (cause) {
    const apiError = cause instanceof ApiError ? cause : null;
    error.value = { code: apiError?.code ?? "UNKNOWN", message: apiError?.message ?? "加载失败" };
  } finally {
    loading.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <!-- 首屏：大留白、柔和主视觉，先回答「做什么、现在能不能接」 -->
    <section class="relative overflow-hidden bg-canvas">
      <div
        class="container-page grid gap-12 py-12 sm:py-16 lg:grid-cols-[1.02fr_0.98fr] lg:items-center lg:gap-16 lg:py-20"
      >
        <div>
          <p class="inline-flex items-center gap-2 rounded-full bg-brand-soft px-3.5 py-1.5 text-xs text-brand-strong">
            <span class="size-1.5 rounded-full bg-brand" aria-hidden="true" />
            {{ heroTagline || "兽装定制 · 上海" }}
          </p>

          <h1 class="mt-7 font-display text-[clamp(2.5rem,5vw,4.5rem)] font-medium leading-[1.08] tracking-[-0.04em]">
            {{ t("homePage.heroLine1") }}<br />
            <span class="text-brand-strong">{{ t("homePage.heroLine2") }}</span>
          </h1>

          <p class="mt-6 max-w-xl text-base leading-relaxed text-ink-muted sm:text-lg">
            {{ truncate(heroStatement || "全装、半装、单头与配件，按设定逐件打版。先确认方案，再排期制作。", 92) }}
          </p>

          <div class="mt-8 flex flex-wrap gap-3">
            <AppButton :to="heroPrimary.to" variant="primary" size="lg">
              {{ heroPrimary.label }}
            </AppButton>
            <AppButton :to="heroSecondary.to" variant="secondary" size="lg">
              {{ heroSecondary.label }}
            </AppButton>
          </div>

          <div class="mt-8 flex flex-wrap items-center gap-x-5 gap-y-3 text-xs text-ink-muted">
            <RouterLink
              to="/commission/schedules"
              class="inline-flex items-center gap-2 transition-colors hover:text-ink"
            >
              <span
                class="size-1.5 rounded-full"
                :class="scheduleOpen ? 'bg-jade' : 'bg-ink-faint'"
                aria-hidden="true"
              />
              {{ scheduleLabel || "查看档期" }}
              <span aria-hidden="true">→</span>
            </RouterLink>
            <span class="hidden h-3 w-px bg-line-strong sm:block" aria-hidden="true" />
            <span>手工打版</span>
            <span>可寄送</span>
            <span>星绒币回馈</span>
          </div>
        </div>

        <div class="relative mx-auto w-full max-w-[30rem] lg:max-w-none">
          <div class="relative overflow-hidden rounded-[2.5rem] bg-[#f1e4d7] p-6 shadow-lift sm:p-10">
            <div class="flex items-center justify-between text-[10px] uppercase tracking-[0.2em] text-[#7D7369]">
              <span>STARIUM / SHANGHAI</span>
              <span>NO. 001</span>
            </div>

            <div class="mt-8 flex justify-center sm:mt-10">
              <BrandMark :size="200" decorative />
            </div>

            <div class="mt-7 border-t border-[#2E2923]/10 pt-5">
              <p class="font-display text-2xl font-medium leading-snug tracking-tight text-[#2E2923] sm:text-[1.7rem]">
                一针一线，装配成纪
              </p>
              <p class="mt-2 text-sm text-[#57504A]">从设定到可以穿的角色</p>
            </div>
          </div>

          <div class="absolute -bottom-5 right-4 w-48 rounded-2xl border border-line bg-surface/95 p-4 shadow-lift backdrop-blur sm:right-6">
            <p class="text-[10px] uppercase tracking-[0.18em] text-ink-faint">档期</p>
            <div class="mt-2">
              <StatusBadge
                :tone="scheduleOpen ? 'jade' : 'amber'"
                :label="scheduleLabel || '查看档期'"
                icon="●"
              />
            </div>
            <p v-if="schedule" class="mt-2 text-[11px] leading-relaxed text-ink-muted">
              {{ schedule.title }} · 截止 {{ formatDate(schedule.end_at) }}
            </p>
            <RouterLink to="/commission/schedules" class="mt-2 inline-block text-[11px] text-brand-strong hover:underline">
              查看详情
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <!-- 一句安静的信任条，不做跑马灯 -->
    <div class="border-y border-line bg-surface/60">
      <div class="container-page flex flex-wrap items-center justify-between gap-x-8 gap-y-2 py-4 text-[11px] tracking-[0.04em] text-ink-muted">
        <span>上海 · 手工兽装工作室</span>
        <span>全装 / 半装 / 单头 / 配件</span>
        <span>先确认方案，再排期制作</span>
        <span>全国寄送 · 星绒币回馈</span>
      </div>
    </div>

    <StarLoader v-if="loading" message="正在加载首页" class="container-page py-24" />

    <div v-else-if="error" class="container-page py-20">
      <EmptyState tone="error" title="首页暂时加载失败" :description="error.message">
        <AppButton variant="secondary" @click="load">重新加载</AppButton>
      </EmptyState>
    </div>

    <template v-else-if="home">
      <!-- 三条业务线 -->
      <section class="container-page py-16 sm:py-24">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <SectionHeading eyebrow="Services" title="做装，也做养护" description="三条线独立下单，共用同一套售后。" />
          <RouterLink to="/shop" class="w-fit text-sm text-brand-strong hover:underline">全部商品 →</RouterLink>
        </div>

        <div class="mt-10 grid gap-4 md:grid-cols-3">
          <RouterLink
            v-for="card in serviceCards"
            :key="card.to"
            :to="card.to"
            class="group rounded-2xl border border-line bg-surface p-7 shadow-soft transition-all duration-300 hover:-translate-y-1 hover:shadow-lift"
          >
            <span class="inline-flex rounded-full px-3 py-1 text-[10px] tracking-[0.16em]" :class="card.tone">
              {{ card.label }}
            </span>
            <h3 class="mt-6 font-display text-xl font-medium">{{ card.title }}</h3>
            <p class="mt-2 max-w-[22ch] text-sm leading-relaxed text-ink-muted">{{ card.body }}</p>
            <span class="mt-7 inline-flex items-center gap-1 text-sm text-brand-strong">
              去看看 <span aria-hidden="true" class="transition-transform group-hover:translate-x-1">→</span>
            </span>
          </RouterLink>
        </div>
      </section>

      <!-- 代表作品 -->
      <section v-if="workImages.length" class="border-y border-line bg-[#f6f0e8]/70 py-16 sm:py-24">
        <div class="container-page">
          <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <SectionHeading eyebrow="Works" title="最近完成" description="每一套都从一张设定图开始。" />
            <AppButton to="/works" variant="ghost" size="sm">全部作品 →</AppButton>
          </div>

          <div class="mt-10 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <RouterLink
              v-for="work in workImages.slice(0, 3)"
              :key="work.id"
              :to="work.route"
              class="group overflow-hidden rounded-2xl bg-surface shadow-soft transition-all duration-300 hover:-translate-y-1 hover:shadow-lift"
            >
              <div class="aspect-[4/3] overflow-hidden bg-[#f2e5d8]">
                <img
                  v-if="work.cover"
                  :src="work.cover.src"
                  :alt="work.cover.alt"
                  :width="work.cover.width"
                  :height="work.cover.height"
                  loading="lazy"
                  class="size-full object-cover transition-transform duration-700 group-hover:scale-[1.03]"
                />
                <div v-else class="flex size-full items-center justify-center text-3xl text-ink-faint" aria-hidden="true">✦</div>
              </div>
              <div class="p-5">
                <p class="text-[10px] uppercase tracking-[0.14em] text-ink-faint">{{ work.species }} · {{ work.work_type }}</p>
                <h3 class="mt-1.5 font-display text-lg font-medium">{{ displayTitle(work.title) }}</h3>
                <p class="mt-1 text-sm text-ink-muted">{{ truncate(work.summary, 42) }}</p>
              </div>
            </RouterLink>
          </div>
        </div>
      </section>

      <!-- 系列 -->
      <section v-if="seriesCards.length" class="container-page py-16 sm:py-24">
        <SectionHeading eyebrow="Series" title="选一个起点" description="价格与包含项写清楚，不玩配置迷宫。" />
        <div class="mt-10 grid gap-4 md:grid-cols-2">
          <SurfaceCard v-for="item in seriesCards" :key="item.id" class="p-6 sm:p-7">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h3 class="font-display text-xl font-medium tracking-tight">{{ item.title }}</h3>
                <p class="mt-1 text-sm text-ink-muted">{{ item.summary }}</p>
              </div>
              <p class="shrink-0 text-right">
                <span class="block text-[10px] uppercase tracking-wide text-ink-faint">起价</span>
                <span class="font-display text-lg font-medium">{{ formatPriceFrom(item.base_price_cents, item.currency) }}</span>
              </p>
            </div>
            <dl class="mt-6 grid gap-2 text-sm sm:grid-cols-2">
              <div class="rounded-xl bg-[#eef4ef] p-3.5">
                <dt class="text-[10px] uppercase tracking-wide text-[#557565]">包含</dt>
                <dd class="mt-1 text-ink-soft">{{ item.price_includes }}</dd>
              </div>
              <div class="rounded-xl bg-[#faf0ed] p-3.5">
                <dt class="text-[10px] uppercase tracking-wide text-[#96615e]">不含</dt>
                <dd class="mt-1 text-ink-soft">{{ item.price_excludes }}</dd>
              </div>
            </dl>
            <div class="mt-6 flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p class="text-[11px] text-ink-faint">交期 / {{ item.lead_time_note }}</p>
              <AppButton :to="item.route" variant="secondary" size="sm">查看规格</AppButton>
            </div>
          </SurfaceCard>
        </div>
      </section>

      <!-- 流程 -->
      <section v-if="steps.length" class="container-page pb-4 sm:pb-8">
        <div class="rounded-[2.5rem] bg-[#f1e4d7] px-6 py-12 sm:px-10 sm:py-14">
          <p class="text-[11px] uppercase tracking-[0.18em] text-brand-strong">Process</p>
          <h2 class="mt-3 font-display text-3xl font-medium tracking-[-0.03em] sm:text-4xl">四步开始</h2>
          <p class="mt-2 text-sm text-ink-muted">先谈方案，再谈排期。</p>
          <ol class="mt-9 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <li v-for="(step, index) in steps" :key="step.step">
              <span class="font-display text-3xl font-light text-brand/50">{{ String(index + 1).padStart(2, "0") }}</span>
              <h3 class="mt-3 font-display text-lg font-medium">{{ resolveText(step.title, locale) }}</h3>
              <p class="mt-1.5 text-sm leading-relaxed text-ink-muted">{{ resolveText(step.description, locale) }}</p>
            </li>
          </ol>
          <div class="mt-9 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-ink/10 pt-5 text-xs text-ink-muted">
            <span>报价有效期 {{ site.commissionPolicy.quote_valid_days }} 天</span>
            <span class="h-3 w-px bg-ink/15" aria-hidden="true" />
            <span>定金 {{ site.commissionPolicy.prepayment_percent }}%</span>
            <span class="h-3 w-px bg-ink/15" aria-hidden="true" />
            <span>尾款 {{ site.commissionPolicy.balance_due_days }} 天内</span>
            <span class="h-3 w-px bg-ink/15" aria-hidden="true" />
            <span>免费修改 {{ site.commissionPolicy.free_revision_count }} 次</span>
          </div>
        </div>
      </section>

      <!-- 商城 -->
      <section v-if="products.length" class="container-page py-16 sm:py-24">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <SectionHeading eyebrow="Shop" title="顺手带走" description="现货、道具、周边与洗毛套餐。" />
          <AppButton to="/shop" variant="ghost" size="sm">逛商城 →</AppButton>
        </div>
        <div class="mt-10 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <RouterLink
            v-for="product in products.slice(0, 4)"
            :key="product.id"
            :to="`/shop/${product.slug}`"
            class="group overflow-hidden rounded-2xl bg-surface shadow-soft transition-all duration-300 hover:-translate-y-1 hover:shadow-lift"
          >
            <div class="aspect-square overflow-hidden bg-[#f2e5d8]">
              <img
                v-if="product.images[0]"
                :src="product.images[0].src"
                :alt="product.images[0].alt"
                :width="product.images[0].width"
                :height="product.images[0].height"
                loading="lazy"
                class="size-full object-cover transition-transform duration-700 group-hover:scale-[1.03]"
              />
              <div v-else class="flex size-full items-center justify-center text-3xl text-ink-faint" aria-hidden="true">✦</div>
            </div>
            <div class="p-4">
              <p class="text-[10px] uppercase tracking-wide text-ink-faint">{{ product.subtitle }}</p>
              <h3 class="mt-1 line-clamp-1 font-display text-[15px] font-medium">{{ product.title }}</h3>
              <p class="mt-2 font-display text-base font-medium text-brand-strong">
                {{ formatMoney(product.price_from_cents, product.currency === "USD" ? "USD" : "CNY") }}
              </p>
            </div>
          </RouterLink>
        </div>
      </section>

      <!-- 公告 + FAQ -->
      <section class="border-y border-line bg-[#f8f3ec] py-16 sm:py-24">
        <div class="container-page grid gap-12 lg:grid-cols-2">
          <div v-if="news.length">
            <div class="flex items-end justify-between">
              <SectionHeading eyebrow="News" title="工作室公告" />
              <RouterLink to="/news" class="text-sm text-brand-strong hover:underline">全部 →</RouterLink>
            </div>
            <ul class="mt-7 divide-y divide-line">
              <li v-for="item in news.slice(0, 3)" :key="item.id">
                <RouterLink :to="item.route" class="block py-4 transition-colors hover:text-brand-strong">
                  <p class="text-[11px] text-ink-faint">{{ formatDate(item.published_at) }}</p>
                  <p class="mt-1 font-medium">{{ item.title }}</p>
                  <p class="mt-1 text-sm text-ink-muted">{{ truncate(item.summary, 56) }}</p>
                </RouterLink>
              </li>
            </ul>
          </div>

          <div v-if="faq.length">
            <div class="flex items-end justify-between">
              <SectionHeading eyebrow="FAQ" title="先看这几条" />
              <RouterLink to="/faq" class="text-sm text-brand-strong hover:underline">全部 →</RouterLink>
            </div>
            <div class="mt-7 divide-y divide-line">
              <details v-for="item in faq.slice(0, 4)" :key="item.id" class="group py-3">
                <summary class="cursor-pointer list-none font-medium marker:hidden">
                  <span class="flex items-center justify-between gap-3">
                    {{ item.title }}
                    <span class="text-brand-strong transition-transform group-open:rotate-45" aria-hidden="true">+</span>
                  </span>
                </summary>
                <div class="prose-quiet mt-2 text-sm" v-html="item.body_html" />
              </details>
            </div>
          </div>
        </div>
      </section>

      <!-- 联系 -->
      <section class="container-page py-16 sm:py-24">
        <div class="rounded-[2.5rem] bg-[#f1e4d7] px-6 py-10 sm:px-10 sm:py-14">
          <div class="grid gap-10 lg:grid-cols-[1.3fr_1fr]">
            <div>
              <p class="text-[11px] uppercase tracking-[0.2em] text-brand-strong">Contact</p>
              <h2 class="mt-3 font-display text-4xl font-medium tracking-[-0.04em] sm:text-5xl">
                有想法，<br />直接说。
              </h2>
              <p class="mt-5 max-w-xl text-sm leading-relaxed text-ink-muted">
                询价 3 个工作日内回复，反馈 7 个工作日内回复。工作时段 {{ site.brand.service_hours }}。
              </p>
              <div class="mt-7 flex flex-wrap gap-3">
                <AppButton to="/commission/new" variant="primary">提交询价</AppButton>
                <AppButton to="/contact" variant="secondary">联系方式</AppButton>
              </div>
            </div>
            <dl class="grid content-start gap-3 text-sm">
              <div class="rounded-xl bg-surface/80 px-4 py-3.5">
                <dt class="text-[10px] uppercase tracking-wide text-ink-faint">邮箱</dt>
                <dd class="mt-1">
                  <a class="underline decoration-line-strong underline-offset-4 hover:text-brand-strong" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
                </dd>
              </div>
              <div class="rounded-xl bg-surface/80 px-4 py-3.5">
                <dt class="text-[10px] uppercase tracking-wide text-ink-faint">经营主体</dt>
                <dd class="mt-1 text-ink-soft">{{ site.brand.legal_entity }}</dd>
              </div>
              <div class="rounded-xl bg-surface/80 px-4 py-3.5">
                <dt class="text-[10px] uppercase tracking-wide text-ink-faint">积分</dt>
                <dd class="mt-1 text-ink-soft">每 {{ site.loyaltyRules.denominator_yuan }} 元得 1 星绒币</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.prose-quiet :deep(p) {
  margin: 0.35rem 0;
  color: var(--color-ink-muted);
  line-height: 1.7;
}
</style>
