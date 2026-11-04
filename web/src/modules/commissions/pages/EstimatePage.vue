<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import { AppButton, FieldRow, SectionHeading, StatusBadge } from "@/shared/ui/base";
import { ApiError } from "@/shared/lib/http";
import { applyPageHead } from "@/modules/content/api/content";
import { formatMoney } from "@/shared/lib/format";
import {
  fetchEstimateOptions,
  submitEstimate,
  type EstimateContactType,
  type EstimateOptions,
  type EstimateRequestCreated,
  type EstimateSeries,
} from "@/modules/commissions/api/estimate";

const route = useRoute();
const options = ref<EstimateOptions | null>(null);
const loading = ref(true);
const loadError = ref("");
const submitting = ref(false);
const submitError = ref("");
const fieldErrors = ref<Record<string, string>>({});
const created = ref<EstimateRequestCreated | null>(null);

const form = reactive({
  name: "",
  contact: "",
  contactType: "email" as EstimateContactType,
  seriesCode: "",
  style: "standard",
  selectedOptions: [] as string[],
  characterName: "",
  species: "",
  note: "",
  consent: false,
  website: "",
});

const contactTypes: { value: EstimateContactType; label: string; placeholder: string }[] = [
  { value: "email", label: "邮箱", placeholder: "you@example.com" },
  { value: "phone", label: "手机", placeholder: "138 0000 0000" },
  { value: "wechat", label: "微信", placeholder: "微信号" },
];

const selectedSeries = computed<EstimateSeries | null>(
  () => options.value?.series.find((item) => item.code === form.seriesCode) ?? null,
);

const selectedStyle = computed(
  () => options.value?.styles.find((item) => item.value === form.style) ?? null,
);

const optionPrice = computed(() =>
  (options.value?.options ?? [])
    .filter((item) => form.selectedOptions.includes(item.value))
    .reduce((sum, item) => sum + item.price_cents, 0),
);

const estimateLow = computed(() => {
  if (!selectedSeries.value) return 0;
  const factor = selectedStyle.value?.factor ?? 100;
  return Math.floor((selectedSeries.value.base_price_cents * factor) / 100) + optionPrice.value;
});

const estimateHigh = computed(() => Math.max(estimateLow.value, Math.floor(
  estimateLow.value * (options.value?.range_high_percent ?? 125) / 100,
)));

const contactPlaceholder = computed(
  () => contactTypes.find((item) => item.value === form.contactType)?.placeholder ?? "",
);

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = "";
  try {
    options.value = await fetchEstimateOptions();
    const requested = typeof route.query.series === "string" ? route.query.series : "";
    const first = options.value.series.find((item) => item.code === requested) ?? options.value.series[0];
    form.seriesCode = first?.code ?? "";
    const requestedStyle = typeof route.query.style === "string" ? route.query.style : "";
    if (options.value.styles.some((item) => item.value === requestedStyle)) {
      form.style = requestedStyle;
    } else if (options.value.styles[0]) {
      form.style = options.value.styles[0].value;
    }
  } catch {
    loadError.value = "估价配置暂时无法读取，请稍后再试。";
  } finally {
    loading.value = false;
  }
}

function toggleOption(value: string): void {
  const index = form.selectedOptions.indexOf(value);
  if (index >= 0) form.selectedOptions.splice(index, 1);
  else form.selectedOptions.push(value);
}

function validate(): boolean {
  const errors: Record<string, string> = {};
  if (form.name.trim().length < 2) errors.name = "请填写称呼";
  if (form.contact.trim().length < 5) errors.contact = "请填写有效联系方式";
  if (form.characterName.trim().length < 2) errors.characterName = "请填写角色名";
  if (!form.consent) errors.consent = "请先确认隐私说明";
  fieldErrors.value = errors;
  return Object.keys(errors).length === 0;
}

async function submit(): Promise<void> {
  submitError.value = "";
  if (!validate() || submitting.value) return;
  submitting.value = true;
  try {
    created.value = await submitEstimate({
      name: form.name.trim(),
      contact: form.contact.trim(),
      contact_type: form.contactType,
      series_code: form.seriesCode,
      style: form.style,
      options: [...form.selectedOptions],
      character_name: form.characterName.trim(),
      species: form.species.trim(),
      note: form.note.trim(),
      consent: form.consent,
      website: form.website,
    });
    window.scrollTo({ top: 0, behavior: "auto" });
  } catch (error) {
    if (error instanceof ApiError) {
      fieldErrors.value = error.fields;
      submitError.value = error.message;
    } else {
      submitError.value = "提交失败，请稍后再试。";
    }
  } finally {
    submitting.value = false;
  }
}

function restart(): void {
  created.value = null;
  submitError.value = "";
  fieldErrors.value = {};
}

onMounted(() => {
  applyPageHead({
    title: "委托估价",
    description: "无需登录，选择系列、风格与加项，立即查看参考价格区间；最终报价由工作室人工确认。",
    canonical: "/estimate",
  });
  void load();
});
</script>

<template>
  <div class="bg-canvas">
    <section class="border-b border-line bg-white">
      <div class="container-page py-12 sm:py-16">
        <nav class="text-xs text-ink-faint" aria-label="面包屑">
          <RouterLink to="/" class="hover:text-brand-strong">首页</RouterLink>
          <span class="mx-2">/</span>
          <span class="text-ink-muted">委托估价</span>
        </nav>
        <div class="mt-7 flex flex-wrap items-end justify-between gap-6">
          <SectionHeading
            eyebrow="ESTIMATE"
            title="先估个价，再决定"
            description="选系列、风格与加项，立刻看到参考区间。无需登录，最终价格由工作室人工确认。"
          />
          <StatusBadge tone="jade" label="无需登录" />
        </div>
      </div>
    </section>

    <div v-if="loading" class="container-page py-16">
      <div class="grid gap-10 lg:grid-cols-[1fr_340px]">
        <div class="space-y-4">
          <div v-for="index in 4" :key="index" class="h-32 animate-pulse rounded-2xl bg-canvas-sunk" />
        </div>
        <div class="h-80 animate-pulse rounded-2xl bg-canvas-sunk" />
      </div>
    </div>

    <div v-else-if="loadError" class="container-page py-16">
      <div class="rounded-2xl bg-white px-6 py-12 text-center shadow-soft">
        <p class="text-lg font-medium">估价暂时不可用</p>
        <p class="mt-2 text-sm text-ink-muted">{{ loadError }}</p>
        <AppButton class="mt-6" variant="secondary" @click="load">重新加载</AppButton>
      </div>
    </div>

    <div v-else-if="created" class="container-page py-16 sm:py-20">
      <div class="mx-auto max-w-2xl rounded-2xl bg-white p-8 shadow-lift sm:p-12">
        <p class="text-xs font-medium uppercase tracking-[0.16em] text-jade">已提交</p>
        <h1 class="mt-3 font-display text-3xl font-medium text-ink sm:text-4xl">参考区间已生成</h1>
        <p class="mt-3 text-sm leading-7 text-ink-muted">
          编号 {{ created.id }}。工作室会在 {{ created.response_workdays }} 个工作日内联系你，确认角色细节后再给正式报价。
        </p>
        <p class="mt-8 font-display text-4xl font-medium text-brand-strong sm:text-5xl">
          {{ formatMoney(created.estimate_low_cents, created.currency as "CNY" | "USD") }}
          <span class="text-ink-faint">—</span>
          {{ formatMoney(created.estimate_high_cents, created.currency as "CNY" | "USD") }}
        </p>
        <p class="mt-5 text-xs leading-6 text-ink-faint">
          这是按公开配置计算的参考区间，不构成报价，也不产生订单或付款。最终金额以人工确认的复杂度、材料与工期为准。
        </p>
        <div class="mt-8 flex flex-wrap gap-3">
          <AppButton to="/works" variant="primary">看看作品</AppButton>
          <AppButton variant="secondary" @click="restart">再估一次</AppButton>
        </div>
      </div>
    </div>

    <p v-else-if="!options?.series.length || !options.styles.length" class="container-page py-16" role="status">暂无可估价的系列或风格。</p>

    <div v-else class="container-page py-10 sm:py-14">
      <div class="grid gap-10 lg:grid-cols-[1fr_340px] lg:items-start">
        <form id="estimate-form" class="space-y-10" @submit.prevent="submit">

          <fieldset>
            <legend class="text-lg font-medium">01 · 选择制作系列</legend>
            <div class="mt-4 grid gap-3 sm:grid-cols-2">
              <button
                v-for="item in options?.series ?? []"
                :key="item.code"
                type="button"
                class="rounded-2xl px-5 py-5 text-left transition-colors"
                :class="form.seriesCode === item.code ? 'bg-brand-soft' : 'bg-canvas-sunk hover:bg-canvas'"
                @click="form.seriesCode = item.code"
              >
                <span class="flex items-center justify-between gap-3">
                  <span class="font-medium">{{ item.title }}</span>
                  <span v-if="form.seriesCode === item.code" class="text-xs text-brand-strong">已选</span>
                </span>
                <span class="mt-2 block text-sm text-ink-muted">
                  {{ formatMoney(item.base_price_cents, item.currency as "CNY" | "USD") }} 起
                </span>
              </button>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-lg font-medium">02 · 选择风格</legend>
            <div class="mt-4 flex flex-wrap gap-2">
              <button
                v-for="item in options?.styles ?? []"
                :key="item.value"
                type="button"
                class="rounded-full px-4 py-2 text-sm transition-colors"
                :class="form.style === item.value ? 'bg-brand text-white' : 'bg-canvas-sunk text-ink-soft'"
                @click="form.style = item.value"
              >
                {{ item.label }}
              </button>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-lg font-medium">03 · 加项</legend>
            <p class="mt-1 text-sm text-ink-muted">可选；勾选后参考区间会实时更新。</p>
            <div class="mt-4 grid gap-2 sm:grid-cols-2">
              <label
                v-for="item in options?.options ?? []"
                :key="item.value"
                class="flex cursor-pointer items-center justify-between gap-3 rounded-xl px-4 py-3 text-sm"
                :class="form.selectedOptions.includes(item.value) ? 'bg-brand-soft' : 'bg-canvas-sunk'"
              >
                <span class="flex items-center gap-3">
                  <input
                    type="checkbox"
                    class="h-4 w-4 accent-[var(--color-brand)]"
                    :checked="form.selectedOptions.includes(item.value)"
                    @change="toggleOption(item.value)"
                  />
                  <span>{{ item.label }}</span>
                </span>
                <span class="text-ink-muted">+{{ formatMoney(item.price_cents) }}</span>
              </label>
            </div>
          </fieldset>

          <fieldset>
            <legend class="text-lg font-medium">04 · 角色与联系</legend>
            <div class="mt-4 grid gap-4 sm:grid-cols-2">
              <FieldRow label="怎么称呼你" for="estimate-name" required :error="fieldErrors.name">
                <input id="estimate-name" v-model="form.name" class="field-base" maxlength="40" autocomplete="name" />
              </FieldRow>
              <FieldRow label="角色名" for="estimate-character" required :error="fieldErrors.characterName">
                <input id="estimate-character" v-model="form.characterName" class="field-base" maxlength="60" />
              </FieldRow>
              <FieldRow label="物种 / 设定" for="estimate-species">
                <input id="estimate-species" v-model="form.species" class="field-base" maxlength="60" placeholder="例如：龙、狐狸" />
              </FieldRow>
              <FieldRow label="补充说明" for="estimate-note">
                <textarea
                  id="estimate-note"
                  v-model="form.note"
                  class="field-base min-h-[96px] py-3"
                  maxlength="1000"
                  placeholder="用途、期望时间、已有设定等"
                />
              </FieldRow>
            </div>

            <div class="mt-5 grid gap-4 sm:grid-cols-[150px_1fr]">
              <FieldRow label="联系方式" for="estimate-contact-type">
                <select id="estimate-contact-type" v-model="form.contactType" class="field-base">
                  <option v-for="item in contactTypes" :key="item.value" :value="item.value">
                    {{ item.label }}
                  </option>
                </select>
              </FieldRow>
              <FieldRow label="如何联系你" for="estimate-contact" required :error="fieldErrors.contact">
                <input
                  id="estimate-contact"
                  v-model="form.contact"
                  class="field-base"
                  :placeholder="contactPlaceholder"
                  maxlength="120"
                  :autocomplete="form.contactType === 'email' ? 'email' : form.contactType === 'phone' ? 'tel' : 'off'"
                />
              </FieldRow>
            </div>

            <label class="mt-6 flex items-start gap-3 text-sm leading-6 text-ink-muted">
              <input v-model="form.consent" type="checkbox" class="mt-1 h-4 w-4 accent-[var(--color-brand)]" />
              <span>
                我同意工作室使用以上联系方式回访本次估价，并理解参考区间不构成报价。
                <RouterLink to="/legal/privacy" class="link-underline text-brand-strong">隐私政策</RouterLink>
              </span>
            </label>
            <p v-if="fieldErrors.consent" class="mt-2 text-xs text-wine">{{ fieldErrors.consent }}</p>
            <input v-model="form.website" type="text" class="hidden" tabindex="-1" autocomplete="off" aria-hidden="true" />
          </fieldset>
        </form>

        <aside class="lg:sticky lg:top-24">
          <div class="rounded-2xl bg-canvas-sunk p-6 sm:p-8">
            <p class="text-xs font-medium uppercase tracking-[0.16em] text-ink-faint">参考区间</p>
            <p class="mt-3 font-display text-3xl font-medium text-ink sm:text-4xl">
              {{ formatMoney(estimateLow, (selectedSeries?.currency as "CNY" | "USD") ?? "CNY") }}
            </p>
            <p class="mt-1 text-sm text-ink-muted">
              — {{ formatMoney(estimateHigh, (selectedSeries?.currency as "CNY" | "USD") ?? "CNY") }}
            </p>
            <dl class="mt-6 space-y-3 border-t border-line pt-5 text-sm">
              <div class="flex justify-between gap-3">
                <dt class="text-ink-muted">系列</dt>
                <dd>{{ selectedSeries?.title ?? "未选择" }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-muted">风格</dt>
                <dd>{{ selectedStyle?.label ?? "标准比例" }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-muted">加项</dt>
                <dd>{{ form.selectedOptions.length }} 项</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-muted">回复时间</dt>
                <dd>{{ options?.response_workdays ?? 3 }} 个工作日</dd>
              </div>
            </dl>
            <p v-if="submitError" class="mt-5 rounded-xl bg-wine-soft px-4 py-3 text-sm text-wine">
              {{ submitError }}
            </p>
            <AppButton
              class="mt-6 w-full"
              type="submit"
              form="estimate-form"
              variant="primary"
              size="lg"
              :loading="submitting"
              :disabled="!form.seriesCode"
            >
              提交估价请求
            </AppButton>
            <p class="mt-3 text-center text-xs text-ink-faint">不付款、不锁档期，先沟通再决定。</p>
          </div>
          <p class="mt-4 px-2 text-xs leading-6 text-ink-faint">{{ options?.disclaimer }}</p>
        </aside>
      </div>
    </div>
  </div>
</template>
