<script setup lang="ts">
/**
 * 对账：日账单、差异归因、处置、导出审计。
 *
 * 约束（08 文档）：渠道账单与本地流水必须逐日核对；差异必须归因并留下处置记录；
 * 导出属于批量数据动作，需要独立权限与审计（这里要求填写导出理由）。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type ReconciliationRunRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDate } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<ReconciliationRunRecord>>(
  () => adminApi.reconciliationRuns({ status: statusFilter.value || undefined, business_date: dateFilter.value || undefined, limit: 20 }),
  { items: [] },
  "对账批次",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const dateFilter = ref("");

const columns: TableColumn[] = [
  { key: "business_date", label: "账期" },
  { key: "channel", label: "渠道", width: "110px" },
  { key: "channel_total_cents", label: "渠道合计", width: "130px" },
  { key: "local_total_cents", label: "本地合计", width: "130px" },
  { key: "difference_cents", label: "差异", width: "120px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "attribution", label: "归因", hideOnMobile: true },
];

const attributeTarget = ref<ReconciliationRunRecord | null>(null);
const attribution = ref("");
const attributionNote = ref("");
const attributeError = ref("");
const attributePending = ref(false);

const resolveTarget = ref<ReconciliationRunRecord | null>(null);
const resolution = ref("");
const resolutionReason = ref("");
const resolveError = ref("");
const resolvePending = ref(false);

const exportConfirmOpen = ref(false);
const exportReason = ref("");
const exportError = ref("");
const exportPending = ref(false);

const ATTRIBUTIONS = [
  { value: "TIMING", label: "跨日到账（渠道入账时间与本地不同）" },
  { value: "FEE", label: "渠道手续费扣除" },
  { value: "REFUND_LAG", label: "退款到账延迟" },
  { value: "MISSING_NOTIFICATION", label: "通知丢失，需要查单补记" },
  { value: "DUPLICATE", label: "重复记账，需要冲正" },
  { value: "UNKNOWN", label: "暂时无法归因，需要人工核查" },
];

const RESOLUTIONS = [
  { value: "ACCEPTED", label: "确认无误，按渠道账单为准并记录说明" },
  { value: "QUERY_CHANNEL", label: "发起渠道查单后重新对账" },
  { value: "MANUAL_ADJUST", label: "由财务按流程手工调整（仅限手续费类差异）" },
  { value: "ESCALATED", label: "升级给技术核查通知与幂等记录" },
];

const differenceItems = computed(() =>
  state.data.value.items.filter((item) => typeof item.difference_cents === "number" && item.difference_cents !== 0),
);

const differenceTone = (value: unknown): "jade" | "wine" | "neutral" => {
  const amount = typeof value === "number" ? value : null;
  if (amount === null) return "neutral";
  return amount === 0 ? "jade" : "wine";
};

function openAttribute(row: ReconciliationRunRecord): void {
  attributeTarget.value = row;
  attribution.value = String(row.attribution ?? "");
  attributionNote.value = "";
  attributeError.value = "";
}

async function submitAttribution(): Promise<void> {
  const row = attributeTarget.value;
  if (!row) return;
  if (!attribution.value) {
    attributeError.value = "请选择差异归因类型。";
    return;
  }
  if (attributionNote.value.trim().length < 6) {
    attributeError.value = "请填写至少 6 个字的核查说明，例如「渠道 02-11 汇总含跨日到账订单 3 笔」。";
    return;
  }
  attributePending.value = true;
  try {
    await adminApi.attributeDifference(row.id, { attribution: attribution.value, note: attributionNote.value.trim() });
    toast.success("差异归因已记录", "归因会写入审计；之后仍需要选择处置方式。");
    attributeTarget.value = null;
    await state.load();
  } catch (caught) {
    attributeError.value = actionErrorMessage(caught, "提交归因失败，请稍后重试。").message;
  } finally {
    attributePending.value = false;
  }
}

function openResolve(row: ReconciliationRunRecord): void {
  resolveTarget.value = row;
  resolution.value = "";
  resolutionReason.value = "";
  resolveError.value = "";
}

async function submitResolution(): Promise<void> {
  const row = resolveTarget.value;
  if (!row) return;
  if (!resolution.value) {
    resolveError.value = "请选择处置方式。";
    return;
  }
  if (resolutionReason.value.trim().length < 6) {
    resolveError.value = "请填写至少 6 个字的处置说明。";
    return;
  }
  resolvePending.value = true;
  try {
    await adminApi.resolveDifference(row.id, { resolution: resolution.value, reason: resolutionReason.value.trim() });
    toast.success("处置已记录", "差异处置与理由都会进入审计记录；账期状态随服务端结果更新。");
    resolveTarget.value = null;
    await state.load();
  } catch (caught) {
    resolveError.value = actionErrorMessage(caught, "提交处置失败，请稍后重试。").message;
  } finally {
    resolvePending.value = false;
  }
}

async function submitExport(): Promise<void> {
  if (exportReason.value.trim().length < 6) {
    exportError.value = "请填写至少 6 个字的导出理由；批量导出会留下审计记录。";
    return;
  }
  exportPending.value = true;
  try {
    await adminApi.exportReconciliation(
      { business_date: dateFilter.value || undefined },
      { reason: exportReason.value.trim() },
    );
    toast.success("导出请求已提交", "导出文件按最小授权生成；请通过合规渠道传递，不要转发到公共网盘。");
    exportConfirmOpen.value = false;
    exportReason.value = "";
    exportError.value = "";
  } catch (caught) {
    exportError.value = actionErrorMessage(caught, "导出失败，请稍后重试。").message;
    exportConfirmOpen.value = false;
  } finally {
    exportPending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Reconciliation"
      title="对账"
      description="按账期核对渠道账单与本地流水，给差异归因并选择处置方式。所有差异处理都会记录操作人、时间与理由。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
        <AppButton size="sm" variant="danger" @click="exportConfirmOpen = true">导出对账数据</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="对账以渠道账单为准，不改写本地记账"
      description="后台不提供修改历史流水的入口。差异只能通过归因、渠道查单或财务流程处理；任何批量导出都会单独审计。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="账期" for="recon-date" hint="只按单个日期筛选，避免一次拉取过多数据">
        <input id="recon-date" v-model="dateFilter" type="date" class="field-base" />
      </FieldRow>
      <FieldRow label="状态" for="recon-status">
        <select id="recon-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="MATCHED">一致</option>
          <option value="DIFFERENCE">存在差异</option>
          <option value="RESOLVED">已处置</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="dateFilter = ''; statusFilter = ''; state.load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="没有对账批次"
      empty-description="对账批次由定时任务生成；如果长期为空，请到「任务与死信」检查对账任务。"
      @retry="state.load()"
    >
      <template #cell-business_date="{ row }">
        <span class="tabular-nums">{{ formatDate(row.business_date) }}</span>
      </template>
      <template #cell-channel_total_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.channel_total_cents as number) }}</span>
      </template>
      <template #cell-local_total_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.local_total_cents as number) }}</span>
      </template>
      <template #cell-difference_cents="{ row }">
        <StatusBadge
          :tone="differenceTone(row.difference_cents)"
          :label="row.difference_cents === 0 || row.difference_cents === null ? '无差异' : formatMoneyExact(row.difference_cents as number)"
        />
      </template>
      <template #cell-attribution="{ row }">
        <span class="text-xs">{{ text(row.attribution, "未归因") }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openAttribute(row)">记录归因</AppButton>
        <AppButton
          size="sm"
          :disabled="typeof row.difference_cents === 'number' && row.difference_cents === 0"
          :disabled-reason="typeof row.difference_cents === 'number' && row.difference_cents === 0 ? '该账期没有差异，不需要处置' : ''"
          @click="openResolve(row)"
        >
          记录处置
        </AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
      <SurfaceCard v-if="attributeTarget">
        <h2 class="font-display text-lg">差异归因</h2>
        <FieldList
          class="mt-3"
          :items="[
            { label: '账期', value: formatDate(attributeTarget.business_date) },
            { label: '渠道', value: text(attributeTarget.channel) },
            { label: '渠道合计', value: formatMoneyExact(attributeTarget.channel_total_cents) },
            { label: '本地合计', value: formatMoneyExact(attributeTarget.local_total_cents) },
            { label: '差异', value: formatMoneyExact(attributeTarget.difference_cents) },
          ]"
        />
        <div class="mt-4 space-y-4">
          <FieldRow label="归因类型" required for="recon-attribution">
            <select id="recon-attribution" v-model="attribution" class="field-base">
              <option value="">请选择</option>
              <option v-for="item in ATTRIBUTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </FieldRow>
          <FieldRow label="核查说明" required for="recon-note" hint="写清楚你核对了什么、结论是什么">
            <textarea id="recon-note" v-model="attributionNote" rows="3" class="field-base resize-y" />
          </FieldRow>
          <p v-if="attributeError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ attributeError }}
          </p>
          <div class="flex flex-wrap gap-3">
            <AppButton :loading="attributePending" @click="submitAttribution">提交归因</AppButton>
            <AppButton variant="quiet" @click="attributeTarget = null">取消</AppButton>
          </div>
        </div>
      </SurfaceCard>

      <SurfaceCard v-if="resolveTarget">
        <h2 class="font-display text-lg">差异处置</h2>
        <p class="mt-2 text-sm text-ink-muted">
          账期 {{ formatDate(resolveTarget.business_date) }}，差异
          {{ formatMoneyExact(resolveTarget.difference_cents) }}。
        </p>
        <div class="mt-4 space-y-4">
          <FieldRow label="处置方式" required for="recon-resolution">
            <select id="recon-resolution" v-model="resolution" class="field-base">
              <option value="">请选择</option>
              <option v-for="item in RESOLUTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
            </select>
          </FieldRow>
          <FieldRow label="处置说明" required for="recon-resolution-reason">
            <textarea id="recon-resolution-reason" v-model="resolutionReason" rows="3" class="field-base resize-y" />
          </FieldRow>
          <p v-if="resolveError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            {{ resolveError }}
          </p>
          <div class="flex flex-wrap gap-3">
            <AppButton :loading="resolvePending" @click="submitResolution">提交处置</AppButton>
            <AppButton variant="quiet" @click="resolveTarget = null">取消</AppButton>
          </div>
        </div>
      </SurfaceCard>

      <SurfaceCard v-if="!attributeTarget && !resolveTarget">
        <h2 class="font-display text-lg">待处理差异</h2>
        <p class="mt-1 text-xs text-ink-faint">当前筛选结果中差异不为 0 的账期。</p>
        <ul v-if="differenceItems.length" class="mt-4 space-y-2 text-sm">
          <li v-for="item in differenceItems" :key="item.id" class="rounded-sm border border-line px-4 py-3">
            <div class="flex items-center justify-between gap-3">
              <span class="tabular-nums">{{ formatDate(item.business_date) }}</span>
              <StatusBadge tone="wine" :label="formatMoneyExact(item.difference_cents)" />
            </div>
            <p class="mt-1 text-xs text-ink-faint">
              归因：{{ text(item.attribution, "未归因") }} · 处置：{{ text(item.resolution, "未处置") }}
            </p>
          </li>
        </ul>
        <p v-else class="mt-4 text-sm text-ink-muted">当前页没有差异账期。</p>
      </SurfaceCard>

      <SurfaceCard>
        <h2 class="font-display text-lg">导出说明</h2>
        <ul class="mt-3 space-y-2 text-sm text-ink-muted">
          <li>· 批量导出需要独立权限与审计：每次导出都会记录操作人、时间与理由。</li>
          <li>· 导出文件不含手机号明文、地址明文与支付报文；CSV 已做公式注入防护。</li>
          <li>· 导出结果请通过合规渠道传递，不要转发到公共网盘或群聊。</li>
        </ul>
      </SurfaceCard>
    </div>

    <ConfirmDialog
      v-model="exportConfirmOpen"
      title="导出对账数据"
      :target="dateFilter ? `账期 ${dateFilter} 的对账数据` : '当前筛选条件下的对账数据'"
      change="生成导出文件并记录审计；本页数据不会被修改。"
      consequence="批量导出属于高敏动作：导出内容会被审计，包含金额与订单号，请勿转发到公共渠道。"
      confirm-label="确认导出"
      tone="danger"
      :pending="exportPending"
      reason-label="导出理由（会写入审计记录）"
      @confirm="submitExport"
    />

    <p v-if="exportError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
      {{ exportError }}
    </p>
  </div>
</template>
