<script setup lang="ts">
/**
 * 询价与报价：分派、补件、发布报价版本、拒绝理由。
 *
 * 约束（07 文档）：询价是需求记录，报价是有期限的商业提案，订单才承载收款。
 * 发布报价必须给出金额、币种、有效期与条款版本；拒绝必须写理由并通知客户。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type QuoteRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, SurfaceCard } from "@/shared/ui/base";
import { formatCountdown, formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<QuoteRecord>>(
  () => adminApi.quotes({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "询价列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");
const selected = ref<QuoteRecord | null>(null);
const panelMode = ref<"assign" | "supplement" | "quote" | "reject">("assign");
const errorText = ref("");
const pending = ref(false);

const assignee = ref("");
const supplementReason = ref("");
const quoteForm = ref({ amount_yuan: 0, valid_days: 14, terms_version: "commission-1", note: "" });
const rejectReason = ref("");

const columns: TableColumn[] = [
  { key: "character_name", label: "角色 / 需求" },
  { key: "status", label: "状态", width: "130px" },
  { key: "assignee", label: "负责人", width: "110px", hideOnMobile: true },
  { key: "quote_version", label: "报价版本", width: "100px" },
  { key: "amount_cents", label: "报价金额", width: "130px" },
  { key: "valid_until", label: "有效期", width: "170px", hideOnMobile: true },
];

const confirmOpen = ref(false);

const quoteAmountCents = computed(() => Math.round(quoteForm.value.amount_yuan * 100));

function openPanel(row: QuoteRecord, mode: typeof panelMode.value): void {
  selected.value = row;
  panelMode.value = mode;
  errorText.value = "";
  assignee.value = String(row.assignee ?? "");
  supplementReason.value = "";
  rejectReason.value = "";
  quoteForm.value = { amount_yuan: 0, valid_days: 14, terms_version: "commission-1", note: "" };
}

async function runAssign(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (!assignee.value.trim()) {
    errorText.value = "请填写负责本次沟通的员工登录名。";
    return;
  }
  pending.value = true;
  try {
    await adminApi.assignQuote(row.id, { assignee: assignee.value.trim() });
    toast.success("已分派", `询价已分派给 ${assignee.value.trim()}；客户侧不会看到内部负责人姓名。`);
    selected.value = null;
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "分派失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

async function runSupplement(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (supplementReason.value.trim().length < 6) {
    errorText.value = "请写清楚需要客户补充什么，例如「缺少尾巴长度与配色参考图」。";
    return;
  }
  if (!row.version) {
    errorText.value = "缺少询价版本号，请刷新列表后重试。";
    return;
  }
  pending.value = true;
  try {
    await adminApi.requestSupplement(row.id, {
      reason: supplementReason.value.trim(),
      version: row.version,
    });
    toast.success("已发出补件请求", "客户会收到补件通知；补齐后询价回到处理中。");
    selected.value = null;
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "补件请求失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

function requestQuoteConfirm(): void {
  if (!Number.isInteger(quoteAmountCents.value) || quoteAmountCents.value <= 0) {
    errorText.value = "报价金额必须大于 0。";
    return;
  }
  if (quoteForm.value.valid_days < 1 || quoteForm.value.valid_days > 60) {
    errorText.value = "报价有效期建议在 1–60 天之间，请与经营规则保持一致。";
    return;
  }
  errorText.value = "";
  confirmOpen.value = true;
}

async function submitQuote(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  pending.value = true;
  try {
    const result = await adminApi.publishQuote(row.id, {
      amount_cents: quoteAmountCents.value,
      currency: "CNY",
      valid_days: quoteForm.value.valid_days,
      terms_version: quoteForm.value.terms_version,
      note: quoteForm.value.note,
    });
    toast.success("报价版本已发布", `版本 ${text(result.quote_version, "新")} 已发出，客户需在有效期内确认。`);
    confirmOpen.value = false;
    selected.value = null;
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "发布报价失败，请稍后重试。").message;
    confirmOpen.value = false;
  } finally {
    pending.value = false;
  }
}

async function runReject(): Promise<void> {
  const row = selected.value;
  if (!row) return;
  if (rejectReason.value.trim().length < 6) {
    errorText.value = "拒绝理由会同步给客户，请写清楚具体原因。";
    return;
  }
  pending.value = true;
  try {
    await adminApi.rejectQuote(row.id, { reason: rejectReason.value.trim() });
    toast.success("已拒绝", "拒绝理由已记录，客户可以在账户里看到这条说明。");
    selected.value = null;
    await state.load();
  } catch (caught) {
    errorText.value = actionErrorMessage(caught, "拒绝失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Quotes"
      title="询价与报价"
      description="分派沟通、发出补件请求、发布有期限的报价版本，或写清理由拒绝。报价是商业提案，客户接受后才会生成订单。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="报价有效期与条款版本由服务端校验"
      description="报价发出后客户需要在有效期内接受；过期报价不能再被接受，需要重新发布版本。金额一律以整数分提交。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="state.load()">
      <FieldRow label="角色名或需求关键词" for="quote-q">
        <input id="quote-q" v-model="search" type="search" class="field-base" />
      </FieldRow>
      <FieldRow label="状态" for="quote-status">
        <select id="quote-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="OPEN">待处理</option>
          <option value="SUPPLEMENT_REQUIRED">已请求补件</option>
          <option value="QUOTED">已报价</option>
          <option value="ACCEPTED">客户已接受</option>
          <option value="REJECTED">已拒绝</option>
          <option value="EXPIRED">已过期</option>
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
      empty-title="没有待处理的询价"
      empty-description="当前筛选条件下没有记录。可以换个状态看看，或稍后刷新。"
      @retry="state.load()"
    >
      <template #cell-character_name="{ row }">
        <span class="font-medium">{{ text(row.character_name, "未命名需求") }}</span>
        <p class="mt-1 font-mono text-xs text-ink-faint">{{ text(row.id) }}</p>
      </template>
      <template #cell-amount_cents="{ row }">
        <span class="tabular-nums">{{ row.amount_cents ? formatMoneyExact(row.amount_cents as number) : "未报价" }}</span>
      </template>
      <template #cell-valid_until="{ row }">
        <span class="tabular-nums text-xs">
          {{ row.valid_until ? `${formatDateTime(row.valid_until)} · ${formatCountdown(row.valid_until)}` : "—" }}
        </span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" @click="openPanel(row, 'assign')">分派</AppButton>
        <AppButton size="sm" variant="quiet" @click="openPanel(row, 'supplement')">请求补件</AppButton>
        <AppButton size="sm" @click="openPanel(row, 'quote')">发布报价</AppButton>
        <AppButton size="sm" variant="danger" @click="openPanel(row, 'reject')">拒绝</AppButton>
      </template>
    </DataTable>

    <SurfaceCard v-if="selected">
      <SectionHeading
        level="2"
        :title="`询价 ${text(selected.character_name, text(selected.id))}`"
        description="下面按当前动作显示需要填写的内容；客户侧只会看到补件要求、报价与拒绝理由。"
      />
      <FieldList
        class="mt-4"
        :items="[
          { label: '当前状态', value: text(selected.status) },
          { label: '负责人', value: text(selected.assignee, '未分派') },
          { label: '系列', value: text(selected.series) },
          { label: '最近更新', value: formatDateTime(selected.updated_at) },
          { label: '报价版本', value: text(selected.quote_version) },
          { label: '已发布金额', value: selected.amount_cents ? formatMoneyExact(selected.amount_cents) : '未报价' },
        ]"
      />

      <div v-if="panelMode === 'assign'" class="mt-5 space-y-4">
        <FieldRow label="负责人登录名" required for="quote-assignee" hint="只写员工登录名，界面与通知都不会显示手机号">
          <input id="quote-assignee" v-model="assignee" type="text" class="field-base font-mono" />
        </FieldRow>
        <AppButton :loading="pending" @click="runAssign">确认分派</AppButton>
      </div>

      <div v-else-if="panelMode === 'supplement'" class="mt-5 space-y-4">
        <FieldRow label="需要客户补充的内容" required for="quote-supplement" hint="例如：缺少尾巴长度、配色参考图与预算区间">
          <textarea id="quote-supplement" v-model="supplementReason" rows="3" class="field-base resize-y" />
        </FieldRow>
        <AppButton :loading="pending" @click="runSupplement">发出补件请求</AppButton>
      </div>

      <div v-else-if="panelMode === 'quote'" class="mt-5 space-y-4">
        <div class="grid gap-4 sm:grid-cols-3">
          <FieldRow label="报价金额（元）" required for="quote-amount" hint="提交时按整数分换算，避免小数误差">
            <input id="quote-amount" v-model.number="quoteForm.amount_yuan" type="number" min="0" step="0.01" class="field-base tabular-nums" />
          </FieldRow>
          <FieldRow label="有效期（天）" required for="quote-days">
            <input id="quote-days" v-model.number="quoteForm.valid_days" type="number" min="1" max="60" step="1" class="field-base tabular-nums" />
          </FieldRow>
          <FieldRow label="条款版本" required for="quote-terms" hint="与规则页展示的版本号保持一致">
            <input id="quote-terms" v-model="quoteForm.terms_version" type="text" class="field-base font-mono" />
          </FieldRow>
        </div>
        <FieldRow label="报价说明" for="quote-note" hint="包含项与不含项分开写，不承诺未核准的交期">
          <textarea id="quote-note" v-model="quoteForm.note" rows="3" class="field-base resize-y" />
        </FieldRow>
        <p class="text-sm text-ink-soft">
          本次将发布：{{ formatMoneyExact(quoteAmountCents) }} · 有效期 {{ quoteForm.valid_days }} 天 · 条款
          {{ quoteForm.terms_version }}
        </p>
        <AppButton @click="requestQuoteConfirm">提交报价版本</AppButton>
      </div>

      <div v-else class="mt-5 space-y-4">
        <FieldRow label="拒绝理由（会同步给客户）" required for="quote-reject">
          <textarea id="quote-reject" v-model="rejectReason" rows="3" class="field-base resize-y" />
        </FieldRow>
        <AppButton variant="danger" :loading="pending" @click="runReject">确认拒绝</AppButton>
      </div>

      <p v-if="errorText" class="mt-4 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ errorText }}
      </p>

      <div class="mt-4">
        <AppButton variant="quiet" @click="selected = null">关闭面板</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="confirmOpen"
      title="发布报价版本"
      :target="selected ? `询价 ${text(selected.character_name, text(selected.id))}` : ''"
      :change="`新增报价版本，有效期 ${quoteForm.valid_days} 天（预计版本 ${Number(selected?.quote_version ?? 0) + 1}）`"
      :amount="`${formatMoneyExact(quoteAmountCents)} · 条款 ${quoteForm.terms_version}`"
      consequence="报价发出后客户可以在有效期内接受并生成订单；接受后金额与条款即成为订单依据。发布错误只能通过重新发布新版本修正，旧版本不会被覆盖。"
      confirm-label="确认发布这个报价版本"
      tone="primary"
      :pending="pending"
      @confirm="submitQuote"
    />
  </div>
</template>
