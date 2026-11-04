<script setup lang="ts">
/**
 * 结算页。
 *
 * 规则来源：`文档/08_商城订单支付与退款.md`。
 * - 客户端只提交 SKU、数量、地址 ID、条款版本与接受标记；单价、金额与积分一律不提交。
 * - 创建订单时服务端重新校验价格与库存：`PRICE_CHANGED` / `STOCK_NOT_ENOUGH` 必须让用户
 *   看见具体变化后再次确认，不允许静默改价或静默下单。
 * - 同一份提交复用同一个幂等键（首次点击时生成并保存在组件状态里）；按钮防连点只改善体验，
 *   不承担幂等保证。
 * - 条款未勾选不可提交，并在按钮旁说明原因。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import {
  createAddress,
  createOrder,
  fetchAddresses,
  fetchCart,
  previewCheckout,
  type Address,
  type CartLine,
  type CheckoutLine,
  type CheckoutPreview,
} from "@/modules/commerce/api/commerce";
import MoneySummary from "@/modules/commerce/components/MoneySummary.vue";
import PaymentCountdown from "@/modules/commerce/components/PaymentCountdown.vue";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  fetchPointsRedemptionQuote,
  type PointsRedemptionQuote,
} from "@/modules/loyalty/api/loyalty";
import { phoneFormatError } from "@/modules/identity/api/identity";
import { formatDateTime, formatMoneyExact, formatPoints } from "@/shared/lib/format";
import { resolveText, useI18n } from "@/shared/lib/i18n";
import { ApiError, newIdempotencyKey } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";
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

const router = useRouter();
const site = useSiteStore();
const toast = useToast();
const { locale } = useI18n();

interface PriceChangeRow {
  sku_id: string;
  title: string;
  sku_name: string;
  before_quantity: number;
  after_quantity: number;
  before_unit_price_cents: number;
  after_unit_price_cents: number;
  currency: "CNY" | "USD";
  quantity_changed: boolean;
  unit_price_changed: boolean;
}

const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);
const sessionExpired = ref(false);

const cartLines = ref<CartLine[]>([]);
const addresses = ref<Address[]>([]);
const selectedAddressId = ref<string>("");
const preview = ref<CheckoutPreview | null>(null);
const previewLoading = ref(false);
let previewRequest = 0;
const previewError = ref<string | null>(null);

const accepted = ref(false);
const submitting = ref(false);
const submitError = ref<string | null>(null);
/** 价格变化待确认：服务端返回的新价格快照与上一份报价的差异 */
const priceChange = ref<{ rows: PriceChangeRow[]; summaryBefore: string; summaryAfter: string } | null>(null);
/** 库存不足：指出具体 SKU */
const stockIssues = ref<Array<{ sku_id: string; title: string; sku_name: string; available: number | null; requested: number }>>(
  [],
);
/** 幂等键：整份提交（含价格确认后的重试）复用同一个键 */
const submissionKey = ref<string | null>(null);

const addressFormOpen = ref(false);
const addressDirty = ref(false);
const addressSaving = ref(false);
const addressSaved = ref(false);
const addressError = ref<string | null>(null);
const addressFieldErrors = ref<Record<string, string>>({});
const addressForm = ref({ recipient: "", phone: "", region: "", detail: "", postal_code: "", is_default: false });


const currency = computed(() => preview.value?.currency ?? "CNY");
const items = computed(() => cartLines.value.map((line) => ({ sku_id: line.sku_id, quantity: line.quantity })));

/* ------------------------------------------------------------------ 星绒币抵扣（C15）
 * 规则与开关都来自服务端配置：未开启、或规则没配全时**不出现入口**（09 文档：
 * 不能宣传未上线的兑换权益）。能用多少、抵多少钱一律由服务端在预览里裁定，前端只提交选择。
 */
const redeemPoints = ref(0);
const pointsInput = ref("");
/** 未使用积分时的独立报价：只为展示「本单最多可用」，金额仍由服务端给出。 */
const pointsBaseQuote = ref<PointsRedemptionQuote | null>(null);
const pointsBackendDisabled = ref(false);
const pointsRulesReady = computed(() => {
  const rules = site.loyaltyRules;
  return (
    rules.redemption_enabled === true &&
    (rules.redemption_points_per_yuan ?? 0) > 0 &&
    (rules.redemption_min_points ?? 0) > 0 &&
    (rules.redemption_max_ratio_percent ?? 0) > 0
  );
});
const pointsAvailable = computed(
  () =>
    !pointsBackendDisabled.value &&
    site.featureEnabled("points_redemption") &&
    pointsRulesReady.value,
);
const pointsQuote = computed(() => preview.value?.points_quote ?? pointsBaseQuote.value);
const pointsInputError = computed(() => {
  const raw = pointsInput.value.trim();
  if (raw === "") return "";
  if (!/^\d+$/.test(raw)) return "只能填写整数星绒币数量。";
  const value = Number(raw);
  const min = pointsQuote.value?.min_points ?? site.loyaltyRules.redemption_min_points ?? 0;
  if (value > 0 && value < min) return `单笔至少使用 ${min} ${site.loyaltyRules.points_name}。`;
  return "";
});

async function applyPointsRedeem(): Promise<void> {
  if (pointsInputError.value) return;
  const value = pointsInput.value.trim() === "" ? 0 : Number(pointsInput.value.trim());
  redeemPoints.value = value;
  accepted.value = false;
  await refreshPreview();
}

async function clearPointsRedeem(): Promise<void> {
  pointsInput.value = "";
  redeemPoints.value = 0;
  accepted.value = false;
  await refreshPreview();
}

const canSubmit = computed(
  () =>
    preview.value !== null &&
    selectedAddressId.value !== "" &&
    accepted.value &&
    !submitting.value &&
    preview.value.lines.length > 0 &&
    pointsInputError.value === "",
);

const submitBlockedReason = computed(() => {
  if (submitting.value) return "正在提交，请稍候。";
  if (!preview.value) return previewLoading.value ? "正在获取结算预览。" : "需要先取得服务端结算预览。";
  if (preview.value.lines.length === 0) return "购物车中没有可结算的商品。";
  if (selectedAddressId.value === "") return "请先选择收货地址。";
  if (pointsInputError.value) return pointsInputError.value;
  if (!accepted.value) return "请勾选并接受订单条款后再提交。";
  return "";
});

const addressRequirement = computed(() => {
  const policy = site.commercePolicy;
  return {
    returns: policy.returns_reference,
    crossBorder: policy.cross_border_note,
  };
});

/* 跨境（C02/C24、C17；N07 首版比例待运营配置）
 * 是否跨境、预估多少一律由服务端判定：前端不按地区自己算税率。
 * 未配置比例时**不显示金额**，只说「以清关为准」——0 元会被误读成免关税。
 */
const crossBorderNotice = computed(() => {
  const current = preview.value;
  if (!current || !current.cross_border) return null;
  const hasEstimate = current.estimate_available && current.duty_estimate_cents !== null;
  return {
    // 说明文案可能是按语言给出的运营配置：先取服务端冻结的说明，再回退站点政策文案。
    note: resolveText(current.duty_note, locale.value) || addressRequirement.value.crossBorder,
    estimateText: hasEstimate
      ? `${formatMoneyExact(current.duty_estimate_cents ?? 0, currency.value)}（按 ${current.duty_percent}% 预估）`
      : "",
    hasEstimate,
  };
});

/** 定制与现货的售后条件分别呈现；文案来自站点配置，不硬编码条款 */
const fulfillmentTypes = computed(() => {
  const types = new Set(cartLines.value.map((line) => line.fulfillment_type));
  return Array.from(types);
});

async function loadBase(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  sessionExpired.value = false;
  try {
    const [cart, addressList] = await Promise.all([fetchCart(), fetchAddresses()]);
    cartLines.value = cart.items;
    addresses.value = addressList;
    const preferred = addressList.find((item) => item.is_default) ?? addressList[0];
    selectedAddressId.value = preferred ? preferred.id : "";
    addressFormOpen.value = addressList.length === 0;

    if (cart.items.length === 0) {
      preview.value = null;
      return;
    }
    if (selectedAddressId.value) {
      await refreshPreview();
    }
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        error.value = "登录状态已过期，请重新登录后继续结算。";
      } else if (caught.status === 403) {
        forbidden.value = true;
        error.value = "当前账号无权访问该结算信息。";
      } else if (caught.status === 404) {
        notFound.value = true;
        error.value = "购物车或地址不存在，可能已在其他设备清空。";
      } else {
        error.value = caught.message || "结算信息加载失败。";
      }
    } else {
      error.value = "结算信息加载失败，请稍后重试。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await Promise.allSettled([site.load()]);
  await loadBase();
});

const expiresText = computed(() =>
  preview.value ? formatDateTime(preview.value.expires_at) : "",
);

async function refreshPreview(): Promise<CheckoutPreview | null> {
  const request = ++previewRequest;
  preview.value = null;
  pointsBaseQuote.value = null;
  if (!selectedAddressId.value || items.value.length === 0) {
    previewLoading.value = false;
    return null;
  }
  previewLoading.value = true;
  previewError.value = null;
  try {
    const next = await previewCheckout({
      items: items.value,
      address_id: selectedAddressId.value,
      redeem_points: redeemPoints.value,
    });
    if (request !== previewRequest) return null;
    preview.value = next;
    void refreshPointsBaseQuote(next);
    return next;
  } catch (caught) {
    if (request !== previewRequest) return null;
    if (caught instanceof ApiError) {
      previewError.value = caught.message || "结算预览获取失败。";
      if (caught.code === "STOCK_NOT_ENOUGH") {
        stockIssues.value = parseStockIssues(caught);
      }
      if (caught.needsLogin) sessionExpired.value = true;
    } else {
      previewError.value = "结算预览获取失败，请稍后重试。";
    }
    return null;
  } finally {
    if (request === previewRequest) previewLoading.value = false;
  }
}

/**
 * 未使用积分时单独取一次报价，用来展示「本单最多可用」。
 * 服务端回 `FEATURE_DISABLED` 说明规则或开关并未真正就绪——此时把入口收起来，
 * 而不是继续展示一个按站点配置猜出来的上限。
 */
async function refreshPointsBaseQuote(next: CheckoutPreview): Promise<void> {
  if (!pointsRulesReady.value || !site.featureEnabled("points_redemption")) {
    pointsBaseQuote.value = null;
    return;
  }
  const amount = Math.max(0, next.goods_cents - next.discount_cents);
  if (amount <= 0) {
    pointsBaseQuote.value = null;
    return;
  }
  try {
    const quote = await fetchPointsRedemptionQuote(amount, 0);
    if (preview.value?.checkout_token !== next.checkout_token) return;
    pointsBaseQuote.value = quote;
    pointsBackendDisabled.value = false;
  } catch (caught) {
    if (preview.value?.checkout_token !== next.checkout_token) return;
    pointsBaseQuote.value = null;
    if (caught instanceof ApiError && caught.code === "FEATURE_DISABLED") {
      pointsBackendDisabled.value = true;
    }
  }
}

watch(selectedAddressId, (value, previous) => {
  if (value === previous) return;
  // 地址变化会重新计算运费与可售情况，因此需要重新确认条款
  accepted.value = false;
  stockIssues.value = [];
  if (!loading.value) void refreshPreview();
});

/** 对比上一份报价与服务端最新价格，生成逐行差异 */
function buildPriceChangeRows(previous: CheckoutLine[], next: CheckoutLine[]): PriceChangeRow[] {
  const rows: PriceChangeRow[] = [];
  for (const after of next) {
    const before = previous.find((line) => line.sku_id === after.sku_id);
    if (!before) continue;
    const unitChanged = before.unit_price_cents !== after.unit_price_cents;
    const quantityChanged = before.quantity !== after.quantity;
    if (!unitChanged && !quantityChanged) continue;
    rows.push({
      sku_id: after.sku_id,
      title: after.product_title,
      sku_name: after.sku_name,
      before_quantity: before.quantity,
      after_quantity: after.quantity,
      before_unit_price_cents: before.unit_price_cents,
      after_unit_price_cents: after.unit_price_cents,
      currency: after.currency,
      quantity_changed: quantityChanged,
      unit_price_changed: unitChanged,
    });
  }
  return rows;
}

function summarize(previewValue: CheckoutPreview | null): string {
  if (!previewValue) return "—";
  return formatMoneyExact(previewValue.total_cents, previewValue.currency);
}

async function submit(): Promise<void> {
  if (!preview.value || !canSubmit.value) return;

  // 同一份提交复用同一个幂等键；重试不会新建订单
  if (submissionKey.value === null) {
    submissionKey.value = newIdempotencyKey();
  }
  const key = submissionKey.value;
  const previousLines = preview.value.lines;
  const previousTotal = summarize(preview.value);

  submitting.value = true;
  submitError.value = null;
  priceChange.value = null;
  stockIssues.value = [];

  try {
    const result = await createOrder({
      checkout_token: preview.value.checkout_token,
      address_id: selectedAddressId.value,
      terms_version: preview.value.terms_version,
      accepted: true,
      // 与预览一致的服务端裁定用量；不一致时后端返回 PRICE_CHANGED 让用户重新确认。
      redeem_points: preview.value.points_redeemed,
      idempotencyKey: key,
    });
    toast.success("订单已创建", `订单号 ${result.order_no}，请在到期前完成付款。`);
    await router.push({ name: "pay", params: { id: result.installment_id } });
  } catch (caught) {
    if (caught instanceof ApiError) {
      await handleSubmitError(caught, previousLines, previousTotal);
    } else {
      submitError.value = "下单请求失败，请检查网络后重试。";
    }
  } finally {
    submitting.value = false;
  }
}

async function handleSubmitError(
  caught: ApiError,
  previousLines: CheckoutLine[],
  previousTotal: string,
): Promise<void> {
  if (caught.needsLogin) {
    sessionExpired.value = true;
    submitError.value = "登录状态已过期，请重新登录后继续结算；已选择的地址不会丢失。";
    return;
  }

  if (caught.code === "PRICE_CHANGED") {
    // 让用户看见变化，再决定是否按新价格下单
    const changed = caught.fields["lines"];
    const nextLines: CheckoutLine[] = changed ? safeParseLines(changed) : [];
    const rows = nextLines.length ? buildPriceChangeRows(previousLines, nextLines) : [];
    priceChange.value = { rows, summaryBefore: previousTotal, summaryAfter: previousTotal };
    accepted.value = false;
    const fresh = await refreshPreview();
    if (priceChange.value) {
      priceChange.value.summaryAfter = summarize(fresh);
    }
    toast.info("价格已变化", "请核对新的商品价格与合计，并重新确认后再提交。");
    return;
  }

  if (caught.code === "STOCK_NOT_ENOUGH") {
    stockIssues.value = parseStockIssues(caught);
    accepted.value = false;
    await refreshPreview();
    toast.info("库存不足", "服务端返回的库存不足以完成下单，请调整数量后重新确认。");
    return;
  }

  if (caught.code === "CHECKOUT_TOKEN_INVALID") {
    submissionKey.value = null;
    accepted.value = false;
    await refreshPreview();
    submitError.value = "结算预览已失效，已为你重新获取最新价格，请核对后再次提交。";
    return;
  }

  if (caught.code === "IDEMPOTENCY_CONFLICT") {
    submissionKey.value = newIdempotencyKey();
    submitError.value = "本次提交与服务端已有记录冲突，已生成新的提交请求，请重新提交一次。";
    return;
  }

  if (caught.code === "STATE_CONFLICT") {
    submitError.value = "订单状态已变化，请刷新订单列表后重试。";
    return;
  }

  if (caught.code === "FEATURE_DISABLED") {
    submitError.value = "该结算方式当前未启用，请稍后重试或联系客服。";
    return;
  }

  submitError.value = caught.message || "下单失败，请稍后重试。";
}

function safeParseLines(raw: string): CheckoutLine[] {
  try {
    const parsed = JSON.parse(raw) as unknown;
    return Array.isArray(parsed) ? (parsed as CheckoutLine[]) : [];
  } catch {
    return [];
  }
}

function parseStockIssues(
  caught: ApiError,
): Array<{ sku_id: string; title: string; sku_name: string; available: number | null; requested: number }> {
  const raw = caught.fields["items"] ?? caught.fields["sku_ids"] ?? "";
  const codes = raw
    .split(",")
    .map((value) => value.trim())
    .filter((value) => value.length > 0);
  const source = codes.length ? codes : cartLines.value.map((line) => line.sku_id);
  return source.map((skuId) => {
    const line = cartLines.value.find((item) => item.sku_id === skuId);
    return {
      sku_id: skuId,
      title: line?.product_title ?? "购物车中的商品",
      sku_name: line?.sku_name ?? "",
      available: line?.available_quantity ?? null,
      requested: line?.quantity ?? 0,
    };
  });
}

/**
 * 把界面上的单行「省市区」拆成服务端字段。
 *
 * 服务端按 province / city / region_code 保存（05 文档的地址模型），
 * 因此这里只做展示层的拆分：第一个词作为省/直辖市，其余作为市/区；
 * 区域码不在本表单收集，默认 CN，跨境配送需在账户地址页维护。
 */
function splitRegion(region: string): { province: string; city: string } {
  const [province = "", ...rest] = region.split(/\s+/).filter(Boolean);
  return { province, city: rest.join(" ") };
}

async function saveAddress(): Promise<void> {
  addressError.value = null;
  addressFieldErrors.value = {};
  addressSaved.value = false;

  if (!addressForm.value.recipient.trim()) {
    addressFieldErrors.value["recipient"] = "请填写收货人姓名。";
  }
  const phoneError = phoneFormatError(addressForm.value.phone);
  if (phoneError) {
    addressFieldErrors.value["phone"] = `${phoneError}（用于配送联系）`;
  }
  if (!addressForm.value.region.trim()) {
    addressFieldErrors.value["region"] = "请填写所在地区，例如：上海市 徐汇区。";
  }
  if (!addressForm.value.detail.trim()) {
    addressFieldErrors.value["detail"] = "请填写详细地址。";
  }
  if (Object.keys(addressFieldErrors.value).length > 0) return;

  addressSaving.value = true;
  try {
    const created = await createAddress({
      recipient: addressForm.value.recipient.trim(),
      phone: addressForm.value.phone.trim(),
      // 表单只收一行「省市区」；服务端按 province/city 保存，region_code 默认 CN。
      ...splitRegion(addressForm.value.region.trim()),
      detail: addressForm.value.detail.trim(),
      postal_code: addressForm.value.postal_code.trim() || null,
      is_default: addressForm.value.is_default,
    });
    addresses.value = [...addresses.value, created];
    selectedAddressId.value = created.id;
    addressDirty.value = false;
    addressSaved.value = true;
    addressFormOpen.value = false;
    addressForm.value = { recipient: "", phone: "", region: "", detail: "", postal_code: "", is_default: false };
    toast.success("地址已保存", "订单会保存当时的地址快照，之后修改地址不影响历史订单。");
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        addressError.value = "登录状态已过期，请重新登录后再保存地址。";
      } else {
        addressFieldErrors.value = { ...(caught.fields ?? {}) };
        addressError.value = caught.message || "地址保存失败。";
      }
    } else {
      addressError.value = "地址保存失败，请稍后重试。";
    }
  } finally {
    addressSaving.value = false;
  }
}

function markDirty(): void {
  addressDirty.value = true;
  addressSaved.value = false;
  addressError.value = null;
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[{ label: '首页', to: '/' }, { label: '购物车', to: '/cart' }, { label: '结算' }]"
    />

    <SectionHeading
      class="mt-6"
      :level="1"
      eyebrow="Checkout"
      title="确认订单并结算"
      description="应付金额、运费、优惠与积分抵扣都由服务端计算。这里只提交商品、数量、收货地址与「想用多少星绒币」这一选择，不提交单价或金额。"
    />

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="6" aria-label="正在读取结算信息" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : notFound ? 'not-found' : 'error'"
      :title="
        forbidden
          ? '无法访问该结算信息'
          : notFound
            ? '结算信息不存在'
            : sessionExpired
              ? '登录已过期'
              : '结算信息加载失败'
      "
      :description="error"
    >
      <AppButton v-if="sessionExpired" size="sm" to="/login?returnTo=/checkout">重新登录</AppButton>
      <AppButton v-else size="sm" @click="loadBase()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/cart">返回购物车</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="cartLines.length === 0"
      class="mt-8"
      title="购物车中没有可结算的商品"
      description="先挑选现货、配件或已通过审批的掉落；定制委托需要先接受报价才会生成应付订单。"
    >
      <AppButton size="sm" to="/shop">去商城</AppButton>
      <AppButton size="sm" variant="secondary" to="/cart">返回购物车</AppButton>
    </EmptyState>

    <div v-else class="mt-8 grid gap-8 lg:grid-cols-[1.5fr_1fr] lg:items-start">
      <div class="space-y-6">
        <!-- 价格变更提示：必须让用户看见变化后再次确认 -->
        <SurfaceCard v-if="priceChange" class="!border-amber !bg-amber-soft">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h2 class="font-display text-lg text-amber-strong">价格已变化，请确认后再提交</h2>
            <StatusBadge tone="amber" label="需要重新确认" />
          </div>
          <p class="mt-3 text-sm text-amber-strong">
            服务端在创建订单时重新校验了价格：下单前的合计 {{ priceChange.summaryBefore }}，当前合计
            {{ priceChange.summaryAfter }}。我们不会静默按新价格下单，请你核对后再次确认。
          </p>
          <ul v-if="priceChange.rows.length" class="mt-4 divide-y divide-amber/40 rounded-sm bg-surface">
            <li v-for="row in priceChange.rows" :key="row.sku_id" class="p-3 text-sm">
              <p class="font-medium">{{ row.title }} · {{ row.sku_name }}</p>
              <p v-if="row.unit_price_changed" class="mt-1 tabular-nums text-ink-soft">
                单价：{{ formatMoneyExact(row.before_unit_price_cents, row.currency) }} →
                {{ formatMoneyExact(row.after_unit_price_cents, row.currency) }}
              </p>
              <p v-if="row.quantity_changed" class="mt-1 tabular-nums text-ink-soft">
                数量：{{ row.before_quantity }} → {{ row.after_quantity }}
              </p>
            </li>
          </ul>
          <p v-else class="mt-3 text-xs text-amber-strong">
            服务端未返回逐行明细，请以上方最新金额摘要与下方商品清单为准。
          </p>
          <p class="mt-4 text-xs text-amber-strong">勾选订单条款后再次点击「提交订单」即表示接受新价格。</p>
        </SurfaceCard>

        <!-- 库存不足：指出具体 SKU -->
        <SurfaceCard v-if="stockIssues.length" class="!border-wine !bg-wine-soft">
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h2 class="font-display text-lg text-wine">部分商品库存不足</h2>
            <StatusBadge tone="wine" label="未创建订单" />
          </div>
          <ul class="mt-3 space-y-1 text-sm text-wine">
            <li v-for="issue in stockIssues" :key="issue.sku_id">
              {{ issue.title }}<span v-if="issue.sku_name"> · {{ issue.sku_name }}</span>
              <span v-if="issue.available !== null" class="tabular-nums">
                ：当前可售 {{ issue.available }} 件，购物车中为 {{ issue.requested }} 件
              </span>
              <span v-else>：服务端判定可售数量不足，请调整数量</span>
            </li>
          </ul>
          <p class="mt-3 text-xs text-wine">
            调整数量后金额会重新计算，需要重新勾选条款再提交。没有成功扣减库存就不会产生订单。
          </p>
          <div class="mt-4">
            <AppButton size="sm" variant="secondary" to="/cart">返回购物车调整数量</AppButton>
          </div>
        </SurfaceCard>

        <!-- 收货地址 -->
        <SurfaceCard>
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h2 class="font-display text-lg">收货地址</h2>
            <AppButton size="sm" variant="secondary" @click="addressFormOpen = !addressFormOpen">
              {{ addressFormOpen ? "收起新增地址" : "新增地址" }}
            </AppButton>
          </div>

          <p v-if="addresses.length === 0 && !addressFormOpen" class="mt-3 text-sm text-ink-muted">
            还没有收货地址。结算前需要先填写一个地址，订单会保存当时的地址快照。
          </p>

          <fieldset v-if="addresses.length" class="mt-4 space-y-3">
            <legend class="sr-only">选择收货地址</legend>
            <label
              v-for="address in addresses"
              :key="address.id"
              class="flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors"
              :class="
                selectedAddressId === address.id
                  ? 'border-amber bg-amber-soft'
                  : 'border-line bg-surface hover:border-line-strong'
              "
            >
              <input
                v-model="selectedAddressId"
                type="radio"
                name="shipping-address"
                class="mt-1 size-4 accent-amber"
                :value="address.id"
              />
              <span class="min-w-0 text-sm">
                <span class="flex flex-wrap items-center gap-2">
                  <span class="font-medium">{{ address.recipient }}</span>
                  <span class="tabular-nums text-ink-muted">{{ address.phone_masked }}</span>
                  <StatusBadge v-if="address.is_default" tone="neutral" label="默认地址" />
                  <StatusBadge v-if="selectedAddressId === address.id" tone="amber" label="已选择" />
                </span>
                <span class="mt-1 block text-ink-muted">{{ address.province }} {{ address.city }} {{ address.detail }}</span>
              </span>
            </label>
          </fieldset>

          <form v-if="addressFormOpen" class="mt-4 space-y-4 border-t border-line pt-4" @submit.prevent="saveAddress">
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="收货人" required for="address-recipient" :error="addressFieldErrors['recipient'] ?? ''">
                <input
                  id="address-recipient"
                  v-model="addressForm.recipient"
                  class="field-base"
                  type="text"
                  autocomplete="name"
                  @input="markDirty"
                />
              </FieldRow>
              <FieldRow
                label="手机号"
                required
                for="address-phone"
                hint="仅用于配送联系，页面只回显掩码。"
                :error="addressFieldErrors['phone'] ?? ''"
              >
                <input
                  id="address-phone"
                  v-model="addressForm.phone"
                  class="field-base"
                  type="tel"
                  inputmode="numeric"
                  autocomplete="tel"
                  @input="markDirty"
                />
              </FieldRow>
            </div>
            <FieldRow
              label="所在地区"
              required
              for="address-region"
              hint="例如：上海市 徐汇区"
              :error="addressFieldErrors['region'] ?? ''"
            >
              <input
                id="address-region"
                v-model="addressForm.region"
                class="field-base"
                type="text"
                autocomplete="address-level1"
                @input="markDirty"
              />
            </FieldRow>
            <FieldRow label="详细地址" required for="address-detail" :error="addressFieldErrors['detail'] ?? ''">
              <input
                id="address-detail"
                v-model="addressForm.detail"
                class="field-base"
                type="text"
                autocomplete="street-address"
                @input="markDirty"
              />
            </FieldRow>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="邮政编码" for="address-postal" hint="选填">
                <input
                  id="address-postal"
                  v-model="addressForm.postal_code"
                  class="field-base"
                  type="text"
                  inputmode="numeric"
                  autocomplete="postal-code"
                  @input="markDirty"
                />
              </FieldRow>
              <label class="flex items-center gap-3 self-end pb-2 text-sm text-ink-soft">
                <input v-model="addressForm.is_default" type="checkbox" class="size-4 accent-amber" @change="markDirty" />
                设为默认地址
              </label>
            </div>

            <p v-if="addressError" class="text-xs font-medium text-wine" role="alert">{{ addressError }}</p>
            <p v-else-if="addressSaved" class="text-xs text-jade" role="status">地址已保存。</p>
            <p v-else-if="addressDirty" class="text-xs text-ink-faint">有未保存的修改。</p>

            <div class="flex flex-wrap items-center gap-3">
              <AppButton type="submit" size="sm" :loading="addressSaving">保存地址</AppButton>
              <span v-if="addressSaving" class="text-xs text-ink-faint" role="status">正在保存</span>
              <AppButton size="sm" variant="ghost" @click="addressFormOpen = false">取消</AppButton>
            </div>
          </form>
        </SurfaceCard>

        <!-- 配送方式 -->
        <SurfaceCard>
          <h2 class="font-display text-lg">配送方式</h2>
          <p v-if="preview?.shipping_option" class="mt-3 text-sm text-ink-soft">
            {{ preview.shipping_option.name }} ·
            <span class="tabular-nums">{{ formatMoneyExact(preview.shipping_option.fee_cents, preview.shipping_option.currency) }}</span>
          </p>
          <p v-else class="mt-3 text-sm text-ink-muted">
            配送方式与运费由服务端按收货地区计算；未取得预览时无法提交订单。
          </p>
          <p v-if="preview?.shipping_option?.note" class="mt-2 text-xs text-ink-faint">
            {{ preview.shipping_option.note }}
          </p>
          <p class="mt-3 text-xs text-ink-faint">{{ site.commercePolicy.shipping_template_note }}</p>
          <p v-if="addressRequirement.crossBorder" class="mt-1 text-xs text-ink-faint">
            {{ addressRequirement.crossBorder }}
          </p>
        </SurfaceCard>

        <!-- 商品清单（快照预览） -->
        <SurfaceCard>
          <div class="flex flex-wrap items-center justify-between gap-3">
            <h2 class="font-display text-lg">商品清单</h2>
            <span v-if="preview" class="text-xs text-ink-faint">预览于 {{ formatDateTime(preview.expires_at) }} 前有效</span>
          </div>

          <div v-if="previewLoading" class="mt-4">
            <SkeletonBlock :lines="3" aria-label="正在获取结算预览" />
          </div>
          <p v-else-if="previewError" class="mt-4 text-sm text-wine" role="alert">
            {{ previewError }}
            <button type="button" class="link-underline ml-2" @click="refreshPreview()">重新获取预览</button>
          </p>
          <ul v-else-if="preview" class="mt-4 divide-y divide-line">
            <li v-for="line in preview.lines" :key="line.sku_id" class="grid gap-3 py-4 sm:grid-cols-[64px_1fr_auto]">
              <div class="overflow-hidden rounded-sm bg-canvas-sunk">
                <img
                  v-if="line.image"
                  :src="line.image.thumb ?? line.image.src"
                  :alt="line.image.alt || `${line.product_title} 商品图`"
                  :width="line.image.width"
                  :height="line.image.height"
                  loading="lazy"
                  decoding="async"
                  class="aspect-square size-full object-cover"
                />
                <span v-else class="flex aspect-square items-center justify-center text-xs text-ink-faint">无图</span>
              </div>
              <div class="min-w-0 text-sm">
                <p class="font-medium">{{ line.product_title }}</p>
                <p class="mt-1 text-xs text-ink-faint">{{ line.sku_name }}</p>
                <p v-if="line.availability !== 'available'" class="mt-1 text-xs text-wine">
                  {{ line.availability_note ?? "该商品当前不可结算，请返回购物车调整。" }}
                </p>
              </div>
              <div class="text-right text-sm">
                <p class="tabular-nums">{{ formatMoneyExact(line.line_total_cents, line.currency) }}</p>
                <p class="text-xs text-ink-faint tabular-nums">
                  {{ formatMoneyExact(line.unit_price_cents, line.currency) }} × {{ line.quantity }}
                </p>
              </div>
            </li>
          </ul>
          <p v-else class="mt-4 text-sm text-ink-muted">请选择收货地址以获取服务端结算预览。</p>
        </SurfaceCard>

        <!-- 星绒币抵扣（C15）：未开启或规则未配置时整块不出现 -->
        <SurfaceCard v-if="pointsAvailable && preview">
          <h2 class="font-display text-lg">{{ site.loyaltyRules.points_name }}抵扣</h2>
          <p class="mt-3 text-sm text-ink-muted">
            兑换率与抵扣上限由运营配置，这里只提交“想用多少”，能用多少、抵多少钱由服务端计算。
          </p>
          <dl class="mt-4 space-y-2 text-xs text-ink-muted">
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">兑换率</dt>
              <dd class="tabular-nums">
                每 1 元需要 {{ site.loyaltyRules.redemption_points_per_yuan }} {{ site.loyaltyRules.points_name }}
              </dd>
            </div>
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">单笔最低</dt>
              <dd class="tabular-nums">{{ site.loyaltyRules.redemption_min_points }} {{ site.loyaltyRules.points_name }}</dd>
            </div>
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">抵扣上限</dt>
              <dd class="tabular-nums">商品金额的 {{ site.loyaltyRules.redemption_max_ratio_percent }}%</dd>
            </div>
            <div v-if="(site.loyaltyRules.redemption_expiry_days ?? 0) > 0" class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">有效期</dt>
              <dd class="tabular-nums">{{ site.loyaltyRules.redemption_expiry_days }} 天</dd>
            </div>
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">退款时</dt>
              <dd>{{ site.loyaltyRules.redemption_restore_on_refund === false ? "已用积分不返还（按规则执行）" : "按原订单分摊返还已用积分" }}</dd>
            </div>
          </dl>

          <div class="mt-4 flex flex-wrap items-end gap-3">
            <label class="text-sm" for="checkout-redeem-points">
              <span class="text-ink-faint">使用数量</span>
              <input
                id="checkout-redeem-points"
                v-model="pointsInput"
                type="text"
                inputmode="numeric"
                autocomplete="off"
                class="mt-1 w-32 rounded-sm border border-line bg-surface px-3 py-2 text-sm tabular-nums"
                :placeholder="pointsQuote ? String(pointsQuote.max_points) : ''"
                :aria-describedby="pointsInputError ? 'checkout-redeem-error' : undefined"
              />
            </label>
            <AppButton size="sm" :disabled="!!pointsInputError" @click="applyPointsRedeem()">
              使用{{ site.loyaltyRules.points_name }}
            </AppButton>
            <AppButton v-if="redeemPoints > 0" size="sm" variant="quiet" @click="clearPointsRedeem()">
              不使用
            </AppButton>
          </div>
          <p v-if="pointsInputError" id="checkout-redeem-error" class="mt-2 text-xs text-wine" role="alert">
            {{ pointsInputError }}
          </p>
          <p v-else-if="pointsQuote" class="mt-2 text-xs text-ink-muted">
            可用 {{ formatPoints(pointsQuote.available_points) }}，本单最多可用
            <span class="tabular-nums">{{ formatPoints(pointsQuote.max_points) }}</span>（抵
            <span class="tabular-nums">{{ formatMoneyExact(pointsQuote.max_cents, currency) }}</span>）。
            <template v-if="pointsQuote.applied_points > 0">
              已按 <span class="tabular-nums">{{ formatPoints(pointsQuote.applied_points) }}</span> {{ site.loyaltyRules.points_name }} 抵扣
              <span class="tabular-nums">{{ formatMoneyExact(pointsQuote.applied_cents, currency) }}</span>。
            </template>
            <template v-if="pointsQuote.capped">请求数量超过上限或余额，已按最大可用量计算。</template>
            <template v-else-if="pointsQuote.rounded_points > 0">
              折算后只需 <span class="tabular-nums">{{ formatPoints(pointsQuote.applied_points) }}</span>，零头不会扣除。
            </template>
          </p>
          <p v-if="pointsQuote?.ratio_note" class="mt-1 text-xs text-ink-faint">
            {{ resolveText(pointsQuote.ratio_note, locale) }}
          </p>
        </SurfaceCard>

        <!-- 订单条款 -->
        <SurfaceCard>
          <h2 class="font-display text-lg">订单条款</h2>
          <p class="mt-3 text-sm text-ink-muted">
            当前条款版本：<span class="font-medium text-ink-soft">{{ preview?.terms_version ?? "待预览" }}</span>。
            条款内容与版本由运营发布，接受记录保存在订单里，之后不会回填修改。
          </p>
          <dl class="mt-4 space-y-2 text-xs text-ink-muted">
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">售后参照</dt>
              <dd>{{ addressRequirement.returns }}</dd>
            </div>
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">履约类型</dt>
              <dd>{{ fulfillmentTypes.join(" / ") || "—" }}</dd>
            </div>
          </dl>

          <label class="mt-4 flex items-start gap-3 text-sm text-ink-soft">
            <input v-model="accepted" type="checkbox" class="mt-1 size-4 accent-amber" />
            <span>
              我已阅读并接受当前版本的订单条款、售后条件与隐私政策，并确认收货地址与商品信息无误。
            </span>
          </label>
          <p v-if="!accepted" class="mt-3 rounded-sm bg-surface-muted px-3 py-2 text-xs text-ink-muted">
            未勾选条款时不能提交订单：订单会记录你接受的具体条款版本，作为履约与售后依据。
          </p>
        </SurfaceCard>
      </div>

      <!-- 右侧：付款摘要始终可见 -->
      <div class="space-y-4 lg:sticky lg:top-24">
        <MoneySummary
          v-if="preview"
          :goods-cents="preview.goods_cents"
          :discount-cents="preview.discount_cents"
          :points-discount-cents="preview.points_discount_cents"
          :points-redeemed="preview.points_redeemed"
          :points-name="site.loyaltyRules.points_name"
          :shipping-cents="preview.shipping_cents"
          :total-cents="preview.total_cents"
          :currency="currency"
          title="结算金额摘要"
          :note="`服务端预览，${expiresText} 前有效。提交订单时会再次校验价格与库存。`"
        />
        <SurfaceCard v-else>
          <h3 class="font-display text-base">结算金额摘要</h3>
          <p class="mt-3 text-sm text-ink-muted">尚未取得服务端预览，无法显示金额，也无法提交订单。</p>
        </SurfaceCard>

        <SurfaceCard v-if="preview">
          <PaymentCountdown
            :deadline="preview.expires_at"
            label="结算预览剩余时间"
            expired-hint="结算预览已过期，提交时会重新校验并可能返回新的价格。"
          />
        </SurfaceCard>

        <!-- 跨境：预估金额单列，并明确不计入应付合计 -->
        <SurfaceCard v-if="crossBorderNotice">
          <h3 class="font-display text-base">跨境配送与关税</h3>
          <dl class="mt-3 space-y-2 text-xs text-ink-muted">
            <div class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">配送范围</dt>
              <dd>该收货地址在中国大陆以外，按跨境订单处理。</dd>
            </div>
            <div v-if="crossBorderNotice.hasEstimate" class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">关税与进口税费预估</dt>
              <dd class="tabular-nums">{{ crossBorderNotice.estimateText }}</dd>
            </div>
            <div v-else class="flex flex-wrap gap-2">
              <dt class="text-ink-faint">关税与进口税费</dt>
              <dd>暂无预估金额：运营尚未配置预估比例，实际金额以清关时海关核定为准。</dd>
            </div>
          </dl>
          <p v-if="crossBorderNotice.note" class="mt-3 text-xs text-ink-faint">
            {{ crossBorderNotice.note }}
          </p>
          <p class="mt-2 text-xs text-ink-muted">
            预估金额不计入上方应付合计，也不由本站代收；实际关税、进口税费与偏远地区附加费由承运方或海关在清关时收取。
          </p>
        </SurfaceCard>

        <p v-if="submitError" class="rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
          {{ submitError }}
        </p>

        <AppButton
          block
          size="lg"
          :disabled="!canSubmit"
          :loading="submitting"
          :disabled-reason="submitBlockedReason"
          @click="submit()"
        >
          提交订单
        </AppButton>
        <p class="text-center text-xs text-ink-faint">
          提交后订单状态为待付款，款项与到期时间以服务端返回为准。防连点只避免重复点击，幂等保证由服务端按幂等键完成。
        </p>
        <AppButton block variant="secondary" to="/cart">返回购物车</AppButton>
      </div>
    </div>
  </div>
</template>
