<script setup lang="ts">
/**
 * 首页：一张大轮播，一段工作室说明，一排最近作品。
 * 价格与档期都来自服务端；估价入口无需登录。
 */

import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { fetchHome, type HomeAggregate } from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { displayTitle } from "@/shared/lib/demo-title";
import { useI18n, resolveText } from "@/shared/lib/i18n";
import { BrandMark } from "@/shared/ui/BrandMark";
import { AppButton } from "@/shared/ui/base";
import { formatDate, truncate } from "@/shared/lib/format";
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

const schedule = computed(() => home.value?.schedule ?? null);
const scheduleOpen = computed(() => schedule.value?.status === "open");
const scheduleLabel = computed(() => {
  const value = schedule.value;
  if (!value) return "查看档期";
  if (value.status === "open") return `本期余 ${value.remaining} 席`;
  if (value.status === "upcoming") return "下期未开放";
  return "档期已满";
});

const works = computed(() => home.value?.featured_works.items ?? []);
const coverWork = computed(() => works.value.find((item) => item.cover) ?? null);

const slides = computed(() => [
  {
    key: "studio",
    label: "01 / STUDIO",
    caption: "上海 · 手工兽装工作室",
    tone: "bg-[#f1ede6]",
    image: "",
  },
  {
    key: "works",
    label: "02 / WORKS",
    caption: coverWork.value ? displayTitle(coverWork.value.title) : "每一套都从设定图开始",
    tone: "bg-[#eaf0ec]",
    image: coverWork.value?.cover?.src ?? "",
  },
  {
    key: "care",
    label: "03 / CARE & SHOP",
    caption: "保养服务、道具与商城",
    tone: "bg-[#f5eee4]",
    image: "",
  },
]);

const active = ref(0);
let timer: number | undefined;
let paused = false;
const reducedMotion = ref(false);

function go(index: number): void {
  const total = slides.value.length;
  active.value = (index + total) % total;
  restart();
}

function next(): void {
  go(active.value + 1);
}

function previous(): void {
  go(active.value - 1);
}

function stop(): void {
  if (timer) window.clearInterval(timer);
  timer = undefined;
}

function restart(): void {
  stop();
  if (paused || reducedMotion.value || slides.value.length < 2) return;
  timer = window.setInterval(() => {
    active.value = (active.value + 1) % slides.value.length;
  }, 6000);
}

function pause(): void {
  paused = true;
  stop();
}

function resume(): void {
  paused = false;
  restart();
}

function onVisibility(): void {
  if (document.hidden) stop();
  else restart();
}

async function load(): Promise<void> {
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

onMounted(() => {
  reducedMotion.value = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  document.addEventListener("visibilitychange", onVisibility);
  restart();
  void load();
});

onBeforeUnmount(() => {
  stop();
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<template>
  <div>
    <section class="pb-6 pt-6 sm:pb-10 sm:pt-10" aria-label="工作室主视觉">
      <div class="container-page">
        <div
          class="group relative min-h-[500px] overflow-hidden rounded-[2rem] bg-canvas-sunk sm:min-h-[620px]"
          aria-roledescription="carousel"
          aria-label="工作室视觉轮播"
          @mouseenter="pause"
          @mouseleave="resume"
          @focusin="pause"
          @focusout="resume"
        >
          <div
            v-for="(slide, index) in slides"
            :key="slide.key"
            class="absolute inset-0 transition-opacity duration-700"
            :class="[slide.tone, index === active ? 'opacity-100' : 'opacity-0']"
            :aria-hidden="index === active ? 'false' : 'true'"
          >
            <img
              v-if="slide.image"
              :src="slide.image"
              alt=""
              class="size-full object-cover"
              loading="eager"
            />
            <div v-if="index === 0" class="absolute inset-y-0 right-[6%] hidden items-center lg:flex">
              <BrandMark :size="260" decorative class="opacity-95" />
            </div>
            <div v-if="index === 0" class="pointer-events-none absolute -right-8 top-16 lg:hidden">
              <BrandMark :size="188" decorative class="opacity-[0.07]" />
            </div>
            <div v-else class="absolute inset-y-0 right-[3%] hidden items-center lg:flex">
              <p
                class="select-none text-right font-display text-[clamp(4rem,10vw,8rem)] font-medium leading-none tracking-[-0.06em] text-ink/10"
                aria-hidden="true"
              >
                CARE<br />PROPS
              </p>
            </div>
            <div
              v-if="slide.image"
              class="absolute inset-0 bg-gradient-to-r from-white/90 via-white/55 to-white/10"
              aria-hidden="true"
            />
            <p
              class="absolute bottom-6 left-6 text-[10px] uppercase tracking-[0.2em] text-ink-faint"
            >
              {{ slide.label }}
            </p>
          </div>

          <div class="relative z-10 flex min-h-[500px] flex-col justify-between p-6 sm:min-h-[620px] sm:p-10 lg:p-14">
            <div class="flex items-center justify-between gap-4">
              <p class="inline-flex items-center gap-2 rounded-full bg-white/70 px-3.5 py-1.5 text-xs text-ink-soft backdrop-blur">
                <span class="size-1.5 rounded-full bg-brand" aria-hidden="true" />
                {{ heroTagline || "兽装定制 · 上海" }}
              </p>
              <RouterLink
                to="/commission/schedules"
                class="hidden items-center gap-2 text-xs text-ink-soft hover:text-ink sm:inline-flex"
              >
                <span class="size-1.5 rounded-full" :class="scheduleOpen ? 'bg-jade' : 'bg-ink-faint'" aria-hidden="true" />
                {{ scheduleLabel }}
                <span aria-hidden="true">→</span>
              </RouterLink>
            </div>

            <div class="max-w-2xl pb-8 sm:pb-14">
              <h1
                class="font-display text-[clamp(2.8rem,6vw,5.4rem)] font-medium leading-[0.98] tracking-[-0.055em] text-ink"
              >
                {{ t("homePage.heroLine1") }}<br />
                <span class="text-brand-strong">{{ t("homePage.heroLine2") }}</span>
              </h1>
              <p class="mt-5 max-w-lg text-sm leading-7 text-ink-soft sm:text-base">
                {{ truncate(heroStatement || "从设定到实装，一步一步做成可以穿在身上的角色。先估价，再决定。", 70) }}
              </p>
              <div class="mt-7 flex flex-wrap gap-3">
                <AppButton to="/estimate" variant="primary" size="lg">开始估价</AppButton>
                <AppButton to="/works" variant="secondary" size="lg">看看作品</AppButton>
              </div>
              <p v-if="schedule" class="mt-5 text-xs text-ink-muted">
                {{ schedule.title }} · 截止 {{ formatDate(schedule.end_at) }}
              </p>
            </div>
          </div>

          <div class="absolute bottom-6 right-6 z-20 flex items-center gap-2">
            <button
              type="button"
              class="flex size-9 items-center justify-center rounded-full bg-white/80 text-ink-muted backdrop-blur transition-colors hover:text-ink"
              aria-label="上一张"
              @click="previous"
            >
              ←
            </button>
            <button
              type="button"
              class="flex size-9 items-center justify-center rounded-full bg-white/80 text-ink-muted backdrop-blur transition-colors hover:text-ink"
              aria-label="下一张"
              @click="next"
            >
              →
            </button>
          </div>

          <div class="absolute bottom-7 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2">
            <button
              v-for="(slide, index) in slides"
              :key="slide.key"
              type="button"
              class="size-1.5 rounded-full transition-all"
              :class="index === active ? 'w-5 bg-ink' : 'bg-ink/20'"
              :aria-label="`第 ${index + 1} 张`"
              :aria-current="index === active ? 'true' : undefined"
              @click="go(index)"
            />
          </div>
        </div>
      </div>
    </section>

    <p v-if="loading" class="sr-only">正在读取作品与系列</p>

    <section class="container-page py-12 sm:py-20">
      <div class="grid gap-10 lg:grid-cols-[1.1fr_0.9fr] lg:items-end">
        <div>
          <p class="text-[11px] uppercase tracking-[0.2em] text-brand-strong">STARIUM / SHANGHAI</p>
          <h2 class="mt-3 max-w-xl font-display text-3xl font-medium leading-tight tracking-[-0.03em] sm:text-4xl">
            做装，也做养护。
          </h2>
          <p class="mt-4 max-w-xl text-sm leading-7 text-ink-muted">
            全装、半装、单头与配件按设定逐件打版；角色汤、保养服务与商城由同一间工作室打理。
          </p>
        </div>
        <nav class="grid gap-1 text-sm" aria-label="快速入口">
          <RouterLink to="/estimate" class="flex items-center justify-between border-b border-line py-3 text-ink-soft hover:text-brand-strong">
            <span>查询价格与委托估价</span><span aria-hidden="true">→</span>
          </RouterLink>
          <RouterLink to="/characters" class="flex items-center justify-between border-b border-line py-3 text-ink-soft hover:text-brand-strong">
            <span>角色汤 · 领养一个设定</span><span aria-hidden="true">→</span>
          </RouterLink>
          <RouterLink to="/wash" class="flex items-center justify-between border-b border-line py-3 text-ink-soft hover:text-brand-strong">
            <span>保养服务 · 清洗与养护</span><span aria-hidden="true">→</span>
          </RouterLink>
          <RouterLink to="/shop" class="flex items-center justify-between border-b border-line py-3 text-ink-soft hover:text-brand-strong">
            <span>商城 · 道具与周边</span><span aria-hidden="true">→</span>
          </RouterLink>
        </nav>
      </div>
    </section>

    <section v-if="works.length" class="container-page pb-16 sm:pb-24">
      <div class="flex items-end justify-between gap-4">
        <div>
          <p class="text-[11px] uppercase tracking-[0.2em] text-ink-faint">WORKS</p>
          <h2 class="mt-2 font-display text-2xl font-medium">最近完成</h2>
        </div>
        <AppButton to="/works" variant="ghost" size="sm">全部作品 →</AppButton>
      </div>
      <RouterLink
        v-if="works.length === 1 && works[0]"
        :to="works[0].route"
        class="group mt-6 grid overflow-hidden rounded-2xl bg-surface shadow-soft transition-shadow hover:shadow-lift sm:grid-cols-2"
      >
        <div class="aspect-[16/10] overflow-hidden bg-canvas-sunk">
          <img
            v-if="works[0].cover"
            :src="works[0].cover.src"
            :alt="works[0].cover.alt"
            :width="works[0].cover.width"
            :height="works[0].cover.height"
            loading="lazy"
            class="size-full object-cover"
          />
          <p v-else class="flex size-full items-center justify-center text-sm text-ink-muted">暂无封面</p>
        </div>
        <div class="flex flex-col justify-center p-7 sm:p-10">
          <p class="text-[10px] uppercase tracking-[0.14em] text-ink-faint">
            {{ works[0].species }} · {{ works[0].work_type }}
          </p>
          <h3 class="mt-2 font-display text-2xl font-medium">{{ displayTitle(works[0].title) }}</h3>
          <p class="mt-3 text-sm leading-7 text-ink-muted">{{ truncate(works[0].summary, 80) }}</p>
          <span class="mt-6 text-sm text-brand-strong">查看作品 →</span>
        </div>
      </RouterLink>

      <div v-else class="mt-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        <RouterLink
          v-for="work in works.slice(0, 3)"
          :key="work.id"
          :to="work.route"
          class="group overflow-hidden rounded-2xl bg-surface shadow-soft transition-shadow hover:shadow-lift"
        >
          <div class="aspect-[4/3] overflow-hidden bg-canvas-sunk">
            <img
              v-if="work.cover"
              :src="work.cover.src"
              :alt="work.cover.alt"
              :width="work.cover.width"
              :height="work.cover.height"
              loading="lazy"
              class="size-full object-cover"
            />
            <p v-else class="flex size-full items-center justify-center text-sm text-ink-muted">暂无封面</p>
          </div>
          <div class="p-4">
            <p class="text-[10px] uppercase tracking-[0.14em] text-ink-faint">{{ work.species }} · {{ work.work_type }}</p>
            <h3 class="mt-1 line-clamp-1 font-display text-base font-medium">{{ displayTitle(work.title) }}</h3>
          </div>
        </RouterLink>
      </div>
    </section>

    <p v-if="!loading && !error && !works.length" class="container-page pb-16 text-sm text-ink-muted">暂无公开作品。</p>

    <div v-if="error" role="status" class="container-page pb-16">
      <div class="rounded-2xl bg-canvas-sunk px-6 py-5 text-sm text-ink-muted">
        首页动态内容暂时没有加载出来，不影响浏览其他页面。
        <button type="button" class="ml-2 text-brand-strong" @click="load">重试</button>
      </div>
    </div>
  </div>
</template>
