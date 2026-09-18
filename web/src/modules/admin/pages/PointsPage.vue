<script setup lang="ts">
/**
 * 星绒币与调整：流水查询、调整申请与审批分离。
 *
 * 硬约束（09 文档）：**不允许直接改余额**。所有变化都必须通过积分调整申请，
 * 由另一位员工审批后才能入账；这里没有任何「修改当前余额」的输入框。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type Dict,
  type FailureView,
  type PointAdjustmentRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, formatPoints, pointsWithUnit } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const adjustments = usePageData<AdminList<PointAdjustmentRecord>>(
  () => adminApi.pointAdjustments({ status: statusFilter.value || undefined, limit: 20 }),
  { items: [] },
  "调整申请列表",
  () => auth.handleSessionExpired(),
);

const ledger = usePageData<AdminList<Dict>>(
  () => adminApi.pointLedger({ q: ledgerQuery.value || undefined, limit: 20 }),
  { items: [] },
  "积分流水",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const ledgerQuery = ref("");
const ledgerError = ref<FailureView | null>(null);
const ledgerItems = ref<Dict[]>([]);

const adjustColumns: TableColumn[] = [
  { key: "user_label", label: "客户标识" },
  { key: "delta_points", label: "调整数量", width: "120px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "reason", label: "理由", hideOnMobile: true },
  { key: "requested_by", label: "申请人", width: "120px", hideOnMobile: true },
  { key: "created_at", label: "申请时间", width: "170px", hideOnMobile: true },
];

const ledgerColumns: TableColumn[] = [
  { key: "created_at", label: "发生时间", width: "170px" },
  { key: "delta_points", label: "变化", width: "110px" },
  { key: "description", label: "业务说明" },
  { key: "source", label: "来源", width: "150px", hideOnMobile: true },
  { key: "balance_after", label: "变化后余额", width: "130px", hideOnMobile: true },
];

const requestForm = ref({ user_id: "", delta_points: 0, reason: "" });
const requestError = ref("");
const requestPending = ref(false);

const approveTarget = ref<PointAdjustmentRecord | null>(null);
const approveReason = ref("");
const approveError = ref("");
const approvePending = ref(false);
const approveConfirmOpen = ref(false);

const bothPermissions = computed(
  () => auth.can(ADMIN_PERMISSIONS.pointsAdjust) && auth.can("points.adjust.approve"),
);

const approveTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "APPROVED") return "jade";
  if (status === "PENDING") return "amber";
  if (status === "REJECTED") return "wine";
  return "neutral";
};

async function submitRequest(): Promise<void> {
  requestError.value = "";
  if (!requestForm.value.user_id.trim()) {
    requestError.value = "请填写客户编号（不要填写手机号）。";
    return;
  }
  if (!Number.isInteger(requestForm.value.delta_points) || requestForm.value.delta_points === 0) {
    requestError.value = "调整数量必须是整数且不能为 0。";
    return;
  }
  if (requestForm.value.reason.trim().length < 6) {
    requestError.value = "请填写至少 6 个字的调整理由，例如「反馈奖励补发，原审核漏发」。";
    return;
  }
  requestPending.value = true;
  try {
    await adminApi.requestPointAdjustment({
      user_id: requestForm.value.user_id.trim(),
      delta_points: requestForm.value.delta_points,
      reason: requestForm.value.reason.trim(),
    });
    toast.success("调整申请已提交", "申请需要另一位员工审批后才会入账；当前余额不会被改动。");
    requestForm.value = { user_id: "", delta_points: 0, reason: "" };
    await adjustments.load();
  } catch (caught) {
    requestError.value = actionErrorMessage(caught, "提交调整申请失败，请稍后重试。").message;
  } finally {
    requestPending.value = false;
  }
}

function openApprove(row: PointAdjustmentRecord): void {
  approveTarget.value = row;
  approveReason.value = "";
  approveError.value = "";
}

function requestApproveConfirm(): void {
  if (approveReason.value.trim().length < 6) {
    approveError.value = "请填写至少 6 个字的审批理由，说明核对依据。";
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
    await adminApi.approvePointAdjustment(row.id, { reason: approveReason.value.trim() });
    toast.success("调整已批准并入账", "客户可以在星绒币流水里看到这次调整与理由。");
    approveConfirmOpen.value = false;
    approveTarget.value = null;
    await adjustments.load();
  } catch (caught) {
    approveError.value = actionErrorMessage(caught, "批准失败，请稍后重试。").message;
    approveConfirmOpen.value = false;
  } finally {
    approvePending.value = false;
  }
}

async function loadAll(): Promise<void> {
  await Promise.all([adjustments.load(), loadLedger()]);
}

async function loadLedger(): Promise<void> {
  await ledger.load();
  ledgerItems.value = ledger.data.value.items;
  ledgerError.value = ledger.error.value;
}

onMounted(loadAll);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Points"
      title="星绒币与调整"
      description="查询流水、提交调整申请、审批他人的申请。余额由服务端按流水计算，后台没有直接改余额的入口。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="adjustments.loading.value" @click="loadAll">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="danger"
      title="不允许直接改余额"
      description="这个页面没有任何「设置当前余额」的输入框。所有变化都必须先提交调整申请，再由另一位员工审批后入账；每一次调整都会写审计。"
    />

    <InlineNotice
      v-if="bothPermissions"
      tone="warn"
      title="职责分离提醒：当前账号同时具备申请与审批权限"
      description="请尽量由另一位员工审批，避免自我批准；如岗位确实需要两项权限，请在审计记录里保留核对依据。"
    />

    <div class="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
      <div class="space-y-6">
        <div class="space-y-4">
          <h2 class="font-display text-lg">调整申请</h2>
          <DataTable
            :columns="adjustColumns"
            :rows="adjustments.data.value.items"
            :loading="adjustments.loading.value"
            :error="adjustments.error.value"
            :has-more="Boolean(adjustments.data.value.has_more)"
            empty-title="没有调整申请"
            empty-description="反馈奖励补发、活动补发等场景需要先提交申请。"
            @retry="adjustments.load()"
          >
            <template #cell-delta_points="{ row }">
              <StatusBadge
                :tone="typeof row.delta_points === 'number' && row.delta_points > 0 ? 'jade' : 'wine'"
                :label="pointsWithUnit(row.delta_points as number)"
              />
            </template>
            <template #cell-status="{ row }">
              <StatusBadge :tone="approveTone(row.status)" :label="text(row.status, '待审批')" />
            </template>
            <template #cell-created_at="{ row }">
              <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
            </template>
            <template #actions="{ row }">
              <PermissionGate code="points.adjust.approve" :show-missing="false">
                <AppButton
                  v-if="row.status === 'PENDING'"
                  size="sm"
                  variant="danger"
                  @click="openApprove(row)"
                >
                  审批
                </AppButton>
              </PermissionGate>
            </template>
          </DataTable>

          <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="adjustments.load()">
            <FieldRow label="状态筛选" for="adj-status">
              <select id="adj-status" v-model="statusFilter" class="field-base" @change="adjustments.load()">
                <option value="">全部状态</option>
                <option value="PENDING">待审批</option>
                <option value="APPROVED">已批准</option>
                <option value="REJECTED">已驳回</option>
              </select>
            </FieldRow>
            <div class="flex items-end">
              <AppButton type="submit" size="sm" variant="secondary">刷新申请列表</AppButton>
            </div>
          </form>
        </div>

        <div class="space-y-4">
          <h2 class="font-display text-lg">积分流水</h2>
          <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="loadLedger">
            <FieldRow label="客户编号或业务说明关键词" for="ledger-q" hint="不要用手机号查询">
              <input id="ledger-q" v-model="ledgerQuery" type="search" class="field-base" />
            </FieldRow>
            <div class="flex items-end gap-3">
              <AppButton type="submit" size="sm">查询流水</AppButton>
              <AppButton size="sm" variant="quiet" @click="ledgerQuery = ''; loadLedger()">清空</AppButton>
            </div>
          </form>
          <DataTable
            :columns="ledgerColumns"
            :rows="ledgerItems"
            :loading="ledger.loading.value"
            :error="ledgerError"
            empty-title="没有流水记录"
            empty-description="客户获取或使用星绒币后，明细会出现在这里。"
            @retry="loadLedger"
          >
            <template #cell-created_at="{ row }">
              <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at as string) }}</span>
            </template>
            <template #cell-delta_points="{ row }">
              <span class="tabular-nums">{{ formatPoints(row.delta_points as number) }}</span>
            </template>
            <template #cell-description="{ row }">
              <span>{{ text(row.description, "未提供说明") }}</span>
            </template>
            <template #cell-balance_after="{ row }">
              <span class="tabular-nums">{{ formatPoints(row.balance_after as number) }}</span>
            </template>
            <template #actions>
              <span class="text-xs text-ink-faint">流水不可修改</span>
            </template>
          </DataTable>
        </div>
      </div>

      <div class="space-y-6">
        <SurfaceCard>
          <h2 class="font-display text-lg">提交调整申请</h2>
          <PermissionGate code="points.adjust">
            <div class="mt-4 space-y-4">
              <FieldRow label="客户编号" required for="adj-user" hint="填写系统客户编号，不要填写手机号">
                <input id="adj-user" v-model="requestForm.user_id" type="text" class="field-base font-mono" />
              </FieldRow>
              <FieldRow label="调整数量（正数发放，负数扣减）" required for="adj-delta">
                <input id="adj-delta" v-model.number="requestForm.delta_points" type="number" step="1" class="field-base tabular-nums" />
              </FieldRow>
              <FieldRow label="调整理由" required for="adj-reason" hint="例如：反馈奖励补发，原审核漏发">
                <textarea id="adj-reason" v-model="requestForm.reason" rows="3" class="field-base resize-y" />
              </FieldRow>
              <p class="text-sm text-ink-soft">申请调整：{{ pointsWithUnit(requestForm.delta_points) }}</p>
              <p v-if="requestError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
                {{ requestError }}
              </p>
              <AppButton :loading="requestPending" @click="submitRequest">提交调整申请</AppButton>
            </div>
          </PermissionGate>
        </SurfaceCard>

        <ModuleCard v-if="approveTarget" title="审批调整申请" status="待处理">
          <FieldList
            :items="[
              { label: '客户标识', value: text(approveTarget.user_label) },
              { label: '调整数量', value: pointsWithUnit(approveTarget.delta_points) },
              { label: '申请理由', value: text(approveTarget.reason) },
              { label: '申请人', value: text(approveTarget.requested_by) },
              { label: '申请时间', value: formatDateTime(approveTarget.created_at) },
            ]"
            :columns="1"
          />
          <FieldRow class="mt-4" label="审批理由" required for="adj-approve-reason" hint="例如：已核对反馈审核记录与流水">
            <input id="adj-approve-reason" v-model="approveReason" type="text" class="field-base" />
          </FieldRow>
          <p v-if="approveError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ approveError }}
          </p>
          <div class="mt-4 flex flex-wrap gap-3">
            <AppButton variant="danger" @click="requestApproveConfirm">提交审批</AppButton>
            <AppButton variant="quiet" @click="approveTarget = null">取消</AppButton>
          </div>
        </ModuleCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">规则提醒</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>· 星绒币按订单实付金额计算，运费与退款部分不计入。</li>
            <li>· 反馈奖励只在审核通过后发放，且同一问题只奖励一次。</li>
            <li>· 冻结与待入账积分不是可用余额，客户页面会分开显示。</li>
            <li>· 任何调整都必须能追溯到申请人与审批人。</li>
          </ul>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="approveConfirmOpen"
      title="批准积分调整并入账"
      :target="approveTarget ? `客户 ${text(approveTarget.user_label, text(approveTarget.user_id))}` : ''"
      change="审批通过 → 调整立即计入客户星绒币流水。"
      :amount="pointsWithUnit(approveTarget?.delta_points)"
      consequence="入账后余额立即变化且不能直接改回；如需纠正必须再提交一笔反向调整并重新审批。"
      confirm-label="确认批准并入账"
      tone="danger"
      :pending="approvePending"
      @confirm="submitApprove"
    />
  </div>
</template>
