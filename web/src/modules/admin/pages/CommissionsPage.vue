<script setup lang="ts">
/**
 * 制作单：节点更新、私密相册、确认记录、变更单。
 *
 * 约束（07/10 文档）：私密相册只登记素材编号，后台不显示签名下载地址；
 * 量体与地址等隐私资料只显示是否已提交，不显示内容。
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
  type CommissionContract,
  type CommissionProjectRecord,
  type Dict,
  type FailureView,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<CommissionProjectRecord>>(
  () => adminApi.commissionProjects({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "制作单列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");
const selected = ref<CommissionProjectRecord | null>(null);
const confirmations = ref<Dict[]>([]);
const changeRequests = ref<Dict[]>([]);
const detailError = ref<FailureView | null>(null);
const detailLoading = ref(false);

const stageForm = ref({ stage_code: "", note: "", requires_confirmation: true });
const stageError = ref("");
const stagePending = ref(false);

/** 制作节点来自服务端登记的流程（07 文档 PROJECT_FLOW），后台不能自造节点名。 */
const STAGE_OPTIONS = [
  { value: "MEASUREMENT_PENDING", label: "待量体" },
  { value: "DESIGN_CONFIRMING", label: "设定确认中" },
  { value: "MATERIALS_READY", label: "材料已备齐" },
  { value: "PRODUCING", label: "制作中" },
  { value: "CUSTOMER_REVIEW", label: "客户验收中" },
  { value: "READY_TO_SHIP", label: "待发货" },
  { value: "SHIPPED", label: "已发货" },
  { value: "COMPLETED", label: "已完成" },
];

const albumForm = ref({ label: "", asset_ids: "" });
const albumError = ref("");
const albumPending = ref(false);

const changeTarget = ref<Dict | null>(null);
const changeMode = ref<"APPROVE" | "REJECT">("APPROVE");
const changeError = ref("");
const changePending = ref(false);
const changeConfirmOpen = ref(false);

/* ---------------- 合同电子签（C07/C19） ----------------
 * 后台只能"重新发送"或"作废"，**不能**代替客户或监护人签署，也不能把未签改成已签。
 */
const projectDetail = ref<CommissionProjectRecord | null>(null);
const contract = computed<CommissionContract | null>(() => projectDetail.value?.contract ?? null);
const contractPending = ref(false);
const contractError = ref("");
const voidReason = ref("");
const voidConfirmOpen = ref(false);

const CONTRACT_STATUS_LABEL: Record<string, string> = {
  NOT_REQUESTED: "尚未发起",
  PENDING_SEND: "待发送",
  SENT: "待签署",
  PARTIALLY_SIGNED: "部分签署完成",
  SIGNED: "已签署完成",
  DECLINED: "已拒签",
  EXPIRED: "已过期",
  VOID: "已作废",
};

const CONTRACT_ROLE_LABEL: Record<string, string> = {
  customer: "委托方",
  guardian: "监护人",
  staff: "工作室",
};

const contractLabel = computed(() =>
  contract.value ? (CONTRACT_STATUS_LABEL[String(contract.value.status ?? "")] ?? text(contract.value.status)) : "无签署任务",
);

const contractTone = computed(() => {
  switch (String(contract.value?.status ?? "")) {
    case "SIGNED":
      return "jade" as const;
    case "DECLINED":
    case "EXPIRED":
      return "wine" as const;
    case "SENT":
    case "PARTIALLY_SIGNED":
      return "amber" as const;
    default:
      return "neutral" as const;
  }
});

const contractSignerSummary = computed(() => {
  const signers = contract.value?.signers ?? [];
  return signers
    .map((signer) => {
      const role = CONTRACT_ROLE_LABEL[String(signer.role ?? "")] ?? text(signer.role);
      const state =
        signer.status === "SIGNED" ? "已签署" : signer.status === "DECLINED" ? "已拒签" : "待签署";
      return `${role} ${text(signer.name)}（${state}）`;
    })
    .join("；");
});

/** 作废需要理由；已签署的合同不允许作废（服务端同样拒绝）。 */
const contractBlockReason = computed(() => {
  if (!contract.value) return "该制作单没有签署任务";
  if (String(contract.value.status ?? "") === "SIGNED") return "已签署的合同不能作废";
  if (String(contract.value.status ?? "") === "VOID") return "该签署任务已作废";
  return "";
});

async function resendContract(): Promise<void> {
  const row = selected.value;
  if (!row || contractPending.value) return;
  contractPending.value = true;
  contractError.value = "";
  try {
    const updated = (await adminApi.sendContract(row.id)) as CommissionContract;
    if (projectDetail.value) projectDetail.value = { ...projectDetail.value, contract: updated };
    const sent = String(updated.status ?? "") !== "PENDING_SEND";
    if (sent) {
      toast.success("合同已发送", "签署链接已通过邮件发给每位签署人。");
    } else {
      toast.error("合同未发送", text(updated.failure_reason, "电子签渠道不可用，请检查配置。"));
    }
  } catch (caught) {
    contractError.value = actionErrorMessage(caught, "重新发送合同失败，请稍后重试。").message;
  } finally {
    contractPending.value = false;
  }
}

function openVoidContract(): void {
  voidReason.value = "";
  contractError.value = "";
  voidConfirmOpen.value = true;
}

async function submitVoidContract(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (voidReason.value.trim().length < 5) {
    contractError.value = "请填写至少 5 个字的作废理由（会写入审计记录）。";
    return;
  }
  contractPending.value = true;
  try {
    const updated = (await adminApi.voidContract(row.id, { reason: voidReason.value.trim() })) as CommissionContract;
    if (projectDetail.value) projectDetail.value = { ...projectDetail.value, contract: updated };
    voidConfirmOpen.value = false;
    toast.success("签署任务已作废", "客户侧的合同状态会显示为已作废，可重新发起签署。");
  } catch (caught) {
    contractError.value = actionErrorMessage(caught, "作废签署任务失败，请稍后重试。").message;
  } finally {
    contractPending.value = false;
  }
}

const columns: TableColumn[] = [
  { key: "code", label: "制作单号" },
  { key: "series", label: "系列 / 规格" },
  { key: "stage", label: "当前节点", width: "140px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "assignee", label: "负责装师", width: "120px", hideOnMobile: true },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
];

const changeColumns: TableColumn[] = [
  { key: "title", label: "变更内容" },
  { key: "status", label: "状态", width: "120px" },
  { key: "created_at", label: "提交时间", width: "170px", hideOnMobile: true },
];

const confirmColumns: TableColumn[] = [
  { key: "stage", label: "阶段" },
  { key: "status", label: "确认结果", width: "130px" },
  { key: "confirmed_at", label: "确认时间", width: "170px", hideOnMobile: true },
];

const changeItems = computed(() =>
  changeRequests.value.map((item) => ({
    id: String(item.id ?? ""),
    title: text(item.title ?? item.summary, "未命名变更"),
    status: text(item.status, "待处理"),
    created_at: item.created_at,
  })),
);

const confirmItems = computed(() =>
  confirmations.value.map((item) => ({
    id: String(item.id ?? item.stage ?? Math.random()),
    stage: text(item.stage ?? item.label, "阶段"),
    status: text(item.status ?? item.result, "已确认"),
    confirmed_at: item.confirmed_at ?? item.created_at,
  })),
);

async function selectProject(row: CommissionProjectRecord): Promise<void> {
  selected.value = row;
  detailLoading.value = true;
  detailError.value = null;
  projectDetail.value = null;
  contractError.value = "";
  const result = await collect<{
    detail: CommissionProjectRecord;
    confirmations: AdminList<Dict>;
    changes: AdminList<Dict>;
  }>({
    detail: () => adminApi.commissionDetail(row.id),
    confirmations: () => adminApi.confirmRecords(row.id),
    changes: () => adminApi.changeRequests(row.id),
  });
  projectDetail.value = result.ok.detail ?? null;
  confirmations.value = result.ok.confirmations?.items ?? [];
  changeRequests.value = result.ok.changes?.items ?? [];
  const failure = result.failed[0];
  detailError.value = failure
    ? describeFailure(
        failure.error,
        failure.key === "changes" ? "变更单列表" : failure.key === "detail" ? "制作单详情" : "确认记录",
      )
    : null;
  detailLoading.value = false;
}

async function submitStage(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (!stageForm.value.stage_code) {
    stageError.value = "请选择制作节点。";
    return;
  }
  if (stageForm.value.note.trim().length < 6) {
    stageError.value = "请填写至少 6 个字的节点说明；客户会在制作进度里看到这条更新。";
    return;
  }
  stagePending.value = true;
  try {
    await adminApi.createProductionStage(row.id, {
      stage_code: stageForm.value.stage_code,
      note: stageForm.value.note.trim(),
      requires_confirmation: stageForm.value.requires_confirmation,
    });
    toast.success("节点已记录", "客户可以在「制作进度」里看到这次更新。");
    stageForm.value = { stage_code: "", note: "", requires_confirmation: true };
    stageError.value = "";
    await state.load();
  } catch (caught) {
    stageError.value = actionErrorMessage(caught, "节点更新失败，请稍后重试。").message;
  } finally {
    stagePending.value = false;
  }
}

async function submitAlbum(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  const ids = albumForm.value.asset_ids
    .split(/[\s,，]+/)
    .map((item) => item.trim())
    .filter(Boolean);
  if (!albumForm.value.label.trim() || !ids.length) {
    albumError.value = "请填写相册名称，并至少提供一个已扫描完成的素材编号。";
    return;
  }
  albumPending.value = true;
  try {
    await adminApi.uploadPrivateAlbum(row.id, { label: albumForm.value.label.trim(), asset_ids: ids });
    toast.success("相册已更新", "私密相册只对客户本人与分配的装师开放，下载链接是短期签名地址。");
    albumForm.value = { label: "", asset_ids: "" };
    albumError.value = "";
  } catch (caught) {
    albumError.value = actionErrorMessage(caught, "相册更新失败，请稍后重试。").message;
  } finally {
    albumPending.value = false;
  }
}

function openChange(row: Dict, mode: "APPROVE" | "REJECT"): void {
  changeTarget.value = row;
  changeMode.value = mode;
  changeError.value = "";
  changeConfirmOpen.value = true;
}

async function submitChange(payload: { reason: string }): Promise<void> {
  const row = changeTarget.value;
  if (!row) return;
  const changeId = String(row.id ?? "");
  if (!changeId) {
    changeError.value = "这条变更单缺少编号，无法提交；请刷新后重试。";
    changeConfirmOpen.value = false;
    return;
  }
  changePending.value = true;
  try {
    await adminApi.decideChangeRequest(changeId, { decision: changeMode.value, reason: payload.reason });
    toast.success(changeMode.value === "APPROVE" ? "变更已通过" : "变更已拒绝", "处理结果已记录在变更单里。");
    changeConfirmOpen.value = false;
    changeTarget.value = null;
    if (selected.value) await selectProject(selected.value);
  } catch (caught) {
    changeError.value = actionErrorMessage(caught, "处理变更单失败，请稍后重试。").message;
    changeConfirmOpen.value = false;
  } finally {
    changePending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Production"
      title="制作单"
      description="更新制作节点、维护私密相册、查看阶段确认与变更单。客户在账户里看到的进度就是这里的记录。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="私密资料只登记编号，不显示内容"
      description="量体数据、收货地址与私密相册都按对象逐次鉴权，后台只显示「是否已提交」与素材编号，不显示明文与签名下载地址。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="制作单号或系列" for="commission-q">
        <input id="commission-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="状态" for="commission-status">
        <select id="commission-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="IN_PROGRESS">制作中</option>
          <option value="WAITING_CONFIRM">待客户确认</option>
          <option value="COMPLETED">已完成</option>
          <option value="PAUSED">已暂停</option>
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
      empty-title="没有制作中的单子"
      empty-description="客户接受报价并付款后，制作单会出现在这里。"
      @retry="state.load()"
    >
      <template #cell-code="{ row }">
        <span class="font-mono text-xs">{{ text(row.code ?? row.id) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="selectProject(row)">打开制作单</AppButton>
      </template>
    </DataTable>

    <div v-if="selected" class="grid gap-6 xl:grid-cols-[1.4fr_1fr]">
      <div class="space-y-6">
        <SurfaceCard>
          <div class="flex flex-wrap items-start justify-between gap-3">
            <SectionHeading level="2" :title="`制作单 ${text(selected.code ?? selected.id)}`" description="节点更新会立刻出现在客户的制作进度里，请写清楚这一阶段完成了什么。" />
            <StatusBadge :tone="String(selected.status ?? '') === 'COMPLETED' ? 'jade' : 'amber'" :label="text(selected.status, '制作中')" />
          </div>
          <FieldList
            class="mt-4"
            :items="[
              { label: '系列 / 规格', value: text(selected.series) },
              { label: '当前节点', value: text(selected.stage) },
              { label: '负责装师', value: text(selected.assignee) },
              { label: '最近更新', value: formatDateTime(selected.updated_at) },
            ]"
          />

          <!-- 合同电子签（C07/C19）：未签完不能进入制作；渠道未接入时如实标注 -->
          <div class="mt-5 rounded-md border border-line bg-surface-muted p-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <div class="flex flex-wrap items-center gap-2">
                <p class="text-sm font-medium text-ink">合同签署</p>
                <StatusBadge :tone="contractTone" :label="contractLabel" />
                <StatusBadge
                  v-if="contract?.guardian_required"
                  tone="neutral"
                  label="需监护人签署"
                />
              </div>
              <div class="flex flex-wrap gap-2">
                <AppButton
                  v-if="contract?.id && !['SIGNED', 'VOID', 'EXPIRED'].includes(String(contract.status))"
                  size="sm"
                  variant="secondary"
                  :loading="contractPending"
                  @click="resendContract()"
                >
                  重新发送合同
                </AppButton>
                <AppButton
                  v-if="contract?.id && contract.status !== 'SIGNED' && contract.status !== 'VOID'"
                  size="sm"
                  variant="quiet"
                  :disabled="!!contractBlockReason"
                  :disabled-reason="contractBlockReason"
                  @click="openVoidContract()"
                >
                  作废签署任务
                </AppButton>
              </div>
            </div>

            <p v-if="!contract" class="mt-3 text-xs text-ink-muted">
              该制作单还没有合同签署任务（未接入电子签渠道或报价尚未被接受）。
            </p>
            <template v-else>
              <p v-if="!contract.provider_configured" class="mt-3 text-xs text-wine" role="status">
                尚未接入电子签渠道（当前 {{ text(contract.esign_provider, "disabled") }}）：
                合同无法在线签署，制作开工前必须解决；这里是如实状态，不是已完成。
              </p>
              <p v-else-if="contract.failure_reason" class="mt-3 text-xs text-wine" role="status">
                发送/签署存在问题：{{ contract.failure_reason }}
              </p>
              <dl class="mt-3 space-y-1 text-xs text-ink-muted">
                <div v-if="contract.signers?.length" class="flex flex-wrap gap-2">
                  <dt class="text-ink-faint">签署人</dt>
                  <dd>
                    {{ contractSignerSummary }}
                  </dd>
                </div>
                <div v-if="contract.awaiting_roles?.length" class="flex flex-wrap gap-2">
                  <dt class="text-ink-faint">待签署</dt>
                  <dd>{{ contract.awaiting_roles.join("、") }}</dd>
                </div>
                <div v-if="contract.completed_at" class="flex flex-wrap gap-2">
                  <dt class="text-ink-faint">签署完成</dt>
                  <dd class="tabular-nums">{{ formatDateTime(String(contract.completed_at)) }}</dd>
                </div>
              </dl>
              <FieldRow
                v-if="contract.id && contract.status !== 'SIGNED' && contract.status !== 'VOID'"
                class="mt-3"
                label="作废理由"
                for="void-contract-reason"
                hint="只有在需要作废签署任务时填写；会写入审计记录（至少 5 个字）"
              >
                <input id="void-contract-reason" v-model="voidReason" type="text" class="field-base" />
              </FieldRow>
              <p v-if="contractError" class="mt-3 text-xs text-wine" role="alert">{{ contractError }}</p>
            </template>
          </div>

          <div class="mt-6 space-y-4 border-t border-line pt-5">
            <h3 class="text-sm font-medium text-ink-soft">新增节点更新</h3>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="制作节点" required for="stage-code" hint="节点来自已登记的流程，不能自造名称">
                <select id="stage-code" v-model="stageForm.stage_code" class="field-base">
                  <option value="">请选择节点</option>
                  <option v-for="item in STAGE_OPTIONS" :key="item.value" :value="item.value">
                    {{ item.label }}
                  </option>
                </select>
              </FieldRow>
              <FieldRow label="节点说明" required for="stage-note" hint="客户可见，请勿写入手机号、地址等隐私">
                <input id="stage-note" v-model="stageForm.note" type="text" class="field-base" />
              </FieldRow>
            </div>
            <label class="flex items-center gap-2 text-sm text-ink-soft">
              <input v-model="stageForm.requires_confirmation" type="checkbox" />
              需要客户确认这个节点（确认前不会推进到下一阶段）
            </label>
            <p v-if="stageError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ stageError }}
            </p>
            <AppButton :loading="stagePending" @click="submitStage">记录节点更新</AppButton>
          </div>
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading level="2" title="私密相册" description="只接受已完成扫描的素材编号；相册对客户本人与分配的装师可见。" />
          <div class="mt-4 space-y-4">
            <FieldRow label="相册名称" required for="album-label" hint="例如：头部进度（第三轮）">
              <input id="album-label" v-model="albumForm.label" type="text" class="field-base" />
            </FieldRow>
            <FieldRow label="素材编号" required for="album-ids" hint="多个编号用空格或逗号分隔；编号来自素材库">
              <textarea id="album-ids" v-model="albumForm.asset_ids" rows="2" class="field-base font-mono text-xs" />
            </FieldRow>
            <p v-if="albumError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ albumError }}
            </p>
            <p class="text-xs text-ink-faint">
              未完成扫描、或不属于本制作单的素材会被服务端拒绝；后台不生成也不显示签名下载地址。
            </p>
            <AppButton :loading="albumPending" @click="submitAlbum">更新私密相册</AppButton>
          </div>
        </SurfaceCard>
      </div>

      <div class="space-y-6">
        <ModuleCard title="阶段确认记录" :status="detailLoading ? '加载中' : '实时'">
          <p v-if="detailError" class="mb-3 text-sm text-ink-muted">
            {{ detailError.description }} {{ detailError.retryHint }}
          </p>
          <DataTable
            :columns="confirmColumns"
            :rows="confirmItems"
            :loading="detailLoading"
            dense
            empty-title="还没有阶段确认"
            empty-description="客户在账户里确认阶段后，记录会出现在这里。"
            @retry="selected && selectProject(selected)"
          />
        </ModuleCard>

        <ModuleCard title="变更单" :status="detailLoading ? '加载中' : '实时'">
          <DataTable
            :columns="changeColumns"
            :rows="changeItems"
            :loading="detailLoading"
            dense
            empty-title="没有变更单"
            empty-description="客户提出的修改请求会出现在这里，需要人工处理并写理由。"
            @retry="selected && selectProject(selected)"
          >
            <template #actions="{ row }">
              <AppButton size="sm" @click="openChange(row as unknown as Dict, 'APPROVE')">通过</AppButton>
              <AppButton size="sm" variant="danger" @click="openChange(row as unknown as Dict, 'REJECT')">拒绝</AppButton>
            </template>          </DataTable>
        </ModuleCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="changeConfirmOpen"
      :title="changeMode === 'APPROVE' ? '通过变更单并计入制作' : '拒绝变更单'"
      :target="changeTarget ? `变更单 ${text(changeTarget.id)}：${text(changeTarget.title ?? changeTarget.summary, '未命名变更')}` : ''"
      :change="changeMode === 'APPROVE' ? '状态 → 已通过；变更进入制作流程。' : '状态 → 已拒绝；制作按原方案继续。'"
      :consequence="
        changeMode === 'APPROVE'
          ? '通过后可能影响交期与费用；如果涉及增项，需要另外发布新的报价或补充协议。'
          : '拒绝后客户会收到理由，制作按原确认方案继续。'
      "
      :confirm-label="changeMode === 'APPROVE' ? '确认通过变更' : '确认拒绝变更'"
      :tone="changeMode === 'APPROVE' ? 'primary' : 'danger'"
      :pending="changePending"
      @confirm="submitChange"
    />

    <ConfirmDialog
      v-model="voidConfirmOpen"
      title="作废合同签署任务"
      :target="`制作单 ${text(selected?.code ?? selected?.id)}`"
      change="签署任务 → 已作废；已发出的签署链接不再有效。"
      consequence="作废后客户不能再通过原链接签署，需要重新发起签署任务；已签署的合同不允许作废（服务端同样拒绝）。"
      :amount="voidReason"
      confirm-label="确认作废签署任务"
      tone="danger"
      :pending="contractPending"
      :disabled-reason="contractBlockReason || (voidReason.trim().length < 5 ? '请先填写至少 5 个字的作废理由' : '')"
      @confirm="submitVoidContract"
    />
  </div>
</template>
