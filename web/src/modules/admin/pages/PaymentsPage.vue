<script setup lang="ts">
/**
 * 收款与异常单：收款记录、异常单、渠道查单。
 *
 * 约束（08 文档）：成功只认已验签通知或服务端主动查单；超时返回 UNKNOWN 时必须用**同商户单号**查单，
 * 不能换新单号重新下单，也不能把未确认渲染成失败。这里不显示支付原始报文。
 */
import { onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  collect,
  describeFailure,
  text,
  usePageData,
  type AdminList,
  type FailureView,
  type PaymentAttentionItem,
  type PaymentRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<PaymentRecord>>(
  () => adminApi.payments({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "收款记录",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");
const attention = ref<PaymentAttentionItem[]>([]);
const attentionError = ref<FailureView | null>(null);

const columns: TableColumn[] = [
  { key: "order_id", label: "订单" },
  { key: "channel", label: "渠道", width: "110px" },
  { key: "amount_cents", label: "金额", width: "130px" },
  { key: "status", label: "状态", width: "130px" },
  { key: "verified_at", label: "确认时间", width: "170px", hideOnMobile: true },
];

const attentionColumns: TableColumn[] = [
  { key: "order_id", label: "订单" },
  { key: "kind", label: "异常类型", width: "160px" },
  { key: "amount_cents", label: "金额", width: "130px" },
  { key: "status", label: "状态", width: "130px" },
  { key: "created_at", label: "发生时间", width: "170px", hideOnMobile: true },
];

const queryTarget = ref<PaymentRecord | null>(null);
const queryReason = ref("");
const queryError = ref("");
const queryPending = ref(false);
const queryConfirmOpen = ref(false);

/** 未确认与失败必须分开显示：超时不等于失败（08 文档）。 */
const statusTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "SUCCEEDED" || status === "PAID" || status === "VERIFIED") return "jade";
  if (status === "PENDING" || status === "UNKNOWN" || status === "PROCESSING") return "amber";
  if (status === "FAILED" || status === "CLOSED") return "wine";
  return "neutral";
};

const statusLabel = (value: unknown): string => {
  const map: Record<string, string> = {
    SUCCEEDED: "已确认收款",
    PAID: "已确认收款",
    VERIFIED: "已确认收款",
    PENDING: "结果确认中",
    UNKNOWN: "结果尚未确认",
    PROCESSING: "处理中",
    FAILED: "渠道失败",
    CLOSED: "渠道已关闭",
  };
  const status = String(value ?? "");
  return map[status] ?? text(value, "未知状态");
};

async function load(): Promise<void> {
  await state.load();
  const result = await collect<{ attention: AdminList<PaymentAttentionItem> }>({
    attention: () => adminApi.paymentAttention({ limit: 20 }),
  });
  if (result.ok.attention) {
    attention.value = result.ok.attention.items;
    attentionError.value = null;
  } else {
    attention.value = [];
    attentionError.value = describeFailure(result.failed[0]?.error, "异常单列表");
  }
}

function openQuery(row: PaymentRecord): void {
  queryTarget.value = row;
  queryReason.value = "";
  queryError.value = "";
}

function requestQueryConfirm(): void {
  if (queryReason.value.trim().length < 6) {
    queryError.value = "请填写至少 6 个字的查单理由，例如「客户反馈已付款但页面未更新」。";
    return;
  }
  queryError.value = "";
  queryConfirmOpen.value = true;
}

async function submitQuery(): Promise<void> {
  const row = queryTarget.value;
  if (!row) return;
  queryPending.value = true;
  try {
    const result = await adminApi.queryPaymentChannel(row.id);
    toast.success("已向渠道查单", `渠道返回状态：${statusLabel(result.status)}。查单使用原商户单号，不会新建订单。`);
    queryConfirmOpen.value = false;
    queryTarget.value = null;
    await load();
  } catch (caught) {
    queryError.value = actionErrorMessage(caught, "查单失败，请稍后重试。").message;
    queryConfirmOpen.value = false;
  } finally {
    queryPending.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Payments"
      title="收款与异常单"
      description="核对收款记录、处理异常单、向渠道主动查单。这里只显示本地状态与渠道状态，不显示支付原始报文与商户密钥。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="「结果尚未确认」不等于失败"
      description="渠道超时返回 UNKNOWN 时，必须用同一商户单号查单，等待确认后再决定是否关闭订单；换新单号重新下单会造成重复收款风险。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="load">
      <FieldRow label="订单号或流水号" for="pay-q">
        <input id="pay-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="状态" for="pay-status">
        <select id="pay-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="SUCCEEDED">已确认收款</option>
          <option value="PENDING">结果确认中</option>
          <option value="UNKNOWN">结果尚未确认</option>
          <option value="FAILED">渠道失败</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; statusFilter = ''; load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="没有收款记录"
      empty-description="客户发起支付后，记录会出现在这里；只有确认成功的才计入已收金额。"
      @retry="load"
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
      <template #cell-verified_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.verified_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openQuery(row)">向渠道查单</AppButton>
        <AppButton size="sm" variant="quiet" :to="`/admin/orders/${row.order_id}`">查看订单</AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
      <SurfaceCard>
        <h2 class="font-display text-lg">异常单</h2>
        <p class="mt-1 text-xs text-ink-faint">金额不一致、重复通知、超时未确认等需要人工处理的记录。</p>
        <div class="mt-4">
          <DataTable
            :columns="attentionColumns"
            :rows="attention"
            :loading="state.loading.value"
            :error="attentionError"
            dense
            empty-title="没有异常单"
            empty-description="当前没有需要人工处理的支付异常。"
            @retry="load"
          >
            <template #cell-amount_cents="{ row }">
              <span class="tabular-nums">{{ formatMoneyExact(row.amount_cents as number) }}</span>
            </template>
            <template #cell-status="{ row }">
              <StatusBadge :tone="statusTone(row.status)" :label="statusLabel(row.status)" />
            </template>
            <template #cell-created_at="{ row }">
              <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
            </template>
            <template #actions>
              <span class="text-xs text-ink-faint">先查单，再决定退款或关闭</span>
            </template>
          </DataTable>
        </div>
      </SurfaceCard>

      <ModuleCard title="处理原则" status="流程说明">
        <ul class="space-y-2 text-sm text-ink-muted">
          <li>· 已确认收款：可以进入履约；退款走「退款」模块，申请与批准分离。</li>
          <li>· 结果尚未确认：先查单，禁止把订单渲染成已取消。</li>
          <li>· 渠道失败：可以引导客户重新发起支付，但沿用同一订单与商户单号。</li>
          <li>· 金额与订单不一致：转入「对账」，留下归因与处置记录。</li>
        </ul>
      </ModuleCard>
    </div>

    <SurfaceCard v-if="queryTarget">
      <h2 class="font-display text-lg">向渠道查单</h2>
      <FieldList
        class="mt-3"
        :items="[
          { label: '本地流水号', value: text(queryTarget.id) },
          { label: '订单', value: text(queryTarget.order_id) },
          { label: '渠道', value: text(queryTarget.channel) },
          { label: '金额', value: formatMoneyExact(queryTarget.amount_cents) },
          { label: '当前状态', value: statusLabel(queryTarget.status) },
        ]"
      />
      <FieldRow class="mt-4" label="查单理由" required for="pay-query-reason" hint="例如：客户反馈已付款但页面未更新">
        <input id="pay-query-reason" v-model="queryReason" type="text" class="field-base" />
      </FieldRow>
      <p v-if="queryError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ queryError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton @click="requestQueryConfirm">提交查单</AppButton>
        <AppButton variant="quiet" @click="queryTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="queryConfirmOpen"
      title="向支付渠道主动查单"
      :target="queryTarget ? `流水 ${text(queryTarget.id)} · 订单 ${text(queryTarget.order_id)}` : ''"
      change="按原商户单号向渠道查询，并把结果写入本地状态。"
      :amount="formatMoneyExact(queryTarget?.amount_cents)"
      consequence="查单只读取渠道结果，不会新建支付单、不会重复收款；如果渠道返回成功，订单会按规则入账并记录审计。"
      confirm-label="确认发起查单"
      tone="primary"
      :pending="queryPending"
      @confirm="submitQuery"
    />
  </div>
</template>
