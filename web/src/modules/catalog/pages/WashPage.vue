<script setup lang="ts">
/**
 * 洗毛与护理（FR-025 / C31）。
 *
 * 预约走 stock_applications（kind=cleaning）：不收款、不锁库存，
 * 工作室确认毛面与产能后给出报价与购买窗口。页面状态必须覆盖
 * loading / empty / error / 未登录 / 提交中 / 成功。
 */

import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import {
  fetchProducts,
  submitStockApplication,
  type ProductSummary,
} from "@/modules/catalog/api/catalog";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useI18n, type MessageKey } from "@/shared/lib/i18n";
import { ApiError } from "@/shared/lib/http";
import { formatMoney } from "@/shared/lib/format";
import { AppButton, EmptyState, SectionHeading, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";

const { t } = useI18n();
const auth = useAuthStore();

const packages = ref<ProductSummary[]>([]);
const loading = ref(true);
const loadError = ref("");
const selectedId = ref("");
const preferredDate = ref("");
const returnMethod = ref<"pickup" | "ship">("pickup");
const note = ref("");
const submitting = ref(false);
const formError = ref("");
const success = ref("");
const dateError = ref("");

const selected = computed(() => packages.value.find((item) => item.id === selectedId.value) ?? null);

function shanghaiToday(): string {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(new Date());
}

function addDays(iso: string, days: number): string {
  const [year, month, day] = iso.split("-").map(Number) as [number, number, number];
  const value = new Date(Date.UTC(year, month - 1, day + days));
  return value.toISOString().slice(0, 10);
}

const minDate = computed(() => addDays(shanghaiToday(), 2));
const maxDate = computed(() => addDays(shanghaiToday(), 60));

const faqs = computed(() => [
  { q: t("washPage.faq1Q"), a: t("washPage.faq1A") },
  { q: t("washPage.faq2Q"), a: t("washPage.faq2A") },
  { q: t("washPage.faq3Q"), a: t("washPage.faq3A") },
]);

async function load() {
  loading.value = true;
  loadError.value = "";
  try {
    const page = await fetchProducts({ category: "service", limit: 50 });
    packages.value = page.items
      .filter((item) => item.service_kind === "cleaning")
      .sort(
        (a, b) =>
          (a.price_from_cents ?? Number.MAX_SAFE_INTEGER) -
          (b.price_from_cents ?? Number.MAX_SAFE_INTEGER),
      );
    if (!selectedId.value && packages.value[0]) selectedId.value = packages.value[0].id;
  } catch (cause) {
    const error = cause instanceof ApiError ? cause : null;
    loadError.value = error?.message ?? "加载失败";
  } finally {
    loading.value = false;
  }
}

async function submit() {
  formError.value = "";
  dateError.value = "";
  success.value = "";
  if (!selected.value) {
    formError.value = t("washPage.empty");
    return;
  }
  if (!preferredDate.value) {
    dateError.value = t("washPage.dateHint");
    return;
  }
  if (preferredDate.value < minDate.value || preferredDate.value > maxDate.value) {
    dateError.value = t("washPage.dateHint");
    return;
  }
  submitting.value = true;
  try {
    await submitStockApplication({
      product_id: selected.value.id,
      kind: "cleaning",
      reason: `洗毛预约：${selected.value.title}`.slice(0, 1000),
      measurement_note: note.value.trim() || null,
      preferred_date: preferredDate.value,
      return_method: returnMethod.value,
    });
    success.value = t("washPage.success");
    note.value = "";
  } catch (cause) {
    const error = cause instanceof ApiError ? cause : null;
    if (error?.code === "BOOKING_DATE_INVALID") {
      dateError.value = error.message;
    } else if (error?.needsLogin) {
      formError.value = t("washPage.login");
    } else {
      formError.value = error?.message ?? t("washPage.error");
    }
  } finally {
    submitting.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div>
    <section class="bg-[#eaf2ee] text-ink">
      <div class="container-page grid gap-12 py-12 lg:grid-cols-[1.05fr_0.95fr] lg:items-center lg:py-16">
        <div class="text-[#2E2923]">
          <p class="text-[11px] uppercase tracking-[0.18em] text-[#557565]">{{ t("washPage.eyebrow") }}</p>
          <h1 class="mt-4 text-balance font-display text-4xl font-medium leading-[1.08] tracking-[-0.04em] sm:text-5xl lg:text-6xl">
            {{ t("washPage.title") }}
          </h1>
          <p class="mt-5 max-w-xl text-base leading-relaxed text-[#57504A] sm:text-lg">{{ t("washPage.intro") }}</p>
          <div class="mt-7 flex flex-wrap gap-3">
            <a
              href="#booking"
              class="inline-flex min-h-[52px] items-center rounded-full bg-brand px-6 text-sm text-white shadow-soft transition-all hover:bg-brand-hover hover:shadow-lift"
            >
              {{ t("washPage.cta") }}
            </a>
            <AppButton to="/care" variant="secondary" class="!bg-white/70 !text-[#2E2923] hover:!bg-white">日常养护指南</AppButton>
          </div>
          <ul class="mt-7 flex flex-wrap gap-2 text-[11px] text-[#57504A]">
            <li class="rounded-full bg-white/70 px-3 py-1.5">先预约后报价</li>
            <li class="rounded-full bg-white/70 px-3 py-1.5">不收款 / 不锁库存</li>
            <li class="rounded-full bg-white/70 px-3 py-1.5">到店 / 寄回</li>
          </ul>
        </div>

        <div class="mx-auto w-full max-w-sm rounded-2xl bg-surface p-7 shadow-lift">
          <p class="text-[10px] uppercase tracking-[0.22em] text-[#557565]">WASH / CARE</p>
          <p class="mt-4 font-display text-2xl font-medium leading-snug tracking-tight">洗掉灰，<br />留下毛茸茸。</p>
          <p class="mt-2 text-sm leading-relaxed text-ink-muted">单头、半装、全装，按毛面与工作量报价。</p>
          <ul class="mt-6 space-y-2.5 text-sm text-ink-soft">
            <li class="flex items-center gap-2.5"><span class="text-[#557565]" aria-hidden="true">·</span>毛面检查与局部预处理</li>
            <li class="flex items-center gap-2.5"><span class="text-[#557565]" aria-hidden="true">·</span>深层清洁、吹干与梳理</li>
            <li class="flex items-center gap-2.5"><span class="text-[#557565]" aria-hidden="true">·</span>消毒养护，到店取回或寄回</li>
          </ul>
          <p class="mt-6 rounded-xl bg-[#f2f7f3] px-4 py-2.5 text-center text-[11px] text-[#557565]">
            先预约 · 确认档期后再付款
          </p>
        </div>
      </div>
    </section>

    <section class="container-page py-14 sm:py-20">
      <SectionHeading eyebrow="PACKAGES" :title="t('washPage.packages')" :description="t('washPage.packagesNote')" />

      <StarLoader v-if="loading" :message="t('common.loading')" class="py-10" />
      <EmptyState v-else-if="loadError" tone="error" title="加载失败" :description="loadError">
        <AppButton variant="secondary" @click="load">重试</AppButton>
      </EmptyState>
      <EmptyState v-else-if="!packages.length" tone="empty" :title="t('washPage.empty')" />

      <div v-else class="mt-10 grid gap-4 sm:grid-cols-2">
        <article
          v-for="item in packages"
          :key="item.id"
          class="relative flex flex-col rounded-2xl border bg-surface p-6 shadow-soft transition-all"
          :class="selectedId === item.id ? 'border-brand shadow-lift ring-1 ring-brand/20' : 'border-line'"
        >
          <span class="mb-4 inline-flex w-fit rounded-full bg-[#eaf2ee] px-2.5 py-1 text-[10px] text-[#557565]">
            预约制
          </span>
          <h3 class="font-display text-lg font-medium">{{ item.title.replace("（演示内容）", "") }}</h3>
          <p class="mt-2 text-sm text-ink-muted">{{ item.subtitle }}</p>
          <p class="mt-7 font-display text-2xl font-medium text-brand-strong">
            {{ formatMoney(item.price_from_cents, item.currency === "USD" ? "USD" : "CNY") }}
            <span class="text-xs font-normal text-ink-muted">起</span>
          </p>
          <div class="mt-7 flex flex-wrap gap-2">
            <RouterLink
              :to="`/shop/${item.slug}`"
              class="rounded-full bg-canvas-sunk px-3.5 py-1.5 text-xs text-ink-soft transition-colors hover:bg-brand-soft hover:text-brand-strong"
            >
              查看详情
            </RouterLink>
            <button
              type="button"
              class="rounded-full px-3.5 py-1.5 text-xs transition-colors"
              :class="selectedId === item.id ? 'bg-brand text-white' : 'bg-canvas-sunk text-ink-soft hover:text-ink'"
              @click="selectedId = item.id; success = ''"
            >
              {{ selectedId === item.id ? "已选择" : "选这个" }}
            </button>
          </div>
        </article>
      </div>
    </section>

    <section class="bg-[#f0ebf2] py-14 text-[#2E2923]">
      <div class="container-page">
        <SectionHeading eyebrow="PROCESS" :title="t('washPage.process')" />
        <ol class="mt-9 grid gap-7 sm:grid-cols-2 lg:grid-cols-4">
          <li v-for="index in 4" :key="index">
            <span class="font-display text-3xl font-light text-[#887a96]/70">{{ String(index).padStart(2, "0") }}</span>
            <h3 class="mt-3 font-display text-base font-medium">{{ t(`washPage.step${index}` as MessageKey) }}</h3>
            <p class="mt-1.5 text-sm leading-relaxed text-[#57504A]">{{ t(`washPage.step${index}Body` as MessageKey) }}</p>
          </li>
        </ol>
      </div>
    </section>

    <section id="booking" class="container-page grid gap-10 py-14 sm:py-20 lg:grid-cols-[1.05fr_0.95fr]">
      <div>
        <SectionHeading eyebrow="BOOKING" :title="t('washPage.booking')" :description="t('washPage.bookingHint')" />

        <div v-if="!auth.isAuthenticated" class="mt-7 rounded-2xl border border-line bg-surface p-6 shadow-soft">
          <p class="text-sm text-ink-muted">预约需要登录，用于保存套餐、日期与进度。</p>
          <AppButton :to="'/login?next=/wash'" variant="primary" class="mt-4">
            {{ t("washPage.login") }}
          </AppButton>
        </div>

        <form v-else class="mt-7 space-y-5 rounded-2xl bg-surface p-5 shadow-soft sm:p-7" @submit.prevent="submit">
          <div>
            <label class="text-sm font-medium" for="wash-package">{{ t("washPage.packageLabel") }}</label>
            <select id="wash-package" v-model="selectedId" class="field-base mt-2" required>
              <option v-for="item in packages" :key="item.id" :value="item.id">
                {{ item.title.replace("（演示内容）", "") }} · {{ formatMoney(item.price_from_cents, "CNY") }} 起
              </option>
            </select>
          </div>

          <div class="grid gap-4 sm:grid-cols-2">
            <div>
              <label class="text-sm font-medium" for="wash-date">{{ t("washPage.dateLabel") }}</label>
              <input
                id="wash-date"
                v-model="preferredDate"
                type="date"
                class="field-base mt-2"
                :min="minDate"
                :max="maxDate"
                required
              />
              <p class="mt-1.5 text-[11px] text-ink-faint">{{ t("washPage.dateHint") }}（{{ minDate }} — {{ maxDate }}）</p>
            </div>
            <div>
              <label class="text-sm font-medium" for="wash-return">{{ t("washPage.returnLabel") }}</label>
              <select id="wash-return" v-model="returnMethod" class="field-base mt-2">
                <option value="pickup">{{ t("washPage.pickup") }}</option>
                <option value="ship">{{ t("washPage.ship") }}</option>
              </select>
            </div>
          </div>

          <div>
            <label class="text-sm font-medium" for="wash-note">{{ t("washPage.noteLabel") }}</label>
            <textarea
              id="wash-note"
              v-model="note"
              class="field-base mt-2 min-h-24"
              maxlength="500"
              :placeholder="t('washPage.notePlaceholder')"
            />
          </div>

          <p v-if="dateError" class="rounded-lg bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">{{ dateError }}</p>
          <p v-if="formError" class="rounded-lg bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">{{ formError }}</p>
          <p v-if="success" class="rounded-lg bg-jade-soft px-3 py-2 text-sm text-jade" role="status">{{ success }}</p>

          <div class="flex flex-wrap items-center gap-3">
            <AppButton type="submit" variant="primary" :disabled="submitting || !packages.length">
              {{ submitting ? t("washPage.submitting") : t("washPage.submit") }}
            </AppButton>
            <p class="text-[11px] text-ink-faint">提交后可在账户中查看进度，不产生付款。</p>
          </div>
          <p v-if="!packages.length" class="text-xs text-wine" role="status">暂时没有可选套餐，请稍后再试。</p>
        </form>
      </div>

      <aside>
        <SectionHeading eyebrow="FAQ" :title="t('washPage.faq')" />
        <div class="mt-7 divide-y divide-line">
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
        <SurfaceCard class="mt-7">
          <p class="font-display text-base font-medium">还没决定？</p>
          <p class="mt-1 text-sm text-ink-muted">把毛面照片和问题发来，先判断能不能洗。</p>
          <AppButton to="/contact" variant="secondary" size="sm" class="mt-4">联系工作室</AppButton>
        </SurfaceCard>
      </aside>
    </section>
  </div>
</template>
