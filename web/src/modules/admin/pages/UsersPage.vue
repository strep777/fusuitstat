<script setup lang="ts">
/**
 * 用户：脱敏详情、会话撤销、限制状态与原因。
 *
 * 硬约束（10 文档）：只显示脱敏手机号；不显示地址明文、验证码与会话标识明文。
 * 限制账号属于危险动作，必须显示具体对象与后果并填写理由。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  collect,
  describeFailure,
  text,
  usePageData,
  type AdminList,
  type FailureView,
  type UserAdminSummary,
  type UserSessionRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<UserAdminSummary>>(
  () => adminApi.users({ q: search.value || undefined, status: statusFilter.value || undefined, limit: 20 }),
  { items: [] },
  "用户列表",
  () => auth.handleSessionExpired(),
);

const search = ref("");
const statusFilter = ref("");

const columns: TableColumn[] = [
  { key: "phone_masked", label: "手机号（脱敏）", width: "150px" },
  { key: "nickname", label: "昵称" },
  { key: "status", label: "账号状态", width: "120px" },
  { key: "restriction_status", label: "限制状态", width: "140px" },
  { key: "active_sessions", label: "活跃会话", width: "110px", hideOnMobile: true },
  { key: "last_login_at", label: "最近登录", width: "170px", hideOnMobile: true },
];

const selected = ref<UserAdminSummary | null>(null);
const sessions = ref<UserSessionRecord[]>([]);
const sessionsError = ref<FailureView | null>(null);
const detailLoading = ref(false);

const restrictionTarget = ref<UserAdminSummary | null>(null);
const restrictionStatus = ref("LIMITED");
const restrictionReason = ref("");
const restrictionError = ref("");
const restrictionPending = ref(false);
const restrictionConfirmOpen = ref(false);

const revokeTarget = ref<UserAdminSummary | null>(null);
const revokeReason = ref("");
const revokeError = ref("");
const revokePending = ref(false);
const revokeConfirmOpen = ref(false);

const sessionColumns: TableColumn[] = [
  { key: "device_label", label: "设备 / 浏览器" },
  { key: "created_at", label: "登录时间", width: "170px" },
  { key: "last_seen_at", label: "最近活动", width: "170px", hideOnMobile: true },
  { key: "current", label: "当前会话", width: "110px" },
];

const sessionItems = computed(() =>
  sessions.value.map((item, index) => ({
    id: String(item.id ?? `session-${index}`),
    device_label: item.device_label,
    created_at: item.created_at,
    last_seen_at: item.last_seen_at,
    current: item.current ? "是" : "否",
  })),
);

async function openDetail(row: UserAdminSummary): Promise<void> {
  selected.value = row;
  detailLoading.value = true;
  sessionsError.value = null;
  const result = await collect<{ sessions: AdminList<UserSessionRecord> }>({
    sessions: () => adminApi.userSessions(row.id),
  });
  if (result.ok.sessions) {
    sessions.value = result.ok.sessions.items;
  } else {
    sessions.value = [];
    sessionsError.value = describeFailure(result.failed[0]?.error, "会话列表");
  }
  detailLoading.value = false;
}

function openRestriction(row: UserAdminSummary): void {
  restrictionTarget.value = row;
  restrictionStatus.value = row.restriction_status === "BANNED" ? "BANNED" : "LIMITED";
  restrictionReason.value = "";
  restrictionError.value = "";
}

function requestRestrictionConfirm(): void {
  if (restrictionReason.value.trim().length < 6) {
    restrictionError.value = "请填写至少 6 个字的限制理由，理由会记录并可按需向用户说明。";
    return;
  }
  restrictionError.value = "";
  restrictionConfirmOpen.value = true;
}

async function submitRestriction(): Promise<void> {
  const row = restrictionTarget.value;
  if (!row) return;
  restrictionPending.value = true;
  try {
    await adminApi.setUserRestriction(row.id, {
      status: restrictionStatus.value,
      reason: restrictionReason.value.trim(),
    });
    toast.success("账号限制已更新", "限制状态与理由已记录；解除限制同样需要填写理由。");
    restrictionConfirmOpen.value = false;
    restrictionTarget.value = null;
    await state.load();
  } catch (caught) {
    restrictionError.value = actionErrorMessage(caught, "更新限制失败，请稍后重试。").message;
    restrictionConfirmOpen.value = false;
  } finally {
    restrictionPending.value = false;
  }
}

function openRevoke(row: UserAdminSummary): void {
  revokeTarget.value = row;
  revokeReason.value = "";
  revokeError.value = "";
}

function requestRevokeConfirm(): void {
  if (revokeReason.value.trim().length < 6) {
    revokeError.value = "请填写至少 6 个字的撤销理由，例如「用户反馈账号异常，先下线全部会话」。";
    return;
  }
  revokeError.value = "";
  revokeConfirmOpen.value = true;
}

async function submitRevoke(): Promise<void> {
  const row = revokeTarget.value;
  if (!row) return;
  revokePending.value = true;
  try {
    const result = await adminApi.revokeUserSessions(row.id, { reason: revokeReason.value.trim() });
    toast.success("会话已撤销", `已下线 ${result.revoked ?? 0} 个会话；用户需要重新登录。`);
    revokeConfirmOpen.value = false;
    revokeTarget.value = null;
    if (selected.value?.id === row.id) await openDetail(row);
    await state.load();
  } catch (caught) {
    revokeError.value = actionErrorMessage(caught, "撤销会话失败，请稍后重试。").message;
    revokeConfirmOpen.value = false;
  } finally {
    revokePending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Users"
      title="用户"
      description="查询脱敏账号信息、查看登录会话、撤销会话、设置限制状态。后台不显示手机号明文、地址明文与验证码。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="最小必要原则"
      description="客服与运营只能看到处理工单所需的信息。涉及隐私的完整数据需要用户本人授权或法定程序，不能在后台随意导出。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="昵称或脱敏手机号" for="user-q" hint="支持按脱敏手机号后四位查询，不接受明文号码检索">
        <input id="user-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="账号状态" for="user-status">
        <select id="user-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="ACTIVE">正常</option>
          <option value="LIMITED">受限</option>
          <option value="BANNED">已停用</option>
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
      empty-title="没有匹配的用户"
      empty-description="换个关键词或状态再看看；也可以让用户提供订单号以便定位账号。"
      @retry="state.load()"
    >
      <template #cell-phone_masked="{ row }">
        <span class="font-mono text-xs">{{ text(row.phone_masked, "未绑定") }}</span>
      </template>
      <template #cell-status="{ row }">
        <StatusBadge
          :tone="String(row.status ?? '') === 'ACTIVE' ? 'jade' : 'wine'"
          :label="text(row.status, '未知')"
        />
      </template>
      <template #cell-restriction_status="{ row }">
        <span class="text-xs">{{ text(row.restriction_status, "无限制") }}</span>
      </template>
      <template #cell-last_login_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.last_login_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openDetail(row)">脱敏详情</AppButton>
        <PermissionGate :code="ADMIN_PERMISSIONS.userManage" :show-missing="false">
          <AppButton size="sm" variant="quiet" @click="openRestriction(row)">设置限制</AppButton>
          <AppButton size="sm" variant="danger" @click="openRevoke(row)">撤销会话</AppButton>
        </PermissionGate>
      </template>
    </DataTable>

    <div v-if="selected" class="grid gap-6 lg:grid-cols-[1fr_1.3fr]">
      <SurfaceCard>
        <SectionHeading level="2" title="脱敏详情" description="只显示处理工单所需的字段；手机号、地址与证件信息保持脱敏。" />
        <FieldList
          class="mt-4"
          :items="[
            { label: '用户编号', value: text(selected.id) },
            { label: '脱敏手机号', value: text(selected.phone_masked) },
            { label: '昵称', value: text(selected.nickname) },
            { label: '账号状态', value: text(selected.status) },
            { label: '限制状态', value: text(selected.restriction_status, '无限制') },
            { label: '限制原因', value: text(selected.restriction_reason, '无') },
            { label: '注册时间', value: formatDateTime(selected.created_at) },
            { label: '最近登录', value: formatDateTime(selected.last_login_at) },
          ]"
          :columns="1"
        />
      </SurfaceCard>

      <SurfaceCard>
        <SectionHeading level="2" title="登录会话" description="会话标识保持隐藏；撤销后用户需要重新登录。" />
        <p v-if="sessionsError" class="mt-4 rounded-md border border-line bg-surface-muted px-4 py-3 text-sm text-ink-muted">
          {{ sessionsError.description }} {{ sessionsError.retryHint }}
        </p>
        <div class="mt-4">
          <DataTable
            :columns="sessionColumns"
            :rows="sessionItems"
            :loading="detailLoading"
            dense
            empty-title="没有活跃会话"
            empty-description="用户当前没有登录中的设备。"
            @retry="selected && openDetail(selected)"
          >
            <template #cell-created_at="{ row }">
              <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at as string) }}</span>
            </template>
            <template #cell-last_seen_at="{ row }">
              <span class="tabular-nums text-xs">{{ formatDateTime(row.last_seen_at as string) }}</span>
            </template>
            <template #actions>
              <span class="text-xs text-ink-faint">仅支持整账号撤销或全部下线</span>
            </template>
          </DataTable>
        </div>
        <div class="mt-4">
          <PermissionGate :code="ADMIN_PERMISSIONS.userManage">
            <AppButton variant="danger" @click="openRevoke(selected)">撤销该用户全部会话</AppButton>
          </PermissionGate>
        </div>
      </SurfaceCard>
    </div>

    <SurfaceCard v-if="restrictionTarget">
      <h2 class="font-display text-lg">设置账号限制：{{ text(restrictionTarget.phone_masked, "未绑定手机号") }}</h2>
      <div class="mt-4 space-y-4">
        <FieldRow label="限制类型" required for="restrict-status">
          <select id="restrict-status" v-model="restrictionStatus" class="field-base">
            <option value="LIMITED">限制敏感操作（可登录，但不能下单/提交反馈）</option>
            <option value="BANNED">停用账号（禁止登录）</option>
            <option value="NONE">解除限制，恢复正常</option>
          </select>
        </FieldRow>
        <FieldRow label="限制理由" required for="restrict-reason" hint="例如：多次提交垃圾反馈，警告无效，工单 Q-2026-0310">
          <textarea id="restrict-reason" v-model="restrictionReason" rows="3" class="field-base resize-y" />
        </FieldRow>
        <p v-if="restrictionError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ restrictionError }}
        </p>
        <div class="flex flex-wrap gap-3">
          <AppButton variant="danger" @click="requestRestrictionConfirm">提交限制变更</AppButton>
          <AppButton variant="quiet" @click="restrictionTarget = null">取消</AppButton>
        </div>
      </div>
    </SurfaceCard>

    <SurfaceCard v-if="revokeTarget">
      <h2 class="font-display text-lg">撤销全部会话：{{ text(revokeTarget.phone_masked, "未绑定手机号") }}</h2>
      <p class="mt-2 text-sm text-ink-muted">
        撤销后该用户在所有设备上都会被登出，需要重新用手机号验证码登录；已下单的订单不受影响。
      </p>
      <FieldRow class="mt-4" label="撤销理由" required for="revoke-reason">
        <input id="revoke-reason" v-model="revokeReason" type="text" class="field-base" />
      </FieldRow>
      <p v-if="revokeError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ revokeError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton variant="danger" @click="requestRevokeConfirm">提交撤销</AppButton>
        <AppButton variant="quiet" @click="revokeTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="restrictionConfirmOpen"
      title="变更账号限制状态"
      :target="restrictionTarget ? `用户 ${text(restrictionTarget.phone_masked, text(restrictionTarget.id))}` : ''"
      :change="`限制状态 → ${restrictionStatus === 'BANNED' ? '停用账号' : restrictionStatus === 'LIMITED' ? '限制敏感操作' : '解除限制'}`"
      consequence="停用后用户无法登录，正在进行的售后与退款仍需人工跟进；限制与解除都会写入审计并可按需向用户说明。"
      confirm-label="确认变更限制状态"
      tone="danger"
      :pending="restrictionPending"
      @confirm="submitRestriction"
    />

    <ConfirmDialog
      v-model="revokeConfirmOpen"
      title="撤销用户全部会话"
      :target="revokeTarget ? `用户 ${text(revokeTarget.phone_masked, text(revokeTarget.id))}` : ''"
      change="该用户在所有设备上的会话全部失效。"
      :amount="`撤销 ${text(revokeTarget?.active_sessions, '全部')} 个会话`"
      consequence="用户会被立即登出并需要重新登录；购物车与未提交的表单内容可能丢失，订单与积分不受影响。"
      confirm-label="确认撤销全部会话"
      tone="danger"
      :pending="revokePending"
      @confirm="submitRevoke"
    />
  </div>
</template>
