<script setup lang="ts">
/**
 * 公开估价请求：只读联系信息、标记已联系或关闭。
 * 估价是参考区间，不是报价；这里不生成报价、订单或付款。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  usePageData,
  type AdminList,
  type EstimateRequestRecord,
} from "@/modules/admin/api/admin";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import { AdminPageHeader, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow } from "@/shared/ui/base";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const statusFilter = ref("");
const state = usePageData<AdminList<EstimateRequestRecord>>(
  () => adminApi.estimates({ status: statusFilter.value || undefined, limit: 50 }),
  { items: [] },
  "估价请求",
  () => auth.handleSessionExpired(),
);

const pending = ref(false);
const confirmOpen = ref(false);
const target = ref<EstimateRequestRecord | null>(null);
const mode = ref<"CONTACTED" | "CLOSED">("CONTACTED");

onMounted(state.load);

const columns: TableColumn[] = [
  { key: "applicant", label: "申请人 / 角色" },
  { key: "contact", label: "联系方式", width: "190px" },
  { key: "series", label: "系列 / 风格", width: "150px", hideOnMobile: true },
  { key: "range", label: "参考区间", width: "150px" },
  { key: "status_label", label: "状态", width: "100px" },
  { key: "created", label: "提交时间", width: "160px", hideOnMobile: true },
];

const STYLE_LABEL: Record<string, string> = {
  standard: "标准比例",
  fitted: "贴身剪裁",
  special: "特殊结构",
};
const CONTACT_LABEL: Record<string, string> = { email: "邮箱", phone: "手机", wechat: "微信" };
const STATUS_LABEL: Record<string, string> = { NEW: "待处理", CONTACTED: "已联系", CLOSED: "已关闭" };

const rows = computed(() =>
  state.data.value.items.map((item) => ({
    ...item,
    _status: item.status ?? "NEW",
    applicant: item.character_name || item.name || "未命名",
    contact: `${CONTACT_LABEL[item.contact_type ?? ""] ?? "联系方式"} · ${item.contact_hint || item.contact || "—"}`,
    series: `${item.series_title || item.series_code || "—"} · ${STYLE_LABEL[item.style ?? ""] ?? item.style ?? "—"}`,
    range: `${formatMoneyExact(item.estimate_low_cents, (item.currency as "CNY" | "USD") ?? "CNY")} – ${formatMoneyExact(item.estimate_high_cents, (item.currency as "CNY" | "USD") ?? "CNY")}`,
    status_label: STATUS_LABEL[item.status ?? "NEW"] ?? item.status ?? "—",
    created: formatDateTime(item.created_at),
  })),
);

function openAction(row: EstimateRequestRecord, next: "CONTACTED" | "CLOSED"): void {
  target.value = row;
  mode.value = next;
  confirmOpen.value = true;
}

async function submit(payload: { reason: string }): Promise<void> {
  const row = target.value;
  if (!row) return;
  pending.value = true;
  try {
    await adminApi.updateEstimateStatus(row.id, { status: mode.value, note: payload.reason });
    toast.success(
      mode.value === "CONTACTED" ? "已标记为已联系" : "已关闭估价请求",
      "估价请求不会生成报价、订单或付款记录。",
    );
    confirmOpen.value = false;
    target.value = null;
    await state.load();
  } catch (caught) {
    toast.error("操作失败", actionErrorMessage(caught, "请稍后重试。").message);
  } finally {
    pending.value = false;
  }
}
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Estimates"
      title="估价请求"
      description="来自公开估价页的参考估价请求。这里只记录联系与跟进状态，不生成报价、订单或付款。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="参考区间不是报价"
      description="客户提交时已看到区间与免责声明。正式报价仍需人工确认角色复杂度、材料与工期后发出。"
    />

    <form class="grid gap-4 rounded-lg bg-surface p-5 shadow-soft sm:grid-cols-[220px_auto]" @submit.prevent="state.load()">
      <FieldRow label="状态" for="estimate-status">
        <select id="estimate-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="NEW">待处理</option>
          <option value="CONTACTED">已联系</option>
          <option value="CLOSED">已关闭</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="statusFilter = ''; state.load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="rows"
      :loading="state.loading.value"
      :error="state.error.value"
      empty-title="还没有估价请求"
      empty-description="公开估价页提交后会出现在这里；当前筛选条件下没有记录。"
      @retry="state.load()"
    >
      <template #cell-applicant="{ row }">
        <span class="font-medium">{{ row.applicant }}</span>
        <p class="mt-0.5 text-xs text-ink-faint">{{ row.name }} · {{ row.species || "未填物种" }}</p>
      </template>
      <template #cell-status_label="{ row }">
        <span
          class="inline-flex rounded-full px-2.5 py-1 text-[11px]"
          :class="row._status === 'NEW' ? 'bg-amber-soft text-amber-strong' : row._status === 'CONTACTED' ? 'bg-jade-soft text-jade' : 'bg-canvas-sunk text-ink-muted'"
        >
          {{ row.status_label }}
        </span>
      </template>
      <template #actions="{ row }">
        <AppButton
          v-if="row._status !== 'CLOSED' && row._status !== 'CONTACTED'"
          size="sm"
          variant="secondary"
          @click="openAction(row, 'CONTACTED')"
        >
          标记已联系
        </AppButton>
        <AppButton
          v-if="row._status !== 'CLOSED'"
          size="sm"
          variant="quiet"
          @click="openAction(row, 'CLOSED')"
        >
          关闭
        </AppButton>
        <span v-if="row._status === 'CLOSED'" class="text-xs text-ink-faint">已结束</span>
      </template>
    </DataTable>

    <ConfirmDialog
      v-model="confirmOpen"
      :title="mode === 'CONTACTED' ? '标记估价请求已联系' : '关闭估价请求'"
      :target="target?.character_name || target?.name || '估价请求'"
      :change="mode === 'CONTACTED' ? '待处理 → 已联系' : '当前状态 → 已关闭'"
      consequence="只会更新跟进状态并写入审计；不会生成报价、订单或付款，也不会通知客户付款。"
      :confirm-label="mode === 'CONTACTED' ? '确认已联系' : '确认关闭'"
      :tone="mode === 'CONTACTED' ? 'primary' : 'danger'"
      :pending="pending"
      @confirm="submit"
      @cancel="confirmOpen = false"
    />
  </div>
</template>
