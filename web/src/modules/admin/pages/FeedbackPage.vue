<script setup lang="ts">
/**
 * 反馈与奖励：去重、回复、奖励等级、申诉。
 *
 * 约束（09 文档）：反馈奖励要按等级发放并说明理由；重复反馈先合并再去重标记；
 * 不能在提交环节承诺积分，奖励在审核通过后才入账。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type FeedbackRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, pointsWithUnit } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<FeedbackRecord>>(
  () => adminApi.feedback({ status: statusFilter.value || undefined, category: categoryFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "反馈列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const categoryFilter = ref("");
const search = ref("");

const columns: TableColumn[] = [
  { key: "title", label: "反馈" },
  { key: "category", label: "分类", width: "110px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "reward_level", label: "奖励等级", width: "130px" },
  { key: "duplicate_of", label: "重复于", width: "140px", hideOnMobile: true },
  { key: "created_at", label: "提交时间", width: "170px", hideOnMobile: true },
];

/** 奖励等级对应的星绒币数量（C14：minor=5 / normal=20 / major=50）。数值以服务端规则为准，这里只做界面提示。 */
const REWARD_LEVELS = [
  { value: "", label: "不奖励", points: 0 },
  { value: "minor", label: "minor 轻微问题或小建议", points: 5 },
  { value: "normal", label: "normal 可复现的缺陷", points: 20 },
  { value: "major", label: "major 重大缺陷或关键体验改进", points: 50 },
];

const selected = ref<FeedbackRecord | null>(null);
const replyBody = ref("");
const rewardLevel = ref("");
const reviewReason = ref("");
const reviewCriteria = ref("");
const reviewImpact = ref("");
const errorText = ref("");
const pending = ref(false);

const duplicateTargetId = ref("");
const duplicateReason = ref("");
const duplicateError = ref("");

const appealTarget = ref<FeedbackRecord | null>(null);
const appealMode = ref<"APPROVE" | "REJECT">("APPROVE");
const appealReason = ref("");
const appealError = ref("");
const appealPending = ref(false);
const appealConfirmOpen = ref(false);

const reviewConfirmOpen = ref(false);

const rewardPoints = computed(() => REWARD_LEVELS.find((item) => item.value === rewardLevel.value)?.points ?? 0);

const statusTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "REWARDED" || status === "RESOLVED" || status === "ACCEPTED") return "jade";
  if (status === "SUBMITTED" || status === "REVIEWING" || status === "PENDING") return "amber";
  if (status === "REJECTED" || status === "DUPLICATE") return "wine";
  return "neutral";
};

function openPanel(row: FeedbackRecord): void {
  selected.value = row;
  replyBody.value = "";
  rewardLevel.value = String(row.reward_level ?? "");
  reviewReason.value = "";
  reviewCriteria.value = "";
  reviewImpact.value = "";
  errorText.value = "";
  duplicateTargetId.value = String(row.duplicate_of ?? "");
  duplicateReason.value = "";
  duplicateError.value = "";
}

async function sendReply(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (replyBody.value.trim().length < 4) {
    errorText.value = "回复内容至少 4 个字；请直接回答客户的问题。";
    return;
  }
  pending.value = true;
  try {
    await adminApi.replyFeedback(row.id, { body: replyBody.value.trim() });
    toast.success("回复已发送", "客户会在反馈详情里看到这条回复。");
    replyBody.value = "";
    errorText.value = "";
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "回复失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

function requestReviewConfirm(): void {
  if (reviewReason.value.trim().length < 6) {
    errorText.value = "请填写至少 6 个字的审核说明；奖励与不奖励都需要理由，客户可以看到。";
    return;
  }
  if (rewardLevel.value && reviewCriteria.value.trim().length < 4) {
    errorText.value = "发放奖励必须记录判定依据，例如「含复现步骤且影响结算金额」。";
    return;
  }
  if (rewardLevel.value === "major" && reviewImpact.value.trim().length < 4) {
    errorText.value = "major 等级必须记录影响范围与判据。";
    return;
  }
  errorText.value = "";
  reviewConfirmOpen.value = true;
}

async function submitReview(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  pending.value = true;
  try {
    // 结论统一走 RESOLVED：发奖时必须带判据，不发奖时由服务端给出「未达奖励标准」的说明。
    await adminApi.reviewFeedback(row.id, {
      result: "RESOLVED",
      reason: reviewReason.value.trim(),
      ...(rewardLevel.value
        ? {
            reward_level: rewardLevel.value,
            criteria: reviewCriteria.value.trim(),
            ...(reviewImpact.value.trim() ? { impact: reviewImpact.value.trim() } : {}),
          }
        : {}),
    });
    toast.success(
      rewardLevel.value ? "审核完成并发放奖励" : "审核完成，不发放奖励",
      rewardLevel.value
        ? `已按 ${rewardLevel.value} 发放 ${pointsWithUnit(rewardPoints.value)}；积分入账会记录在流水里。`
        : "不奖励的决定与理由已记录，客户可以在反馈详情里看到。",
    );
    reviewConfirmOpen.value = false;
    selected.value = null;
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "审核失败，请稍后重试。").message;
    reviewConfirmOpen.value = false;
  } finally {
    pending.value = false;
  }
}

async function markDuplicate(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (!duplicateTargetId.value.trim()) {
    duplicateError.value = "请填写被合并到的反馈编号（原反馈）。";
    return;
  }
  if (duplicateReason.value.trim().length < 6) {
    duplicateError.value = "请填写至少 6 个字的重复判定说明，例如「与 FB-2026-0042 同一复现步骤」。";
    return;
  }
  pending.value = true;
  try {
    await adminApi.markDuplicate(row.id, {
      duplicate_of: duplicateTargetId.value.trim(),
      reason: duplicateReason.value.trim(),
    });
    toast.success("已标记为重复反馈", "重复反馈不会重复发放奖励；原反馈的进度继续有效。");
    selected.value = null;
    await state.load();
  } catch (caught) {
    duplicateError.value = actionErrorMessage(caught, "标记重复失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

function openAppeal(row: FeedbackRecord, mode: "APPROVE" | "REJECT"): void {
  appealTarget.value = row;
  appealMode.value = mode;
  appealReason.value = "";
  appealError.value = "";
}

function requestAppealConfirm(): void {
  if (appealReason.value.trim().length < 6) {
    appealError.value = "请填写至少 6 个字的处理理由，申诉结果会同步给客户。";
    return;
  }
  appealError.value = "";
  appealConfirmOpen.value = true;
}

async function submitAppeal(): Promise<void> {
  const row = appealTarget.value;
  if (!row) return;
  appealPending.value = true;
  try {
    await adminApi.handleAppeal(row.id, { decision: appealMode.value, reason: appealReason.value.trim() });
    toast.success(appealMode.value === "APPROVE" ? "申诉已受理" : "申诉已驳回", "处理结果与理由已记录。");
    appealConfirmOpen.value = false;
    appealTarget.value = null;
    await state.load();
  } catch (caught) {
    appealError.value = actionErrorMessage(caught, "处理申诉失败，请稍后重试。").message;
    appealConfirmOpen.value = false;
  } finally {
    appealPending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Feedback"
      title="反馈与奖励"
      description="先查重再回复，最后按等级决定奖励。奖励理由与等级都会同步给客户，积分在审核通过后才入账。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="奖励不能口头承诺"
      description="提交反馈时不会发放积分；只有审核通过并按等级发放后，星绒币才会进入客户流水。重复反馈只奖励一次。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-4" @submit.prevent="state.load()">
      <FieldRow label="关键词" for="fb-q">
        <input id="fb-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="分类" for="fb-category">
        <select id="fb-category" v-model="categoryFilter" class="field-base">
          <option value="">全部分类</option>
          <option value="bug">缺陷</option>
          <option value="suggestion">建议</option>
          <option value="experience">体验</option>
          <option value="other">其它</option>
        </select>
      </FieldRow>
      <FieldRow label="状态" for="fb-status">
        <select id="fb-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="SUBMITTED">待处理</option>
          <option value="REVIEWING">处理中</option>
          <option value="REWARDED">已奖励</option>
          <option value="DUPLICATE">已合并</option>
          <option value="REJECTED">已驳回</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; categoryFilter = ''; statusFilter = ''; state.load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="没有待处理的反馈"
      empty-description="客户提交反馈后会出现在这里；可以先按标题与复现步骤查重。"
      @retry="state.load()"
    >
      <template #cell-title="{ row }">
        <button
          type="button"
          class="link-underline text-left font-medium hover:text-amber-strong"
          @click="openPanel(row)"
        >
          {{ text(row.title, "未命名反馈") }}
        </button>
        <p class="mt-1 text-xs text-ink-faint">回复 {{ text(row.reply_count, "0") }} 条</p>
      </template>
      <template #cell-status="{ row }">
        <StatusBadge :tone="statusTone(row.status)" :label="text(row.status, '待处理')" />
      </template>
      <template #cell-reward_level="{ row }">
        <span class="text-xs">{{ text(row.reward_level, "未发放") }}</span>
      </template>      <template #cell-created_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openPanel(row)">打开处理面板</AppButton>
        <AppButton
          v-if="row.appeal_status"
          size="sm"
          variant="quiet"
          @click="openAppeal(row, 'APPROVE')"
        >
          处理申诉（{{ text(row.appeal_status) }}）
        </AppButton>
      </template>
    </DataTable>

    <SurfaceCard v-if="selected">
      <SectionHeading
        level="2"
        :title="`处理反馈：${text(selected.title, text(selected.id))}`"
        description="先判断是否为重复反馈，再决定回复与奖励等级。奖励理由客户可见。"
      />
      <FieldList
        class="mt-4"
        :items="[
          { label: '分类', value: text(selected.category) },
          { label: '当前状态', value: text(selected.status) },
          { label: '奖励等级', value: text(selected.reward_level, '未发放') },
          { label: '重复于', value: text(selected.duplicate_of, '无') },
          { label: '提交时间', value: formatDateTime(selected.created_at) },
          { label: '回复条数', value: text(selected.reply_count, '0') },
        ]"
      />

      <div class="mt-6 grid gap-6 lg:grid-cols-2">
        <div class="space-y-4">
          <h3 class="text-sm font-medium text-ink-soft">回复客户</h3>
          <FieldRow label="回复内容" for="fb-reply" hint="不要写内部工单号以外的隐私信息，也不要承诺未核准的交期">
            <textarea id="fb-reply" v-model="replyBody" rows="3" class="field-base resize-y" />
          </FieldRow>
          <AppButton :loading="pending" @click="sendReply">发送回复</AppButton>
        </div>

        <div class="space-y-4">
          <h3 class="text-sm font-medium text-ink-soft">奖励与审核</h3>
          <FieldRow label="奖励等级" for="fb-reward" hint="等级对应星绒币数量由服务端规则裁定；这里只选择等级">
            <select id="fb-reward" v-model="rewardLevel" class="field-base">
              <option v-for="item in REWARD_LEVELS" :key="item.value" :value="item.value">
                {{ item.label }}{{ item.points ? ` · ${item.points} 星绒币` : "" }}
              </option>
            </select>
          </FieldRow>
          <p class="text-sm text-ink-soft">预计发放：{{ pointsWithUnit(rewardPoints) }}</p>
          <FieldRow
            v-if="rewardLevel"
            label="判定依据（内部与审计可见）"
            required
            for="fb-criteria"
            hint="例如：含复现步骤、影响结算金额；发奖必须记录依据"
          >
            <textarea id="fb-criteria" v-model="reviewCriteria" rows="2" class="field-base resize-y" />
          </FieldRow>
          <FieldRow
            v-if="rewardLevel === 'major'"
            label="影响与判据（major 必填）"
            required
            for="fb-impact"
          >
            <textarea id="fb-impact" v-model="reviewImpact" rows="2" class="field-base resize-y" />
          </FieldRow>
          <FieldRow label="审核说明（客户可见）" required for="fb-reason">
            <textarea id="fb-reason" v-model="reviewReason" rows="3" class="field-base resize-y" />
          </FieldRow>
          <AppButton :disabled="pending" @click="requestReviewConfirm">提交审核结果</AppButton>
        </div>
      </div>

      <div class="mt-6 border-t border-line pt-5">
        <h3 class="text-sm font-medium text-ink-soft">去重</h3>
        <p class="mt-1 text-xs text-ink-faint">同一问题只奖励一次；合并后原反馈的进度继续有效。</p>
        <div class="mt-3 grid gap-4 sm:grid-cols-2">
          <FieldRow label="原反馈编号" for="fb-duplicate" hint="例如 FB-2026-0042">
            <input id="fb-duplicate" v-model="duplicateTargetId" type="text" class="field-base font-mono" />
          </FieldRow>
          <FieldRow label="重复判定说明" for="fb-duplicate-reason">
            <input id="fb-duplicate-reason" v-model="duplicateReason" type="text" class="field-base" />
          </FieldRow>
        </div>
        <p v-if="duplicateError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ duplicateError }}
        </p>
        <div class="mt-3">
          <AppButton variant="secondary" :loading="pending" @click="markDuplicate">标记为重复反馈</AppButton>
        </div>
      </div>

      <p v-if="errorText" class="mt-4 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ errorText }}
      </p>
      <div class="mt-4">
        <AppButton variant="quiet" @click="selected = null">关闭面板</AppButton>
      </div>
    </SurfaceCard>

    <SurfaceCard v-if="appealTarget">
      <h2 class="font-display text-lg">
        处理申诉：{{ text(appealTarget.title, text(appealTarget.id)) }}
      </h2>
      <p class="mt-2 text-sm text-ink-muted">
        申诉状态：{{ text(appealTarget.appeal_status) }}。处理结果与理由会同步给客户。
      </p>
      <div class="mt-4 space-y-4">
        <FieldRow label="处理方式" required for="appeal-mode">
          <select id="appeal-mode" v-model="appealMode" class="field-base">
            <option value="APPROVE">受理申诉，重新核定奖励</option>
            <option value="REJECT">驳回申诉，维持原结论</option>
          </select>
        </FieldRow>
        <FieldRow label="处理理由" required for="appeal-reason">
          <textarea id="appeal-reason" v-model="appealReason" rows="3" class="field-base resize-y" />
        </FieldRow>
        <p v-if="appealError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ appealError }}
        </p>
        <div class="flex flex-wrap gap-3">
          <AppButton :variant="appealMode === 'APPROVE' ? 'primary' : 'danger'" @click="requestAppealConfirm">
            提交处理结果
          </AppButton>
          <AppButton variant="quiet" @click="appealTarget = null">取消</AppButton>
        </div>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="reviewConfirmOpen"
      title="提交反馈审核结果并发放奖励"
      :target="selected ? `反馈 ${text(selected.title, text(selected.id))}` : ''"
      :change="rewardLevel ? `奖励等级 → ${rewardLevel}` : '奖励等级 → 不奖励'"
      :amount="pointsWithUnit(rewardPoints)"
      consequence="发放后星绒币会进入客户流水，不能直接改写余额；如需纠正，必须走积分调整申请与审批流程。"
      :confirm-label="rewardLevel ? '确认发放奖励' : '确认不发放奖励'"
      tone="primary"
      :pending="pending"
      @confirm="submitReview"
    />

    <ConfirmDialog
      v-model="appealConfirmOpen"
      title="处理反馈申诉"
      :target="appealTarget ? `反馈 ${text(appealTarget.title, text(appealTarget.id))}` : ''"
      :change="appealMode === 'APPROVE' ? '受理申诉，重新核定奖励' : '驳回申诉，维持原结论'"
      consequence="处理结果与理由会同步给客户，并写入审计；受理申诉后需要重新核定奖励等级。"
      :confirm-label="appealMode === 'APPROVE' ? '确认受理申诉' : '确认驳回申诉'"
      :tone="appealMode === 'APPROVE' ? 'primary' : 'danger'"
      :pending="appealPending"
      @confirm="submitAppeal"
    />
  </div>
</template>
