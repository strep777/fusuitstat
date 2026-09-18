<script setup lang="ts">
/**
 * 支付页 `/pay/:id`（`:id` 为应付项 installment id）。
 *
 * 规则来源：`文档/08_商城订单支付与退款.md` 与 `文档/06_API接口契约.md`「支付」小节。
 * - 成功状态只能由已验签通知或服务端主动查单确认；返回页、用户截图、前端回调都不入账。
 * - 因此页面明确写出：不凭支付返回页认定付款成功，以服务端查单结果为准。
 * - 轮询退避：初始 2 秒，逐步退避到最大 15 秒；`document.hidden` 时暂停；到期提示
 *   “结果确认中／重新查询”，绝不把超时渲染成已取消。
 * - 渠道可用性由服务端 `PAYMENT_PROVIDER_DISABLED` 决定；不可用时说明，不生成伪成功页。
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { fetchOrder, fetchOrders, type OrderDetail, type OrderInstallment } from "@/modules/commerce/api/commerce";
import PaymentCountdown from "@/modules/commerce/components/PaymentCountdown.vue";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  PAYMENT_PROVIDERS,
  PAYMENT_POLL_INITIAL_MS,
  createPaymentAttempt,
  fetchPaymentAttempt,
  newPaymentIdempotencyKey,
  nextPaymentPollDelay,
  type PaymentAttemptStatusView,
  type PaymentAttempt,
  type PaymentProvider,
} from "@/modules/payments/api/payments";
import { formatDateTime, formatMoneyExact, isExpired } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  SkeletonBlock,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";

const route = useRoute();
const router = useRouter();
const site = useSiteStore();
const toast = useToast();

const order = ref<OrderDetail | null>(null);
const installment = ref<OrderInstallment | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);
const sessionExpired = ref(false);

const selectedProvider = ref<PaymentProvider>("wechat");
const attempt = ref<PaymentAttempt | null>(null);
const attemptStatus = ref<PaymentAttemptStatusView["status"] | null>(null);
const verifiedAt = ref<string | null>(null);
const creating = ref(false);
const createError = ref<string | null>(null);
const disabledProviders = ref<Record<string, string>>({});
const unknownNotice = ref<string | null>(null);

const polling = ref(false);
const querying = ref(false);
const lastCheckedAt = ref<string | null>(null);
const pollDelay = ref(PAYMENT_POLL_INITIAL_MS);
const pollStoppedReason = ref<string | null>(null);

const installmentId = computed(() => String(route.params["id"] ?? ""));

const amountCents = computed(() => installment.value?.amount_cents ?? order.value?.total_cents ?? 0);
const currency = computed(() => installment.value?.currency ?? order.value?.currency ?? "CNY");
const paymentDeadline = computed(
  () => attempt.value?.expires_at ?? installment.value?.due_at ?? order.value?.expires_at ?? null,
);

const statusLabel = computed(() => {
  const status = attemptStatus.value;
  if (status === null) return "尚未创建支付";
  switch (status) {
    case "SUCCEEDED":
      return "已确认收款";
    case "PENDING":
    case "UNKNOWN":
      return "结果确认中";
    case "CLOSED":
      return "支付单已关闭";
    case "FAILED":
      return "渠道返回失败";
    default:
      return "已创建，等待付款";
  }
});

const statusTone = computed<"jade" | "amber" | "wine" | "neutral">(() => {
  if (attemptStatus.value === "SUCCEEDED") return "jade";
  if (attemptStatus.value === "PENDING" || attemptStatus.value === "UNKNOWN") return "amber";
  if (attemptStatus.value === "FAILED") return "wine";
  return "neutral";
});

const resultUnknown = computed(
  () => attemptStatus.value === "PENDING" || attemptStatus.value === "UNKNOWN",
);

const providerDisabledReason = computed(() => disabledProviders.value[selectedProvider.value] ?? "");

const allProvidersDisabled = computed(() =>
  PAYMENT_PROVIDERS.every((option) => Boolean(disabledProviders.value[option.provider])),
);

let pollTimer: ReturnType<typeof setTimeout> | null = null;
let queryController: AbortController | null = null;
let disposed = false;

function clearPoll(): void {
  if (pollTimer) clearTimeout(pollTimer);
  pollTimer = null;
  polling.value = false;
}

/**
 * 安排下一次查单。退避序列由 `nextPaymentPollDelay` 给出（2s → 最大 15s）。
 * 页面不可见时暂停，等 `visibilitychange` 恢复后再继续。
 */
function schedulePoll(delayMs: number): void {
  clearPoll();
  if (disposed) return;
  if (typeof document !== "undefined" && document.hidden) {
    polling.value = false;
    return;
  }
  if (isExpired(paymentDeadline.value)) {
    polling.value = false;
    pollStoppedReason.value = "付款窗口已到期，自动查单已暂停。你可以手动重新查询，结果以服务端为准。";
    return;
  }
  polling.value = true;
  pollTimer = setTimeout(() => void query(), delayMs);
}

async function query(manual = false): Promise<void> {
  if (!attempt.value) return;
  if (querying.value) return;

  querying.value = true;
  queryController = new AbortController();
  try {
    const view = await fetchPaymentAttempt(attempt.value.payment_attempt_id, {
      signal: queryController.signal,
    });
    attemptStatus.value = view.status;
    verifiedAt.value = view.verified_at;
    unknownNotice.value = null;
    lastCheckedAt.value = new Date().toISOString();

    if (view.status === "SUCCEEDED") {
      clearPoll();
      pollStoppedReason.value = null;
      toast.success("已确认收款", "服务端查单确认到账，正在跳转到订单详情。");
      await router.push({ name: "account-order-detail", params: { id: view.order_id } });
      return;
    }

    if (view.status === "PENDING" || view.status === "UNKNOWN" || view.status === "CREATED") {
      // 未确认：继续退避查单，绝不渲染为失败或已取消
      pollStoppedReason.value = null;
      // 第一次自动查单保持 2 秒，之后按 1.6 倍退避到最长 15 秒
      pollDelay.value = manual ? PAYMENT_POLL_INITIAL_MS : nextPaymentPollDelay(pollDelay.value);
      schedulePoll(pollDelay.value);
      return;
    }

    // CLOSED / FAILED：停止自动查单，但支付尝试失败不等于订单取消
    clearPoll();
    pollStoppedReason.value =
      view.status === "FAILED"
        ? "该支付尝试渠道返回失败。订单是否取消以订单详情为准，可以重新发起支付。"
        : "该支付单已关闭。订单状态以订单详情为准，可以按需重新发起支付。";
  } catch (caught) {
    // 查单失败不等于付款失败：标记为结果确认中并继续尝试
    unknownNotice.value =
      caught instanceof ApiError && caught.code === "FEATURE_DISABLED"
        ? "服务端暂时未启用查单，请稍后重试或联系客服核对。"
        : "暂时无法取得支付结果，已标记为“结果确认中”。这不是付款失败，稍后会自动重试。";
    pollDelay.value = nextPaymentPollDelay(pollDelay.value);
    schedulePoll(pollDelay.value);
  } finally {
    querying.value = false;
    queryController = null;
  }
}

function onVisibilityChange(): void {
  if (document.hidden) {
    clearPoll();
    pollStoppedReason.value = "页面不在前台，自动查单已暂停。回到本页会继续查询。";
    return;
  }
  if (attempt.value && !pollTimer) {
    pollStoppedReason.value = null;
    pollDelay.value = PAYMENT_POLL_INITIAL_MS;
    schedulePoll(PAYMENT_POLL_INITIAL_MS);
  }
}

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  sessionExpired.value = false;
  try {
    const found = await findOrderForInstallment(installmentId.value);
    order.value = found.order;
    installment.value = found.installment;

    const active = found.installment.active_payment_attempt_id;
    if (active) {
      attempt.value = {
        payment_attempt_id: active,
        status: "PENDING",
        action: { type: "redirect", url: "" },
        expires_at: found.installment.due_at ?? found.order.expires_at ?? "",
      };
      await query();
    }
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        error.value = "登录状态已过期，请重新登录后继续付款。";
      } else if (caught.status === 403) {
        forbidden.value = true;
        error.value = "该应付项不属于当前账号，或你无权支付。";
      } else if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") {
        notFound.value = true;
        error.value = "找不到对应的应付项。请从订单详情重新进入支付。";
      } else {
        error.value = caught.message || "支付信息加载失败。";
      }
    } else {
      error.value = "支付信息加载失败，请稍后重试。";
    }
  } finally {
    loading.value = false;
  }
}

/** 应付项只通过所属订单读取；服务端按 owner 过滤。 */
async function findOrderForInstallment(
  id: string,
): Promise<{ order: OrderDetail; installment: OrderInstallment }> {
  const list = await listOrderCandidates();
  for (const candidate of list) {
    const detail = await fetchOrder(candidate);
    const found = detail.installments.find((item) => item.id === id);
    if (found) return { order: detail, installment: found };
  }
  throw new ApiError(404, {
    code: "RESOURCE_NOT_FOUND",
    message: "找不到对应的应付项。",
    fields: {},
    trace_id: "",
  });
}

async function listOrderCandidates(): Promise<string[]> {
  const raw = String(route.query["order"] ?? "");
  if (raw) return [raw];
  const payload = await fetchOrders({ page: 1, limit: 20 });
  return payload.items.map((item) => item.id);
}

onMounted(async () => {
  document.addEventListener("visibilitychange", onVisibilityChange);
  await Promise.allSettled([site.load()]);
  await load();
});

onBeforeUnmount(() => {
  disposed = true;
  clearPoll();
  queryController?.abort();
  document.removeEventListener("visibilitychange", onVisibilityChange);
});

async function createAttempt(): Promise<void> {
  if (!installment.value) return;
  if (creating.value) return;

  creating.value = true;
  createError.value = null;
  clearPoll();
  pollStoppedReason.value = null;
  pollDelay.value = PAYMENT_POLL_INITIAL_MS;

  try {
    const created = await createPaymentAttempt({
      installmentId: installment.value.id,
      provider: selectedProvider.value,
      scene: PAYMENT_PROVIDERS.find((item) => item.provider === selectedProvider.value)?.scene ?? "native",
      idempotencyKey: newPaymentIdempotencyKey(),
    });
    attempt.value = created;
    attemptStatus.value = created.status;
    verifiedAt.value = null;
    if (created.action.type === "redirect" && created.action.url) {
      window.open(created.action.url, "_blank", "noopener,noreferrer");
    }
    toast.info("支付已创建", "请在到期前完成付款。付款结果以服务端查单为准，返回本页会自动查询。");
    await query();
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        createError.value = "登录状态已过期，请重新登录后继续付款。";
      } else if (caught.code === "PAYMENT_PROVIDER_DISABLED") {
        disabledProviders.value = {
          ...disabledProviders.value,
          [selectedProvider.value]:
            caught.message || "该支付方式当前不可用（渠道未开通或暂时停用），请选择其他支付方式。",
        };
        createError.value = "该支付方式当前不可用，请换一种方式。";
      } else if (caught.code === "FEATURE_DISABLED") {
        createError.value = "在线支付当前未启用，请联系客服安排付款方式。";
      } else if (caught.code === "STATE_CONFLICT") {
        createError.value = "该应付项当前状态不允许创建支付，可能已付款或订单已关闭。";
        await load();
      } else if (caught.code === "IDEMPOTENCY_CONFLICT") {
        createError.value = "本次支付请求与服务端记录冲突，请重新点击发起支付。";
      } else if (caught.isPaymentUnknown) {
        attemptStatus.value = "UNKNOWN";
        unknownNotice.value = "服务端未返回明确的支付结果，已标记为“结果确认中”。请不要重复付款。";
        schedulePoll(PAYMENT_POLL_INITIAL_MS);
      } else {
        createError.value = caught.message || "发起支付失败，请稍后重试。";
      }
    } else {
      createError.value = "发起支付失败，请稍后重试。";
    }
  } finally {
    creating.value = false;
  }
}

function recheck(): void {
  pollDelay.value = PAYMENT_POLL_INITIAL_MS;
  pollStoppedReason.value = null;
  void query(true);
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '我的订单', to: '/account/orders' },
        { label: '支付' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="6" aria-label="正在读取支付信息" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : notFound ? 'not-found' : 'error'"
      :title="
        forbidden
          ? '无法支付该应付项'
          : notFound
            ? '找不到应付项'
            : sessionExpired
              ? '登录已过期'
              : '支付信息加载失败'
      "
      :description="error"
    >
      <AppButton v-if="sessionExpired" size="sm" to="/login">重新登录</AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/orders">返回订单列表</AppButton>
    </EmptyState>

    <template v-else-if="order && installment">
      <SectionHeading
        class="mt-6"
        :level="1"
        eyebrow="Payment"
        title="完成付款"
        :description="`订单 ${order.order_no} · ${installment.title}`"
      >
        <template #actions>
          <AppButton size="sm" variant="secondary" :to="`/account/orders/${order.id}`">查看订单详情</AppButton>
        </template>
      </SectionHeading>

      <div class="mt-8 grid gap-8 lg:grid-cols-[1.4fr_1fr] lg:items-start">
        <div class="space-y-6">
          <!-- 必须写明的判定口径 -->
          <div class="rounded-lg border border-amber bg-amber-soft p-5 text-sm text-amber-strong">
            <p class="font-medium">付款成功只以服务端查单结果为准</p>
            <p class="mt-1">
              支付渠道跳转回来时，地址里可能带有渠道附加的 success 之类的参数，本页不读取、也不据此判定成交；
              本页同样不凭支付返回页认定付款成功。只有服务端收到已验签通知或主动查单确认后，
              才会把该支付尝试记为已确认收款。
            </p>
          </div>

          <!-- 支付方式 -->
          <SurfaceCard>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">支付方式</h2>
              <StatusBadge :tone="statusTone" :label="statusLabel" />
            </div>

            <fieldset class="mt-4 space-y-3">
              <legend class="sr-only">选择支付方式</legend>
              <label
                v-for="option in PAYMENT_PROVIDERS"
                :key="option.provider"
                class="flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors"
                :class="
                  selectedProvider === option.provider
                    ? 'border-amber bg-amber-soft'
                    : 'border-line bg-surface hover:border-line-strong'
                "
              >
                <input
                  v-model="selectedProvider"
                  type="radio"
                  name="payment-provider"
                  class="mt-1 size-4 accent-amber"
                  :value="option.provider"
                  :disabled="Boolean(disabledProviders[option.provider])"
                />
                <span class="min-w-0 text-sm">
                  <span class="flex flex-wrap items-center gap-2">
                    <span class="font-medium">{{ option.label }}</span>
                    <StatusBadge v-if="selectedProvider === option.provider" tone="amber" label="已选择" />
                    <StatusBadge v-if="disabledProviders[option.provider]" tone="wine" label="当前不可用" />
                  </span>
                  <span class="mt-1 block text-xs text-ink-muted">{{ option.description }}</span>
                  <span v-if="disabledProviders[option.provider]" class="mt-1 block text-xs text-wine">
                    {{ disabledProviders[option.provider] }}
                  </span>
                </span>
              </label>
            </fieldset>

            <p v-if="allProvidersDisabled" class="mt-4 rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine" role="status">
              所有在线支付方式当前都不可用。请联系客服确认其他付款安排；我们不会生成假的支付成功页。
            </p>

            <div class="mt-5 flex flex-wrap items-center gap-3">
              <AppButton size="lg" :loading="creating" :disabled="providerDisabledReason !== ''" :disabled-reason="providerDisabledReason" @click="createAttempt()">
                {{ attempt ? "重新获取支付参数" : "发起支付" }}
              </AppButton>
              <p class="text-sm text-ink-muted">
                应付金额
                <span class="font-display text-lg tabular-nums">{{ formatMoneyExact(amountCents, currency) }}</span>
              </p>
            </div>
            <p class="mt-2 text-xs text-ink-faint">
              页面不提交金额或商户号；切换支付方式前，服务端会先关闭旧支付单并核对，避免重复收款。
            </p>
            <p v-if="createError" class="mt-3 text-sm font-medium text-wine" role="alert">{{ createError }}</p>
          </SurfaceCard>

          <!--
            发起支付时服务端未返回明确结果（PAYMENT_UNKNOWN / 查单超时）：
            此时可能还没有可展示的支付单，但必须写明“结果确认中”并提醒不要重复付款，
            否则用户会以为按钮没生效而反复点击。
          -->
          <p
            v-if="!attempt && unknownNotice"
            class="rounded-sm bg-amber-soft px-3 py-2 text-sm text-amber-strong"
            role="status"
          >
            <span class="font-medium">结果尚未确认。</span>
            {{ unknownNotice }}
            这不等于付款失败，也不等于订单已取消；请不要重复创建新的支付单。
          </p>

          <!-- 二维码或跳转 -->
          <SurfaceCard v-if="attempt">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">{{ attempt.action.type === "qr" ? "扫码支付" : "前往渠道完成付款" }}</h2>
              <StatusBadge :tone="statusTone" :label="statusLabel" />
            </div>

            <div v-if="attempt.action.type === 'qr'" class="mt-5 flex flex-col items-center gap-4">
              <div class="w-full max-w-[280px] rounded-lg border border-line bg-surface p-4">
                <img
                  v-if="attempt.action.url"
                  :src="attempt.action.url"
                  alt="支付二维码"
                  width="248"
                  height="248"
                  loading="eager"
                  decoding="async"
                  class="aspect-square w-full"
                />
                <p v-else class="py-10 text-center text-sm text-ink-muted">
                  服务端未返回二维码内容，请改用跳转方式或重新发起支付。
                </p>
              </div>
              <p class="text-center text-sm text-ink-muted">
                请用对应 App 扫描二维码完成付款。付款后本页会自动查询结果，也可以点下方「立即查询付款结果」。
              </p>
              <a
                v-if="attempt.action.url"
                :href="attempt.action.url"
                target="_blank"
                rel="noopener noreferrer"
                class="link-underline text-xs text-ink-muted"
              >
                二维码无法显示时，在新窗口打开支付内容
              </a>
            </div>

            <div v-else class="mt-5 space-y-3">
              <p class="text-sm text-ink-muted">
                已完成跳转授权：如果没有自动打开新窗口，请点击下方按钮前往渠道页面完成付款。
              </p>
              <AppButton
                v-if="attempt.action.url"
                :href="attempt.action.url"
                variant="secondary"
              >
                打开支付页面
              </AppButton>
              <p v-else class="text-sm text-ink-muted">服务端未返回跳转地址，请重新发起支付。</p>
            </div>

            <div class="mt-5 border-t border-line pt-4">
              <PaymentCountdown
                :deadline="paymentDeadline"
                label="付款剩余时间"
                expired-hint="付款窗口已到期。到期不等于订单已取消：支付结果以服务端查单为准，请点击「立即查询付款结果」。"
              />

              <div class="mt-4 flex flex-wrap items-center gap-3">
                <AppButton size="sm" variant="secondary" :loading="querying" @click="recheck()">
                  立即查询付款结果
                </AppButton>
                <span v-if="lastCheckedAt" class="text-xs text-ink-faint tabular-nums">
                  上次查询 {{ formatDateTime(lastCheckedAt) }}
                </span>
              </div>

              <p v-if="resultUnknown || unknownNotice" class="mt-3 rounded-sm bg-amber-soft px-3 py-2 text-sm text-amber-strong" role="status">
                <span class="font-medium">结果尚未确认。</span>
                {{ unknownNotice ?? "服务端还没有收到该笔付款的确认结果，正在按退避策略继续查单。" }}
                这不等于付款失败，也不等于订单已取消；请不要重复创建新的支付单。
              </p>
              <p v-if="polling" class="mt-2 text-xs text-ink-faint" role="status">
                自动查单进行中（间隔从 2 秒逐步退避到最长 15 秒；页面切到后台会暂停）。
              </p>
              <p v-else-if="pollStoppedReason" class="mt-2 text-xs text-ink-faint">{{ pollStoppedReason }}</p>

              <p v-if="verifiedAt" class="mt-2 text-xs text-ink-faint tabular-nums">
                服务端查单确认时间 {{ formatDateTime(verifiedAt) }}
              </p>
            </div>
          </SurfaceCard>
        </div>

        <div class="space-y-4 lg:sticky lg:top-24">
          <SurfaceCard>
            <h3 class="font-display text-base">本期应付</h3>
            <dl class="mt-3 space-y-2 text-sm">
              <div class="flex items-baseline justify-between gap-4">
                <dt class="text-ink-muted">款项</dt>
                <dd>{{ installment.title }}</dd>
              </div>
              <div class="flex items-baseline justify-between gap-4">
                <dt class="text-ink-muted">金额</dt>
                <dd class="tabular-nums">{{ formatMoneyExact(installment.amount_cents, installment.currency) }}</dd>
              </div>
              <div class="flex items-baseline justify-between gap-4">
                <dt class="text-ink-muted">已核实收款</dt>
                <dd class="tabular-nums">{{ formatMoneyExact(installment.paid_cents, installment.currency) }}</dd>
              </div>
              <div v-if="installment.due_at" class="flex items-baseline justify-between gap-4">
                <dt class="text-ink-muted">到期时间</dt>
                <dd class="tabular-nums">{{ formatDateTime(installment.due_at) }}</dd>
              </div>
              <div class="flex items-baseline justify-between gap-4">
                <dt class="text-ink-muted">订单状态</dt>
                <dd>{{ order.status }}</dd>
              </div>
            </dl>
          </SurfaceCard>

          <SurfaceCard>
            <h3 class="font-display text-base">付款遇到问题？</h3>
            <ul class="mt-3 space-y-2 text-sm text-ink-muted">
              <li>付款后回到本页，点「立即查询付款结果」即可获得服务端权威状态。</li>
              <li>二维码过期可以重新发起支付；旧支付单的结果仍会由服务端核对。</li>
              <li>不要在两个渠道同时付款，重复收款会进入多付处置流程。</li>
              <li>
                需要人工核对时请联系 {{ site.brand.support_email }}（{{ site.brand.service_hours }}），
                提供订单号 {{ order.order_no }}。
              </li>
            </ul>
            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton size="sm" variant="secondary" :to="`/account/orders/${order.id}`">订单详情</AppButton>
              <AppButton size="sm" variant="ghost" to="/contact">联系客服</AppButton>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </template>
  </div>
</template>
