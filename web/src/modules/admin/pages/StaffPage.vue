<script setup lang="ts">
/**
 * 员工与权限：员工、角色、MFA 状态、权限变更审计。
 *
 * 硬约束（10 文档）：无公开注册、无默认密码；员工权限变更属于危险动作，
 * 必须显示具体对象（谁、哪些角色）与后果并填写理由；MFA 重置需身份核验与审计。
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
  type RoleRecord,
  type StaffRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<StaffRecord>>(
  () => adminApi.staff({ limit: 50 }),
  { items: [] },
  "员工列表",
  () => auth.handleSessionExpired(),
);

const roles = ref<RoleRecord[]>([]);
const rolesError = ref<FailureView | null>(null);

const columns: TableColumn[] = [
  { key: "display_name", label: "员工" },
  { key: "login_name", label: "登录名", width: "150px" },
  { key: "roles", label: "角色", width: "200px" },
  { key: "mfa_enabled", label: "MFA", width: "110px" },
  { key: "status", label: "状态", width: "110px" },
  { key: "last_login_at", label: "最近登录", width: "170px", hideOnMobile: true },
];

const createForm = ref({ login_name: "", display_name: "", roles: [] as string[] });
const createError = ref("");
const createPending = ref(false);
const createOpen = ref(false);

const editTarget = ref<StaffRecord | null>(null);
const editRoles = ref<string[]>([]);
const editStatus = ref("ACTIVE");
const editReason = ref("");
const editError = ref("");
const editPending = ref(false);
const editConfirmOpen = ref(false);

const resetTarget = ref<StaffRecord | null>(null);
const resetReason = ref("");
const resetError = ref("");
const resetPending = ref(false);
const resetConfirmOpen = ref(false);

/** 权限变更摘要：只展示角色与权限码，不展示任何凭据。 */
const editSummary = computed(() => {
  const before = (editTarget.value?.roles ?? []).slice().sort().join("、") || "无角色";
  const after = editRoles.value.slice().sort().join("、") || "无角色（将失去全部权限）";
  return `${before} → ${after}`;
});

const editPermissions = computed(() => {
  const codes = new Set<string>();
  for (const role of roles.value) {
    if (!editRoles.value.includes(role.code)) continue;
    for (const code of role.permissions ?? []) codes.add(code);
  }
  return Array.from(codes).sort();
});

async function load(): Promise<void> {
  await state.load();
  const result = await collect<{ roles: AdminList<RoleRecord> }>({ roles: () => adminApi.roles() });
  if (result.ok.roles) {
    roles.value = result.ok.roles.items;
    rolesError.value = null;
  } else {
    roles.value = [];
    rolesError.value = describeFailure(result.failed[0]?.error, "角色列表");
  }
}

function toggleRole(code: string, target: "create" | "edit"): void {
  const list = target === "create" ? createForm.value.roles : editRoles.value;
  const index = list.indexOf(code);
  if (index >= 0) list.splice(index, 1);
  else list.push(code);
}

async function createStaff(): Promise<void> {
  createError.value = "";
  if (!createForm.value.login_name.trim() || !createForm.value.display_name.trim()) {
    createError.value = "登录名与显示名都是必填项。";
    return;
  }
  if (!createForm.value.roles.length) {
    createError.value = "请至少选择一个角色；没有角色的员工无法进入任何模块。";
    return;
  }
  createPending.value = true;
  try {
    await adminApi.createStaff({
      login_name: createForm.value.login_name.trim(),
      display_name: createForm.value.display_name.trim(),
      roles: [...createForm.value.roles],
    });
    toast.success("员工已创建", "初始口令由系统生成；首次登录必须登记 MFA，员工需要自行设置验证器。");
    createForm.value = { login_name: "", display_name: "", roles: [] };
    createOpen.value = false;
    await load();
  } catch (caught) {
    createError.value = actionErrorMessage(caught, "创建员工失败，请稍后重试。").message;
  } finally {
    createPending.value = false;
  }
}

function openEdit(row: StaffRecord): void {
  editTarget.value = row;
  editRoles.value = [...(row.roles ?? [])];
  editStatus.value = String(row.status ?? "ACTIVE");
  editReason.value = "";
  editError.value = "";
}

function requestEditConfirm(): void {
  if (editReason.value.trim().length < 6) {
    editError.value = "请填写至少 6 个字的变更理由，例如「岗位调整，转岗客服，工单 HR-2026-014」。";
    return;
  }
  editError.value = "";
  editConfirmOpen.value = true;
}

async function submitEdit(): Promise<void> {
  const row = editTarget.value;
  if (!row) return;
  editPending.value = true;
  try {
    await adminApi.updateStaff(row.id, {
      roles: [...editRoles.value],
      status: editStatus.value,
      reason: editReason.value.trim(),
      version: row.version ?? undefined,
    });
    toast.success("员工权限已更新", "权限变更立即生效，并已写入审计记录。");
    editConfirmOpen.value = false;
    editTarget.value = null;
    await load();
  } catch (caught) {
    editError.value = actionErrorMessage(caught, "更新员工失败，请稍后重试。").message;
    editConfirmOpen.value = false;
  } finally {
    editPending.value = false;
  }
}

function openReset(row: StaffRecord): void {
  resetTarget.value = row;
  resetReason.value = "";
  resetError.value = "";
}

function requestResetConfirm(): void {
  if (resetReason.value.trim().length < 6) {
    resetError.value = "请填写至少 6 个字的重置理由，例如「员工遗忘口令，已通过内线电话核验身份」。";
    return;
  }
  resetError.value = "";
  resetConfirmOpen.value = true;
}

async function submitReset(): Promise<void> {
  const row = resetTarget.value;
  if (!row) return;
  resetPending.value = true;
  try {
    await adminApi.resetStaffPassword(row.id, { reason: resetReason.value.trim() });
    toast.success("已触发口令重置", "系统会下发一次性初始口令；员工登录后必须重新登记 MFA。不要把初始口令写在群里。");
    resetConfirmOpen.value = false;
    resetTarget.value = null;
    await load();
  } catch (caught) {
    resetError.value = actionErrorMessage(caught, "重置口令失败，请稍后重试。").message;
    resetConfirmOpen.value = false;
  } finally {
    resetPending.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Staff"
      title="员工与权限"
      description="维护员工账号、角色与 MFA 状态。角色决定权限码集合；任何权限变更都会记录操作人、时间与理由。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
        <AppButton size="sm" @click="createOpen = !createOpen">{{ createOpen ? "收起新建表单" : "新增员工" }}</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="没有公开注册与默认密码"
      description="员工账号只能由管理员创建；每个账号都必须启用 MFA。权限按角色授予，员工看不到未授权的模块，服务端仍会逐动作校验权限码。"
    />

    <SurfaceCard v-if="createOpen">
      <SectionHeading level="2" title="新增员工" description="创建后系统生成一次性初始口令；首次登录必须登记 MFA 才能进入后台。" />
      <div class="mt-5 grid gap-4 sm:grid-cols-2">
        <FieldRow label="登录名" required for="staff-login" hint="只允许小写字母、数字与点号">
          <input id="staff-login" v-model="createForm.login_name" type="text" class="field-base font-mono" />
        </FieldRow>
        <FieldRow label="显示名" required for="staff-name">
          <input id="staff-name" v-model="createForm.display_name" type="text" class="field-base" />
        </FieldRow>
      </div>
      <fieldset class="mt-4 rounded-md border border-line p-4">
        <legend class="px-1 text-sm font-medium text-ink-soft">角色</legend>
        <p v-if="rolesError" class="mt-2 text-sm text-ink-muted">
          {{ rolesError.description }} {{ rolesError.retryHint }}
        </p>
        <div v-else class="mt-3 flex flex-wrap gap-3">
          <label v-for="role in roles" :key="role.code" class="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              class="size-4"
              :checked="createForm.roles.includes(role.code)"
              @change="toggleRole(role.code, 'create')"
            />
            <span>{{ role.name }}</span>
          </label>
        </div>
      </fieldset>
      <p v-if="createError" class="mt-4 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ createError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton :loading="createPending" @click="createStaff">创建员工</AppButton>
        <AppButton variant="quiet" @click="createOpen = false">取消</AppButton>
      </div>
    </SurfaceCard>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      empty-title="没有员工记录"
      empty-description="如果这里为空，请确认员工接口是否已就绪。"
      @retry="load"
    >
      <template #cell-display_name="{ row }">
        <span class="font-medium">{{ text(row.display_name) }}</span>
      </template>
      <template #cell-login_name="{ row }">
        <span class="font-mono text-xs">{{ text(row.login_name) }}</span>
      </template>
      <template #cell-roles="{ row }">
        <span class="text-xs">{{ (row.roles ?? []).join("、") || "未分配角色" }}</span>
      </template>
      <template #cell-mfa_enabled="{ row }">
        <StatusBadge
          :tone="row.mfa_enabled ? 'jade' : 'wine'"
          :label="row.mfa_enabled ? '已启用' : '未启用（必须启用）'"
        />
      </template>
      <template #cell-last_login_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.last_login_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openEdit(row)">调整角色与状态</AppButton>
        <AppButton size="sm" variant="danger" @click="openReset(row)">重置口令</AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-2">
      <SurfaceCard v-if="editTarget">
        <h2 class="font-display text-lg">调整角色与状态：{{ text(editTarget.display_name) }}</h2>
        <FieldList
          class="mt-3"
          :items="[
            { label: '登录名', value: text(editTarget.login_name) },
            { label: 'MFA', value: editTarget.mfa_enabled ? '已启用' : '未启用' },
            { label: '当前角色', value: (editTarget.roles ?? []).join('、') || '无' },
          ]"
          :columns="1"
        />
        <fieldset class="mt-4 rounded-md border border-line p-4">
          <legend class="px-1 text-sm font-medium text-ink-soft">角色</legend>
          <div class="mt-3 flex flex-wrap gap-3">
            <label v-for="role in roles" :key="role.code" class="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                class="size-4"
                :checked="editRoles.includes(role.code)"
                @change="toggleRole(role.code, 'edit')"
              />
              <span>{{ role.name }}</span>
            </label>
          </div>
          <p class="mt-3 text-xs text-ink-faint">
            权限变更后：{{ editSummary }}。共 {{ editPermissions.length }} 个权限码。
          </p>
        </fieldset>
        <FieldRow class="mt-4" label="账号状态" for="staff-status">
          <select id="staff-status" v-model="editStatus" class="field-base">
            <option value="ACTIVE">正常</option>
            <option value="SUSPENDED">暂停登录</option>
          </select>
        </FieldRow>
        <FieldRow class="mt-4" label="变更理由" required for="staff-reason" hint="例如：岗位调整，转岗客服，工单 HR-2026-014">
          <input id="staff-reason" v-model="editReason" type="text" class="field-base" />
        </FieldRow>
        <p v-if="editError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ editError }}
        </p>
        <div class="mt-4 flex flex-wrap gap-3">
          <AppButton variant="danger" @click="requestEditConfirm">提交权限变更</AppButton>
          <AppButton variant="quiet" @click="editTarget = null">取消</AppButton>
        </div>
      </SurfaceCard>

      <SurfaceCard v-if="resetTarget">
        <h2 class="font-display text-lg">重置员工口令：{{ text(resetTarget.display_name) }}</h2>
        <p class="mt-2 text-sm text-ink-muted">
          重置前必须完成身份核验（内线电话或当面确认）。重置后旧口令立即失效，员工首次登录需重新登记 MFA。
        </p>
        <FieldRow class="mt-4" label="核验与重置理由" required for="staff-reset-reason">
          <input id="staff-reset-reason" v-model="resetReason" type="text" class="field-base" />
        </FieldRow>
        <p v-if="resetError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ resetError }}
        </p>
        <div class="mt-4 flex flex-wrap gap-3">
          <AppButton variant="danger" @click="requestResetConfirm">提交重置</AppButton>
          <AppButton variant="quiet" @click="resetTarget = null">取消</AppButton>
        </div>
      </SurfaceCard>

      <SurfaceCard v-if="!editTarget && !resetTarget">
        <h2 class="font-display text-lg">角色与权限码</h2>
        <p class="mt-1 text-xs text-ink-faint">权限码由角色携带；界面按权限码隐藏入口，服务端仍会逐动作校验。</p>
        <ul v-if="roles.length" class="mt-4 space-y-2 text-sm">
          <li v-for="role in roles" :key="role.code" class="rounded-sm border border-line px-4 py-3">
            <p class="font-medium">{{ role.name }} <span class="font-mono text-xs text-ink-faint">{{ role.code }}</span></p>
            <p v-if="role.description" class="mt-1 text-xs text-ink-muted">{{ role.description }}</p>
            <p class="mt-1 font-mono text-xs text-ink-faint">
              {{ (role.permissions ?? []).join("、") || "无权限码" }}
            </p>
          </li>
        </ul>
        <p v-else class="mt-4 text-sm text-ink-muted">角色列表尚未就绪或为空。</p>
      </SurfaceCard>
    </div>

    <ConfirmDialog
      v-model="editConfirmOpen"
      title="变更员工角色与权限"
      :target="editTarget ? `员工 ${text(editTarget.display_name)}（${text(editTarget.login_name)}）` : ''"
      :change="`角色：${editSummary}；账号状态 → ${editStatus === 'ACTIVE' ? '正常' : '暂停登录'}`"
      :amount="`涉及 ${editPermissions.length} 个权限码`"
      consequence="权限变更立即生效：被移除权限的模块会从该员工的导航消失，正在进行的操作也会被服务端拒绝。变更记录会进入审计，事后可追溯。"
      confirm-label="确认变更权限"
      tone="danger"
      :pending="editPending"
      @confirm="submitEdit"
    />

    <ConfirmDialog
      v-model="resetConfirmOpen"
      title="重置员工口令"
      :target="resetTarget ? `员工 ${text(resetTarget.display_name)}（${text(resetTarget.login_name)}）` : ''"
      change="旧口令立即失效，系统生成一次性初始口令；员工需重新登记 MFA。"
      consequence="口令重置会让该员工当前会话失效。初始口令属于凭据，必须通过安全渠道单独告知员工，不要写在群聊或工单评论里。"
      confirm-label="确认重置口令"
      tone="danger"
      :pending="resetPending"
      @confirm="submitReset"
    />
  </div>
</template>
