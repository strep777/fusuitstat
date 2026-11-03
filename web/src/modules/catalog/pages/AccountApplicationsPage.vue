<script setup lang="ts">
/**
 * 掉落与服务申请（/account/applications）。
 *
 * 规则（C28 文档）：掉落商品走“申请 → 人工审批 → 生成审批通过列表 → 按优先级购买”，
 * 服务走“申请 → 报价 → 合同 → 履约”。审批通过前不能购买，本页只提交与查看申请，
 * 不预先承诺审批结果与库存。
 *
 * 状态覆盖（11 文档）：列表有 loading/empty/error/forbidden；表单有 dirty/saving/saved/validation-error。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  STOCK_APPLICATION_KIND_OPTIONS,
  applyPageMeta,
  fetchCharacter,
  fetchProduct,
  fetchProducts,
  fetchStockApplications,
  stockApplicationKindLabel,
  stockApplicationStatusMeta,
  submitStockApplication,
  withQuery,
  type ProductSummary,
  type Sku,
  type StockApplication,
} from "@/modules/catalog/api/catalog";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  FieldRow,
  SectionHeading,
  SkeletonBlock,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";
import { formatCountdown, formatDateTime, isExpired, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const toast = useToast();

const applications = ref<StockApplication[]>([]);
const applicationsLoading = ref(true);
const applicationsError = ref<string | null>(null);
const forbidden = ref(false);
const page = ref(1);
const hasMore = ref(false);

const options = ref<ProductSummary[]>([]);
const optionsLoading = ref(true);
const optionsError = ref<string | null>(null);

const skus = ref<Sku[]>([]);
const skusLoading = ref(false);

const form = reactive({
  product_id: "",
  sku_id: "",
  kind: "drop",
  reason: "",
  measurement_note: "",
});
const pristine = ref(JSON.stringify(form));
const errors = ref<Record<string, string>>({});
const saving = ref(false);
const savedApplication = ref<StockApplication | null>(null);
const submitError = ref<string | null>(null);
const prefillNote = ref<string | null>(null);
const now = ref(Date.now());

const dirty = computed(() => JSON.stringify(form) !== pristine.value);

/** 洗毛有独立预约页与日期字段，通用申请不提供 cleaning，避免缺日期被服务端拒绝。 */
const applicationKindOptions = computed(() =>
  STOCK_APPLICATION_KIND_OPTIONS.filter((option) => option.value !== "cleaning"),
);

const hasOpenWindow = computed(() =>
  applications.value.some(
    (item) =>
      item.status === "approved" &&
      Boolean(item.purchase_expires_at) &&
      !isExpired(item.purchase_expires_at, now.value),
  ),
);

/** C28：服务类申请通过后先报价——只有已审批且在购买窗口内的服务申请可以带进询价。 */
function canStartInquiry(item: StockApplication): boolean {
  return (
    item.kind === "service" &&
    item.status === "approved" &&
    (!item.purchase_expires_at || !isExpired(item.purchase_expires_at, now.value))
  );
}

let ticker: ReturnType<typeof setInterval> | null = null;

function stopTicker(): void {
  if (ticker) {
    clearInterval(ticker);
    ticker = null;
  }
}

watch(hasOpenWindow, (value) => {
  stopTicker();
  if (value) {
    ticker = setInterval(() => {
      now.value = Date.now();
    }, 30_000);
  }
});

function markPristine(): void {
  pristine.value = JSON.stringify(form);
}

async function loadApplications(): Promise<void> {
  applicationsLoading.value = true;
  applicationsError.value = null;
  forbidden.value = false;
  try {
    const rows = await fetchStockApplications({ limit: 20 });
    // 服务端返回本人申请数组；分页在服务端一次给足 20 条，`has_more` 由长度推断。
    applications.value = rows;
    hasMore.value = rows.length >= 20;
  } catch (caught) {
    if (caught instanceof ApiError && (caught.needsLogin || caught.status === 403)) {
      forbidden.value = true;
      applications.value = [];
    } else {
      applicationsError.value =
        caught instanceof ApiError ? caught.message : "申请列表加载失败，请稍后重试。";
    }
  } finally {
    applicationsLoading.value = false;
  }
}

async function loadOptions(): Promise<void> {
  optionsLoading.value = true;
  optionsError.value = null;
  try {
    const payload = await fetchProducts({ limit: 100 });
    // 洗毛套餐有独立预约页（/wash）与日期字段，不进入通用申请表单，避免缺日期被服务端拒绝。
    options.value = payload.items.filter(
      (item) => item.requires_approval === true && item.service_kind !== "cleaning",
    );
  } catch (caught) {
    optionsError.value =
      caught instanceof ApiError ? caught.message : "可申请商品加载失败，请稍后重试。";
  } finally {
    optionsLoading.value = false;
  }
}

/** 从 URL 参数恢复申请目标：商品详情与角色详情都会带上它。 */
async function applyPrefill(): Promise<void> {
  const kind = String(route.query.kind ?? "");
  if (kind === "drop" || kind === "service") form.kind = kind;

  const productId = String(route.query.product_id ?? "");
  let productSlug = String(route.query.product_slug ?? "");
  const characterSlug = String(route.query.character ?? "");

  if (!productId && characterSlug) {
    try {
      const character = await fetchCharacter(characterSlug);
      productSlug = productSlug || character.product_slug || "";
      if (character.product_id) {
        form.product_id = character.product_id;
        prefillNote.value = `已选择角色「${character.name}」关联的商品。`;
        markPristine();
        return;
      }
    } catch {
      // 角色读取失败不阻塞申请：用户仍可手动选择商品。
    }
  }

  if (productId) {
    const known = options.value.find((item) => item.id === productId);
    form.product_id = productId;
    prefillNote.value = known ? `已选择商品「${known.title}」。` : null;
    markPristine();
    return;
  }

  const local = productSlug ? options.value.find((item) => item.slug === productSlug) : undefined;
  if (local) {
    form.product_id = local.id;
    prefillNote.value = `已选择商品「${local.title}」。`;
    markPristine();
    return;
  }

  if (productSlug) {
    try {
      const product = await fetchProduct(productSlug);
      if (product.service_kind === "cleaning") {
        prefillNote.value = "洗毛套餐需要选择期望日期与返还方式，请到「洗毛与护理」页预约。";
      } else if (product.requires_approval) {
        options.value = [product, ...options.value];
        form.product_id = product.id;
        prefillNote.value = `已选择商品「${product.title}」。`;
      } else {
        prefillNote.value = "该商品不需要人工审批，可直接在商品页购买。";
      }
    } catch {
      prefillNote.value = "未能自动带上目标商品，请在下面手动选择。";
    }
    markPristine();
  }
}

watch(
  () => form.product_id,
  async (productId) => {
    form.sku_id = "";
    skus.value = [];
    const product = options.value.find((item) => item.id === productId);
    if (!product) return;
    skusLoading.value = true;
    try {
      const detail = await fetchProduct(product.slug);
      skus.value = detail.skus ?? [];
    } catch {
      // 规格读取失败时仍可提交不带 SKU 的申请。
      skus.value = [];
    } finally {
      skusLoading.value = false;
    }
  },
);

function validate(): boolean {
  const next: Record<string, string> = {};
  if (!form.product_id) next.product_id = "请选择要申请的商品或服务。";
  const reason = form.reason.trim();
  if (reason.length < 5) next.reason = "请写明用途与期望，至少 5 个字，便于人工审批。";
  else if (reason.length > 500) next.reason = "申请理由请控制在 500 字以内。";
  if (form.measurement_note.trim().length > 500) {
    next.measurement_note = "量体说明请控制在 500 字以内。";
  }
  errors.value = next;
  return Object.keys(next).length === 0;
}

function resetForm(): void {
  form.product_id = "";
  form.sku_id = "";
  form.reason = "";
  form.measurement_note = "";
  skus.value = [];
  markPristine();
}

async function submit(): Promise<void> {
  submitError.value = null;
  if (!validate()) {
    toast.error("申请未提交", "请先修正标出的字段。");
    return;
  }
  saving.value = true;
  try {
    const created = await submitStockApplication({
      product_id: form.product_id,
      sku_id: form.sku_id || null,
      kind: form.kind,
      reason: form.reason.trim(),
      measurement_note: form.measurement_note.trim() || null,
    });
    savedApplication.value = created;
    resetForm();
    toast.success("申请已提交", "进入人工审批队列；结果以站内通知与申请状态为准，不在页面预先承诺。");
    await loadApplications();
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) {
        forbidden.value = true;
      } else if (caught.status === 422 || Object.keys(caught.fields ?? {}).length > 0) {
        errors.value = { ...caught.fields };
        toast.error("申请未通过校验", "服务端返回了字段问题，请按提示修改后重新提交。");
      } else {
        submitError.value = caught.message;
        toast.error("提交失败", caught.message);
      }
    } else {
      submitError.value = "提交失败，请稍后重试。";
      toast.error("提交失败", "网络或服务异常，可以稍后重试。");
    }
  } finally {
    saving.value = false;
  }
}

onMounted(async () => {
  applyPageMeta({
    title: "掉落与服务申请",
    description: "提交掉落商品或服务申请，查看人工审批状态与审批通过后的购买时间。",
    canonicalPath: "/account/applications",
  });
  await Promise.all([loadApplications(), loadOptions()]);
  await applyPrefill();
});

onBeforeUnmount(stopTicker);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '掉落与服务申请' },
      ]"
    />

    <div class="mt-6">
      <SectionHeading
        :level="1"
        eyebrow="Applications"
        title="掉落与服务申请"
        description="掉落商品与服务都需要先提交申请，由工作人员人工审批。掉落按“申请 → 审批 → 生成审批通过列表 → 按优先级购买”执行；服务按“申请 → 报价 → 合同 → 履约”执行。"
      >
        <template #actions>
          <AppButton to="/shop" size="sm" variant="secondary">返回商城</AppButton>
          <AppButton to="/contact" size="sm">联系客服</AppButton>
        </template>
      </SectionHeading>
    </div>

    <EmptyState
      v-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="登录后才能查看与提交申请"
      description="申请与审批记录属于个人数据，需要手机号登录后访问；未登录时不会展示任何申请是否存在。"
    >
      <AppButton :to="withQuery('/login', { returnTo: route.fullPath })" size="sm">手机号登录</AppButton>
    </EmptyState>

    <template v-else>
      <!-- 申请入口 -->
      <SurfaceCard class="mt-10">
        <h2 class="font-display text-xl">提交新申请</h2>
        <p class="mt-2 text-sm text-ink-muted">
          只列出需要人工审批的商品与服务。审批通过后会生成购买优先级；
          未通过审批前不能购买，也不会预留库存。
        </p>

        <p
          v-if="prefillNote"
          class="mt-4 rounded-lg border border-line bg-surface-muted p-3 text-sm text-ink-muted"
        >
          {{ prefillNote }}
        </p>

        <div v-if="optionsLoading" class="mt-6">
          <SkeletonBlock :lines="3" aria-label="正在加载可申请商品" />
        </div>

        <EmptyState
          v-else-if="optionsError"
          class="mt-6"
          tone="error"
          title="可申请商品暂时无法加载"
          :description="`${optionsError} 列表恢复后即可提交申请。`"
        >
          <AppButton size="sm" @click="loadOptions()">重新加载</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="options.length === 0"
          class="mt-6"
          title="当前没有需要申请的商品"
          description="掉落商品与服务由运营在上架时标记为“需人工审批”；没有这类商品时可直接在商城购买现货。"
        >
          <AppButton to="/shop" size="sm" variant="secondary">浏览商城</AppButton>
        </EmptyState>

        <form v-else class="mt-6 grid gap-5" novalidate @submit.prevent="submit()">
          <FieldRow
            label="申请商品或服务"
            required
            for="application-product"
            :error="errors.product_id"
            hint="只显示需要人工审批的项目。"
          >
            <select id="application-product" v-model="form.product_id" class="field-base">
              <option value="">请选择</option>
              <option v-for="item in options" :key="item.id" :value="item.id">
                {{ item.title }}
              </option>
            </select>
          </FieldRow>

          <FieldRow
            label="规格"
            for="application-sku"
            :error="errors.sku_id"
            hint="可选；不确定时留空，工作人员会在审批时确认。"
          >
            <select id="application-sku" v-model="form.sku_id" class="field-base" :disabled="skusLoading">
              <option value="">{{ skusLoading ? "正在读取规格…" : "暂不确定" }}</option>
              <option v-for="sku in skus" :key="sku.id" :value="sku.id">
                {{ sku.label }}（{{ sku.sku_code }}）
              </option>
            </select>
          </FieldRow>

          <fieldset class="space-y-2">
            <legend class="text-sm font-medium text-ink-soft">申请类型</legend>
            <div class="flex flex-wrap gap-3">
              <label
                v-for="option in applicationKindOptions"
                :key="option.value"
                class="inline-flex min-h-[44px] items-center gap-2 rounded-full border-2 px-4 text-sm"
                :class="
                  form.kind === option.value
                    ? 'border-brand bg-brand-soft text-brand-strong'
                    : 'border-line-strong'
                "
              >
                <input v-model="form.kind" type="radio" name="application-kind" :value="option.value" />
                {{ option.label }}
              </label>
            </div>
            <p class="text-xs text-ink-faint">
              掉落商品审批通过后按优先级购买；服务需要先报价、签合同再履约。
            </p>
          </fieldset>

          <FieldRow
            label="申请理由与用途"
            required
            for="application-reason"
            :error="errors.reason"
            hint="写明用途、期望规格与时间要求，便于人工判断；不要填写与履约无关的隐私信息。"
          >
            <textarea
              id="application-reason"
              v-model="form.reason"
              class="field-base min-h-[120px]"
              maxlength="500"
              rows="5"
              placeholder="例如：希望用于线下漫展，需要可拆卸尾巴与散热结构"
            />
          </FieldRow>

          <FieldRow
            label="量体与尺寸说明"
            for="application-measurement"
            :error="errors.measurement_note"
            hint="可选。若涉及尺寸或量体，请填写已知数据；正式量体在合同阶段单独收集。"
          >
            <textarea
              id="application-measurement"
              v-model="form.measurement_note"
              class="field-base min-h-[80px]"
              maxlength="500"
              rows="3"
            />
          </FieldRow>

          <p v-if="submitError" class="text-sm text-wine" role="alert">{{ submitError }}</p>

          <div class="flex flex-wrap items-center gap-4">
            <AppButton type="submit" :loading="saving" :disabled="saving">
              {{ saving ? "正在提交" : "提交申请" }}
            </AppButton>
            <span v-if="dirty && !saving" class="text-xs text-ink-faint">有未提交的修改</span>
            <span v-else-if="!saving" class="text-xs text-ink-faint">尚未修改</span>
          </div>
        </form>

        <div
          v-if="savedApplication"
          class="mt-6 rounded-lg border border-jade bg-jade-soft p-4 text-sm text-jade"
          role="status"
        >
          <p class="font-medium">申请已提交，编号 {{ savedApplication.id }}</p>
          <p class="mt-1">
            当前状态：{{ stockApplicationStatusMeta(savedApplication.status).label }}。
            审批结果会写入站内通知；页面不会预先显示审批结论。
          </p>
        </div>
      </SurfaceCard>

      <!-- 申请列表 -->
      <section class="mt-16">
        <SectionHeading
          eyebrow="My applications"
          title="我的申请"
          description="审批通过且仍在购买时间内的申请可以按优先级购买；过时后需要重新申请。"
        >
          <template #actions>
            <AppButton size="sm" variant="secondary" @click="loadApplications()">刷新列表</AppButton>
          </template>
        </SectionHeading>

        <div v-if="applicationsLoading" class="mt-6 rounded-lg border border-line bg-surface p-6">
          <SkeletonBlock :lines="4" aria-label="正在加载申请列表" />
        </div>

        <EmptyState
          v-else-if="applicationsError"
          class="mt-6"
          tone="error"
          title="申请列表暂时无法加载"
          :description="`${applicationsError} 已提交的申请不会丢失，可稍后刷新。`"
        >
          <AppButton size="sm" @click="loadApplications()">重新加载</AppButton>
        </EmptyState>

        <ul v-else-if="applications.length" class="mt-6 space-y-4">
          <li v-for="item in applications" :key="item.id">
            <SurfaceCard>
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div>
                  <p class="text-xs text-ink-faint">
                    {{ stockApplicationKindLabel(item.kind) }} · 提交于
                    {{ formatDateTime(item.created_at) }}
                  </p>
                  <h3 class="mt-1 font-display text-lg">{{ item.product_title }}</h3>
                  <p v-if="item.sku_label" class="mt-1 text-xs text-ink-faint">
                    规格 {{ item.sku_label }}
                  </p>
                </div>
                <StatusBadge
                  :tone="stockApplicationStatusMeta(item.status).tone"
                  :label="stockApplicationStatusMeta(item.status).label"
                />
              </div>

              <p class="mt-3 text-sm text-ink-muted">{{ truncate(item.reason, 160) }}</p>
              <p v-if="item.measurement_note" class="mt-2 text-xs text-ink-faint">
                量体说明：{{ truncate(item.measurement_note, 80) }}
              </p>
              <p v-if="item.preferred_date" class="mt-2 text-xs text-ink-faint">
                期望日期：<span class="tabular-nums">{{ item.preferred_date }}</span>
                · 返还方式：{{ item.return_method === "ship" ? "寄回" : "到店取回" }}
              </p>

              <p class="mt-3 text-xs text-ink-muted">
                {{ stockApplicationStatusMeta(item.status).description }}
              </p>

              <dl class="mt-4 grid gap-2 text-xs text-ink-muted sm:grid-cols-2">
                <div v-if="item.reviewed_at">
                  <dt class="text-ink-faint">审批时间</dt>
                  <dd class="tabular-nums">{{ formatDateTime(item.reviewed_at) }}</dd>
                </div>
                <div v-if="item.purchase_priority !== null">
                  <dt class="text-ink-faint">购买优先级</dt>
                  <dd>第 {{ item.purchase_priority }} 位</dd>
                </div>
                <div v-if="item.purchase_expires_at">
                  <dt class="text-ink-faint">购买截止</dt>
                  <dd class="tabular-nums">
                    {{ formatDateTime(item.purchase_expires_at) }}
                    <span v-if="!isExpired(item.purchase_expires_at, now)">
                      （剩余 {{ formatCountdown(item.purchase_expires_at, now) }}）
                    </span>
                    <span v-else>（已结束）</span>
                  </dd>
                </div>
              </dl>

              <p
                v-if="item.review_note"
                class="mt-3 rounded-sm border border-line bg-surface-muted p-3 text-xs text-ink-muted"
              >
                审核说明：{{ item.review_note }}
              </p>

              <div class="mt-4 flex flex-wrap gap-3">
                <!-- C28：服务类申请通过后先报价；这里把审批结果带进询价表单，避免用户重复填写。 -->
                <AppButton
                  v-if="canStartInquiry(item)"
                  :to="withQuery('/commission/new', { application_id: item.id })"
                  size="sm"
                >
                  据此发起询价
                </AppButton>
                <AppButton
                  v-if="item.product_slug"
                  :to="`/shop/${item.product_slug}`"
                  size="sm"
                  variant="secondary"
                >
                  查看商品
                </AppButton>
                <AppButton to="/contact" size="sm" variant="ghost">有问题联系客服</AppButton>
              </div>
            </SurfaceCard>
          </li>
        </ul>

        <EmptyState
          v-else
          class="mt-6"
          title="还没有申请记录"
          description="提交申请后可以在这里查看审批状态、购买优先级与剩余购买时间。"
        />

        <div v-if="applications.length" class="mt-6 flex flex-wrap items-center justify-between gap-4">
          <AppButton
            v-if="page > 1"
            size="sm"
            variant="secondary"
            @click="
              page -= 1;
              loadApplications();
            "
          >
            上一页
          </AppButton>
          <span v-else class="text-xs text-ink-faint">已是第一页</span>

          <AppButton
            v-if="hasMore"
            size="sm"
            variant="secondary"
            @click="
              page += 1;
              loadApplications();
            "
          >
            下一页
          </AppButton>
          <span v-else class="text-xs text-ink-faint">已显示全部申请</span>
        </div>
      </section>
    </template>
  </div>
</template>
