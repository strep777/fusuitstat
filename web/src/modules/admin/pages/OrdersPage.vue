<script setup lang="ts">
/**
 * 订单列表：明细入口、发货、关闭原因、售后入口。
 *
 * 硬约束（08/11 文档）：**禁止修改已付金额**。界面明确写出这一点，
 * 金额变化只能通过真实收款或原路退款发生。列表只显示脱敏买家标识。
 */
import { onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type OrderRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<OrderRecord>>(
  () => adminApi.orders({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "订单列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");

const columns: TableColumn[] = [
  { key: "order_no", label: "订单号" },
  { key: "status", label: "状态", width: "130px" },
  { key: "total_cents", label: "应付", width: "120px" },
  { key: "paid_cents", label: "已付", width: "120px" },
  { key: "refunded_cents", label: "已退款", width: "120px", hideOnMobile: true },
  { key: "buyer_label", label: "买家标识", width: "140px", hideOnMobile: true },
  { key: "created_at", label: "下单时间", width: "170px", hideOnMobile: true },
];

const closeTarget = ref<OrderRecord | null>(null);
const closeReason = ref("");
const closeError = ref("");
const closePending = ref(false);
const closeConfirmOpen = ref(false);

function openClose(row: OrderRecord): void {
  closeTarget.value = row;
  closeReason.value = "";
  closeError.value = "";
}

function requestCloseConfirm(): void {
  if (closeReason.value.trim().length < 6) {
    closeError.value = "请填写至少 6 个字的关闭理由，客户与审计都会看到。";
    return;
  }
  closeError.value = "";
  closeConfirmOpen.value = true;
}

async function submitClose(): Promise<void> {
  const row = closeTarget.value;
  if (!row) return;
  closePending.value = true;
  try {
    await adminApi.closeOrder(row.id, { reason: closeReason.value.trim(), version: row.version ?? undefined });
    toast.success("订单已关闭", "关闭不会删除订单历史；如已收款需要先走退款流程。");
    closeConfirmOpen.value = false;
    closeTarget.value = null;
    await state.load();
  } catch (caught) {
    closeError.value = actionErrorMessage(caught, "关闭订单失败，请稍后重试。").message;
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
      eyebrow="Orders"
      title="订单"
      description="查看订单明细、发起发货、写清关闭原因，并进入售后流程。金额与状态由服务端按支付通知或主动查单裁定。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="danger"
      title="禁止修改已付金额"
      description="后台没有任何改写「已付金额」的入口。金额变化只能由真实收款或原路退款产生；发现金额不一致请走「对账」核对渠道账单，不要手工改数。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="订单号或买家标识" for="order-q" hint="买家标识已脱敏，不含手机号与地址">
        <input id="order-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="状态" for="order-status">
        <select id="order-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="PENDING_PAYMENT">待付款</option>
          <option value="PAID">已付款</option>
          <option value="FULFILLING">备货中</option>
          <option value="SHIPPED">已发货</option>
          <option value="COMPLETED">已完成</option>
          <option value="CLOSED">已关闭</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; statusFilter = ''; state.load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="没有符合条件的订单"
      empty-description="可以调整状态筛选，或稍后再刷新。"
      @retry="state.load()"
    >
      <template #cell-order_no="{ row }">
        <RouterLink :to="`/admin/orders/${row.id}`" class="link-underline font-mono text-xs hover:text-amber-strong">
          {{ text(row.order_no ?? row.id) }}
        </RouterLink>
      </template>
      <template #cell-total_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.total_cents as number) }}</span>
      </template>
      <template #cell-paid_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.paid_cents as number) }}</span>
      </template>
      <template #cell-refunded_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.refunded_cents as number) }}</span>
      </template>
      <template #cell-created_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" :to="`/admin/orders/${row.id}`">查看明细</AppButton>
        <PermissionGate :code="ADMIN_PERMISSIONS.orderFulfill" :show-missing="false">
          <AppButton
            v-if="row.status !== 'CLOSED'"
            size="sm"
            variant="quiet"
            @click="openClose(row)"
          >
            关闭订单
          </AppButton>
        </PermissionGate>
      </template>
    </DataTable>

    <SurfaceCard v-if="closeTarget">
      <h2 class="font-display text-lg">关闭订单：{{ text(closeTarget.order_no ?? closeTarget.id) }}</h2>
      <FieldList
        class="mt-3"
        :items="[
          { label: '当前状态', value: text(closeTarget.status) },
          { label: '应付金额', value: formatMoneyExact(closeTarget.total_cents) },
          { label: '已付金额（只读）', value: formatMoneyExact(closeTarget.paid_cents) },
          { label: '已退款金额（只读）', value: formatMoneyExact(closeTarget.refunded_cents) },
        ]"
      />
      <p class="mt-3 text-xs text-ink-faint">
        关闭不会改写已付金额；如果客户已经付款，需要先走退款流程，再关闭订单。
      </p>
      <FieldRow class="mt-4" label="关闭原因" required for="close-reason" hint="例如：客户主动取消，沟通记录见工单 Q-2026-0208">
        <input id="close-reason" v-model="closeReason" type="text" class="field-base" />
      </FieldRow>
      <p v-if="closeError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ closeError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton variant="danger" @click="requestCloseConfirm">提交关闭</AppButton>
        <AppButton variant="quiet" @click="closeTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="closeConfirmOpen"
      title="关闭订单"
      :target="closeTarget ? `订单 ${text(closeTarget.order_no ?? closeTarget.id)}` : ''"
      change="状态 → 已关闭；订单不再进入发货流程。"
      :amount="`应付 ${formatMoneyExact(closeTarget?.total_cents)} · 已付 ${formatMoneyExact(closeTarget?.paid_cents)}`"
      consequence="关闭不会删除订单与支付记录，也不会自动退款。已收款订单必须先完成原路退款，否则客户资金会滞留在渠道。"
      confirm-label="确认关闭这个订单"
      tone="danger"
      :pending="closePending"
      :require-typed="closeTarget ? String(closeTarget.order_no ?? closeTarget.id) : ''"
      typed-label="请输入订单号确认关闭"
      @confirm="submitClose"
    />
  </div>
</template>
