<script setup lang="ts">
/**
 * 经营设置：经营信息、规则版本、通知模板、功能开关。
 *
 * 硬约束（10/11 文档）：**不展示任何密钥**。密钥类配置即使存在于服务端，也不会下发到后台界面；
 * 修改设置需要版本号（乐观锁）与理由，冲突时提示刷新后重新提交。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type SettingRecord,
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

const state = usePageData<AdminList<SettingRecord>>(
  () => adminApi.settings(),
  { items: [] },
  "经营设置",
  () => auth.handleSessionExpired(),
);

/** 密钥类键名拒绝在界面显示与编辑：即使服务端误下发也不渲染。 */
const SECRET_PATTERN = /(secret|password|passwd|token|key|signature|salt|credential|webhook)/i;

const settings = computed(() => state.data.value.items);

const visibleSettings = computed(() => settings.value.filter((item) => !SECRET_PATTERN.test(item.key) && !SECRET_PATTERN.test(item.description ?? "")));
const hiddenSecrets = computed(() => settings.value.length - visibleSettings.value.length);

const groups = computed(() => {
  const map = new Map<string, SettingRecord[]>();
  for (const item of visibleSettings.value) {
    const group = item.key.includes(".")
      ? item.key.split(".")[0]!
      : item.key.includes("_")
        ? item.key.split("_")[0]!
        : "其它";
    const list = map.get(group) ?? [];
    list.push(item);
    map.set(group, list);
  }
  return Array.from(map.entries()).map(([group, items]) => ({ group, items }));
});

const columns: TableColumn[] = [
  { key: "key", label: "配置键" },
  { key: "value", label: "当前值" },
  { key: "version", label: "版本", width: "80px" },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
  { key: "updated_by", label: "更新人", width: "120px", hideOnMobile: true },
];

const editTarget = ref<SettingRecord | null>(null);
const editValue = ref("");
const editReason = ref("");
const editError = ref("");
const editPending = ref(false);
const editConfirmOpen = ref(false);
const conflictNotice = ref("");

const isBooleanValue = computed(() => typeof editTarget.value?.value === "boolean" || /^(true|false)$/i.test(editValue.value.trim()));

function openEdit(row: SettingRecord): void {
  editTarget.value = row;
  editValue.value = row.value === null || row.value === undefined ? "" : String(row.value);
  editReason.value = "";
  editError.value = "";
  conflictNotice.value = "";
}

function requestEditConfirm(): void {
  if (!editValue.value.trim() && typeof editTarget.value?.value !== "boolean") {
    editError.value = "请填写新的配置值；如果要停用某项能力，请用功能开关明确关闭。";
    return;
  }
  if (editReason.value.trim().length < 6) {
    editError.value = "请填写至少 6 个字的变更理由，例如「按 2026 春季价目表调整预付款比例」。";
    return;
  }
  editError.value = "";
  editConfirmOpen.value = true;
}

function normalizedValue(): string | number | boolean {
  const raw = editValue.value.trim();
  if (/^(true|false)$/i.test(raw)) return raw.toLowerCase() === "true";
  if (/^-?\d+$/.test(raw)) return Number(raw);
  return raw;
}

async function submitEdit(): Promise<void> {
  const row = editTarget.value;
  if (!row) return;
  editPending.value = true;
  try {
    await adminApi.updateSetting(row.key, {
      value: normalizedValue(),
      version: row.version ?? 0,
      reason: editReason.value.trim(),
    });
    toast.success("设置已更新", "新值立即生效，前端展示与服务端校验使用同一份配置。");
    editConfirmOpen.value = false;
    editTarget.value = null;
    await state.load();
  } catch (caught) {
    const view = actionErrorMessage(caught, "保存设置失败，请稍后重试。");
    editError.value = view.message;
    conflictNotice.value = view.versionConflict ? view.message : "";
    editConfirmOpen.value = false;
  } finally {
    editPending.value = false;
  }
}

async function refreshAfterConflict(): Promise<void> {
  conflictNotice.value = "";
  editError.value = "";
  editTarget.value = null;
  await state.load();
  toast.info("已刷新", "配置已重新载入最新版本，请重新打开要修改的项。");
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Settings"
      title="经营设置"
      description="经营信息、规则版本、通知模板与功能开关。这些值同时决定前台展示与服务端校验，修改需要理由并留下版本记录。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="danger"
      title="本页不展示任何密钥"
      description="支付商户密钥、短信签名密钥、存储访问凭据都不下发到后台界面，也不允许在这里查看或编辑；如需轮换密钥，请按运维手册在服务端执行。"
    />

    <p v-if="hiddenSecrets > 0" class="rounded-lg border border-dashed border-line-strong bg-surface-muted px-5 py-4 text-sm text-ink-muted" role="status">
      接口返回了 {{ hiddenSecrets }} 条疑似凭据类配置，已按规范在界面上隐藏（不显示键值）。
    </p>

    <InlineNotice
      v-if="conflictNotice"
      tone="danger"
      title="配置已被他人更新"
      :description="`${conflictNotice} 请刷新取得最新版本后再提交，避免覆盖同事的修改。`"
    >
      <AppButton size="sm" variant="danger" @click="refreshAfterConflict">刷新并载入最新版本</AppButton>
    </InlineNotice>

    <DataTable
      :columns="columns"
      :rows="visibleSettings"
      :loading="state.loading.value"
      :error="state.error.value"
      empty-title="没有可编辑的设置项"
      empty-description="设置接口尚未就绪，或所有配置都属于不可展示的凭据类。"
      @retry="state.load()"
    >
      <template #cell-key="{ row }">
        <span class="font-mono text-xs">{{ text(row.key) }}</span>
        <p v-if="row.description" class="mt-1 text-xs text-ink-faint">{{ row.description }}</p>
      </template>
      <template #cell-value="{ row }">
        <span v-if="typeof row.value === 'boolean'">
          <StatusBadge :tone="row.value ? 'jade' : 'neutral'" :label="row.value ? '已启用' : '未启用'" />
        </span>
        <span v-else class="break-all">{{ text(row.value) }}</span>
      </template>
      <template #cell-updated_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.updated_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openEdit(row)">修改这项设置</AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
      <SurfaceCard v-if="editTarget">
        <SectionHeading level="2" :title="`修改设置：${editTarget.key}`" description="保存时需要版本号；如果同事已经改过，服务端会返回冲突，需要刷新后重新提交。" />
        <FieldList
          class="mt-4"
          :items="[
            { label: '当前值', value: text(editTarget.value) },
            { label: '当前版本', value: text(editTarget.version) },
            { label: '最近更新人', value: text(editTarget.updated_by) },
            { label: '最近更新时间', value: formatDateTime(editTarget.updated_at) },
          ]"
        />
        <div class="mt-4 space-y-4">
          <FieldRow
            v-if="isBooleanValue"
            label="新值（功能开关）"
            required
            for="setting-value-bool"
            hint="关闭后入口隐藏且后端拒绝（FEATURE_DISABLED），不存在绕过开关的接口"
          >
            <select id="setting-value-bool" v-model="editValue" class="field-base">
              <option value="true">true（启用）</option>
              <option value="false">false（关闭）</option>
            </select>
          </FieldRow>
          <FieldRow v-else label="新值" required for="setting-value" hint="纯数字会按数值保存；true / false 会按开关保存">
            <input id="setting-value" v-model="editValue" type="text" class="field-base font-mono" />
          </FieldRow>
          <FieldRow label="变更理由" required for="setting-reason" hint="例如：按 2026 春季价目表调整预付款比例">
            <textarea id="setting-reason" v-model="editReason" rows="3" class="field-base resize-y" />
          </FieldRow>
          <p v-if="editError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ editError }}
          </p>
          <div class="flex flex-wrap gap-3">
            <AppButton @click="requestEditConfirm">提交修改</AppButton>
            <AppButton variant="quiet" @click="editTarget = null">取消</AppButton>
          </div>
        </div>
      </SurfaceCard>

      <SurfaceCard>
        <h2 class="font-display text-lg">分组概览</h2>
        <p class="mt-1 text-xs text-ink-faint">按配置键前缀分组，便于按主题核对；密钥类配置不会出现在这里。</p>
        <div v-if="groups.length" class="mt-4 space-y-4">
          <div v-for="group in groups" :key="group.group">
            <p class="eyebrow">{{ group.group }}</p>
            <ul class="mt-2 space-y-1 text-sm">
              <li v-for="item in group.items" :key="item.key" class="flex items-baseline justify-between gap-3">
                <span class="font-mono text-xs">{{ item.key }}</span>
                <span class="shrink-0 text-xs text-ink-muted">{{ text(item.value) }}</span>
              </li>
            </ul>
          </div>
        </div>
        <p v-else class="mt-4 text-sm text-ink-muted">没有可展示的配置项。</p>
      </SurfaceCard>
    </div>

    <ConfirmDialog
      v-model="editConfirmOpen"
      title="修改经营设置"
      :target="editTarget ? `配置项 ${editTarget.key}` : ''"
      :change="`${text(editTarget?.value)} → ${editValue}`"
      :amount="`版本 ${text(editTarget?.version)} → ${Number(editTarget?.version ?? 0) + 1}`"
      consequence="设置会同时影响前台展示与服务端校验（价格比例、有效期、开关等）。功能开关关闭后入口隐藏且后端拒绝，不存在绕过方式。"
      confirm-label="确认保存设置"
      tone="primary"
      :pending="editPending"
      @confirm="submitEdit"
    />
  </div>
</template>
