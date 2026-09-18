<script setup lang="ts">
/**
 * 审计：查询操作记录与前后快照摘要。
 *
 * 约束（10/11 文档）：只显示摘要，不显示密钥、会话标识、签名 URL、手机号与地址明文；
 * `JsonDiff` 会对敏感键名统一脱敏。
 */
import { computed, onMounted, ref } from "vue";

import {
  adminApi,
  text,
  usePageData,
  type AdminList,
  type AuditLogRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import JsonDiff from "@/modules/admin/components/JsonDiff.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<AuditLogRecord>>(
  () => adminApi.auditLogs({ action: actionFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "审计记录",
  () => auth.handleSessionExpired(),
);

const actionFilter = ref("");
const search = ref("");
const selected = ref<AuditLogRecord | null>(null);

const columns: TableColumn[] = [
  { key: "created_at", label: "时间", width: "170px" },
  { key: "actor_label", label: "操作人", width: "140px" },
  { key: "action", label: "动作" },
  { key: "target_type", label: "对象类型", width: "130px", hideOnMobile: true },
  { key: "target_id", label: "对象编号", width: "180px", hideOnMobile: true },
  { key: "reason", label: "理由", hideOnMobile: true },
];

const QUICK_ACTIONS = [
  { value: "", label: "全部动作" },
  { value: "content.publish", label: "内容发布" },
  { value: "content.archive", label: "内容下架" },
  { value: "refund.approve", label: "退款批准" },
  { value: "point_adjustment.approve", label: "积分调整批准" },
  { value: "staff.update", label: "员工权限变更" },
  { value: "user.restriction", label: "账号限制" },
  { value: "sku.stock", label: "库存调整" },
];

const detailItems = computed(() => {
  const row = selected.value;
  if (!row) return [];
  return [
    { label: "时间", value: formatDateTime(row.created_at) },
    { label: "操作人", value: text(row.actor_label) },
    { label: "动作", value: text(row.action) },
    { label: "对象", value: `${text(row.target_type)} / ${text(row.target_id)}` },
    { label: "理由", value: text(row.reason) },
    { label: "记录编号", value: text(row.id) },
  ];
});

function openDetail(row: AuditLogRecord): void {
  selected.value = row;
}

function showExportHint(): void {
  toast.info("审计记录只读", "后台不提供审计记录导出；对账数据的批量导出请走「对账」模块，并填写导出理由。");
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Audit"
      title="审计"
      description="按动作、对象或关键词查询操作记录，并查看前后快照摘要。审计记录只读，任何人都不能在界面里修改或删除。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
        <AppButton size="sm" variant="quiet" @click="showExportHint">导出说明</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="审计摘要已脱敏"
      description="快照对比只显示字段级变化摘要，并对密钥、会话标识、签名地址、手机号与地址等字段统一脱敏，不展示完整报文。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="动作" for="audit-action">
        <select id="audit-action" v-model="actionFilter" class="field-base">
          <option v-for="item in QUICK_ACTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </FieldRow>
      <FieldRow label="关键词" for="audit-q" hint="支持对象编号、员工显示名或理由关键词">
        <input id="audit-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">查询</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; actionFilter = ''; state.load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="没有符合条件的审计记录"
      empty-description="可以换个动作或关键词；审计记录只增不减，不会因为筛选为空而消失。"
      @retry="state.load()"
    >
      <template #cell-created_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
      </template>
      <template #cell-action="{ row }">
        <button type="button" class="link-underline text-left font-mono text-xs hover:text-amber-strong" @click="openDetail(row)">
          {{ text(row.action) }}
        </button>
      </template>
      <template #cell-target_id="{ row }">
        <span class="font-mono text-xs">{{ text(row.target_id) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openDetail(row)">查看快照摘要</AppButton>
      </template>
    </DataTable>

    <SurfaceCard v-if="selected">
      <h2 class="font-display text-lg">审计详情</h2>
      <FieldList class="mt-4" :items="detailItems" />
      <div class="mt-5 grid gap-6 lg:grid-cols-2">
        <JsonDiff
          :before="selected.before"
          :after="selected.after"
          title="前后快照摘要"
          :initially-open="false"
        />
        <div class="rounded-lg border border-line bg-surface-muted px-5 py-4 text-sm text-ink-muted">
          <p class="font-medium text-ink-soft">关于这份记录</p>
          <ul class="mt-2 space-y-2 text-xs">
            <li>· 只显示发生变化且可安全展示的字段。</li>
            <li>· 标记为「敏感」的字段内容已被脱敏，只保留变化事实。</li>
            <li>· 审计记录不可编辑、不可删除；如发现异常操作，请连同记录编号反馈给负责人。</li>
          </ul>
        </div>
      </div>
      <div class="mt-4">
        <AppButton variant="quiet" @click="selected = null">关闭详情</AppButton>
      </div>
    </SurfaceCard>
  </div>
</template>
