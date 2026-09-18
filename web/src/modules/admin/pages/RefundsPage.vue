<script setup lang="ts">
/**
 * 退款：申请与批准分离。
 *
 * 硬约束（08/10 文档）：发起退款需要 `refund.request`，批准需要 `refund.approve`；
 * 同一账号同时具备两种权限时必须在界面给出职责分离警告。
 * 只走原路退款；金额超过可退余额会被服务端拒绝。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type RefundRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<RefundRecord>>(
  () => adminApi.refunds({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "退款列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");

/** 职责分离警告：同时具备申请与批准权限时必须显式提示。 */
const bothPermissions = computed(
  () => auth.can(ADMIN_PERMISSIONS.refundRequest) && auth.can(ADMIN_PERMISSIONS.refundApprove),
);

const columns: TableColumn[] = [
  { key: "order_id", label: "订单" },
  { key: "amount_cents", label: "退款金额", width: "130px" },
  { key: "status", label: "状态", width: "130px" },
  { key: "reason", label: "申请理由", hideOnMobile: true },
  { key: "requested_by", label: "申请人", width: "120px", hideOnMobile: true },
  { key: "approved_by", label: "批准人", width: "120px", hideOnMobile: true },
  { key: "created_at", label: "申请时间", width: "170px", hideOnMobile: true },
];

const requestForm = ref({ order_id: "", amount_yuan: 0, reason: "", channel: "wechat" });
const requestError = ref("");
const requestPending = ref(false);

const approveTarget = ref<RefundRecord | null>(null);
const approveReason = ref("");
const approveError = ref("");
const approvePending = ref(false);
const approveConfirmOpen = ref(false);

const retryPendingId = ref("");

const requestCents = computed(() => Math.round(requestForm.value.amount_yuan * 100));

const statusTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "SUCCEEDED" || status === "REFUNDED") return "jade";
  if (status === "PENDING" || status === "REQUESTED" || status === "PROCESSING") return "amber";
  if (status === "FAILED" || status === "REJECTED") return "wine";
  return "neutral";
};

const statusLabel = (value: unknown): string => {
  const map: Record<string, string> = {
    REQUESTED: "待批准",
    PENDING: "待批准",
    APPROVED: "已批准，待渠道处理",
    PROCESSING: "渠道处理中",
    SUCCEEDED: "退款成功",
    REFUNDED: "退款成功",
    FAILED: "退款失败",
    REJECTED: "已驳回",
  };
  return map[String(value ?? "")] ?? text(value, "未知状态");
};

async function submitRequest(): Promise<void> {
  requestError.value = "";
  if (!requestForm.value.order_id.trim()) {
    requestError.value = "请填写需要退款的订单号。";
    return;
  }
  if (!Number.isInteger(requestCents.value) || requestCents.value <= 0) {
    requestError.value = "退款金额必须大于 0。";
    return;
  }
  if (requestForm.value.reason.trim().length < 6) {
    requestError.value = "请填写至少 6 个字的退款理由；理由会写入审计记录。";
    return;
  }
  requestPending.value = true;
  try {
    await adminApi.requestRefund({
      order_id: requestForm.value.order_id.trim(),
      amount_cents: requestCents.value,
      reason: requestForm.value.reason.trim(),
      channel: requestForm.value.channel,
    });
    toast.success("退款申请已提交", "申请只完成登记；需要另一位员工用 refund.approve 权限批准后才会发起渠道退款。");
    requestForm.value = { order_id: "", amount_yuan: 0, reason: "", channel: "wechat" };
    await state.load();
  } catch (caught) {
    requestError.value = actionErrorMessage(caught, "提交退款申请失败，请稍后重试。").message;
  } finally {
    requestPending.value = false;
  }
}

function openApprove(row: RefundRecord): void {
  approveTarget.value = row;
  approveReason.value = "";
  approveError.value = "";
}

function requestApproveConfirm(): void {
  if (approveReason.value.trim().length < 6) {
    approveError.value = "请填写至少 6 个字的批准理由，说明核对依据。";
    return;
  }
  approveError.value = "";
  approveConfirmOpen.value = true;
}

async function submitApprove(): Promise<void> {
  const row = approveTarget.value;
  if (!row) return;
  approvePending.value = true;
  try {
    await adminApi.approveRefund(row.id, { reason: approveReason.value.trim(), version: row.version ?? undefined });
    toast.success("退款已批准", "已按原路发起退款；请到渠道查单确认到账，失败时重新查单而不是重复退款。");
    approveConfirmOpen.value = false;
    approveTarget.value = null;
    await state.load();
  } catch (caught) {
    approveError.value = actionErrorMessage(caught, "批准失败，请稍后重试。").message;
    approveConfirmOpen.value = false;
  } finally {
    approvePending.value = false;
  }
}

async function retryQuery(row: RefundRecord): Promise<void> {
  retryPendingId.value = row.id;
  try {
    const result = await adminApi.retryRefundQuery(row.id);
    toast.success("已重新查单", `渠道返回：${statusLabel(result.status)}。`);
    await state.load();
  } catch (caught) {
    toast.error("查单失败", actionErrorMessage(caught, "请稍后重试。").message);
  } finally {
    retryPendingId.value = "";
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Refunds"
      title="退款"
      description="退款申请与批准必须由不同员工完成：先提交申请，再由具备批准权限的员工核对后批准。全部走原路退款。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      v-if="bothPermissions"
      tone="danger"
      title="职责分离警告：当前账号同时具备退款申请与批准权限"
      description="同一账号完成申请与批准会削弱复核效果，也存在自我批准的风险。请让另一位员工批准，或请管理员收回其中一项权限。"
    />

    <InlineNotice
      title="只做原路退款"
      description="退款金额不能超过该订单可退余额，超过会被服务端拒绝。渠道失败时重新查单确认结果，不要重复发起退款，避免重复出款。"
    />

    <div class="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
      <div class="space-y-6">
        <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
          <FieldRow label="订单号或流水号" for="refund-q">
            <input id="refund-q" v-model="search" type="search" class="field-base" />
          </FieldRow>
          <FieldRow label="状态" for="refund-status">
            <select id="refund-status" v-model="statusFilter" class="field-base">
              <option value="">全部状态</option>
              <option value="REQUESTED">待批准</option>
              <option value="APPROVED">已批准</option>
              <option value="SUCCEEDED">退款成功</option>
              <option value="FAILED">退款失败</option>
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
          empty-title="没有退款记录"
          empty-description="客户提交售后或员工发起退款申请后，记录会出现在这里。"
          @retry="state.load()"
        >
          <template #cell-order_id="{ row }">
            <span class="font-mono text-xs">{{ text(row.order_id) }}</span>
          </template>
          <template #cell-amount_cents="{ row }">
            <span class="tabular-nums">{{ formatMoneyExact(row.amount_cents as number) }}</span>
          </template>
          <template #cell-status="{ row }">
            <StatusBadge :tone="statusTone(row.status)" :label="statusLabel(row.status)" />
          </template>
          <template #cell-created_at="{ row }">
            <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
          </template>
          <template #actions="{ row }">
            <AppButton size="sm" variant="secondary" :to="`/admin/orders/${row.order_id}`">查看订单</AppButton>
            <PermissionGate :code="ADMIN_PERMISSIONS.refundApprove" :show-missing="false">
              <AppButton
                v-if="row.status === 'REQUESTED' || row.status === 'PENDING'"
                size="sm"
                variant="danger"
                @click="openApprove(row)"
              >
                批准退款
              </AppButton>
              <AppButton
                v-if="row.status === 'FAILED' || row.status === 'PROCESSING'"
                size="sm"
                variant="quiet"
                :loading="retryPendingId === row.id"
                @click="retryQuery(row)"
              >
                重新查单
              </AppButton>
            </PermissionGate>
          </template>
        </DataTable>
      </div>

      <div class="space-y-6">
        <SurfaceCard>
          <SectionHeading
            level="2"
            title="发起退款申请"
            description="申请只做登记，不会立即出款；需要另一位员工批准后才会向渠道发起原路退款。"
          />
          <PermissionGate :code="ADMIN_PERMISSIONS.refundRequest">
            <div class="mt-4 space-y-4">
              <FieldRow label="订单号" required for="refund-order" hint="只填写订单号，不需要也不允许粘贴支付报文">
                <input id="refund-order" v-model="requestForm.order_id" type="text" class="field-base font-mono" />
              </FieldRow>
              <div class="grid gap-4 sm:grid-cols-2">
                <FieldRow label="退款金额（元）" required for="refund-amount">
                  <input id="refund-amount" v-model.number="requestForm.amount_yuan" type="number" min="0" step="0.01" class="field-base tabular-nums" />
                </FieldRow>
                <FieldRow label="原支付渠道" required for="refund-channel">
                  <select id="refund-channel" v-model="requestForm.channel" class="field-base">
                    <option value="wechat">微信支付</option>
                    <option value="alipay">支付宝</option>
                  </select>
                </FieldRow>
              </div>
              <FieldRow label="退款理由" required for="refund-reason" hint="例如：客户取消未发货订单，工单 Q-2026-0233">
                <textarea id="refund-reason" v-model="requestForm.reason" rows="3" class="field-base resize-y" />
              </FieldRow>
              <p class="text-sm text-ink-soft">申请金额：{{ formatMoneyExact(requestCents) }}</p>
              <p v-if="requestError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
                {{ requestError }}
              </p>
              <AppButton :loading="requestPending" @click="submitRequest">提交退款申请</AppButton>
            </div>
          </PermissionGate>
        </SurfaceCard>

        <SurfaceCard v-if="approveTarget">
          <h2 class="font-display text-lg">批准退款</h2>
          <FieldList
            class="mt-3"
            :items="[
              { label: '订单', value: text(approveTarget.order_id) },
              { label: '退款金额', value: formatMoneyExact(approveTarget.amount_cents) },
              { label: '申请理由', value: text(approveTarget.reason) },
              { label: '申请人', value: text(approveTarget.requested_by) },
            ]"
          />
          <FieldRow class="mt-4" label="批准理由" required for="refund-approve-reason" hint="例如：已核对订单状态与可退余额">
            <input id="refund-approve-reason" v-model="approveReason" type="text" class="field-base" />
          </FieldRow>
          <p v-if="approveError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ approveError }}
          </p>
          <div class="mt-4 flex flex-wrap gap-3">
            <AppButton variant="danger" @click="requestApproveConfirm">提交批准</AppButton>
            <AppButton variant="quiet" @click="approveTarget = null">取消</AppButton>
          </div>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="approveConfirmOpen"
      title="批准退款（原路退回）"
      :target="approveTarget ? `退款单 ${text(approveTarget.id)} · 订单 ${text(approveTarget.order_id)}` : ''"
      change="状态 → 已批准；随后按原支付渠道发起退款，客户到账时间以渠道为准。"
      :amount="formatMoneyExact(approveTarget?.amount_cents)"
      consequence="批准会真实出款且不可撤回。渠道失败时要重新查单确认结果，禁止重复发起退款；超过可退余额的金额会被服务端拒绝。"
      confirm-label="确认批准并原路退款"
      tone="danger"
      :pending="approvePending"
      :require-typed="approveTarget ? String(approveTarget.order_id ?? '') : ''"
      typed-label="请输入订单号确认出款"
      @confirm="submitApprove"
    />
  </div>
</template>
