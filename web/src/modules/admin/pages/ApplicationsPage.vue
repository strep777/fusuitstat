<script setup lang="ts">
/**
 * 服务与掉落申请审批（FR-025 / C28 / C31）。
 *
 * 洗毛预约与掉落商品共用 stock_applications；这里只做人工审批：
 * 通过时给出购买窗口，不收款、不锁库存。申请人只显示不可逆的用户编号尾部，
 * 不展示手机号等个人信息。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  collect,
  describeFailure,
  text,
  type CatalogApplicationRecord,
  type FailureView,
} from "@/modules/admin/api/admin";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import { AdminPageHeader, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, StatusBadge } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const items = ref<CatalogApplicationRecord[]>([]);
const loading = ref(true);
const loadError = ref<FailureView | null>(null);
const statusFilter = ref("PENDING");

const decisionTarget = ref<CatalogApplicationRecord | null>(null);
const decisionKind = ref<"APPROVED" | "REJECTED">("APPROVED");
const decisionOpen = ref(false);
const decisionPending = ref(false);
const decisionError = ref("");

const columns: TableColumn[] = [
  { key: "product_title", label: "套餐 / 商品" },
  { key: "kind", label: "类型", width: "110px" },
  { key: "preferred_date", label: "期望日期", width: "120px" },
  { key: "return_method", label: "返还", width: "90px" },
  { key: "status", label: "状态", width: "110px" },
  { key: "created_at", label: "提交时间", width: "170px", hideOnMobile: true },
];

const rows = computed(() =>
  items.value
    .filter((item) => {
      if (statusFilter.value === "ALL") return true;
      if (statusFilter.value === "PENDING") return item.status === "SUBMITTED" || item.status === "NEEDS_INFO";
      return item.status === statusFilter.value;
    })
    .map((item) => ({
      ...item,
      _status: item.status,
      kind: kindLabel(item.kind),
      status: statusLabel(item.status),
      return_method: item.return_method === "ship" ? "寄回" : item.return_method === "pickup" ? "到店" : "—",
      preferred_date: item.preferred_date ?? "—",
      created_at: item.created_at ? formatDateTime(item.created_at) : "—",
      product_title: item.product_title ?? "商品已下架",
    })),
);

function kindLabel(value: unknown): string {
  const kind = String(value ?? "");
  if (kind === "cleaning") return "洗毛";
  if (kind === "repair") return "修补";
  if (kind === "service") return "服务";
  if (kind === "drop") return "掉落";
  return kind || "—";
}

function statusLabel(value: unknown): string {
  const status = String(value ?? "");
  return (
    {
      SUBMITTED: "待审批",
      NEEDS_INFO: "待补件",
      APPROVED: "已通过",
      REJECTED: "已拒绝",
      CONVERTED: "已下单",
      EXPIRED: "已过期",
    }[status] ?? status
  );
}

function tone(value: unknown): "jade" | "amber" | "wine" | "neutral" {
  const status = String(value ?? "");
  if (status === "APPROVED" || status === "CONVERTED") return "jade";
  if (status === "SUBMITTED" || status === "NEEDS_INFO") return "amber";
  if (status === "REJECTED" || status === "EXPIRED") return "wine";
  return "neutral";
}

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = null;
  const result = await collect<{ apps: Awaited<ReturnType<typeof adminApi.stockApplications>> }>({
    apps: () => adminApi.stockApplications({ limit: 100 }),
  });
  if (result.ok.apps) {
    items.value = result.ok.apps.items;
  } else {
    items.value = [];
    loadError.value = describeFailure(result.failed[0]?.error, "申请列表");
  }
  loading.value = false;
}

function openDecision(row: CatalogApplicationRecord, kind: "APPROVED" | "REJECTED"): void {
  decisionTarget.value = row;
  decisionKind.value = kind;
  decisionError.value = "";
  decisionOpen.value = true;
}

async function submitDecision(payload: { reason: string }): Promise<void> {
  const target = decisionTarget.value;
  if (!target) return;
  decisionPending.value = true;
  decisionError.value = "";
  try {
    await adminApi.decideStockApplication(target.id, {
      decision: decisionKind.value,
      reason: payload.reason,
      priority_rank: decisionKind.value === "APPROVED" ? 1 : undefined,
      purchase_window_days: 7,
    });
    decisionOpen.value = false;
    decisionTarget.value = null;
    toast.success(
      decisionKind.value === "APPROVED" ? "申请已通过" : "申请已拒绝",
      decisionKind.value === "APPROVED"
        ? "客户会收到站内通知，需在 7 天内下单；预约不锁库存。"
        : "拒绝理由会通知客户，该申请不能恢复。",
    );
    await load();
  } catch (caught) {
    decisionError.value = actionErrorMessage(caught, "审批失败，请稍后重试。").message;
    decisionOpen.value = false;
  } finally {
    decisionPending.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Catalog · Applications"
      title="服务与掉落申请"
      description="审批洗毛预约与掉落申请。通过后给出购买窗口，不收款、不锁库存；预约日期与返还方式只用于排产。"
      :permission="auth.can('catalog.write') ? '已授权 catalog.write' : '只读：缺少 catalog.write'"
    />

    <InlineNotice tone="info" title="审批不等于成交">
      通过只代表可以进入购买或报价阶段；客户未在窗口内下单时，申请自动过期。洗毛套餐的最终价格按毛面与工作量确认。
    </InlineNotice>

    <div class="flex flex-wrap gap-2" role="group" aria-label="按状态筛选">
      <button
        v-for="option in [
          { value: 'PENDING', label: '待处理' },
          { value: 'APPROVED', label: '已通过' },
          { value: 'REJECTED', label: '已拒绝' },
          { value: 'ALL', label: '全部' },
        ]"
        :key="option.value"
        type="button"
        class="min-h-10 rounded-full border-2 px-4 text-sm font-bold"
        :class="statusFilter === option.value ? 'border-brand bg-brand text-white' : 'border-ink/10 bg-surface'"
        @click="statusFilter = option.value"
      >
        {{ option.label }}
      </button>
      <AppButton size="sm" variant="secondary" :loading="loading" @click="load">刷新</AppButton>
    </div>

    <ModuleCard title="申请列表" status="人工审批">
      <p v-if="decisionError" class="mb-4 border-2 border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
        {{ decisionError }}
      </p>
      <DataTable
        :columns="columns"
        :rows="rows"
        :loading="loading"
        :error="loadError"
        empty-title="没有符合条件的申请"
        empty-description="客户在洗毛页或商品页提交后会出现在这里；待处理列表只显示未审批与待补件。"
      >
        <template #cell-status="{ row }">
          <StatusBadge :tone="tone(row._status)" :label="String(row.status ?? '—')" />
        </template>
        <template #actions="{ row }">
          <div v-if="row._status === 'SUBMITTED' || row._status === 'NEEDS_INFO'" class="flex gap-2">
            <AppButton size="sm" @click="openDecision(row, 'APPROVED')">通过</AppButton>
            <AppButton size="sm" variant="secondary" @click="openDecision(row, 'REJECTED')">拒绝</AppButton>
          </div>
          <span v-else class="text-xs text-ink-faint">{{ text(row.decision_reason, "已处理") }}</span>
        </template>
      </DataTable>
      <p class="mt-4 text-xs text-ink-faint">
        当前共 {{ rows.length }} 条；申请编号与用户编号会写入审计，页面不展示手机号或地址。
      </p>
    </ModuleCard>

    <ConfirmDialog
      v-model="decisionOpen"
      :title="decisionKind === 'APPROVED' ? '通过申请并给出购买窗口' : '拒绝申请'"
      :target="
        decisionTarget
          ? `${text(decisionTarget.product_title, '商品')} · ${kindLabel(decisionTarget.kind)} · 期望日期 ${text(decisionTarget.preferred_date, '未填')}`
          : ''
      "
      :change="decisionKind === 'APPROVED' ? '待审批 → 已通过（7 天内可下单）' : '待审批 → 已拒绝'"
      :consequence="
        decisionKind === 'APPROVED'
          ? '通过后客户会收到通知，需在 7 天内下单；洗毛预约不锁库存、不收款。'
          : '拒绝后客户会收到理由，该申请不能恢复，需要重新提交。'
      "
      :confirm-label="decisionKind === 'APPROVED' ? '确认通过' : '确认拒绝'"
      :tone="decisionKind === 'APPROVED' ? 'primary' : 'danger'"
      :pending="decisionPending"
      @confirm="submitDecision"
      @cancel="decisionOpen = false"
    />
  </div>
</template>
