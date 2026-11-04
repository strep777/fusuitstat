<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: t('nav.home'), to: publicPath('/') },
        { label: t('nav.shop'), to: publicPath('/shop') },
        { label: displayTitle || t('productDetail.title') },
      ]"
    />

    <div v-if="loading" class="py-16">
      <StarLoader :message="t('productDetail.loading')" />
    </div>

    <EmptyState
      v-else-if="notFound"
      class="mt-10"
      tone="not-found"
      :title="t('productDetail.missing')"
      :description="t('productDetail.missingDescription')"
    >
      <AppButton :to="publicPath('/shop')" size="sm">{{ t('productDetail.backShop') }}</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      :title="t('productDetail.forbidden')"
      :description="t('productDetail.forbiddenDescription')"
    >
      <AppButton :to="withQuery(publicPath('/login'), { returnTo: route.fullPath })" size="sm">{{ t('productDetail.login') }}</AppButton>
      <AppButton :to="publicPath('/shop')" size="sm" variant="secondary">{{ t('productDetail.backShop') }}</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      :title="t('productDetail.error')"
      :description="t('productDetail.errorDescription', { error })"
    >
      <AppButton size="sm" @click="load()">{{ t('productDetail.retry') }}</AppButton>
      <AppButton size="sm" variant="secondary" :to="publicPath('/shop')">{{ t('productDetail.backShop') }}</AppButton>
    </EmptyState>

    <template v-else-if="detail">
      <div class="mt-6 grid gap-10 lg:grid-cols-[1fr_1fr]">
        <!-- 1. 图片 -->
        <div>
          <div class="overflow-hidden rounded-lg border border-line bg-canvas-sunk">
            <img
              v-if="currentImage"
              :src="currentImage.thumb ?? currentImage.src"
              :srcset="currentImage.srcset?.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
              sizes="(min-width: 1024px) 50vw, 100vw"
              :width="currentImage.width"
              :height="currentImage.height"
              :alt="currentImage.alt || t('productDetail.imageAlt', { title: detail.title })"
              loading="lazy"
              decoding="async"
              class="w-full object-cover"
            />
            <div v-else class="flex aspect-square items-center justify-center text-xs text-ink-faint">
              {{ t('productDetail.imagePending') }}
            </div>
          </div>

          <div v-if="images.length > 1" class="mt-3 flex flex-wrap gap-3">
            <button
              v-for="(image, index) in images"
              :key="image.id"
              type="button"
              class="overflow-hidden rounded-sm border transition-colors"
              :class="index === activeImage ? 'border-amber' : 'border-line hover:border-amber'"
              :aria-label="t('productDetail.imageNumber', { index: index + 1 })"
              :aria-pressed="index === activeImage"
              @click="activeImage = index"
            >
              <img
                :src="image.thumb ?? image.src"
                :width="image.width"
                :height="image.height"
                :alt="image.alt || t('productDetail.imageAltNumber', { title: detail.title, index: index + 1 })"
                loading="lazy"
                decoding="async"
                class="size-20 object-cover"
              />
            </button>
          </div>
        </div>

        <!-- 2~5. SKU/库存 → 价格 → 运费 → 售后 → 购买按钮 -->
        <div class="space-y-6">
          <div>
            <div class="flex flex-wrap items-center gap-2">
              <StatusBadge v-if="displayOnly" tone="neutral" label="仅展示" />
              <StatusBadge v-else :tone="availability.tone" :label="availability.label" />
              <StatusBadge v-if="detail.category" tone="neutral" :label="productCategoryLabel(detail.category)" />
              <StatusBadge v-if="!displayOnly && detail.requires_approval" tone="amber" :label="t('productDetail.approval')" />
              <StatusBadge v-if="!displayOnly && detail.kind" tone="night" :label="productKindLabel(detail.kind)" />
            </div>
            <h1 class="mt-4 font-display text-3xl leading-tight sm:text-4xl">{{ displayTitle }}</h1>
        <p v-if="isDemo" class="mt-2 inline-flex rounded-full bg-ink/8 px-2.5 py-1 text-[11px] font-bold text-ink-muted">演示</p>
            <p v-if="detail.subtitle" class="mt-3 text-sm text-ink-muted">{{ detail.subtitle }}</p>

            <div v-if="displayOnly" class="mt-6 rounded-xl bg-surface-muted p-5 text-sm leading-relaxed text-ink-muted">
              周边目前仅展示，暂不售卖。喜欢的话可以联系工作室，后续开放会先发公告。
              <div class="mt-4">
                <AppButton :to="publicPath('/contact')" size="sm">联系工作室</AppButton>
              </div>
            </div>

            <p v-else class="mt-5 flex flex-wrap items-baseline gap-2">
              <span class="font-display text-3xl">{{ priceLabel }}</span>
              <span v-if="priceHighCents !== null && priceHighCents !== priceLowCents" class="text-xs text-ink-faint">
                {{ t('productDetail.priceRange') }}
              </span>
              <span v-else class="text-xs text-ink-faint">{{ t('productDetail.shippingExcluded') }}</span>
            </p>

            <p v-if="!displayOnly" class="mt-2 text-xs text-ink-muted">
              <template v-if="skus.length">
                {{ t('productDetail.skuCount', { count: skus.length, available: availableSkuCount }) }}
              </template>
              {{ t('productDetail.stockNote') }}
            </p>

            <p
              v-if="!displayOnly && detail.availability === 'sold_out'"
              class="mt-4 rounded-lg border border-line-strong bg-surface-muted p-4 text-sm text-ink-muted"
            >
              {{ t('productDetail.soldOut') }}
            </p>
          </div>

          <template v-if="!displayOnly">
            <p
              v-if="!auth.isAuthenticated"
              class="rounded-lg border border-line bg-surface-muted p-4 text-sm text-ink-muted"
            >
              {{ t('productDetail.loginNote') }}
              <RouterLink class="link-underline text-amber-strong" :to="withQuery(publicPath('/login'), { returnTo: route.fullPath })">
                {{ t('productDetail.login') }}
              </RouterLink>
            </p>

            <p
              v-else-if="expiredApplication"
              class="rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong"
            >
              {{ t('productDetail.expired') }}
            </p>

            <SkuPicker
              :skus="skus"
              :requires-approval="detail.requires_approval"
              :approval-url="approvalUrl"
              :approval-window-label="approvalWindowLabel"
              :purchasable="availability.purchasable"
              :unavailable-reason="
                availability.purchasable
                  ? ''
                  : t('productDetail.unavailable', { status: availability.label })
              "
              :submitting="submitting"
              @add="onAdd"
            >
              <template #shipping>
                <p class="eyebrow">Shipping</p>
                <p class="mt-2 text-sm leading-relaxed text-ink-muted">{{ shippingNote }}</p>
                <p class="mt-2 text-xs text-ink-faint">
                  {{ t('productDetail.shippingNote') }}{{ site.commercePolicy.cross_border_note }}
                </p>
              </template>

              <template #after-sales>
                <p class="eyebrow">After sales</p>
                <p class="mt-2 text-sm leading-relaxed text-ink-muted">
                  {{ t('productDetail.afterSales', { note: afterSalesNote }) }}
                </p>
                <p class="mt-2 text-xs text-ink-faint">
                  {{ t('productDetail.refund') }}
                </p>
                <AppButton :to="publicPath('/legal/returns')" size="sm" variant="secondary" class="mt-3">
                  {{ t('productDetail.returns') }}
                </AppButton>
              </template>
            </SkuPicker>
          </template>
        </div>
      </div>

      <!-- 商品说明 -->
      <section v-if="detail.description_html" class="mt-16">
        <SectionHeading eyebrow="Detail" :title="t('productDetail.descriptionTitle')" :description="t('productDetail.descriptionNote')" />
        <div
          class="prose-quiet mt-6 max-w-[720px] text-sm leading-relaxed text-ink-muted"
          v-html="detail.description_html"
        />
      </section>

      <section class="mt-16">
        <SectionHeading
          eyebrow="Next"
          :title="t('productDetail.nextTitle')"
          :description="t('productDetail.nextDescription')"
        >
          <template #actions>
            <AppButton to="/account/applications" size="sm" variant="secondary">{{ t('productDetail.applications') }}</AppButton>
            <AppButton :to="publicPath('/contact')" size="sm">{{ t('productDetail.contact') }}</AppButton>
          </template>
        </SectionHeading>
      </section>
    </template>
  </div>
</template>

<script setup lang="ts">
/**
 * 商品详情（/shop/:slug）。
 *
 * 信息顺序（11 文档）：图片 → SKU/库存 → 价格/运费说明 → 售后说明 → 购买按钮。
 *
 * 约束（03/08/C28 文档）：
 * - 售罄仍可浏览；前端不自行判定可售，可售性只看 `skus[].available` 与下单时服务端校验。
 * - `requires_approval` 的商品必须先提交申请并通过人工审批，页面明确显示并给出申请入口；
 *   已有审批通过记录时显示剩余购买时间。
 * - Product 结构化数据的价格与库存与页面展示同源，不写评价与星级。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import SkuPicker from "@/modules/catalog/components/SkuPicker.vue";
import {
  DEMO_TITLE_MARK,
  applyPageMeta,
  buildProductJsonLd,
  displayProductTitle,
  fetchProduct,
  fetchStockApplications,
  productAvailabilityMeta,
  productCategoryLabel,
  productKindLabel,
  setProductJsonLd,
  toCurrency,
  withQuery,
  type ProductDetail,
  type Sku,
  type StockApplication,
} from "@/modules/catalog/api/catalog";
import { useCartStore } from "@/modules/catalog/stores/cart";
import { useSiteStore } from "@/modules/content/stores/site";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, StatusBadge } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatCountdown, formatMoney, isExpired } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useI18n } from "@/shared/lib/i18n";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const cart = useCartStore();
const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const { t, locale } = useI18n();
const publicPath = (path: string): string => locale.value === "en-US" ? `/en${path === "/" ? "" : path}` : path;

const detail = ref<ProductDetail | null>(null);
const applications = ref<StockApplication[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const notFound = ref(false);
const forbidden = ref(false);
const submitting = ref(false);
const activeImage = ref(0);
const now = ref(Date.now());

const slug = computed(() => String(route.params.slug ?? ""));

const displayTitle = computed(() => displayProductTitle(detail.value?.title));
const isDemo = computed(() => (detail.value?.title ?? "").includes(DEMO_TITLE_MARK));
const displayOnly = computed(() => detail.value?.category === "merch");

const images = computed(() => detail.value?.images ?? []);
const currentImage = computed(() => images.value[activeImage.value] ?? images.value[0] ?? null);
const skus = computed<Sku[]>(() => detail.value?.skus ?? []);
const availability = computed(() => productAvailabilityMeta(detail.value?.availability));
const currency = computed(() => toCurrency(detail.value?.currency));
const availableSkuCount = computed(
  () => skus.value.filter((sku) => sku.available && sku.in_stock).length,
);

/** 价格区间取自已加载的 SKU；没有 SKU 时退回服务端起价，绝不在前端推算价格。 */
const priceLowCents = computed<number | null>(() => {
  const prices = skus.value.map((sku) => sku.price_cents).filter((value) => Number.isFinite(value));
  if (prices.length === 0) return detail.value?.price_from_cents ?? null;
  return Math.min(...prices);
});
const priceHighCents = computed<number | null>(() => {
  const prices = skus.value.map((sku) => sku.price_cents).filter((value) => Number.isFinite(value));
  if (prices.length === 0) return priceLowCents.value;
  return Math.max(...prices);
});
const priceLabel = computed(() => {
  if (priceLowCents.value === null) return t("productCard.pricePending");
  if (priceHighCents.value !== null && priceHighCents.value !== priceLowCents.value) {
    return `${formatMoney(priceLowCents.value, currency.value)} – ${formatMoney(priceHighCents.value, currency.value)}`;
  }
  return formatMoney(priceLowCents.value, currency.value);
});

/** 审批通过且仍在购买时间内的申请（同一商品取最合适的一条）。 */
const approvedApplication = computed<StockApplication | null>(() => {
  const product = detail.value;
  if (!product) return null;
  const own = applications.value.filter((item) => item.product_id === product.id);
  const approved = own.filter((item) => item.status === "approved");
  const open = approved.find(
    (item) => !item.purchase_expires_at || !isExpired(item.purchase_expires_at, now.value),
  );
  return open ?? null;
});

/** 审批通过但购买时间已结束：给出明确的重申请提示，而不是静默禁用。 */
const expiredApplication = computed<StockApplication | null>(() => {
  const product = detail.value;
  if (!product) return null;
  return (
    applications.value.find(
      (item) =>
        item.product_id === product.id &&
        item.status === "approved" &&
        Boolean(item.purchase_expires_at) &&
        isExpired(item.purchase_expires_at, now.value),
    ) ?? null
  );
});

const approvalWindowLabel = computed(() => {
  const item = approvedApplication.value;
  if (!item) return "";
  if (!item.purchase_expires_at) return t("productDetail.approved");
  return t("productDetail.remaining", { time: formatCountdown(item.purchase_expires_at, now.value, locale.value) });
});

const approvalUrl = computed(() => {
  const product = detail.value;
  if (product?.service_kind === "cleaning") return "/wash";
  if (!auth.isAuthenticated) return withQuery(publicPath("/login"), { returnTo: route.fullPath });
  return withQuery("/account/applications", {
    product_id: product?.id ?? "",
    product_slug: product?.slug ?? "",
    kind: product?.kind === "service" ? "service" : "drop",
  });
});

const shippingNote = computed(
  () => detail.value?.shipping_note || site.commercePolicy.shipping_template_note,
);
const afterSalesNote = computed(
  () => detail.value?.after_sales_note || site.commercePolicy.returns_reference,
);

let countdownTimer: ReturnType<typeof setInterval> | null = null;

function stopCountdown(): void {
  if (countdownTimer) {
    clearInterval(countdownTimer);
    countdownTimer = null;
  }
}

watch(approvedApplication, (value) => {
  stopCountdown();
  if (value?.purchase_expires_at) {
    countdownTimer = setInterval(() => {
      now.value = Date.now();
    }, 30_000);
  }
});

async function loadApproval(): Promise<void> {
  applications.value = [];
  if (!auth.isAuthenticated) return;
  try {
    const rows = await fetchStockApplications({
      product_id: detail.value?.id,
      limit: 20,
    });
    // 防御：任何非数组形状都按「没有审批记录」处理，绝不让 undefined 进入渲染，
    // 否则模板/计算属性会在渲染期抛错并把页面永久留在加载态。
    applications.value = Array.isArray(rows) ? rows : [];
  } catch {
    // 审批记录读取失败不影响浏览与普通购买：页面按“未获准”处理，并保留申请入口。
    applications.value = [];
  }
}

let loadVersion = 0;
async function load(): Promise<void> {
  const version = ++loadVersion;
  if (!slug.value) {
    notFound.value = true;
    loading.value = false;
    return;
  }
  loading.value = true;
  error.value = null;
  notFound.value = false;
  forbidden.value = false;
  activeImage.value = 0;
  try {
    const payload = await fetchProduct(slug.value, site.browsingCurrency);
    if (version !== loadVersion) return;
    detail.value = payload;
    applyPageMeta({
      title: displayProductTitle(payload.title),
      description:
        payload.subtitle ||
        t("productDetail.meta", { title: displayProductTitle(payload.title) }),
      canonicalPath: `/shop/${payload.slug}`,
    });
    syncJsonLd();
    await loadApproval();
  } catch (caught) {
    if (version !== loadVersion) return;
    if (caught instanceof ApiError) {
      if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") notFound.value = true;
      else if (caught.status === 403) forbidden.value = true;
      else error.value = caught.message;
    } else {
      error.value = t("productDetail.error");
    }
  } finally {
    if (version === loadVersion) loading.value = false;
  }
}

/** 结构化数据与页面展示同源；没有价格时不写 offers（不虚构价格与库存）。 */
function syncJsonLd(): void {
  const product = detail.value;
  if (!product) {
    setProductJsonLd(null);
    return;
  }
  const origin = typeof location === "undefined" ? "" : location.origin;
  setProductJsonLd(
    buildProductJsonLd({
      product,
      priceLowCents: displayOnly.value ? null : priceLowCents.value,
      priceHighCents: displayOnly.value ? null : priceHighCents.value,
      skuCount: displayOnly.value ? 0 : skus.value.length,
      url: `${origin}${publicPath(`/shop/${product.slug}`)}`,
    }),
  );
}

watch([detail, skus], () => syncJsonLd());

async function onAdd(payload: { sku: Sku; quantity: number }): Promise<void> {
  if (displayOnly.value) return;
  const product = detail.value;
  if (!product) return;
  if (!auth.isAuthenticated) {
    toast.info(t("productDetail.loginFirst"), t("productDetail.cartSaved"));
    await router.push(withQuery(publicPath("/login"), { returnTo: route.fullPath }));
    return;
  }
  submitting.value = true;
  const ok = await cart.addItem(payload.sku.id, payload.quantity);
  submitting.value = false;
  if (ok) {
    toast.success(
      t("productDetail.added", { title: product.title, sku: payload.sku.label }),
      t("productDetail.cartCount", { count: cart.itemCount }),
    );
  } else {
    toast.error(
      cart.error ?? t("productDetail.addFailed"),
      t("productDetail.addRetry"),
    );
  }
}

onMounted(() => {
  applyPageMeta({
    title: t("productDetail.title"),
    description: t("productDetail.metaDefault"),
    canonicalPath: `/shop/${slug.value}`,
  });
  if (auth.isAuthenticated) void cart.load();
  void load();
});

onBeforeUnmount(() => {
  stopCountdown();
  setProductJsonLd(null);
});

watch([slug, () => site.browsingCurrency], () => void load());
watch(
  () => auth.isAuthenticated,
  () => void loadApproval(),
);
</script>

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
