<script setup lang="ts">
/**
 * 订单详情。
 *
 * 信息顺序（11 文档「订单」布局基线）：
 * 当前需要的动作 → 金额及支付记录 → 商品/合同 → 制作或物流 → 售后。
 *
 * 规则：
 * - 已核实收款金额不可手改；页面只读展示服务端记录。
 * - 支付尝试 `UNKNOWN`/`PENDING` 必须显示“结果确认中”，绝不能渲染为失败或已取消。
 * - 退款状态失败或未知时不能说钱已退。
 * - 取消、确认收货、售后、发票都是服务端状态机动作，前端不做本地状态推演。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import {
  cancelOrder,
  confirmOrderReceipt,
  createAfterSale,
  createInvoice,
  fetchOrder,
  type AfterSaleStatus,
  type OrderDetail,
  type OrderInstallment,
  type OrderPaymentAttempt,
  type OrderStatus,
  type RefundStatus,
} from "@/modules/commerce/api/commerce";
import MoneySummary from "@/modules/commerce/components/MoneySummary.vue";
import OrderStatusTimeline from "@/modules/commerce/components/OrderStatusTimeline.vue";
import PaymentCountdown from "@/modules/commerce/components/PaymentCountdown.vue";
import { useSiteStore } from "@/modules/content/stores/site";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
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

const route = useRoute();
const site = useSiteStore();
const toast = useToast();

const order = ref<OrderDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);
const sessionExpired = ref(false);
const actionError = ref<string | null>(null);
const acting = ref<string | null>(null);

const cancelDialogOpen = ref(false);
const cancelReason = ref("");
const cancelError = ref<string | null>(null);

const afterSaleOpen = ref(false);
const afterSaleForm = ref({ reason: "", description: "", assetIds: "" });
const afterSaleError = ref<string | null>(null);
const afterSaleSubmitting = ref(false);
const afterSaleKey = ref<string | null>(null);

const invoiceOpen = ref(false);
const invoiceForm = ref({ title: "", taxNo: "", email: "", note: "" });
const invoiceError = ref<string | null>(null);
const invoiceSubmitting = ref(false);
const invoiceKey = ref<string | null>(null);

const ORDER_STEPS = [
  { code: "PENDING_PAYMENT", label: "待付款（已预留库存）" },
  { code: "ACTIVE", label: "已付款，等待履约" },
  { code: "FULFILLING", label: "制作或备货中" },
  { code: "SHIPPED", label: "已发货" },
  { code: "COMPLETED", label: "已完成" },
];

const ORDER_TERMINAL = {
  code: "CANCELLED",
  label: "订单已取消",
  description:
    "取消后库存预留已释放。若渠道仍在途或在取消前已真实收款，服务端会记录为异常待处置，不会重新扣减已售出的库存。",
};

const INSTALLMENT_STATUS_LABEL: Record<string, string> = {
  DUE: "待付款",
  PAYING: "支付处理中",
  PAID: "已核实收款",
  CLOSING: "关闭核对中",
  CLOSED: "已关闭",
};

const ATTEMPT_STATUS_LABEL: Record<string, string> = {
  CREATED: "已创建，等待支付",
  PENDING: "支付处理中",
  UNKNOWN: "结果确认中",
  SUCCEEDED: "已确认收款",
  CLOSED: "已关闭",
  FAILED: "已失败",
};

const REFUND_STATUS_LABEL: Record<RefundStatus, string> = {
  REQUESTED: "退款已申请，等待审核",
  APPROVED: "退款已批准，等待执行",
  PROCESSING: "退款处理中，尚未确认到账",
  UNKNOWN: "退款结果确认中，尚未确认到账",
  SUCCEEDED: "退款已成功",
  FAILED: "退款失败",
  REJECTED: "退款被拒绝",
};

const AFTER_SALE_STATUS_LABEL: Record<AfterSaleStatus, string> = {
  OPEN: "已受理，等待客服核查",
  NEEDS_INFO: "需要补充信息",
  APPROVED: "已通过，等待寄回",
  RETURNING: "退货寄回中",
  RESOLVED: "已处理完成",
  REJECTED: "未通过",
};

const orderId = computed(() => String(route.params["id"] ?? ""));

function money(cents: number, currency: OrderDetail["currency"]): string {
  return formatMoneyExact(cents, currency);
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  sessionExpired.value = false;
  try {
    order.value = await fetchOrder(orderId.value);
  } catch (caught) {
    order.value = null;
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        error.value = "登录状态已过期，请重新登录后查看订单。";
      } else if (caught.status === 403) {
        forbidden.value = true;
        error.value = "该订单不属于当前账号，或你无权查看。";
      } else if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") {
        notFound.value = true;
        error.value = "订单不存在，或已被删除访问权限。";
      } else {
        error.value = caught.message || "订单加载失败。";
      }
    } else {
      error.value = "订单加载失败，请稍后重试。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await Promise.allSettled([site.load()]);
  await load();
});

watch(orderId, () => void load());

const isPendingPayment = computed(() => order.value?.status === "PENDING_PAYMENT");
const canConfirmReceipt = computed(
  () => order.value !== null && (order.value.status === "SHIPPED" || order.value.status === "FULFILLING"),
);
const canRequestAfterSale = computed(
  () =>
    order.value !== null &&
    ["ACTIVE", "FULFILLING", "SHIPPED", "COMPLETED"].includes(order.value.status),
);

const dueInstallments = computed(
  () => order.value?.installments.filter((item) => item.status === "DUE" || item.status === "PAYING") ?? [],
);

const payTarget = computed<OrderInstallment | null>(
  () => dueInstallments.value[0] ?? order.value?.installments[0] ?? null,
);

const hasUnknownPayment = computed(
  () =>
    order.value?.payment_attempts.some(
      (attempt) => attempt.status === "UNKNOWN" || attempt.status === "PENDING",
    ) ?? false,
);

/** 当前最需要用户做的事 */
const nextAction = computed(() => {
  const current = order.value;
  if (!current) return null;
  if (current.status === "PENDING_PAYMENT") {
    return dueInstallments.value.length > 0
      ? { title: "完成付款", description: "款项未付清前不会进入履约；付款结果以服务端查单为准。" }
      : { title: "确认付款结果", description: "没有待付款项，但订单仍为待付款，请重新查询支付结果。" };
  }
  if (hasUnknownPayment.value) {
    return { title: "等待支付结果确认", description: "存在结果未确认的支付尝试，服务端会继续查单，请勿重复付款。" };
  }
  if (current.status === "SHIPPED") {
    return { title: "确认收货", description: "收到并验货后再确认收货；确认后订单进入已完成。" };
  }
  if (current.status === "FULFILLING") {
    return { title: "等待发货或阶段确认", description: "制作或备货正在进行，进度以服务端记录为准。" };
  }
  if (current.status === "COMPLETED") {
    return { title: "订单已完成", description: "如需退货或维修，可在下方进入售后流程。" };
  }
  if (current.status === "CANCELLED") {
    return { title: "订单已取消", description: "取消后不再需要付款。如有在途收款，服务端会按异常资金流程处理。" };
  }
  return { title: "等待履约", description: "订单已核实收款，等待进入下一步。" };
});

async function runAction(
  name: string,
  action: () => Promise<OrderDetail>,
  successMessage: string,
): Promise<void> {
  acting.value = name;
  actionError.value = null;
  try {
    order.value = await action();
    toast.success(successMessage);
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        actionError.value = "登录状态已过期，请重新登录后重试。";
      } else if (caught.code === "STATE_CONFLICT") {
        actionError.value = "订单状态已变化，页面已刷新为服务端最新状态。";
        await load();
      } else if (caught.code === "REFUND_LIMIT_EXCEEDED") {
        actionError.value = "可退额度不足：原收款金额已扣除成功退款与处理中的预留退款。";
      } else if (caught.isPaymentUnknown) {
        actionError.value = "支付结果尚未确认，服务端会继续查单；这不等于付款失败。";
      } else {
        actionError.value = caught.message || "操作失败，请稍后重试。";
      }
    } else {
      actionError.value = "操作失败，请稍后重试。";
    }
  } finally {
    acting.value = null;
  }
}

async function submitCancel(): Promise<void> {
  cancelError.value = null;
  if (cancelReason.value.trim().length === 0) {
    cancelError.value = "请填写取消原因，便于服务端记录并核对在途付款。";
    return;
  }
  await runAction("cancel", () => cancelOrder(orderId.value, cancelReason.value.trim()), "订单已取消");
  cancelDialogOpen.value = false;
  cancelReason.value = "";
}

async function submitAfterSale(): Promise<void> {
  afterSaleError.value = null;
  if (!afterSaleForm.value.reason.trim()) {
    afterSaleError.value = "请选择或填写售后原因。";
    return;
  }
  if (afterSaleForm.value.description.trim().length < 5) {
    afterSaleError.value = "请补充至少 5 个字的说明，便于客服核查。";
    return;
  }
  if (afterSaleKey.value === null) afterSaleKey.value = newIdempotencyKey();

  afterSaleSubmitting.value = true;
  try {
    const result = await createAfterSale({
      orderId: orderId.value,
      reason: afterSaleForm.value.reason.trim(),
      description: afterSaleForm.value.description.trim(),
      assetIds: afterSaleForm.value.assetIds
        .split(",")
        .map((value) => value.trim())
        .filter((value) => value.length > 0),
      idempotencyKey: afterSaleKey.value,
    });
    toast.success("售后申请已提交", `售后单状态：${AFTER_SALE_STATUS_LABEL[result.status] ?? result.status}`);
    afterSaleOpen.value = false;
    afterSaleForm.value = { reason: "", description: "", assetIds: "" };
    afterSaleKey.value = null;
    await load();
  } catch (caught) {
    if (caught instanceof ApiError) {
      afterSaleError.value = caught.needsLogin
        ? "登录状态已过期，请重新登录后重试。"
        : caught.message || "售后申请提交失败。";
    } else {
      afterSaleError.value = "售后申请提交失败，请稍后重试。";
    }
  } finally {
    afterSaleSubmitting.value = false;
  }
}

async function submitInvoice(): Promise<void> {
  invoiceError.value = null;
  if (!invoiceForm.value.title.trim()) {
    invoiceError.value = "请填写发票抬头。";
    return;
  }
  if (invoiceKey.value === null) invoiceKey.value = newIdempotencyKey();

  invoiceSubmitting.value = true;
  try {
    await createInvoice({
      orderId: orderId.value,
      title: invoiceForm.value.title.trim(),
      taxNo: invoiceForm.value.taxNo.trim(),
      email: invoiceForm.value.email.trim(),
      note: invoiceForm.value.note.trim(),
      idempotencyKey: invoiceKey.value,
    });
    toast.success("开票申请已提交", "开票进度以服务端记录为准，开具后会显示在订单详情。");
    invoiceOpen.value = false;
    invoiceForm.value = { title: "", taxNo: "", email: "", note: "" };
    invoiceKey.value = null;
    await load();
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.code === "FEATURE_DISABLED") {
        invoiceError.value = "当前未启用线上开票申请，请联系客服。";
      } else {
        invoiceError.value = caught.needsLogin
          ? "登录状态已过期，请重新登录后重试。"
          : caught.message || "开票申请提交失败。";
      }
    } else {
      invoiceError.value = "开票申请提交失败，请稍后重试。";
    }
  } finally {
    invoiceSubmitting.value = false;
  }
}

function attemptTone(status: OrderPaymentAttempt["status"]): "jade" | "amber" | "wine" | "neutral" {
  if (status === "SUCCEEDED") return "jade";
  if (status === "UNKNOWN" || status === "PENDING") return "amber";
  if (status === "FAILED") return "wine";
  return "neutral";
}

function statusLabel(status: OrderStatus): string {
  const found = ORDER_STEPS.find((step) => step.code === status);
  if (found) return found.label;
  if (status === "CANCELLED") return "已取消";
  return status;
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '我的订单', to: '/account/orders' },
        { label: '订单详情' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="6" aria-label="正在读取订单详情" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : notFound ? 'not-found' : 'error'"
      :title="
        forbidden
          ? '无法查看该订单'
          : notFound
            ? '订单不存在'
            : sessionExpired
              ? '登录已过期'
              : '订单加载失败'
      "
      :description="error"
    >
      <AppButton v-if="sessionExpired" size="sm" to="/login?returnTo=/account/orders">重新登录</AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/orders">返回订单列表</AppButton>
    </EmptyState>

    <template v-else-if="order">
      <SectionHeading
        class="mt-6"
        :level="1"
        eyebrow="Order"
        :title="`订单 ${order.order_no}`"
        :description="`下单时间 ${formatDateTime(order.created_at)}。状态与金额来自服务端记录，页面不做本地推演。`"
      >
        <template #actions>
          <AppButton size="sm" variant="secondary" to="/account/orders">全部订单</AppButton>
          <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
        </template>
      </SectionHeading>

      <div class="mt-8 grid gap-8 lg:grid-cols-[1.5fr_1fr] lg:items-start">
        <div class="space-y-6">
          <!-- 1. 当前需要的动作 -->
          <SurfaceCard v-if="nextAction" class="!border-amber !bg-amber-soft">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg text-amber-strong">{{ nextAction.title }}</h2>
              <StatusBadge tone="amber" :label="statusLabel(order.status)" />
            </div>
            <p class="mt-2 text-sm text-amber-strong">{{ nextAction.description }}</p>

            <div v-if="isPendingPayment && order.expires_at" class="mt-4 rounded-sm bg-surface p-3">
              <PaymentCountdown :deadline="order.expires_at" label="付款剩余时间" />
            </div>

            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton
                v-if="isPendingPayment && payTarget"
                size="sm"
                :to="`/pay/${payTarget.id}`"
              >
                去付款
              </AppButton>
              <AppButton
                v-if="canConfirmReceipt"
                size="sm"
                :loading="acting === 'receipt'"
                @click="runAction('receipt', () => confirmOrderReceipt(orderId), '已确认收货')"
              >
                确认收货
              </AppButton>
              <AppButton
                v-if="isPendingPayment"
                size="sm"
                variant="secondary"
                @click="cancelDialogOpen = !cancelDialogOpen"
              >
                {{ cancelDialogOpen ? "收起取消表单" : "取消订单" }}
              </AppButton>
            </div>

            <!-- 取消订单：显示具体对象与后果 -->
            <form
              v-if="cancelDialogOpen"
              class="mt-4 space-y-3 border-t border-amber/40 bg-surface p-4"
              @submit.prevent="submitCancel"
            >
              <p class="text-sm text-ink-soft">
                即将取消订单 <span class="font-medium">{{ order.order_no }}</span>，
                应付金额 {{ money(order.total_cents, order.currency) }}。取消后库存预留会释放，
                若渠道存在在途付款，服务端会先关闭或查询渠道，确认没有在途成功付款后再取消。
              </p>
              <FieldRow label="取消原因" required for="cancel-reason" hint="会记录在订单里，便于核对在途付款。">
                <input id="cancel-reason" v-model="cancelReason" class="field-base" type="text" maxlength="120" />
              </FieldRow>
              <p v-if="cancelError" class="text-xs font-medium text-wine" role="alert">{{ cancelError }}</p>
              <div class="flex flex-wrap gap-3">
                <AppButton type="submit" size="sm" variant="danger" :loading="acting === 'cancel'">
                  确认取消订单
                </AppButton>
                <AppButton size="sm" variant="ghost" @click="cancelDialogOpen = false">再想想</AppButton>
              </div>
            </form>
          </SurfaceCard>

          <p v-if="actionError" class="rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
            {{ actionError }}
          </p>

          <!-- 2. 金额及支付记录 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">金额及支付记录</h2>
            <p class="mt-2 text-xs text-ink-faint">
              已核实收款只统计已分配到该应付项的收款，页面不可手动修改；金额单位为分，显示保留两位小数。
            </p>

            <ul class="mt-4 divide-y divide-line">
              <li v-for="item in order.installments" :key="item.id" class="flex flex-wrap items-center justify-between gap-3 py-3">
                <div class="min-w-0 text-sm">
                  <p class="font-medium">{{ item.title }}</p>
                  <p class="mt-0.5 text-xs text-ink-faint">
                    已核实收款
                    <span class="tabular-nums">{{ money(item.paid_cents, item.currency) }}</span>
                    <span v-if="item.due_at"> · 到期 {{ formatDateTime(item.due_at) }}</span>
                  </p>
                </div>
                <div class="flex items-center gap-3">
                  <p class="tabular-nums text-sm">{{ money(item.amount_cents, item.currency) }}</p>
                  <StatusBadge
                    :tone="item.status === 'PAID' ? 'jade' : item.status === 'DUE' ? 'amber' : 'neutral'"
                    :label="INSTALLMENT_STATUS_LABEL[item.status] ?? item.status"
                  />
                  <AppButton
                    v-if="(item.status === 'DUE' || item.status === 'PAYING') && item.active_payment_attempt_id"
                    size="sm"
                    variant="secondary"
                    :to="`/pay/${item.id}`"
                  >
                    继续付款
                  </AppButton>
                </div>
              </li>
              <li v-if="order.installments.length === 0" class="py-3 text-sm text-ink-muted">
                服务端未返回应付项记录，请联系客服核对。
              </li>
            </ul>

            <h3 class="mt-6 font-display text-base">支付尝试</h3>
            <ul v-if="order.payment_attempts.length" class="mt-3 space-y-3">
              <li v-for="attempt in order.payment_attempts" :key="attempt.id" class="rounded-sm border border-line p-3 text-sm">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <p class="font-medium">
                    {{ attempt.provider_label }}
                    <span v-if="attempt.scene" class="text-xs text-ink-faint">（{{ attempt.scene }}）</span>
                  </p>
                  <StatusBadge :tone="attemptTone(attempt.status)" :label="ATTEMPT_STATUS_LABEL[attempt.status] ?? attempt.status" />
                </div>
                <dl class="mt-2 grid gap-1 text-xs text-ink-muted sm:grid-cols-2">
                  <div class="flex gap-2">
                    <dt class="text-ink-faint">金额</dt>
                    <dd class="tabular-nums">{{ money(attempt.amount_cents, attempt.currency) }}</dd>
                  </div>
                  <div class="flex gap-2">
                    <dt class="text-ink-faint">创建</dt>
                    <dd class="tabular-nums">{{ formatDateTime(attempt.created_at) }}</dd>
                  </div>
                  <div class="flex gap-2">
                    <dt class="text-ink-faint">查单确认</dt>
                    <dd class="tabular-nums">{{ attempt.verified_at ? formatDateTime(attempt.verified_at) : "尚未确认" }}</dd>
                  </div>
                  <div v-if="attempt.allocation_status" class="flex gap-2">
                    <dt class="text-ink-faint">款项分配</dt>
                    <dd>
                      {{
                        attempt.allocation_status === "APPLIED"
                          ? "已分配到应付项"
                          : attempt.allocation_status === "OVERPAYMENT"
                            ? "多付，待退款流程处理"
                            : "待人工核对"
                      }}
                    </dd>
                  </div>
                </dl>
                <p
                  v-if="attempt.status === 'UNKNOWN' || attempt.status === 'PENDING'"
                  class="mt-2 rounded-sm bg-amber-soft px-3 py-2 text-xs text-amber-strong"
                  role="status"
                >
                  结果确认中：服务端会继续查单核实，这不等于付款失败。请不要重复创建新的支付单。
                </p>
                <p v-else-if="attempt.note" class="mt-2 text-xs text-ink-muted">{{ attempt.note }}</p>
              </li>
            </ul>
            <p v-else class="mt-3 text-sm text-ink-muted">还没有支付尝试记录。</p>
          </SurfaceCard>

          <!-- 3. 商品/合同快照 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">商品与合同快照</h2>
            <ul class="mt-4 divide-y divide-line">
              <li v-for="item in order.items" :key="item.id" class="grid gap-3 py-4 sm:grid-cols-[64px_1fr_auto]">
                <div class="overflow-hidden rounded-sm bg-canvas-sunk">
                  <img
                    v-if="item.image"
                    :src="item.image.thumb ?? item.image.src"
                    :alt="item.image.alt || `${item.product_title} 商品图`"
                    :width="item.image.width"
                    :height="item.image.height"
                    loading="lazy"
                    decoding="async"
                    class="aspect-square size-full object-cover"
                  />
                  <span v-else class="flex aspect-square items-center justify-center text-xs text-ink-faint">无图</span>
                </div>
                <div class="min-w-0 text-sm">
                  <RouterLink :to="`/shop/${item.product_slug}`" class="font-medium hover:text-amber-strong">
                    {{ item.product_title }}
                  </RouterLink>
                  <p class="mt-1 text-xs text-ink-faint">{{ item.sku_name }}</p>
                  <p v-if="item.snapshot_note" class="mt-1 text-xs text-ink-muted">{{ item.snapshot_note }}</p>
                </div>
                <div class="text-right text-sm">
                  <p class="tabular-nums">{{ money(item.line_total_cents, item.currency) }}</p>
                  <p class="text-xs text-ink-faint tabular-nums">
                    {{ money(item.unit_price_cents, item.currency) }} × {{ item.quantity }}
                  </p>
                </div>
              </li>
            </ul>

            <dl class="mt-4 space-y-2 border-t border-line pt-4 text-xs text-ink-muted">
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">条款版本</dt>
                <dd>{{ order.terms_version ?? "—" }}</dd>
              </div>
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">接受时间</dt>
                <dd class="tabular-nums">{{ order.terms_accepted_at ? formatDateTime(order.terms_accepted_at) : "—" }}</dd>
              </div>
              <div v-if="order.address" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">收货地址快照</dt>
                <dd>
                  {{ order.address.recipient }} · {{ order.address.phone_masked }} · {{ order.address.region }}
                  {{ order.address.detail }}
                </dd>
              </div>
            </dl>

            <div v-if="order.commission_project_id" class="mt-4">
              <AppButton size="sm" variant="secondary" :to="`/account/commissions/${order.commission_project_id}`">
                查看制作进度
              </AppButton>
            </div>
          </SurfaceCard>

          <!-- 4. 制作或物流 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">制作与物流</h2>
            <div v-if="order.commission_project_id" class="mt-3 text-sm text-ink-muted">
              该订单关联制作项目，量体、阶段确认与变更请求在
              <RouterLink class="link-underline" :to="`/account/commissions/${order.commission_project_id}`">
                制作进度
              </RouterLink>
              页面处理。
            </div>

            <ul v-if="order.shipments.length" class="mt-4 space-y-3">
              <li v-for="shipment in order.shipments" :key="shipment.id" class="rounded-sm border border-line p-3 text-sm">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <p class="font-medium">{{ shipment.carrier }}</p>
                  <StatusBadge tone="neutral" :label="shipment.status" />
                </div>
                <p class="mt-1 text-xs text-ink-muted tabular-nums">运单号 {{ shipment.tracking_no }}</p>
                <p class="mt-1 text-xs text-ink-faint">
                  发货 {{ shipment.shipped_at ? formatDateTime(shipment.shipped_at) : "—" }} ·
                  签收 {{ shipment.delivered_at ? formatDateTime(shipment.delivered_at) : "未记录" }}
                </p>
                <p v-if="shipment.note" class="mt-1 text-xs text-ink-muted">{{ shipment.note }}</p>
              </li>
            </ul>
            <p v-else class="mt-3 text-sm text-ink-muted">还没有物流记录。发货后运单号会显示在这里。</p>
          </SurfaceCard>

          <!-- 5. 售后 -->
          <SurfaceCard>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">售后与退款</h2>
              <AppButton
                v-if="canRequestAfterSale"
                size="sm"
                variant="secondary"
                @click="afterSaleOpen = !afterSaleOpen"
              >
                {{ afterSaleOpen ? "收起申请表单" : "申请售后" }}
              </AppButton>
            </div>

            <dl class="mt-3 space-y-2 text-xs text-ink-muted">
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">现货售后参照</dt>
                <dd>{{ site.commercePolicy.returns_reference }}</dd>
              </div>
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">定制售后说明</dt>
                <dd>
                  定制商品的特殊退货条件不消除质量瑕疵责任；具体条件以订单条款与站点发布的规则文本为准。
                </dd>
              </div>
              <div v-if="order.cross_border" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">跨境订单</dt>
                <dd>
                  下单时按跨境处理；关税与进口税费
                  <template v-if="order.duty_estimate_cents !== null && order.duty_estimate_cents !== undefined">
                    预估 <span class="tabular-nums">{{ money(order.duty_estimate_cents, order.currency) }}</span>，
                    不计入本单应付金额，
                  </template>
                  以清关时海关核定为准。
                </dd>
              </div>
              <div v-if="order.duty_note" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">跨境说明</dt>
                <dd>{{ order.duty_note }}</dd>
              </div>
              <div v-else-if="site.commercePolicy.cross_border_note" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">跨境说明</dt>
                <dd>{{ site.commercePolicy.cross_border_note }}</dd>
              </div>
            </dl>

            <ul v-if="order.after_sales.length" class="mt-4 space-y-2">
              <li v-for="item in order.after_sales" :key="item.id">
                <RouterLink
                  :to="`/account/after-sales/${item.id}`"
                  class="flex flex-wrap items-center justify-between gap-3 rounded-sm border border-line p-3 text-sm hover:border-line-strong"
                >
                  <span>
                    <span class="font-medium">{{ item.reason }}</span>
                    <span class="mt-0.5 block text-xs text-ink-faint tabular-nums">
                      提交于 {{ formatDateTime(item.created_at) }}
                    </span>
                  </span>
                  <StatusBadge tone="neutral" :label="AFTER_SALE_STATUS_LABEL[item.status] ?? item.status" />
                </RouterLink>
              </li>
            </ul>
            <p v-else class="mt-3 text-sm text-ink-muted">还没有售后申请。</p>

            <div v-if="order.refund_summary" class="mt-4 rounded-sm bg-surface-muted p-3 text-sm">
              <p class="font-medium">
                退款状态：{{ REFUND_STATUS_LABEL[order.refund_summary.status] ?? order.refund_summary.status }}
              </p>
              <p class="mt-1 text-xs text-ink-muted tabular-nums">
                已成功退款 {{ money(order.refund_summary.refunded_cents, order.refund_summary.currency) }}
              </p>
              <p
                v-if="['UNKNOWN', 'PROCESSING', 'REQUESTED'].includes(order.refund_summary.status)"
                class="mt-1 text-xs text-ink-muted"
              >
                退款尚未确认到账，不计为已经退钱；服务端会继续查单或等待渠道结果。
              </p>
            </div>

            <form v-if="afterSaleOpen" class="mt-4 space-y-4 border-t border-line pt-4" @submit.prevent="submitAfterSale">
              <FieldRow label="售后原因" required for="after-sale-reason" hint="例如：收到商品破损 / 尺码不符 / 需要维修">
                <input id="after-sale-reason" v-model="afterSaleForm.reason" class="field-base" type="text" maxlength="80" />
              </FieldRow>
              <FieldRow
                label="情况说明"
                required
                for="after-sale-description"
                hint="请描述问题现象与期望处理方式，至少 5 个字。"
              >
                <textarea
                  id="after-sale-description"
                  v-model="afterSaleForm.description"
                  class="field-base min-h-[120px]"
                  maxlength="1000"
                />
              </FieldRow>
              <FieldRow
                label="图片附件 ID"
                for="after-sale-assets"
                hint="选填。先通过上传接口上传问题照片，再填写逗号分隔的附件 ID；未上传的图片不会提交。"
              >
                <input id="after-sale-assets" v-model="afterSaleForm.assetIds" class="field-base" type="text" />
              </FieldRow>
              <p v-if="afterSaleError" class="text-xs font-medium text-wine" role="alert">{{ afterSaleError }}</p>
              <div class="flex flex-wrap gap-3">
                <AppButton type="submit" size="sm" :loading="afterSaleSubmitting">提交售后申请</AppButton>
                <AppButton size="sm" variant="ghost" @click="afterSaleOpen = false">取消</AppButton>
              </div>
              <p class="text-xs text-ink-faint">
                提交后会生成售后单，客服核查与退款审批分别进行；退款是否成功以渠道查证结果为准。
              </p>
            </form>
          </SurfaceCard>

          <!-- 发票 -->
          <SurfaceCard>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">发票</h2>
              <AppButton
                v-if="site.commercePolicy.invoice_enabled"
                size="sm"
                variant="secondary"
                @click="invoiceOpen = !invoiceOpen"
              >
                {{ invoiceOpen ? "收起开票表单" : "申请开票" }}
              </AppButton>
            </div>

            <p v-if="!site.commercePolicy.invoice_enabled" class="mt-3 text-sm text-ink-muted">
              当前未启用线上开票，如需发票请联系 {{ site.brand.support_email }}。
            </p>

            <ul v-if="order.invoices.length" class="mt-4 space-y-2">
              <li v-for="invoice in order.invoices" :key="invoice.id" class="rounded-sm border border-line p-3 text-sm">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <p class="font-medium">{{ invoice.title }}</p>
                  <StatusBadge tone="neutral" :label="invoice.status" />
                </div>
                <p class="mt-1 text-xs text-ink-muted tabular-nums">
                  金额 {{ money(invoice.amount_cents, invoice.currency) }} · 申请于
                  {{ formatDateTime(invoice.created_at) }}
                </p>
                <p v-if="invoice.tax_no_masked" class="mt-1 text-xs text-ink-faint tabular-nums">
                  税号 {{ invoice.tax_no_masked }}
                </p>
                <p v-if="invoice.note" class="mt-1 text-xs text-ink-muted">{{ invoice.note }}</p>
              </li>
            </ul>
            <p v-else-if="site.commercePolicy.invoice_enabled" class="mt-3 text-sm text-ink-muted">
              还没有开票申请。
            </p>

            <form v-if="invoiceOpen" class="mt-4 space-y-4 border-t border-line pt-4" @submit.prevent="submitInvoice">
              <FieldRow label="发票抬头" required for="invoice-title">
                <input id="invoice-title" v-model="invoiceForm.title" class="field-base" type="text" maxlength="80" />
              </FieldRow>
              <div class="grid gap-4 sm:grid-cols-2">
                <FieldRow label="纳税人识别号" for="invoice-tax" hint="企业抬头时填写">
                  <input id="invoice-tax" v-model="invoiceForm.taxNo" class="field-base" type="text" maxlength="40" />
                </FieldRow>
                <FieldRow label="接收邮箱" for="invoice-email" hint="发票文件发送地址">
                  <input id="invoice-email" v-model="invoiceForm.email" class="field-base" type="email" autocomplete="email" />
                </FieldRow>
              </div>
              <FieldRow label="备注" for="invoice-note" hint="选填">
                <input id="invoice-note" v-model="invoiceForm.note" class="field-base" type="text" maxlength="160" />
              </FieldRow>
              <p v-if="invoiceError" class="text-xs font-medium text-wine" role="alert">{{ invoiceError }}</p>
              <div class="flex flex-wrap gap-3">
                <AppButton type="submit" size="sm" :loading="invoiceSubmitting">提交开票申请</AppButton>
                <AppButton size="sm" variant="ghost" @click="invoiceOpen = false">取消</AppButton>
              </div>
              <p class="text-xs text-ink-faint">开票金额以已核实收款为准，未付款部分不计入。</p>
            </form>
          </SurfaceCard>

          <p v-if="order.notes" class="rounded-sm border border-line bg-surface-muted px-3 py-2 text-xs text-ink-muted">
            订单备注：{{ order.notes }}
          </p>
        </div>

        <!-- 右侧：金额摘要与状态时间线 -->
        <div class="space-y-4 lg:sticky lg:top-24">
          <MoneySummary
            :total-cents="order.total_cents"
            :currency="order.currency"
            :paid-cents="order.paid_cents"
            :refunded-cents="order.refunded_cents"
            title="订单金额"
            total-label="订单应付合计"
            :note="`订单包含 ${order.item_count} 件商品。已核实收款与退款金额不可手动修改，均以服务端记录为准。`"
          />

          <SurfaceCard>
            <OrderStatusTimeline
              :status="order.status"
              :steps="ORDER_STEPS"
              :terminal="ORDER_TERMINAL"
              title="履约进度"
              :note="
                order.status === 'CANCELLED'
                  ? order.cancel_reason
                    ? `取消原因：${order.cancel_reason}（${formatDateTime(order.cancelled_at)}）`
                    : '取消原因由服务端记录。'
                  : '退款汇总与履约状态分开记录：即使发生过退款，订单仍保留曾经完成的交付记录。'
              "
            />
          </SurfaceCard>

          <SurfaceCard>
            <h3 class="font-display text-base">需要帮助？</h3>
            <p class="mt-2 text-sm text-ink-muted">
              客服邮箱 {{ site.brand.support_email }}，工作时段 {{ site.brand.service_hours }}。
              付款与退款问题请提供订单号 {{ order.order_no }}。
            </p>
            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton size="sm" variant="secondary" to="/account/after-sales">我的售后</AppButton>
              <AppButton size="sm" variant="ghost" to="/contact">联系客服</AppButton>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </template>
  </div>
</template>
