<script setup lang="ts">
/**
 * 角色汤：独占角色的内容、授权规则、售出展示与掉落释放。
 *
 * 约束：独占角色只有一只，售出后必须立刻反映真实状态；释放（掉落）是危险动作，
 * 需要确认对象、金额与后果，并填写理由。
 */
import { onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  text,
  usePageData,
  type AdminList,
  type CharacterRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, SectionHeading, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<CharacterRecord>>(
  async () => ({ items: [] }),
  { items: [] },
  "角色列表",
  () => auth.handleSessionExpired(),
);

/**
 * 说明：当前后台契约里角色只有创建与释放两个写端点，没有独立的列表端点。
 * 因此这里如实显示「列表接口尚未就绪」，不拿商品接口或其他数据冒充角色列表。
 */
const characters = ref<CharacterRecord[]>([]);
const listError = ref(
  "后台尚无独立的角色列表接口（06 文档当前只有创建与释放动作）。这里不会显示伪造的角色记录；创建或释放成功后，请到前台角色页核对展示结果。",
);

const columns: TableColumn[] = [
  { key: "name", label: "角色名" },
  { key: "species", label: "物种", width: "110px" },
  { key: "status", label: "状态", width: "120px" },
  { key: "price_cents", label: "标价", width: "130px" },
  { key: "rights_note", label: "授权与使用限制", hideOnMobile: true },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
];

const createForm = ref({
  name: "",
  species: "",
  summary: "",
  price_cents: 0,
  currency: "CNY",
  rights_note: "独占角色，售出后不得复制或再次领养",
});
const createPending = ref(false);
const createMessage = ref("");
const createOpen = ref(false);

const releaseTarget = ref<CharacterRecord | null>(null);
const releaseConfirmOpen = ref(false);
const releaseReason = ref("");
const releaseError = ref("");
const releasePending = ref(false);
const releasePrice = ref(0);
const releaseWindow = ref(7);

async function load(): Promise<void> {
  // 列表接口未就绪：只刷新页面状态，不请求不存在的端点，也不伪造数据。
  await state.load();
  characters.value = [];
  listError.value =
    "后台尚无独立的角色列表接口（06 文档当前只有创建与释放动作）。这里不会显示伪造的角色记录；创建或释放成功后，请到前台角色页核对展示结果。";
}

async function createCharacter(): Promise<void> {
  createMessage.value = "";
  if (!createForm.value.name.trim() || !createForm.value.species.trim()) {
    createMessage.value = "角色名与物种是必填项。";
    return;
  }
  createPending.value = true;
  try {
    await adminApi.createCharacter({
      name: createForm.value.name.trim(),
      species: createForm.value.species.trim(),
      summary: createForm.value.summary,
      price_cents: Math.round(createForm.value.price_cents),
      currency: createForm.value.currency,
      rights_note: createForm.value.rights_note,
    });
    toast.success("角色已创建", "角色默认为草稿状态，需要补全图片授权后再公开。");
    createOpen.value = false;
    createForm.value = { name: "", species: "", summary: "", price_cents: 0, currency: "CNY", rights_note: "独占角色，售出后不得复制或再次领养" };
    await load();
  } catch (caught) {
    createMessage.value = actionErrorMessage(caught, "创建角色失败，请稍后重试。").message;
  } finally {
    createPending.value = false;
  }
}

function openRelease(row: CharacterRecord): void {
  releaseTarget.value = row;
  releasePrice.value = typeof row.price_cents === "number" ? row.price_cents : 0;
  releaseWindow.value = 7;
  releaseReason.value = "";
  releaseError.value = "";
}

function requestRelease(): void {
  if (releaseReason.value.trim().length < 6) {
    releaseError.value = "请填写至少 6 个字的理由，说明为什么现在释放这个角色。";
    return;
  }
  if (!Number.isInteger(releasePrice.value) || releasePrice.value < 0) {
    releaseError.value = "释放标价必须是不小于 0 的整数分。";
    return;
  }
  releaseError.value = "";
  releaseConfirmOpen.value = true;
}

async function submitRelease(): Promise<void> {
  const row = releaseTarget.value;
  if (!row) return;
  releasePending.value = true;
  try {
    await adminApi.releaseCharacter(row.id, { version: row.version ?? undefined, reason: releaseReason.value.trim() });
    toast.success("角色已释放为掉落", `《${text(row.name)}》已进入掉落列表，会在审批通过后开放购买。`);
    releaseConfirmOpen.value = false;
    releaseTarget.value = null;
    await load();
  } catch (caught) {
    releaseError.value = actionErrorMessage(caught, "释放失败，请稍后重试。").message;
    releaseConfirmOpen.value = false;
  } finally {
    releasePending.value = false;
  }
}

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Characters"
      title="角色汤"
      description="维护独占角色的内容、授权规则与售出展示。独占角色只有一只：售出后必须立刻反映真实状态，避免二次售卖。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
        <AppButton size="sm" @click="createOpen = !createOpen">{{ createOpen ? "收起新建表单" : "新建角色" }}</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="授权范围必须写清楚"
      description="领养授权说明这只角色可以被如何使用、能否二创、能否转售。没有登记授权的角色不上架。"
    />

    <SurfaceCard v-if="createOpen">
      <SectionHeading level="2" title="新建角色" description="创建后会先保存为草稿，图片授权与扫描完成后再公开。" />
      <div class="mt-5 grid gap-4 sm:grid-cols-2">
        <FieldRow label="角色名" required for="char-name">
          <input id="char-name" v-model="createForm.name" type="text" class="field-base" maxlength="80" />
        </FieldRow>
        <FieldRow label="物种" required for="char-species">
          <input id="char-species" v-model="createForm.species" type="text" class="field-base" maxlength="40" />
        </FieldRow>
        <FieldRow label="标价（分）" for="char-price" hint="示例：380000 表示 ¥3,800.00">
          <input id="char-price" v-model.number="createForm.price_cents" type="number" min="0" step="1" class="field-base tabular-nums" />
        </FieldRow>
        <FieldRow label="币种" for="char-currency">
          <select id="char-currency" v-model="createForm.currency" class="field-base">
            <option value="CNY">人民币 CNY</option>
            <option value="USD">美元 USD</option>
          </select>
        </FieldRow>
        <FieldRow label="一句话介绍" for="char-summary">
          <input id="char-summary" v-model="createForm.summary" type="text" class="field-base" maxlength="160" />
        </FieldRow>
        <FieldRow label="授权与使用限制" for="char-rights" hint="例如：独占角色，售出后不得复制或再次领养">
          <input id="char-rights" v-model="createForm.rights_note" type="text" class="field-base" />
        </FieldRow>
      </div>
      <p v-if="createMessage" class="mt-4 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ createMessage }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton
          :loading="createPending"
          :disabled="!createForm.name.trim() || !createForm.species.trim()"
          :disabled-reason="!createForm.name.trim() || !createForm.species.trim() ? '角色名与物种必须先填写' : ''"
          @click="createCharacter"
        >
          创建角色草稿
        </AppButton>
        <AppButton variant="quiet" @click="createOpen = false">取消</AppButton>
      </div>
    </SurfaceCard>

    <p v-if="listError" class="rounded-lg border border-dashed border-line-strong bg-surface-muted px-5 py-4 text-sm text-ink-muted" role="status">
      {{ listError }}
    </p>

    <DataTable
      :columns="columns"
      :rows="characters"
      :loading="state.loading.value"
      :error="state.error.value"
      empty-title="这里没有可显示的角色记录"
      empty-description="角色列表接口尚未就绪；创建或释放动作成功后，请到前台角色页核对展示结果。"
      @retry="load"
    >
      <template #cell-name="{ row }">
        <span class="font-medium">{{ text(row.name) }}</span>
      </template>
      <template #cell-price_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.price_cents as number) }}</span>
      </template>
      <template #cell-rights_note="{ row }">
        <span class="text-xs">{{ text(row.rights_note, "未登记授权说明") }}</span>
      </template>
      <template #cell-updated_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.updated_at as string) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="danger" @click="openRelease(row)">释放为掉落</AppButton>
      </template>
    </DataTable>

    <SurfaceCard v-if="releaseTarget">
      <h2 class="font-display text-lg">释放为掉落：{{ text(releaseTarget.name) }}</h2>
      <FieldList
        class="mt-3"
        :items="[
          { label: '物种', value: text(releaseTarget.species) },
          { label: '当前状态', value: text(releaseTarget.status) },
          { label: '授权说明', value: text(releaseTarget.rights_note) },
        ]"
      />
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <FieldRow label="掉落标价（分）" required for="release-price">
          <input id="release-price" v-model.number="releasePrice" type="number" min="0" step="1" class="field-base tabular-nums" />
        </FieldRow>
        <FieldRow label="购买窗口（天）" required for="release-window" hint="审批通过后必须在窗口内完成购买">
          <input id="release-window" v-model.number="releaseWindow" type="number" min="1" max="30" step="1" class="field-base tabular-nums" />
        </FieldRow>
        <FieldRow class="sm:col-span-2" label="释放理由" required for="release-reason" hint="例如：原领养人放弃，沟通记录见工单 Q-2026-0142">
          <input id="release-reason" v-model="releaseReason" type="text" class="field-base" />
        </FieldRow>
      </div>
      <p v-if="releaseError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ releaseError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton variant="danger" @click="requestRelease">提交释放</AppButton>
        <AppButton variant="quiet" @click="releaseTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="releaseConfirmOpen"
      title="把独占角色释放为掉落"
      :target="releaseTarget ? `《${text(releaseTarget.name)}》 · ${text(releaseTarget.species)}` : ''"
      change="状态 → 待审批掉落；通过审批后按通过列表开放购买。"
      :amount="`${formatMoneyExact(releasePrice)} · 购买窗口 ${releaseWindow} 天`"
      consequence="释放后角色会进入申请审批队列；一旦有人购买，这个角色就不能再作为定制委托重复制作。误操作只能通过下架与人工沟通挽回。"
      confirm-label="确认释放为掉落"
      tone="danger"
      :pending="releasePending"
      :require-typed="releaseTarget ? String(releaseTarget.name) : ''"
      typed-label="请输入角色名确认释放"
      @confirm="submitRelease"
    />
  </div>
</template>
