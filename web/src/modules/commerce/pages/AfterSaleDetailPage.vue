<script setup lang="ts">
/**
 * 售后详情。
 *
 * 规则（08 文档「退款」与「分离状态」）：
 * - 售后流程状态与退款渠道状态分开呈现，处理记录按时间正序展示。
 * - 退款 `FAILED` / `UNKNOWN` 只能说“未成功 / 未确认到账”，不能说钱已退。
 * - 定制与现货的售后条件分别呈现，文案取站点配置与售后单上的服务端说明，不硬编码条款。
 * - 补件与退货物流是服务端动作，成功后以服务端返回为准刷新页面。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  fetchAfterSale,
  postAfterSaleMessage,
  submitReturnShipment,
  type AfterSaleDetail,
  type AfterSaleStatus,
  type RefundStatus,
} from "@/modules/commerce/api/commerce";
import { useSiteStore } from "@/modules/content/stores/site";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
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

const record = ref<AfterSaleDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);
const sessionExpired = ref(false);

const messageBody = ref("");
const messageError = ref<string | null>(null);
const messageSaving = ref(false);
const messageSaved = ref(false);
const messageDirty = ref(false);

const shipmentForm = ref({ carrier: "", trackingNo: "", note: "" });
const shipmentError = ref<string | null>(null);
const shipmentSaving = ref(false);
const shipmentSaved = ref(false);

const AFTER_SALE_LABEL: Record<AfterSaleStatus, string> = {
  OPEN: "已受理，等待客服核查",
  NEEDS_INFO: "需要补充信息",
  APPROVED: "已通过，等待寄回商品",
  RETURNING: "退货寄回中",
  RESOLVED: "已处理完成",
  REJECTED: "未通过",
};

const REFUND_LABEL: Record<RefundStatus, string> = {
  REQUESTED: "退款已申请，等待审核",
  APPROVED: "退款已批准，等待执行",
  PROCESSING: "退款处理中，尚未确认到账",
  UNKNOWN: "退款结果未知，尚未确认到账",
  SUCCEEDED: "退款已成功",
  FAILED: "退款未成功",
  REJECTED: "退款被拒绝",
};

const afterSaleId = computed(() => String(route.params["id"] ?? ""));

const isCustom = computed(() => {
  const type = record.value?.fulfillment_type ?? "";
  return type === "commission" || type === "custom";
});

const canReply = computed(
  () => record.value !== null && ["OPEN", "NEEDS_INFO", "APPROVED", "RETURNING"].includes(record.value.status),
);

const canSubmitShipment = computed(() => record.value?.status === "APPROVED" || record.value?.status === "RETURNING");

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  sessionExpired.value = false;
  try {
    record.value = await fetchAfterSale(afterSaleId.value);
  } catch (caught) {
    record.value = null;
    if (caught instanceof ApiError) {
      if (caught.needsLogin) {
        sessionExpired.value = true;
        error.value = "登录状态已过期，请重新登录后查看售后记录。";
      } else if (caught.status === 403) {
        forbidden.value = true;
        error.value = "该售后单不属于当前账号，或你无权查看。";
      } else if (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND") {
        notFound.value = true;
        error.value = "售后单不存在，或已被删除访问权限。";
      } else {
        error.value = caught.message || "售后记录加载失败。";
      }
    } else {
      error.value = "售后记录加载失败，请稍后重试。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  await Promise.allSettled([site.load()]);
  await load();
});

watch(afterSaleId, () => void load());

function saleTone(status: AfterSaleStatus): "jade" | "amber" | "wine" | "neutral" {
  if (status === "RESOLVED") return "jade";
  if (status === "NEEDS_INFO" || status === "RETURNING") return "amber";
  if (status === "REJECTED") return "wine";
  return "neutral";
}

function refundTone(status: RefundStatus): "jade" | "amber" | "wine" | "neutral" {
  if (status === "SUCCEEDED") return "jade";
  if (status === "FAILED" || status === "REJECTED") return "wine";
  if (status === "UNKNOWN" || status === "PROCESSING" || status === "REQUESTED") return "amber";
  return "neutral";
}

function refundIsUnconfirmed(status: RefundStatus): boolean {
  return status === "REQUESTED" || status === "APPROVED" || status === "PROCESSING" || status === "UNKNOWN";
}

async function sendMessage(): Promise<void> {
  messageError.value = null;
  messageSaved.value = false;
  if (messageBody.value.trim().length < 2) {
    messageError.value = "请填写至少 2 个字的补充说明。";
    return;
  }

  messageSaving.value = true;
  try {
    await postAfterSaleMessage(afterSaleId.value, messageBody.value.trim());
    messageBody.value = "";
    messageDirty.value = false;
    messageSaved.value = true;
    toast.success("补充说明已提交", "客服会在工作时段内查看并回复。");
    await load();
  } catch (caught) {
    if (caught instanceof ApiError) {
      messageError.value = caught.needsLogin
        ? "登录状态已过期，请重新登录后重试。"
        : caught.code === "STATE_CONFLICT"
          ? "售后单状态已变化，已刷新为服务端最新状态。"
          : caught.message || "补充说明提交失败。";
      if (caught.code === "STATE_CONFLICT") await load();
    } else {
      messageError.value = "补充说明提交失败，请稍后重试。";
    }
  } finally {
    messageSaving.value = false;
  }
}

async function saveShipment(): Promise<void> {
  shipmentError.value = null;
  shipmentSaved.value = false;
  if (!shipmentForm.value.carrier.trim()) {
    shipmentError.value = "请填写快递公司名称。";
    return;
  }
  if (!shipmentForm.value.trackingNo.trim()) {
    shipmentError.value = "请填写退货单号。";
    return;
  }

  shipmentSaving.value = true;
  try {
    record.value = await submitReturnShipment(afterSaleId.value, {
      carrier: shipmentForm.value.carrier.trim(),
      trackingNo: shipmentForm.value.trackingNo.trim(),
      note: shipmentForm.value.note.trim(),
    });
    shipmentSaved.value = true;
    shipmentForm.value = { carrier: "", trackingNo: "", note: "" };
    toast.success("退货物流已提交", "服务端会记录单号，回仓检验合格后才创建补库存动作。");
  } catch (caught) {
    if (caught instanceof ApiError) {
      shipmentError.value = caught.needsLogin
        ? "登录状态已过期，请重新登录后重试。"
        : caught.message || "退货物流提交失败。";
    } else {
      shipmentError.value = "退货物流提交失败，请稍后重试。";
    }
  } finally {
    shipmentSaving.value = false;
  }
}

function markMessageDirty(): void {
  messageDirty.value = true;
  messageSaved.value = false;
  messageError.value = null;
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '售后与退款', to: '/account/after-sales' },
        { label: '售后详情' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="6" aria-label="正在读取售后记录" />
    </div>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      :tone="forbidden ? 'forbidden' : notFound ? 'not-found' : 'error'"
      :title="
        forbidden
          ? '无法查看该售后单'
          : notFound
            ? '售后单不存在'
            : sessionExpired
              ? '登录已过期'
              : '售后记录加载失败'
      "
      :description="error"
    >
      <AppButton v-if="sessionExpired" size="sm" to="/login?returnTo=/account/after-sales">重新登录</AppButton>
      <AppButton v-else size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/after-sales">返回售后列表</AppButton>
    </EmptyState>

    <template v-else-if="record">
      <SectionHeading
        class="mt-6"
        :level="1"
        eyebrow="After sale"
        :title="record.reason"
        :description="`订单 ${record.order_no} · 提交于 ${formatDateTime(record.created_at)}${record.updated_at ? ` · 最近更新 ${formatDateTime(record.updated_at)}` : ''}`"
      >
        <template #actions>
          <AppButton size="sm" variant="secondary" :to="`/account/orders/${record.order_id}`">查看订单</AppButton>
          <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
        </template>
      </SectionHeading>

      <div class="mt-8 grid gap-8 lg:grid-cols-[1.5fr_1fr] lg:items-start">
        <div class="space-y-6">
          <!-- 处理状态与需要补件 -->
          <SurfaceCard>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">处理状态</h2>
              <StatusBadge :tone="saleTone(record.status)" :label="AFTER_SALE_LABEL[record.status]" />
            </div>

            <div
              v-if="record.status === 'NEEDS_INFO'"
              class="mt-4 rounded-sm border border-amber bg-amber-soft p-4 text-sm text-amber-strong"
              role="status"
            >
              <p class="font-medium">客服需要你补充信息</p>
              <p class="mt-1">{{ record.needs_info ?? "请在下方补充说明中回复客服提出的问题。" }}</p>
            </div>

            <dl class="mt-4 space-y-2 text-sm">
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">商品类型</dt>
                <dd>{{ isCustom ? "定制商品" : "现货/配件" }}</dd>
              </div>
              <div v-if="record.item" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">涉及商品</dt>
                <dd>{{ record.item.product_title }} · {{ record.item.sku_name }} × {{ record.item.quantity }}</dd>
              </div>
              <div v-if="record.item" class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">该行金额</dt>
                <dd class="tabular-nums">{{ formatMoneyExact(record.item.line_total_cents, record.item.currency) }}</dd>
              </div>
            </dl>

            <div class="mt-4 rounded-sm bg-surface-muted p-3 text-xs text-ink-muted">
              <p v-if="isCustom">
                定制售后条件：{{ record.policy_note ?? site.commercePolicy.returns_reference }}。
                定制商品的特殊退货条件不消除质量瑕疵责任。
              </p>
              <p v-else>
                现货售后条件：{{ record.policy_note ?? site.commercePolicy.returns_reference }}。
              </p>
              <p v-if="site.commercePolicy.cross_border_note" class="mt-1">
                {{ site.commercePolicy.cross_border_note }}
              </p>
            </div>
          </SurfaceCard>

          <!-- 问题说明与图片 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">问题说明</h2>
            <p class="mt-3 whitespace-pre-line text-sm text-ink-soft">{{ record.description }}</p>

            <div v-if="record.attachments.length" class="mt-4 grid gap-3 sm:grid-cols-3">
              <a
                v-for="asset in record.attachments"
                :key="asset.id"
                :href="asset.url"
                target="_blank"
                rel="noopener noreferrer"
                class="group block overflow-hidden rounded-sm border border-line bg-canvas-sunk"
              >
                <img
                  :src="asset.url"
                  :alt="asset.alt || '售后问题图片'"
                  :width="asset.width"
                  :height="asset.height"
                  loading="lazy"
                  decoding="async"
                  class="aspect-square size-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
                />
                <span class="block px-3 py-2 text-xs text-ink-muted">打开原图</span>
              </a>
            </div>
            <p v-else class="mt-3 text-xs text-ink-faint">没有上传图片附件。</p>
          </SurfaceCard>

          <!-- 退货物流 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">退货物流</h2>

            <div v-if="record.return_shipment" class="mt-3 rounded-sm border border-line p-3 text-sm">
              <p class="font-medium">{{ record.return_shipment.carrier }}</p>
              <p class="mt-1 text-xs text-ink-muted tabular-nums">退货单号 {{ record.return_shipment.tracking_no }}</p>
              <p class="mt-1 text-xs text-ink-faint">
                寄出时间
                {{ record.return_shipment.shipped_at ? formatDateTime(record.return_shipment.shipped_at) : "未记录" }}
              </p>
              <p v-if="record.return_shipment.note" class="mt-1 text-xs text-ink-muted">
                {{ record.return_shipment.note }}
              </p>
            </div>

            <form v-if="canSubmitShipment" class="mt-4 space-y-4" @submit.prevent="saveShipment">
              <p class="text-sm text-ink-muted">
                {{ record.return_shipment ? "需要更正退货单号时请重新填写并提交。" : "寄回商品后请填写快递公司与退货单号，便于客服核对回仓。" }}
              </p>
              <div class="grid gap-4 sm:grid-cols-2">
                <FieldRow label="快递公司" required for="return-carrier">
                  <input id="return-carrier" v-model="shipmentForm.carrier" class="field-base" type="text" maxlength="40" />
                </FieldRow>
                <FieldRow label="退货单号" required for="return-tracking">
                  <input id="return-tracking" v-model="shipmentForm.trackingNo" class="field-base" type="text" maxlength="60" />
                </FieldRow>
              </div>
              <FieldRow label="备注" for="return-note" hint="选填，例如寄回件数或包装情况">
                <input id="return-note" v-model="shipmentForm.note" class="field-base" type="text" maxlength="160" />
              </FieldRow>
              <p v-if="shipmentError" class="text-xs font-medium text-wine" role="alert">{{ shipmentError }}</p>
              <p v-if="shipmentSaved" class="text-xs text-jade" role="status">退货物流已保存。</p>
              <div class="flex flex-wrap gap-3">
                <AppButton type="submit" size="sm" :loading="shipmentSaving">提交退货物流</AppButton>
              </div>
              <p class="text-xs text-ink-faint">
                售后退款不会自动补库存：商品回仓检验合格（或独占角色经授权审核）后才创建补库存动作。
              </p>
            </form>

            <p v-else-if="!record.return_shipment" class="mt-3 text-sm text-ink-muted">
              当前状态还不需要填写退货单号。售后审批通过后，这里会出现物流填写入口。
            </p>
          </SurfaceCard>

          <!-- 处理记录 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">处理记录</h2>

            <ol v-if="record.messages.length" class="mt-4 space-y-4">
              <li v-for="message in record.messages" :key="message.id" class="flex gap-3">
                <span
                  class="mt-1 inline-flex size-8 shrink-0 items-center justify-center rounded-full border text-xs"
                  :class="message.author === 'staff' ? 'border-amber bg-amber-soft text-amber-strong' : 'border-line bg-surface-muted text-ink-muted'"
                  aria-hidden="true"
                >
                  {{ message.author === "staff" ? "客服" : "我" }}
                </span>
                <div class="min-w-0 flex-1">
                  <p class="text-xs text-ink-faint">
                    {{ message.author_label || (message.author === "staff" ? "客服" : "我") }} ·
                    <span class="tabular-nums">{{ formatDateTime(message.created_at) }}</span>
                  </p>
                  <p class="mt-1 whitespace-pre-line text-sm text-ink-soft">{{ message.body }}</p>
                  <div v-if="message.attachments.length" class="mt-2 flex flex-wrap gap-2">
                    <a
                      v-for="asset in message.attachments"
                      :key="asset.id"
                      :href="asset.url"
                      target="_blank"
                      rel="noopener noreferrer"
                      class="block w-20 overflow-hidden rounded-sm border border-line"
                    >
                      <img
                        :src="asset.url"
                        :alt="asset.alt || '处理记录图片'"
                        :width="asset.width"
                        :height="asset.height"
                        loading="lazy"
                        decoding="async"
                        class="aspect-square size-full object-cover"
                      />
                    </a>
                  </div>
                </div>
              </li>
            </ol>
            <p v-else class="mt-3 text-sm text-ink-muted">还没有处理记录。</p>

            <form v-if="canReply" class="mt-6 space-y-3 border-t border-line pt-4" @submit.prevent="sendMessage">
              <FieldRow
                label="补充说明"
                required
                for="after-sale-message"
                hint="客服要求补件时请在这里回复；也可以补充新发现的问题现象。"
              >
                <textarea
                  id="after-sale-message"
                  v-model="messageBody"
                  class="field-base min-h-[110px]"
                  maxlength="1000"
                  @input="markMessageDirty"
                />
              </FieldRow>
              <p v-if="messageError" class="text-xs font-medium text-wine" role="alert">{{ messageError }}</p>
              <p v-else-if="messageSaved" class="text-xs text-jade" role="status">补充说明已提交。</p>
              <p v-else-if="messageDirty" class="text-xs text-ink-faint">有未提交的内容。</p>
              <div class="flex flex-wrap gap-3">
                <AppButton type="submit" size="sm" :loading="messageSaving">提交补充说明</AppButton>
              </div>
            </form>
            <p v-else class="mt-4 text-sm text-ink-muted">
              该售后单已结束，不能再补充说明。仍有问题请通过客服邮箱联系。
            </p>
          </SurfaceCard>

          <!-- 退款结果 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">退款结果</h2>

            <ul v-if="record.refunds.length" class="mt-4 space-y-3">
              <li v-for="refund in record.refunds" :key="refund.id" class="rounded-sm border border-line p-3 text-sm">
                <div class="flex flex-wrap items-center justify-between gap-3">
                  <p class="font-medium tabular-nums">{{ formatMoneyExact(refund.amount_cents, refund.currency) }}</p>
                  <StatusBadge :tone="refundTone(refund.status)" :label="REFUND_LABEL[refund.status]" />
                </div>
                <p class="mt-1 text-xs text-ink-faint">
                  申请于 <span class="tabular-nums">{{ formatDateTime(refund.requested_at) }}</span>
                  <span v-if="refund.updated_at"> · 更新于 <span class="tabular-nums">{{ formatDateTime(refund.updated_at) }}</span></span>
                </p>
                <p v-if="refund.out_refund_no_masked" class="mt-1 text-xs text-ink-faint tabular-nums">
                  退款单号 {{ refund.out_refund_no_masked }}
                </p>
                <p v-if="refund.note" class="mt-1 text-xs text-ink-muted">{{ refund.note }}</p>
                <p
                  v-if="refundIsUnconfirmed(refund.status)"
                  class="mt-2 rounded-sm bg-amber-soft px-3 py-2 text-xs text-amber-strong"
                  role="status"
                >
                  这笔退款尚未确认成功，不计为已经退钱。服务端会继续查单，明确失败且渠道证明未退才会释放额度。
                </p>
                <p
                  v-else-if="refund.status === 'FAILED'"
                  class="mt-2 rounded-sm bg-wine-soft px-3 py-2 text-xs text-wine"
                  role="status"
                >
                  退款未成功：不代表钱已经退回。服务端会按原渠道重试或转人工核对，请留意售后处理记录。
                </p>
                <p
                  v-else-if="refund.status === 'REJECTED'"
                  class="mt-2 rounded-sm bg-wine-soft px-3 py-2 text-xs text-wine"
                  role="status"
                >
                  退款被拒绝。拒绝理由见处理记录，可联系客服进一步说明。
                </p>
                <p v-else-if="refund.status === 'SUCCEEDED'" class="mt-2 text-xs text-jade" role="status">
                  退款已成功，按原支付渠道退回原付款来源。
                </p>
              </li>
            </ul>
            <p v-else class="mt-3 text-sm text-ink-muted">
              还没有退款记录。售后审批通过并由财务执行后才会产生退款单。
            </p>

            <p class="mt-4 text-xs text-ink-faint">
              可退额度 = 原收款金额 − 已成功退款 − 处理中/未知/已批准预留的退款额；退款一律按原渠道退回原付款来源。
            </p>
          </SurfaceCard>
        </div>

        <!-- 右侧：状态与规则 -->
        <div class="space-y-4 lg:sticky lg:top-24">
          <SurfaceCard>
            <h3 class="font-display text-base">售后流程</h3>
            <ol class="mt-3 space-y-2 text-sm">
              <li
                v-for="step in [
                  { code: 'OPEN', label: '已受理，等待客服核查' },
                  { code: 'NEEDS_INFO', label: '需要补充信息' },
                  { code: 'APPROVED', label: '已通过，等待寄回' },
                  { code: 'RETURNING', label: '退货寄回中' },
                  { code: 'RESOLVED', label: '已处理完成' },
                ]"
                :key="step.code"
                class="flex items-start gap-2"
              >
                <span
                  class="mt-1 inline-block size-2 shrink-0 rounded-full border"
                  :class="
                    record.status === step.code
                      ? 'border-amber bg-amber'
                      : 'border-line-strong bg-surface-muted'
                  "
                  aria-hidden="true"
                />
                <span :class="record.status === step.code ? 'font-medium text-ink-soft' : 'text-ink-muted'">
                  {{ step.label }}
                  <span v-if="record.status === step.code" class="text-xs text-amber-strong">（当前）</span>
                </span>
              </li>
              <li v-if="record.status === 'REJECTED'" class="rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine">
                未通过：本条售后已结束，可在处理记录中查看说明。
              </li>
            </ol>
          </SurfaceCard>

          <SurfaceCard>
            <h3 class="font-display text-base">售后规则摘要</h3>
            <dl class="mt-3 space-y-2 text-xs text-ink-muted">
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">{{ isCustom ? "定制条件" : "现货条件" }}</dt>
                <dd>{{ record.policy_note ?? site.commercePolicy.returns_reference }}</dd>
              </div>
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">退款方式</dt>
                <dd>按原支付渠道退回原付款来源，不改为其他收款账号。</dd>
              </div>
              <div class="flex flex-wrap gap-2">
                <dt class="text-ink-faint">发票影响</dt>
                <dd>发票申请与售后独立处理，开票金额以已核实收款为准。</dd>
              </div>
            </dl>
            <div class="mt-4">
              <AppButton size="sm" variant="secondary" to="/care">养护与维修说明</AppButton>
            </div>
          </SurfaceCard>

          <p class="rounded-lg border border-line bg-surface-muted p-4 text-xs text-ink-muted">
            客服邮箱 {{ site.brand.support_email }}，工作时段 {{ site.brand.service_hours }}，首响
            {{ site.brand.response_sla.feedback_workdays }} 个工作日。咨询时请提供订单号
            {{ record.order_no }}。
          </p>
        </div>
      </div>
    </template>
  </div>
</template>
