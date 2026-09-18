<script setup lang="ts">
/**
 * 任务与死信：任务失败、人工重试与死信处理。
 *
 * 约束（04 文档）：outbox 至少一次投递、业务效果一次；人工重试必须先看失败原因，
 * 重试与清理都要写理由并留下审计。死信事件不会自动删除。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type JobRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<JobRecord>>(
  () => adminApi.jobs({ status: statusFilter.value || undefined, name: nameFilter.value || undefined, limit: 20 }),
  { items: [] },
  "任务列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("FAILED");
const nameFilter = ref("");

const columns: TableColumn[] = [
  { key: "event_id", label: "事件" },
  { key: "name", label: "任务名" },
  { key: "status", label: "状态", width: "110px" },
  { key: "attempts", label: "尝试次数", width: "100px" },
  { key: "dead_letter", label: "死信", width: "90px" },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
];

const selected = ref<JobRecord | null>(null);
const retryReason = ref("");
const retryError = ref("");
const retryPending = ref(false);
const retryConfirmOpen = ref(false);

const cleanupOpen = ref(false);
const cleanupReason = ref("");
const cleanupError = ref("");
const cleanupPending = ref(false);

const reconcilePending = ref(false);

const deadLetters = computed(() => state.data.value.items.filter((item) => item.dead_letter));

const summaryItems = computed(() => [
  { label: "当前页任务", value: state.data.value.items.length },
  { label: "失败任务", value: state.data.value.items.filter((item) => String(item.status ?? "") === "FAILED").length },
  { label: "死信事件", value: deadLetters.value.length },
  { label: "筛选状态", value: statusFilter.value || "全部" },
]);

function openRetry(row: JobRecord): void {
  selected.value = row;
  retryReason.value = "";
  retryError.value = "";
}

function requestRetryConfirm(): void {
  if (retryReason.value.trim().length < 6) {
    retryError.value = "请填写至少 6 个字的重试理由，例如「渠道已恢复，通知补投」。";
    return;
  }
  retryError.value = "";
  retryConfirmOpen.value = true;
}

async function submitRetry(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  retryPending.value = true;
  try {
    await adminApi.retryJob(row.event_id, { reason: retryReason.value.trim() });
    toast.success("已提交人工重试", "重试沿用原 event_id；如果已经成功过，业务效果不会重复执行。");
    retryConfirmOpen.value = false;
    selected.value = null;
    await state.load();
  } catch (caught) {
    retryError.value = actionErrorMessage(caught, "重试失败，请稍后重试。").message;
    retryConfirmOpen.value = false;
  } finally {
    retryPending.value = false;
  }
}

function requestCleanupConfirm(): void {
  if (cleanupReason.value.trim().length < 6) {
    cleanupError.value = "请填写至少 6 个字的清理理由。";
    return;
  }
  cleanupError.value = "";
  cleanupOpen.value = true;
}

async function submitCleanup(): Promise<void> {
  cleanupPending.value = true;
  try {
    const result = await adminApi.cleanupJobs({ dry_run: false });
    toast.success("清理任务已执行", `已处理：${text((result as Record<string, unknown>).cleaned, "按服务端结果")}。`);
    cleanupOpen.value = false;
    cleanupReason.value = "";
    await state.load();
  } catch (caught) {
    cleanupError.value = actionErrorMessage(caught, "清理失败，请稍后重试。").message;
    cleanupOpen.value = false;
  } finally {
    cleanupPending.value = false;
  }
}

async function runReconcile(): Promise<void> {
  reconcilePending.value = true;
  try {
    await adminApi.reconcileJobs();
    toast.success("已触发对账任务", "对账结果会出现在「对账」页；差异需要人工归因与处置。");
    await state.load();
  } catch (caught) {
    toast.error("触发失败", actionErrorMessage(caught, "请稍后重试。").message);
  } finally {
    reconcilePending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Jobs"
      title="任务与死信"
      description="查看 outbox 任务与失败原因，人工重试或处理死信。重试沿用原事件编号，保证业务效果只发生一次。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
        <AppButton size="sm" variant="secondary" :loading="reconcilePending" @click="runReconcile">触发对账任务</AppButton>
        <AppButton size="sm" variant="danger" @click="cleanupOpen = true">清理已处理事件</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="重试前先看失败原因"
      description="人工重试不会创建新事件：同一 event_id 已经产生业务效果的不会重复执行。死信事件需要先判断是通知失败还是业务失败。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="状态" for="job-status">
        <select id="job-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="FAILED">失败</option>
          <option value="PENDING">待处理</option>
          <option value="PROCESSED">已处理</option>
        </select>
      </FieldRow>
      <FieldRow label="任务名关键词" for="job-name">
        <input id="job-name" v-model="nameFilter" type="search" class="field-base" placeholder="例如 payment_notify" />
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="statusFilter = 'FAILED'; nameFilter = ''; state.load()">只看失败</AppButton>
      </div>
    </form>

    <div class="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
      <DataTable
        :columns="columns"
        :rows="state.data.value.items"
        :loading="state.loading.value"
        :error="state.error.value"
        :has-more="Boolean(state.data.value.has_more)"
        empty-title="没有符合条件的任务"
        empty-description="当前筛选条件下没有任务记录。可以先看「失败」状态，再决定是否重试。"
        @retry="state.load()"
      >
        <template #cell-event_id="{ row }">
          <span class="font-mono text-xs">{{ text(row.event_id) }}</span>
        </template>
        <template #cell-status="{ row }">
          <StatusBadge
            :tone="String(row.status ?? '') === 'FAILED' ? 'wine' : String(row.status ?? '') === 'PROCESSED' ? 'jade' : 'amber'"
            :label="text(row.status, '未知')"
          />
        </template>
        <template #cell-dead_letter="{ row }">
          <StatusBadge v-if="row.dead_letter" tone="wine" label="死信" />
          <span v-else class="text-xs text-ink-faint">否</span>
        </template>
        <template #cell-updated_at="{ row }">
          <span class="tabular-nums text-xs">{{ formatDateTime(row.updated_at) }}</span>
        </template>
        <template #actions="{ row }">
          <AppButton size="sm" variant="secondary" @click="openRetry(row)">人工重试</AppButton>
        </template>
      </DataTable>

      <div class="space-y-6">
        <ModuleCard title="当前页统计" status="实时">
          <FieldList :items="summaryItems" :columns="1" />
        </ModuleCard>

        <SurfaceCard v-if="selected">
          <h2 class="font-display text-lg">人工重试</h2>
          <FieldList
            class="mt-3"
            :items="[
              { label: '事件编号', value: text(selected.event_id) },
              { label: '任务名', value: text(selected.name) },
              { label: '状态', value: text(selected.status) },
              { label: '尝试次数', value: text(selected.attempts, '0') },
              { label: '最近更新', value: formatDateTime(selected.updated_at) },
              { label: '失败原因', value: text(selected.last_error, '未提供失败原因') },
            ]"
            :columns="1"
          />
          <p class="mt-3 rounded-md border border-line bg-surface-muted px-4 py-3 text-xs text-ink-muted">
            失败原因由服务端返回，可能经过脱敏；不要凭猜测重试，先确认外部依赖（渠道、短信、存储）是否恢复。
          </p>
          <FieldRow class="mt-4" label="重试理由" required for="job-retry-reason">
            <input id="job-retry-reason" v-model="retryReason" type="text" class="field-base" />
          </FieldRow>
          <p v-if="retryError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ retryError }}
          </p>
          <div class="mt-4 flex flex-wrap gap-3">
            <AppButton @click="requestRetryConfirm">提交重试</AppButton>
            <AppButton variant="quiet" @click="selected = null">取消</AppButton>
          </div>
        </SurfaceCard>

        <SurfaceCard v-else>
          <h2 class="font-display text-lg">死信处理</h2>
          <p class="mt-2 text-sm text-ink-muted">
            死信事件不会自动删除。先确认业务效果是否已经发生，再决定重试；重复重试不会造成重复入账。
          </p>
          <ul v-if="deadLetters.length" class="mt-4 space-y-2 text-sm">
            <li v-for="item in deadLetters" :key="item.event_id" class="rounded-sm border border-wine bg-wine-soft px-4 py-3">
              <p class="font-mono text-xs">{{ text(item.event_id) }} · {{ text(item.name) }}</p>
              <p class="mt-1 text-xs">{{ text(item.last_error, "未提供失败原因") }}</p>
            </li>
          </ul>
          <p v-else class="mt-4 text-sm text-ink-muted">当前筛选结果里没有死信事件。</p>
        </SurfaceCard>
      </div>
    </div>

    <SurfaceCard>
      <h2 class="font-display text-lg">清理已处理事件</h2>
      <p class="mt-1 text-xs text-ink-faint">只清理已经成功处理且超过保留期的事件；失败与死信不会被清理。</p>
      <FieldRow class="mt-4" label="清理理由" required for="job-cleanup-reason">
        <input id="job-cleanup-reason" v-model="cleanupReason" type="text" class="field-base" />
      </FieldRow>
      <p v-if="cleanupError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ cleanupError }}
      </p>
      <div class="mt-4">
        <AppButton variant="danger" @click="requestCleanupConfirm">提交清理</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="retryConfirmOpen"
      title="人工重试失败任务"
      :target="selected ? `事件 ${text(selected.event_id)} · ${text(selected.name)}` : ''"
      change="重新投递该事件，沿用原 event_id。"
      :amount="`已尝试 ${text(selected?.attempts, '0')} 次`"
      consequence="重试会再次调用外部依赖（渠道、短信或存储）。已产生业务效果的事件不会重复执行，但外部调用仍可能再次发生，请先确认依赖已恢复。"
      confirm-label="确认重试这个事件"
      tone="primary"
      :pending="retryPending"
      @confirm="submitRetry"
    />

    <ConfirmDialog
      v-model="cleanupOpen"
      title="清理已处理事件"
      target="已成功处理且超过保留期的 outbox 事件"
      change="删除历史事件记录；失败与死信事件不会被删除。"
      consequence="清理会移除用于幂等去重的历史记录，过早清理可能导致重复投递被再次执行。只在确认保留期已过时执行。"
      confirm-label="确认清理"
      tone="danger"
      :pending="cleanupPending"
      require-typed="清理"
      typed-label="请输入「清理」确认"
      @confirm="submitCleanup"
    />
  </div>
</template>
