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
    index: "01",
    eyebrow: "WASH",
    title: "洗毛护理",
    body: "到店或寄回，按套装深度清洁。",
    to: "/wash",
    tone: "bg-[#DFF3EC]",
    chip: "text-[#006B59]",
  },
  {
    index: "02",
    eyebrow: "PROPS",
    title: "道具工坊",
    body: "发光角、可动耳、尾巴支架。",
    to: "/props",
    tone: "bg-[#EDE7FF]",
    chip: "text-[#4A2ED0]",
  },
  {
    index: "03",
    eyebrow: "MERCH",
    title: "周边商店",
    body: "徽章、挂件、托特包，少量现货。",
    to: "/merch",
    tone: "bg-[#FFF1C9]",
    chip: "text-[#8A5A00]",
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
    <!-- 首屏：大字、硬边、现在能不能接 -->
    <section class="relative overflow-hidden border-b-2 border-ink bg-canvas bg-dots">
      <div class="container-page relative grid gap-10 py-10 lg:grid-cols-[1.12fr_0.88fr] lg:items-center lg:gap-14 lg:py-16">
        <div>
          <p class="inline-flex items-center gap-2 border-2 border-ink bg-surface px-3 py-1.5 font-mono text-[10px] uppercase tracking-[0.16em] shadow-[3px_3px_0_var(--color-ink)]">
            <span class="size-2 bg-brand" aria-hidden="true" />
            {{ heroTagline || "兽装定制 · 上海" }}
          </p>

          <h1
            class="mt-6 font-display text-[clamp(2.75rem,5.6vw,5rem)] font-bold leading-[0.94] tracking-[-0.055em]"
          >
            {{ t("homePage.heroLine1") }}<br />
            <span class="text-brand-strong">{{ t("homePage.heroLine2") }}</span>
          </h1>

          <p class="mt-5 max-w-lg text-base leading-relaxed text-ink-soft sm:text-lg">
            {{ truncate(heroStatement || "全装、半装、单头与配件，按设定逐件打版。先确认方案，再排期制作。", 92) }}
          </p>

          <div class="mt-7 flex flex-wrap gap-3">
            <AppButton :to="heroPrimary.to" variant="primary" size="lg">
              {{ heroPrimary.label }}
            </AppButton>
            <AppButton :to="heroSecondary.to" variant="secondary" size="lg">
              {{ heroSecondary.label }}
            </AppButton>
          </div>

          <div class="mt-7 flex flex-wrap items-center gap-3">
            <RouterLink
              to="/commission/schedules"
              class="inline-flex items-center gap-2 border-2 border-ink bg-surface px-3.5 py-2 text-sm font-bold shadow-[3px_3px_0_var(--color-ink)]"
            >
              <span
                class="size-2.5 rounded-full border border-ink"
                :class="scheduleOpen ? 'bg-jade' : 'bg-lemon'"
                aria-hidden="true"
              />
              {{ scheduleLabel || "查看档期" }}
              <span aria-hidden="true">↗</span>
            </RouterLink>
            <span class="font-mono text-[11px] uppercase tracking-[0.14em] text-ink-muted">
              手工打版 / 可寄送 / 星绒币回馈
            </span>
          </div>
        </div>

        <div class="relative mx-auto w-full max-w-md lg:max-w-none">
          <div
            class="relative rotate-[1deg] border-2 border-ink bg-lemon p-6 text-[#17121C] shadow-[10px_10px_0_var(--color-ink)] sm:p-8"
          >
            <div class="flex items-start justify-between font-mono text-[10px] uppercase tracking-[0.16em]">
              <span class="border-2 border-ink bg-surface px-2 py-1">STARIUM / SH</span>
              <span>NO. 001</span>
            </div>
            <div class="mt-8 flex justify-center">
              <BrandMark :size="210" decorative />
            </div>
            <p class="mt-5 font-display text-2xl font-bold leading-tight tracking-[-0.03em] sm:text-3xl">
              一针一线，<br />装配成纪
            </p>
            <p class="mt-2 text-sm text-ink-soft">从设定到可以穿的角色</p>
            <div class="mt-5 flex items-center justify-between border-t-2 border-ink/20 pt-3 font-mono text-[10px] uppercase tracking-[0.14em]">
              <span>上海 · 中国</span>
              <span>FURSUIT STUDIO</span>
            </div>
          </div>

          <div class="sticker-card absolute -right-2 -top-6 w-44 rotate-3 p-4 sm:-right-6">
            <p class="font-mono text-[10px] uppercase tracking-[0.16em] text-ink-muted">档期</p>
            <div class="mt-2 flex items-center gap-2">
              <StatusBadge
                :tone="scheduleOpen ? 'jade' : 'amber'"
                :label="scheduleLabel || '查看档期'"
                icon="●"
              />
            </div>
            <p v-if="schedule" class="mt-2 text-xs leading-relaxed text-ink-muted">
              {{ schedule.title }} · 截止 {{ formatDate(schedule.end_at) }}
            </p>
            <RouterLink to="/commission/schedules" class="mt-2 inline-block text-xs font-bold text-brand-strong link-underline">
              档期详情
            </RouterLink>
          </div>

          <div class="sticker-card absolute -bottom-6 -left-2 w-52 -rotate-2 bg-canvas p-4 text-[#17121C] sm:-left-6">
            <p class="font-mono text-[10px] uppercase tracking-[0.16em] text-[#6B5D74]">不止兽装</p>
            <div class="mt-2 flex flex-wrap gap-1.5 text-xs font-bold">
              <RouterLink to="/wash" class="border-2 border-ink bg-[#DFF3EC] px-2.5 py-1 text-[#17121C] hover:bg-jade hover:text-white">洗毛</RouterLink>
              <RouterLink to="/props" class="border-2 border-ink bg-[#EDE7FF] px-2.5 py-1 text-[#17121C] hover:bg-grape hover:text-white">道具</RouterLink>
              <RouterLink to="/merch" class="border-2 border-ink bg-[#FFF1C9] px-2.5 py-1 text-[#17121C] hover:bg-lemon">周边</RouterLink>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 业务跑马灯 -->
    <div class="overflow-hidden border-b-2 border-ink bg-ink py-2.5" aria-hidden="true">
      <div
        class="animate-marquee flex w-max gap-8 whitespace-nowrap font-mono text-sm font-bold uppercase tracking-[0.18em] text-lemon"
      >
        <span v-for="index in 2" :key="index" class="flex gap-8">
          <span v-for="word in ['全装', '半装', '单头', '洗毛', '道具', '周边', '上海', '接单中']" :key="`${index}-${word}`">
            {{ word }} <span class="text-brand">✦</span>
          </span>
        </span>
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
      <section class="container-page py-14 sm:py-20">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <SectionHeading eyebrow="01 / SERVICES" title="做装，也做养护" description="三条线独立下单，共用同一套售后。" />
          <RouterLink to="/shop" class="w-fit text-sm font-bold text-brand-strong link-underline">全部商品 →</RouterLink>
        </div>

        <div class="mt-8 grid gap-4 md:grid-cols-3">
          <RouterLink
            v-for="card in serviceCards"
            :key="card.to"
            :to="card.to"
            class="group relative overflow-hidden border-2 border-ink p-6 text-[#17121C] shadow-[5px_5px_0_var(--color-ink)] transition-transform hover:-translate-y-1"
            :class="card.tone"
          >
            <div class="flex items-start justify-between">
              <p class="font-mono text-[10px] font-bold uppercase tracking-[0.18em]" :class="card.chip">{{ card.eyebrow }}</p>
              <span class="font-display text-4xl font-bold leading-none tracking-tighter text-[#17121C]/20">{{ card.index }}</span>
            </div>
            <h3 class="mt-3 font-display text-2xl font-bold tracking-tight">{{ card.title }}</h3>
            <p class="mt-2 max-w-[22ch] text-sm leading-relaxed text-[#4A3D52]">{{ card.body }}</p>
            <span class="mt-6 inline-flex items-center gap-1 text-sm font-bold">
              去看看 <span aria-hidden="true" class="transition-transform group-hover:translate-x-1">→</span>
            </span>
          </RouterLink>
        </div>
      </section>

      <!-- 代表作品 -->
      <section v-if="workImages.length" class="border-y-2 border-ink bg-surface-muted py-14 sm:py-20">
        <div class="container-page">
          <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
            <SectionHeading eyebrow="02 / WORKS" title="最近完成" description="每一套都从一张设定图开始。" />
            <AppButton to="/works" variant="ghost" size="sm">全部作品 →</AppButton>
          </div>

          <div class="mt-8 grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            <RouterLink
              v-for="work in workImages.slice(0, 3)"
              :key="work.id"
              :to="work.route"
              class="group overflow-hidden border-2 border-ink bg-surface shadow-[4px_4px_0_var(--color-ink)] transition-transform hover:-translate-y-1"
            >
              <div class="aspect-[4/3] overflow-hidden border-b-2 border-ink bg-[#FFE3DB]">
                <img
                  v-if="work.cover"
                  :src="work.cover.src"
                  :alt="work.cover.alt"
                  :width="work.cover.width"
                  :height="work.cover.height"
                  loading="lazy"
                  class="size-full object-cover transition-transform duration-500 group-hover:scale-105"
                />
                <div v-else class="flex size-full items-center justify-center text-3xl text-[#6B5D74]" aria-hidden="true">✦</div>
              </div>
              <div class="p-5">
                <p class="font-mono text-[10px] font-bold uppercase tracking-[0.14em] text-brand-strong">{{ work.species }} · {{ work.work_type }}</p>
                <h3 class="mt-1 font-display text-xl font-bold">{{ displayTitle(work.title) }}</h3>
                <p class="mt-1 text-sm text-ink-muted">{{ truncate(work.summary, 42) }}</p>
              </div>
            </RouterLink>
          </div>
        </div>
      </section>

      <!-- 系列 -->
      <section v-if="seriesCards.length" class="container-page py-14 sm:py-20">
        <SectionHeading eyebrow="03 / SERIES" title="选一个起点" description="价格与包含项写清楚，不玩配置迷宫。" />
        <div class="mt-8 grid gap-4 md:grid-cols-2">
          <SurfaceCard v-for="item in seriesCards" :key="item.id" class="border-2 border-ink p-6">
            <div class="flex items-start justify-between gap-4">
              <div>
                <h3 class="font-display text-2xl font-bold tracking-tight">{{ item.title }}</h3>
                <p class="mt-1 text-sm text-ink-muted">{{ item.summary }}</p>
              </div>
              <p class="shrink-0 text-right">
                <span class="block font-mono text-[10px] uppercase tracking-wide text-ink-muted">起价</span>
                <span class="font-display text-xl font-bold">{{ formatPriceFrom(item.base_price_cents, item.currency) }}</span>
              </p>
            </div>
            <dl class="mt-5 grid gap-2 text-sm sm:grid-cols-2">
              <div class="border-2 border-ink/15 bg-[#DFF3EC] p-3 text-[#17121C]">
                <dt class="font-mono text-[10px] font-bold uppercase tracking-wide text-[#006B59]">包含</dt>
                <dd class="mt-1 text-[#4A3D52]">{{ item.price_includes }}</dd>
              </div>
              <div class="border-2 border-ink/15 bg-[#FFE0E9] p-3 text-[#17121C]">
                <dt class="font-mono text-[10px] font-bold uppercase tracking-wide text-[#A31C3F]">不含</dt>
                <dd class="mt-1 text-[#4A3D52]">{{ item.price_excludes }}</dd>
              </div>
            </dl>
            <div class="mt-5 flex flex-wrap items-center justify-between gap-3 border-t-2 border-ink/10 pt-4">
              <p class="font-mono text-[11px] text-ink-muted">交期 / {{ item.lead_time_note }}</p>
              <AppButton :to="item.route" variant="secondary" size="sm">查看规格</AppButton>
            </div>
          </SurfaceCard>
        </div>
      </section>

      <!-- 流程 -->
      <section v-if="steps.length" class="border-y-2 border-ink bg-[#17121C] py-14 text-[#FFFCF2] sm:py-20">
        <div class="container-page">
          <p class="font-mono text-[11px] uppercase tracking-[0.18em] text-lemon">04 / PROCESS</p>
          <h2 class="mt-2 font-display text-3xl font-bold tracking-[-0.03em] sm:text-5xl">四步开始</h2>
          <p class="mt-2 text-sm text-white/60">先谈方案，再谈排期。</p>
          <ol class="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <li v-for="(step, index) in steps" :key="step.step" class="border-2 border-white/25 p-5">
              <span class="font-display text-4xl font-bold leading-none text-lemon">{{ String(index + 1).padStart(2, "0") }}</span>
              <h3 class="mt-4 font-display text-lg font-bold">{{ resolveText(step.title, locale) }}</h3>
              <p class="mt-1 text-sm leading-relaxed text-white/60">{{ resolveText(step.description, locale) }}</p>
            </li>
          </ol>
          <div class="mt-6 flex flex-wrap items-center gap-3 border-2 border-white/25 bg-white/5 px-5 py-4 text-sm">
            <span class="font-mono text-[11px] font-bold uppercase tracking-wide text-lemon">报价有效期 {{ site.commissionPolicy.quote_valid_days }} 天</span>
            <span class="text-white/30" aria-hidden="true">/</span>
            <span class="text-white/70">定金 {{ site.commissionPolicy.prepayment_percent }}%</span>
            <span class="text-white/30" aria-hidden="true">/</span>
            <span class="text-white/70">尾款 {{ site.commissionPolicy.balance_due_days }} 天内</span>
            <span class="text-white/30" aria-hidden="true">/</span>
            <span class="text-white/70">免费修改 {{ site.commissionPolicy.free_revision_count }} 次</span>
          </div>
        </div>
      </section>

      <!-- 商城 -->
      <section v-if="products.length" class="container-page py-14 sm:py-20">
        <div class="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
          <SectionHeading eyebrow="05 / SHOP" title="顺手带走" description="现货、道具、周边与洗毛套餐。" />
          <AppButton to="/shop" variant="ghost" size="sm">逛商城 →</AppButton>
        </div>
        <div class="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <RouterLink
            v-for="product in products.slice(0, 4)"
            :key="product.id"
            :to="`/shop/${product.slug}`"
            class="group overflow-hidden border-2 border-ink bg-surface shadow-[4px_4px_0_var(--color-ink)] transition-transform hover:-translate-y-1"
          >
            <div class="aspect-square overflow-hidden border-b-2 border-ink bg-[#FFE3DB]">
              <img
                v-if="product.images[0]"
                :src="product.images[0].src"
                :alt="product.images[0].alt"
                :width="product.images[0].width"
                :height="product.images[0].height"
                loading="lazy"
                class="size-full object-cover transition-transform duration-500 group-hover:scale-105"
              />
              <div v-else class="flex size-full items-center justify-center text-3xl text-[#6B5D74]" aria-hidden="true">✦</div>
            </div>
            <div class="p-4">
              <p class="font-mono text-[10px] uppercase tracking-wide text-ink-muted">{{ product.subtitle }}</p>
              <h3 class="mt-1 line-clamp-1 font-display text-base font-bold">{{ product.title }}</h3>
              <p class="mt-2 font-display text-lg font-bold text-brand-strong">
                {{ formatMoney(product.price_from_cents, product.currency === "USD" ? "USD" : "CNY") }}
              </p>
            </div>
          </RouterLink>
        </div>
      </section>

      <!-- 公告 + FAQ -->
      <section class="border-t-2 border-ink bg-surface-muted py-14 sm:py-20">
        <div class="container-page grid gap-8 lg:grid-cols-2">
          <div v-if="news.length">
            <div class="flex items-end justify-between">
              <SectionHeading eyebrow="06 / NEWS" title="工作室公告" />
              <RouterLink to="/news" class="text-sm font-bold text-brand-strong link-underline">全部 →</RouterLink>
            </div>
            <ul class="mt-6 space-y-3">
              <li v-for="item in news.slice(0, 3)" :key="item.id">
                <RouterLink :to="item.route" class="block border-2 border-ink bg-surface p-4 transition-transform hover:-translate-y-0.5">
                  <p class="font-mono text-[10px] uppercase tracking-wide text-ink-faint">{{ formatDate(item.published_at) }}</p>
                  <p class="mt-1 font-bold">{{ item.title }}</p>
                  <p class="mt-1 text-sm text-ink-muted">{{ truncate(item.summary, 56) }}</p>
                </RouterLink>
              </li>
            </ul>
          </div>

          <div v-if="faq.length">
            <div class="flex items-end justify-between">
              <SectionHeading eyebrow="07 / FAQ" title="先看这几条" />
              <RouterLink to="/faq" class="text-sm font-bold text-brand-strong link-underline">全部 →</RouterLink>
            </div>
            <div class="mt-6 space-y-2">
              <details v-for="item in faq.slice(0, 4)" :key="item.id" class="group border-2 border-ink bg-surface p-4">
                <summary class="cursor-pointer list-none font-bold marker:hidden">
                  <span class="flex items-center justify-between gap-3">
                    {{ item.title }}
                    <span class="font-mono text-brand-strong transition-transform group-open:rotate-45" aria-hidden="true">+</span>
                  </span>
                </summary>
                <div class="prose-quiet mt-2 text-sm" v-html="item.body_html" />
              </details>
            </div>
          </div>
        </div>
      </section>

      <!-- 联系 -->
      <section class="container-page py-14 sm:py-20">
        <div class="relative overflow-hidden border-2 border-ink bg-lemon px-6 py-10 text-[#17121C] shadow-[8px_8px_0_var(--color-ink)] sm:px-10 sm:py-12">
          <div class="relative grid gap-8 lg:grid-cols-[1.3fr_1fr]">
            <div>
              <p class="font-mono text-[11px] font-bold uppercase tracking-[0.2em] text-[#17121C]/60">08 / CONTACT</p>
              <h2 class="mt-3 font-display text-4xl font-bold tracking-[-0.04em] sm:text-6xl">有想法，<br />直接说。</h2>
              <p class="mt-4 max-w-xl text-sm leading-relaxed text-[#4A3D52]">
                询价 3 个工作日内回复，反馈 7 个工作日内回复。工作时段 {{ site.brand.service_hours }}。
              </p>
              <div class="mt-6 flex flex-wrap gap-3">
                <AppButton to="/commission/new" variant="primary">提交询价</AppButton>
                <AppButton to="/contact" variant="secondary">联系方式</AppButton>
              </div>
            </div>
            <dl class="grid content-start gap-3 text-sm">
              <div class="border-2 border-ink bg-surface px-4 py-3 text-[#17121C]">
                <dt class="font-mono text-[10px] uppercase tracking-wide text-[#6B5D74]">邮箱</dt>
                <dd class="mt-1">
                  <a class="font-bold underline decoration-ink/30 underline-offset-4" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
                </dd>
              </div>
              <div class="border-2 border-ink bg-surface px-4 py-3 text-[#17121C]">
                <dt class="font-mono text-[10px] uppercase tracking-wide text-[#6B5D74]">经营主体</dt>
                <dd class="mt-1">{{ site.brand.legal_entity }}</dd>
              </div>
              <div class="border-2 border-ink bg-surface px-4 py-3 text-[#17121C]">
                <dt class="font-mono text-[10px] uppercase tracking-wide text-[#6B5D74]">积分</dt>
                <dd class="mt-1">每 {{ site.loyaltyRules.denominator_yuan }} 元得 1 星绒币</dd>
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
  line-height: 1.65;
}
</style>
