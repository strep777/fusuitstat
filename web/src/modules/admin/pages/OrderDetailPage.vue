<script setup lang="ts">
/**
 * 订单详情：明细/快照、款项、发货、关闭原因、售后入口。
 *
 * 硬约束（08/11 文档）：**禁止修改已付金额**，界面必须写明；
 * 收件信息只显示脱敏摘要，不显示地址明文与手机号明文。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  failureTone,
  text,
  usePageData,
  type OrderDetail,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, EmptyState, FieldRow, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const auth = useAdminAuthStore();
const toast = useToast();

const orderId = computed(() => String(route.params.id ?? ""));

const state = usePageData<OrderDetail | null>(
  () => adminApi.orderDetail(orderId.value),
  null,
  "订单详情",
  () => auth.handleSessionExpired(),
);

const itemColumns: TableColumn[] = [
  { key: "title", label: "商品" },
  { key: "sku_code", label: "SKU", width: "150px" },
  { key: "quantity", label: "数量", width: "80px" },
  { key: "unit_price_cents", label: "单价", width: "120px" },
];

const paymentColumns: TableColumn[] = [
  { key: "channel", label: "渠道", width: "110px" },
  { key: "amount_cents", label: "金额", width: "120px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "verified_at", label: "确认时间", width: "170px", hideOnMobile: true },
];

const shipmentForm = ref({ carrier: "", tracking_no: "", note: "" });
const shipmentError = ref("");
const shipmentPending = ref(false);
const shipmentConfirmOpen = ref(false);

const closeReason = ref("");
const closeError = ref("");
const closePending = ref(false);
const closeConfirmOpen = ref(false);

const amountItems = computed(() => [
  { label: "应付合计", value: formatMoneyExact(state.data.value?.total_cents) },
  { label: "已付金额（只读，后台不可修改）", value: formatMoneyExact(state.data.value?.paid_cents) },
  { label: "已退款金额（只读）", value: formatMoneyExact(state.data.value?.refunded_cents) },
  { label: "币种", value: text(state.data.value?.currency, "CNY") },
]);

const orderItems = computed(() =>
  (state.data.value?.items ?? []).map((item, index) => ({
    id: `${item.sku_code ?? "item"}-${index}`,
    title: item.title,
    sku_code: item.sku_code,
    quantity: item.quantity,
    unit_price_cents: item.unit_price_cents,
  })),
);

const paymentItems = computed(() =>
  (state.data.value?.payments ?? []).map((item) => ({
    id: item.id,
    channel: item.channel,
    amount_cents: item.amount_cents,
    status: item.status,
    verified_at: item.verified_at,
  })),
);

function requestShipmentConfirm(): void {
  if (!shipmentForm.value.carrier.trim() || !shipmentForm.value.tracking_no.trim()) {
    shipmentError.value = "请填写承运商与运单号；运单号会同步给客户。";
    return;
  }
  shipmentError.value = "";
  shipmentConfirmOpen.value = true;
}

async function submitShipment(): Promise<void> {
  shipmentPending.value = true;
  try {
    await adminApi.createShipment({
      order_id: orderId.value,
      carrier: shipmentForm.value.carrier.trim(),
      tracking_no: shipmentForm.value.tracking_no.trim(),
      note: shipmentForm.value.note,
    });
    toast.success("发货已登记", "客户可以在订单详情里看到承运商与运单号。");
    shipmentConfirmOpen.value = false;
    shipmentForm.value = { carrier: "", tracking_no: "", note: "" };
    await state.load();
  } catch (caught) {
    shipmentError.value = actionErrorMessage(caught, "发货登记失败，请稍后重试。").message;
    shipmentConfirmOpen.value = false;
  } finally {
    shipmentPending.value = false;
  }
}

function requestCloseConfirm(): void {
  if (closeReason.value.trim().length < 6) {
    closeError.value = "请填写至少 6 个字的关闭理由。";
    return;
  }
  closeError.value = "";
  closeConfirmOpen.value = true;
}

async function submitClose(): Promise<void> {
  closePending.value = true;
  try {
    await adminApi.closeOrder(orderId.value, {
      reason: closeReason.value.trim(),
      version: state.data.value?.version ?? undefined,
    });
    toast.success("订单已关闭", "关闭不会删除历史；已收款需要先完成退款。");
    closeConfirmOpen.value = false;
    await state.load();
  } catch (caught) {
    closeError.value = actionErrorMessage(caught, "关闭失败，请稍后重试。").message;
    closeConfirmOpen.value = false;
  } finally {
    closePending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Orders · Detail"
      title="订单详情"
      :description="`订单 ${text(state.data.value?.order_no ?? orderId)} 的明细、款项、发货与售后入口。收件信息只显示脱敏摘要。`"
    >
      <template #actions>
        <StatusBadge :tone="state.data.value?.status === 'COMPLETED' ? 'jade' : 'amber'" :label="text(state.data.value?.status, '未知状态')" />
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
        <AppButton size="sm" variant="secondary" to="/admin/orders">返回订单列表</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="danger"
      title="禁止修改已付金额"
      description="这里的「已付金额」是只读快照，由已验签的支付通知或服务端主动查单写入。后台不提供任何改写入口：金额不一致请到「对账」核对渠道账单并留下处置记录。"
    />

    <div v-if="state.loading.value" class="space-y-4">
      <SkeletonBlock :lines="6" aria-label="正在加载订单详情" />
    </div>

    <EmptyState
      v-else-if="state.error.value"
      :tone="failureTone(state.error.value)"
      :title="state.error.value.title"
      :description="`${state.error.value.description} ${state.error.value.retryHint}`"
    >
      <AppButton size="sm" @click="state.load()">重试</AppButton>
      <AppButton size="sm" variant="secondary" to="/admin/orders">返回订单列表</AppButton>
    </EmptyState>

    <div v-else-if="state.data.value" class="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
      <div class="space-y-6">
        <SurfaceCard>
          <SectionHeading level="2" title="订单明细" description="下单时的价格与规格快照，不随后台商品改动而变化。" />
          <div class="mt-4">
            <DataTable
              :columns="itemColumns"
              :rows="orderItems"
              dense
              empty-title="订单没有明细行"
              empty-description="如遇到这种订单，请连同订单号反馈给技术同学核查。"
            >
              <template #cell-unit_price_cents="{ row }">
                <span class="tabular-nums">{{ formatMoneyExact(row.unit_price_cents as number) }}</span>
              </template>
              <template #actions>
                <span class="text-xs text-ink-faint">明细行不可编辑</span>
              </template>
            </DataTable>
          </div>
          <FieldList class="mt-5" :items="amountItems" />
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading level="2" title="支付记录" description="只有已验签的通知或服务端主动查单才能把支付标记为成功。" />
          <div class="mt-4">
            <DataTable
              :columns="paymentColumns"
              :rows="paymentItems"
              dense
              empty-title="还没有支付记录"
              empty-description="客户尚未发起支付，或支付结果还未确认。"
            >
              <template #cell-amount_cents="{ row }">
                <span class="tabular-nums">{{ formatMoneyExact(row.amount_cents as number) }}</span>
              </template>
              <template #cell-verified_at="{ row }">
                <span class="tabular-nums text-xs">{{ formatDateTime(row.verified_at) }}</span>
              </template>
              <template #actions>
                <span class="text-xs text-ink-faint">查单与异常处理在「收款」页</span>
              </template>
            </DataTable>
          </div>
        </SurfaceCard>

        <div class="grid gap-6 sm:grid-cols-2">
          <SurfaceCard>
            <SectionHeading level="2" title="发货" description="登记后客户立即可见；运单号会写入订单时间线。" />
            <div class="mt-4 space-y-4">
              <FieldRow label="承运商" required for="ship-carrier">
                <input id="ship-carrier" v-model="shipmentForm.carrier" type="text" class="field-base" placeholder="例如 顺丰" />
              </FieldRow>
              <FieldRow label="运单号" required for="ship-tracking">
                <input id="ship-tracking" v-model="shipmentForm.tracking_no" type="text" class="field-base font-mono" />
              </FieldRow>
              <FieldRow label="发货备注" for="ship-note" hint="写内部说明即可，客户可见的是承运商与运单号">
                <input id="ship-note" v-model="shipmentForm.note" type="text" class="field-base" />
              </FieldRow>
              <p v-if="shipmentError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
                {{ shipmentError }}
              </p>
              <PermissionGate :code="ADMIN_PERMISSIONS.orderFulfill">
                <AppButton @click="requestShipmentConfirm">登记发货</AppButton>
              </PermissionGate>
            </div>
          </SurfaceCard>

          <SurfaceCard>
            <SectionHeading level="2" title="关闭订单与售后入口" description="关闭需要写明原因；售后与退款在专门模块处理。" />
            <div class="mt-4 space-y-4">
              <FieldRow label="关闭原因" for="order-close-reason" hint="例如：客户主动取消，沟通记录见工单 Q-2026-0208">
                <input id="order-close-reason" v-model="closeReason" type="text" class="field-base" />
              </FieldRow>
              <p v-if="closeError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
                {{ closeError }}
              </p>
              <div class="flex flex-wrap gap-3">
                <PermissionGate :code="ADMIN_PERMISSIONS.orderFulfill" :show-missing="false">
                  <AppButton variant="danger" @click="requestCloseConfirm">提交关闭</AppButton>
                </PermissionGate>
                <AppButton variant="secondary" to="/admin/refunds">进入退款模块</AppButton>
                <AppButton variant="quiet" to="/admin/reconciliation">进入对账模块</AppButton>
              </div>
              <p class="text-xs text-ink-faint">
                售后与退款必须由拥有 <span class="font-mono">refund.request</span> 的员工发起，批准需要另一位员工的
                <span class="font-mono">refund.approve</span>。
              </p>
            </div>
          </SurfaceCard>
        </div>
      </div>

      <div class="space-y-6">
        <ModuleCard title="订单摘要" status="只读">
          <FieldList
            :items="[
              { label: '订单号', value: text(state.data.value.order_no ?? state.data.value.id) },
              { label: '状态', value: text(state.data.value.status) },
              { label: '买家标识（脱敏）', value: text(state.data.value.buyer_label) },
              { label: '下单时间', value: formatDateTime(state.data.value.created_at) },
              { label: '版本', value: text(state.data.value.version) },
            ]"
            :columns="1"
          />
        </ModuleCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">隐私提示</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>· 后台不显示手机号明文、地址明文与支付原始报文。</li>
            <li>· 发货所需的完整收件信息由履约系统按最小授权提供，不在本页展示。</li>
            <li>· 任何敏感操作都会写入审计：谁、在什么时候、对哪个订单做了什么。</li>
          </ul>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="shipmentConfirmOpen"
      title="登记发货"
      :target="`订单 ${text(state.data.value?.order_no ?? orderId)}`"
      :change="`承运商 ${shipmentForm.carrier || '未填写'} · 运单号 ${shipmentForm.tracking_no || '未填写'}`"
      consequence="发货登记后客户立刻可见，订单进入已发货状态；运单号填错需要重新登记，客户会看到多条记录。"
      confirm-label="确认登记发货"
      tone="primary"
      :pending="shipmentPending"
      @confirm="submitShipment"
    />

    <ConfirmDialog
      v-model="closeConfirmOpen"
      title="关闭订单"
      :target="`订单 ${text(state.data.value?.order_no ?? orderId)}`"
      change="状态 → 已关闭；订单不再进入发货流程。"
      :amount="`应付 ${formatMoneyExact(state.data.value?.total_cents)} · 已付 ${formatMoneyExact(state.data.value?.paid_cents)}`"
      consequence="关闭不会删除订单与支付记录，也不会自动退款；已收款必须先完成原路退款，否则客户资金滞留在渠道。"
      confirm-label="确认关闭这个订单"
      tone="danger"
      :pending="closePending"
      :require-typed="String(state.data.value?.order_no ?? orderId)"
      typed-label="请输入订单号确认关闭"
      @confirm="submitClose"
    />
  </div>
</template>
