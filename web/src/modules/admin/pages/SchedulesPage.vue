<script setup lang="ts">
/**
 * 档期与申请：容量、时间窗口、普通申请与候补审批。
 *
 * 约束（07 文档）：名额是权威资源，由服务端在事务内扣减；审批必须写理由，
 * 通过时给出购买窗口；候补与普通申请分开显示，不能混在一起。
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
  type ScheduleApplicationRecord,
  type ScheduleRoundRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatCountdown, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const rounds = ref<ScheduleRoundRecord[]>([]);
const roundsError = ref<FailureView | null>(null);
const applications = ref<ScheduleApplicationRecord[]>([]);
const applicationsError = ref<FailureView | null>(null);
const loading = ref(true);

const state = usePageData<AdminList<ScheduleRoundRecord>>(
  () => adminApi.scheduleRounds({ limit: 20 }),
  { items: [] },
  "档期列表",
  () => auth.handleSessionExpired(),
);

const selectedRoundId = ref("");

const roundColumns: TableColumn[] = [
  { key: "title", label: "档期" },
  { key: "status", label: "状态", width: "110px" },
  { key: "start_at", label: "开始", width: "170px", hideOnMobile: true },
  { key: "end_at", label: "截止", width: "170px", hideOnMobile: true },
  { key: "capacity", label: "容量", width: "110px" },
  { key: "waitlist_count", label: "候补", width: "90px" },
];

const applicationColumns: TableColumn[] = [
  { key: "applicant_label", label: "申请人" },
  { key: "kind", label: "类型", width: "110px" },
  { key: "series", label: "系列", hideOnMobile: true },
  { key: "status", label: "状态", width: "120px" },
  { key: "priority_rank", label: "优先级", width: "90px" },
  { key: "created_at", label: "提交时间", width: "170px", hideOnMobile: true },
];

const selectedRound = computed(() => rounds.value.find((item) => item.id === selectedRoundId.value) ?? null);

const editOpen = ref(false);
const editForm = ref({ title: "", start_at: "", end_at: "", capacity: 0, timezone: "Asia/Shanghai" });
const editError = ref("");
const editPending = ref(false);

const decideTarget = ref<ScheduleApplicationRecord | null>(null);
const decideMode = ref<"SELECTED" | "REJECTED">("SELECTED");
const decideReason = ref("");
const priorityRank = ref(1);
const decideError = ref("");
const decidePending = ref(false);
const decideConfirmOpen = ref(false);

const applyTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "APPROVED" || status === "PURCHASED") return "jade";
  if (status === "PENDING" || status === "WAITLIST") return "amber";
  if (status === "REJECTED" || status === "EXPIRED") return "wine";
  return "neutral";
};

async function loadApplications(): Promise<void> {
  if (!selectedRoundId.value) {
    applications.value = [];
    applicationsError.value = null;
    return;
  }
  const result = await collect<{ apps: AdminList<ScheduleApplicationRecord> }>({
    apps: () => adminApi.scheduleApplications(selectedRoundId.value, { limit: 30 }),
  });
  if (result.ok.apps) {
    applications.value = result.ok.apps.items;
    applicationsError.value = null;
  } else {
    applications.value = [];
    applicationsError.value = describeFailure(result.failed[0]?.error, "申请列表");
  }
}

async function load(): Promise<void> {
  loading.value = true;
  await state.load();
  rounds.value = state.data.value.items;
  roundsError.value = state.error.value;
  if (!selectedRoundId.value && rounds.value.length) {
    selectedRoundId.value = rounds.value[0]!.id;
  }
  await loadApplications();
  loading.value = false;
}

function openEdit(): void {
  const round = selectedRound.value;
  if (!round) return;
  editForm.value = {
    title: String(round.title ?? ""),
    start_at: String(round.start_at ?? "").slice(0, 16),
    end_at: String(round.end_at ?? "").slice(0, 16),
    capacity: typeof round.capacity === "number" ? round.capacity : 0,
    timezone: String(round.timezone ?? "Asia/Shanghai"),
  };
  editError.value = "";
  editOpen.value = true;
}

async function saveRound(): Promise<void> {
  const round = selectedRound.value;
  if (!round) return;
  if (editForm.value.capacity < 0) {
    editError.value = "容量不能是负数。";
    return;
  }
  if (editForm.value.start_at && editForm.value.end_at && editForm.value.end_at <= editForm.value.start_at) {
    editError.value = "截止时间必须晚于开始时间。";
    return;
  }
  editPending.value = true;
  try {
    await adminApi.updateScheduleRound(round.id, {
      title: editForm.value.title,
      start_at: editForm.value.start_at,
      end_at: editForm.value.end_at,
      capacity: Math.round(editForm.value.capacity),
      timezone: editForm.value.timezone,
      version: round.version ?? 0,
    });
    toast.success("档期已更新", "容量与时间窗口变化会立刻影响客户提交申请。");
    editOpen.value = false;
    await load();
  } catch (caught) {
    editError.value = actionErrorMessage(caught, "保存失败，请稍后重试。").message;
  } finally {
    editPending.value = false;
  }
}

/** 审批档期申请：通过后生成排序序号与预留资格（C11）。 */
function openDecide(row: ScheduleApplicationRecord, mode: "SELECTED" | "REJECTED"): void {
  decideTarget.value = row;
  decideMode.value = mode;
  decideReason.value = "";
  priorityRank.value = typeof row.rank_sequence === "number" ? row.rank_sequence : 1;
  decideError.value = "";
}

function requestDecideConfirm(): void {
  if (decideReason.value.trim().length < 6) {
    decideError.value = "请填写至少 6 个字的审批理由，客户与审计都会看到。";
    return;
  }
  if (decideMode.value === "SELECTED" && (!Number.isInteger(priorityRank.value) || priorityRank.value < 1)) {
    decideError.value = "排序序号必须是大于 0 的整数。";
    return;
  }
  decideError.value = "";
  decideConfirmOpen.value = true;
}

async function submitDecide(): Promise<void> {
  const row = decideTarget.value;
  if (!row) return;
  decidePending.value = true;
  try {
    await adminApi.decideScheduleApplication(row.id, {
      decision: decideMode.value,
      reason: decideReason.value.trim(),
      ...(decideMode.value === "SELECTED" ? { rank_sequence: Math.round(priorityRank.value) } : {}),
    });
    toast.success(
      decideMode.value === "SELECTED" ? "申请已通过" : "申请已拒绝",
      decideMode.value === "SELECTED"
        ? `已生成排序 ${Math.round(priorityRank.value)} 与预留资格；客户可在「我的申请」查看。`
        : "拒绝理由已记录并同步给客户。",
    );
    decideConfirmOpen.value = false;
    decideTarget.value = null;
    await loadApplications();
  } catch (caught) {
    decideError.value = actionErrorMessage(caught, "审批失败，请稍后重试。").message;
    decideConfirmOpen.value = false;
  } finally {
    decidePending.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Schedules"
      title="档期与申请"
      description="维护档期容量与时间窗口，审批普通申请与候补。名额由服务端在事务内扣减，后台不会直接改写已用名额。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="loading" @click="load">刷新</AppButton>
        <AppButton
          size="sm"
          :disabled="!selectedRound"
          :disabled-reason="selectedRound ? '' : '请先在下面选择一个档期'"
          @click="openEdit"
        >
          编辑档期容量与时间窗口
        </AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="审批档期申请会占用名额"
      description="通过后生成排序序号与预留资格；客户在「我的申请」中查看。名额由服务端在事务内校验，已处理申请不能重复审批。"
    />

    <DataTable
      :columns="roundColumns"
      :rows="rounds"
      :loading="state.loading.value"
      :error="roundsError"
      empty-title="还没有档期"
      empty-description="档期由运营按季度创建；没有档期时客户无法提交申请。"
      @retry="load"
    >
      <template #cell-title="{ row }">
        <button
          type="button"
          class="link-underline text-left font-medium hover:text-amber-strong"
          :aria-pressed="selectedRoundId === row.id"
          @click="selectedRoundId = String(row.id); loadApplications()"
        >
          {{ text(row.title) }}
        </button>
        <p v-if="selectedRoundId === row.id" class="mt-1 text-xs text-jade">已选中，下面的申请列表对应该档期</p>
      </template>
      <template #cell-end_at="{ row }">
        <span class="tabular-nums text-xs">
          {{ formatDateTime(row.end_at) }} · {{ formatCountdown(row.end_at) }}
        </span>
      </template>
      <template #cell-capacity="{ row }">
        <span class="tabular-nums">{{ text(row.capacity, "0") }}（已用 {{ text(row.used, "0") }}）</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="selectedRoundId = String(row.id); loadApplications()">
          查看申请
        </AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
      <SurfaceCard>
        <h2 class="font-display text-lg">
          申请列表{{ selectedRound ? `：${text(selectedRound.title)}` : "" }}
        </h2>
        <p class="mt-1 text-xs text-ink-faint">普通申请与候补都在这里；按提交时间顺序处理，避免漏掉先到的人。</p>
        <div class="mt-4">
          <DataTable
            :columns="applicationColumns"
            :rows="applications"
            :loading="loading"
            :error="applicationsError"
            :notice="
              selectedRoundId
                ? null
                : { title: '请先选择一个档期', description: '在上面的档期列表里点击档期名称，即可查看该档期的申请。' }
            "
            empty-title="这个档期还没有申请"
            empty-description="申请会在客户提交后出现在这里；候补同样需要人工审批。"
            @retry="loadApplications"
          >
            <template #cell-kind="{ row }">
              <StatusBadge
                :tone="String(row.kind ?? '').includes('WAITLIST') ? 'neutral' : 'amber'"
                :label="String(row.kind ?? '').includes('WAITLIST') ? '候补' : '普通申请'"
              />
            </template>
            <template #cell-status="{ row }">
              <StatusBadge :tone="applyTone(row.status)" :label="text(row.status, '待处理')" />
            </template>
            <template #actions="{ row }">
              <AppButton size="sm" @click="openDecide(row, 'SELECTED')">通过</AppButton>
              <AppButton size="sm" variant="danger" @click="openDecide(row, 'REJECTED')">拒绝</AppButton>
            </template>
          </DataTable>
        </div>
      </SurfaceCard>

      <div class="space-y-6">
        <ModuleCard v-if="selectedRound" title="当前档期" status="只读摘要">
          <FieldList
            :items="[
              { label: '档期', value: text(selectedRound.title) },
              { label: '状态', value: text(selectedRound.status) },
              { label: '开始', value: formatDateTime(selectedRound.start_at) },
              { label: '截止', value: formatDateTime(selectedRound.end_at) },
              { label: '时区', value: text(selectedRound.timezone) },
              { label: '容量 / 已用', value: `${text(selectedRound.capacity, '0')} / ${text(selectedRound.used, '0')}` },
              { label: '候补人数', value: text(selectedRound.waitlist_count, '0') },
            ]"
            :columns="1"
          />
        </ModuleCard>

        <SurfaceCard v-if="editOpen">
          <h2 class="font-display text-lg">编辑档期</h2>
          <div class="mt-4 space-y-4">
            <FieldRow label="档期名称" required for="round-title">
              <input id="round-title" v-model="editForm.title" type="text" class="field-base" />
            </FieldRow>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="开始时间" required for="round-start" hint="本地时间，格式 2026-01-05T10:00">
                <input id="round-start" v-model="editForm.start_at" type="datetime-local" class="field-base" />
              </FieldRow>
              <FieldRow label="截止时间" required for="round-end">
                <input id="round-end" v-model="editForm.end_at" type="datetime-local" class="field-base" />
              </FieldRow>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="容量（名额）" required for="round-capacity">
                <input id="round-capacity" v-model.number="editForm.capacity" type="number" min="0" step="1" class="field-base tabular-nums" />
              </FieldRow>
              <FieldRow label="时区" required for="round-tz">
                <input id="round-tz" v-model="editForm.timezone" type="text" class="field-base" />
              </FieldRow>
            </div>
            <p class="text-xs text-ink-faint">
              缩小容量不会自动取消已通过的申请；服务端会拒绝把容量改到低于已用名额。
            </p>
            <p v-if="editError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ editError }}
            </p>
            <div class="flex flex-wrap gap-3">
              <AppButton :loading="editPending" @click="saveRound">保存档期</AppButton>
              <AppButton variant="quiet" @click="editOpen = false">取消</AppButton>
            </div>
          </div>
        </SurfaceCard>

        <SurfaceCard v-if="decideTarget">
          <h2 class="font-display text-lg">
            {{ decideMode === "SELECTED" ? "通过申请" : "拒绝申请" }}：{{ text(decideTarget.applicant_label, "申请人") }}
          </h2>
          <p class="mt-2 text-sm text-ink-muted">
            类型：{{ String(decideTarget.kind ?? "").includes("WAITLIST") ? "候补" : "普通申请" }}；系列
            {{ text(decideTarget.series) }}。
          </p>
          <div class="mt-4 space-y-4">
            <FieldRow label="审批理由" required for="decide-reason" hint="理由会记录并同步给客户，请写具体依据">
              <input id="decide-reason" v-model="decideReason" type="text" class="field-base" />
            </FieldRow>
            <div v-if="decideMode === 'SELECTED'" class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="排序序号" required for="decide-rank" hint="数字越小越靠前，用于名额冲突排序">
                <input id="decide-rank" v-model.number="priorityRank" type="number" min="1" step="1" class="field-base tabular-nums" />
              </FieldRow>
            </div>
            <p v-if="decideError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ decideError }}
            </p>
            <div class="flex flex-wrap gap-3">
              <AppButton :variant="decideMode === 'SELECTED' ? 'primary' : 'danger'" @click="requestDecideConfirm">
                {{ decideMode === "SELECTED" ? "提交通过" : "提交拒绝" }}
              </AppButton>
              <AppButton variant="quiet" @click="decideTarget = null">取消</AppButton>
            </div>
          </div>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="decideConfirmOpen"
      :title="decideMode === 'SELECTED' ? '通过档期申请并预留名额' : '拒绝档期申请'"
      :target="decideTarget ? `申请人 ${text(decideTarget.applicant_label)} · 类型 ${String(decideTarget.kind ?? '').includes('WAITLIST') ? '候补' : '普通申请'}` : ''"
      :change="decideMode === 'SELECTED' ? `状态 → 已通过，排序 ${priorityRank}` : '状态 → 已拒绝'"
      :amount="decideMode === 'SELECTED' ? '占用 1 个名额' : '不占用名额'"
      :consequence="
        decideMode === 'SELECTED'
          ? '通过后会生成排序与预留资格；名额不足时服务端会拒绝这次审批。'
          : '拒绝会立即通知客户，并记录理由；该申请不能恢复，客户需要重新提交。'
      "
      :confirm-label="decideMode === 'SELECTED' ? '确认通过并预留名额' : '确认拒绝这次申请'"
      :tone="decideMode === 'SELECTED' ? 'primary' : 'danger'"
      :pending="decidePending"
      @confirm="submitDecide"
    />
  </div>
</template>
