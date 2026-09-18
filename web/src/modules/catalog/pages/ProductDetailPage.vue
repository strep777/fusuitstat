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
  applyPageMeta,
  buildProductJsonLd,
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
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const cart = useCartStore();
const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();

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
  if (priceLowCents.value === null) return "价格待公布";
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
  if (!item.purchase_expires_at) return "审批已通过，可直接购买";
  return `剩余购买时间 ${formatCountdown(item.purchase_expires_at, now.value)}`;
});

const approvalUrl = computed(() => {
  const product = detail.value;
  if (!auth.isAuthenticated) return withQuery("/login", { returnTo: route.fullPath });
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

async function load(): Promise<void> {
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
    const payload = await fetchProduct(slug.value);
    detail.value = payload;
    applyPageMeta({
      title: payload.title,
      description:
        payload.subtitle ||
        `${payload.title} 的图片、规格与库存状态、价格与运费说明、售后说明。可售性与金额以下单时服务端校验为准。`,
      canonicalPath: `/shop/${payload.slug}`,
    });
    syncJsonLd();
    await loadApproval();
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") notFound.value = true;
      else if (caught.status === 403) forbidden.value = true;
      else error.value = caught.message;
    } else {
      error.value = "商品详情加载失败。";
    }
  } finally {
    loading.value = false;
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
      priceLowCents: priceLowCents.value,
      priceHighCents: priceHighCents.value,
      skuCount: skus.value.length,
      url: `${origin}/shop/${product.slug}`,
    }),
  );
}

watch([detail, skus], () => syncJsonLd());

async function onAdd(payload: { sku: Sku; quantity: number }): Promise<void> {
  const product = detail.value;
  if (!product) return;
  if (!auth.isAuthenticated) {
    toast.info("请先登录再加入购物车", "购物车保存在服务端，登录后可以在其他设备继续结算。");
    await router.push(withQuery("/login", { returnTo: route.fullPath }));
    return;
  }
  submitting.value = true;
  const ok = await cart.addItem(payload.sku.id, payload.quantity);
  submitting.value = false;
  if (ok) {
    toast.success(
      `已加入购物车：${product.title} · ${payload.sku.label}`,
      `购物车当前共 ${cart.itemCount} 件。价格、库存与运费在结算时由服务端重新校验。`,
    );
  } else {
    toast.error(
      cart.error ?? "加入购物车失败",
      "可以稍后重试；购物车数量与库存以服务端为准，页面不会先按本地状态当成成功。",
    );
  }
}

onMounted(() => {
  applyPageMeta({
    title: "商品详情",
    description: "商品图片、规格与库存状态、价格与运费说明、售后说明。",
    canonicalPath: `/shop/${slug.value}`,
  });
  if (auth.isAuthenticated) void cart.load();
  void load();
});

onBeforeUnmount(() => {
  stopCountdown();
  setProductJsonLd(null);
});

watch(slug, () => void load());
watch(
  () => auth.isAuthenticated,
  () => void loadApproval(),
);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '商城', to: '/shop' },
        { label: detail?.title ?? '商品详情' },
      ]"
    />

    <div v-if="loading" class="py-16">
      <StarLoader message="正在读取商品信息" />
    </div>

    <EmptyState
      v-else-if="notFound"
      class="mt-10"
      tone="not-found"
      title="找不到这个商品"
      description="商品可能已下架或地址有误；下架商品不会通过公开地址继续销售。"
    >
      <AppButton to="/shop" size="sm">返回商城</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="没有访问该商品的权限"
      description="该商品可能仅对特定客户开放。登录后可查看自己的申请与订单记录。"
    >
      <AppButton :to="withQuery('/login', { returnTo: route.fullPath })" size="sm">手机号登录</AppButton>
      <AppButton to="/shop" size="sm" variant="secondary">返回商城</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      title="商品详情暂时无法加载"
      :description="`${error} 网络恢复后可以重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/shop">返回商城</AppButton>
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
              :alt="currentImage.alt || `${detail.title} 商品图`"
              loading="lazy"
              decoding="async"
              class="w-full object-cover"
            />
            <div v-else class="flex aspect-square items-center justify-center text-xs text-ink-faint">
              图片准备中
            </div>
          </div>

          <div v-if="images.length > 1" class="mt-3 flex flex-wrap gap-3">
            <button
              v-for="(image, index) in images"
              :key="image.id"
              type="button"
              class="overflow-hidden rounded-sm border transition-colors"
              :class="index === activeImage ? 'border-amber' : 'border-line hover:border-amber'"
              :aria-label="`查看第 ${index + 1} 张商品图`"
              :aria-pressed="index === activeImage"
              @click="activeImage = index"
            >
              <img
                :src="image.thumb ?? image.src"
                :width="image.width"
                :height="image.height"
                :alt="image.alt || `${detail.title} 商品图 ${index + 1}`"
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
              <StatusBadge :tone="availability.tone" :label="availability.label" />
              <StatusBadge v-if="detail.category" tone="neutral" :label="productCategoryLabel(detail.category)" />
              <StatusBadge v-if="detail.requires_approval" tone="amber" label="需先申请审批" />
              <StatusBadge v-if="detail.kind" tone="night" :label="productKindLabel(detail.kind)" />
            </div>
            <h1 class="mt-4 font-display text-3xl leading-tight sm:text-4xl">{{ detail.title }}</h1>
            <p v-if="detail.subtitle" class="mt-3 text-sm text-ink-muted">{{ detail.subtitle }}</p>

            <p class="mt-5 flex flex-wrap items-baseline gap-2">
              <span class="font-display text-3xl">{{ priceLabel }}</span>
              <span v-if="priceHighCents !== null && priceHighCents !== priceLowCents" class="text-xs text-ink-faint">
                区间来自各规格单价
              </span>
              <span v-else class="text-xs text-ink-faint">未含运费，最终金额以结算页为准</span>
            </p>

            <p class="mt-2 text-xs text-ink-muted">
              <template v-if="skus.length">
                共 {{ skus.length }} 个规格，其中 {{ availableSkuCount }} 个当前可售；
              </template>
              库存与可售性以下单时服务端校验为准，页面不自行判定。
            </p>

            <p
              v-if="detail.availability === 'sold_out'"
              class="mt-4 rounded-lg border border-line-strong bg-surface-muted p-4 text-sm text-ink-muted"
            >
              该商品当前全部规格售罄。售罄仍可浏览页面与规格，补货后会重新开放购买；
              补货时间不做承诺，可以提交申请或关注公告。
            </p>
          </div>

          <p
            v-if="!auth.isAuthenticated"
            class="rounded-lg border border-line bg-surface-muted p-4 text-sm text-ink-muted"
          >
            购物车保存在服务端，登录后才能加入与结算。
            <RouterLink class="link-underline text-amber-strong" :to="withQuery('/login', { returnTo: route.fullPath })">
              手机号登录
            </RouterLink>
          </p>

          <p
            v-else-if="expiredApplication"
            class="rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong"
          >
            上一次审批的购买时间已结束，无法继续按该审批购买。需要重新提交申请并等待人工审批。
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
                : `该商品当前状态为「${availability.label}」，暂不可加入购物车；售罄或待上架仍可浏览规格。`
            "
            :submitting="submitting"
            @add="onAdd"
          >
            <template #shipping>
              <p class="eyebrow">Shipping</p>
              <p class="mt-2 text-sm leading-relaxed text-ink-muted">{{ shippingNote }}</p>
              <p class="mt-2 text-xs text-ink-faint">
                运费在结算页按运费模板计算，页面不预估金额；{{ site.commercePolicy.cross_border_note }}
              </p>
            </template>

            <template #after-sales>
              <p class="eyebrow">After sales</p>
              <p class="mt-2 text-sm leading-relaxed text-ink-muted">
                退换货参照：{{ afterSalesNote }}。定制商品与现货的条件不同，质量问题走独立处理流程，
                不会一概“不退不换”。
              </p>
              <p class="mt-2 text-xs text-ink-faint">
                退款按原渠道退回原付款来源，到账以渠道结果为准。
              </p>
              <AppButton to="/legal/returns" size="sm" variant="secondary" class="mt-3">
                阅读退换与维修规则
              </AppButton>
            </template>
          </SkuPicker>
        </div>
      </div>

      <!-- 商品说明 -->
      <section v-if="detail.description_html" class="mt-16">
        <SectionHeading eyebrow="Detail" title="商品说明" description="以运营发布的说明为准，不含未核准的性能承诺。" />
        <div
          class="prose-quiet mt-6 max-w-[720px] text-sm leading-relaxed text-ink-muted"
          v-html="detail.description_html"
        />
      </section>

      <section class="mt-16">
        <SectionHeading
          eyebrow="Next"
          title="还需要确认？"
          description="可以查看掉落与服务申请规则、售后条款，或直接联系客服确认现货与配件情况。"
        >
          <template #actions>
            <AppButton to="/account/applications" size="sm" variant="secondary">掉落与服务申请</AppButton>
            <AppButton to="/contact" size="sm">联系客服</AppButton>
          </template>
        </SectionHeading>
      </section>
    </template>
  </div>
</template>

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
