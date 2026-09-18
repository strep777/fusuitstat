<script setup lang="ts">
/**
 * 商品与库存：商品列表、低库存 SKU、上下架。
 *
 * 约束（08 文档）：价格与库存由服务端裁定；库存调整必须写明数量与理由并留审计；
 * 界面不提供直接改「已付款金额」的入口。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  collect,
  describeFailure,
  text,
  usePageData,
  type AdminList,
  type Dict,
  type FailureView,
  type ProductRecord,
  type SkuRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<ProductRecord>>(
  () => adminApi.products({ status: statusFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "商品列表",
  () => auth.handleSessionExpired(),
);

const statusFilter = ref("");
const search = ref("");
const lowStock = ref<SkuRecord[]>([]);
const lowStockError = ref<FailureView | null>(null);
const stats = ref<Dict | null>(null);
const statsError = ref<FailureView | null>(null);

const columns: TableColumn[] = [
  { key: "title", label: "商品" },
  { key: "status", label: "状态", width: "110px" },
  { key: "sku_count", label: "SKU 数", width: "90px" },
  { key: "price_from_cents", label: "起价", width: "120px" },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
];

const skuColumns: TableColumn[] = [
  { key: "code", label: "SKU 编码" },
  { key: "spec", label: "规格" },
  { key: "price_cents", label: "售价", width: "120px" },
  { key: "stock_on_hand", label: "现有库存", width: "110px" },
  { key: "low_stock_threshold", label: "阈值", width: "90px" },
];

const stockTarget = ref<SkuRecord | null>(null);
const stockDelta = ref(0);
const stockReason = ref("");
const stockConfirmOpen = ref(false);
const actionError = ref("");
const pending = ref(false);

const statsItems = computed(() => {
  if (!stats.value) return [];
  return Object.entries(stats.value).map(([key, value]) => ({ label: key, value: text(value) }));
});

async function load(): Promise<void> {
  await state.load();
  const result = await collect<{ lowStock: AdminList<SkuRecord>; stats: Dict }>({
    lowStock: () => adminApi.lowStockSkus({ limit: 20 }),
    stats: () => adminApi.catalogStats(),
  });
  if (result.ok.lowStock) {
    lowStock.value = result.ok.lowStock.items;
    lowStockError.value = null;
  } else {
    lowStock.value = [];
    lowStockError.value = describeFailure(result.failed.find((item) => item.key === "lowStock")?.error, "低库存列表");
  }
  if (result.ok.stats) {
    stats.value = result.ok.stats;
    statsError.value = null;
  } else {
    stats.value = null;
    statsError.value = describeFailure(result.failed.find((item) => item.key === "stats")?.error, "商品统计");
  }
}

function openStock(row: SkuRecord): void {
  stockTarget.value = row;
  stockDelta.value = 0;
  stockReason.value = "";
  actionError.value = "";
}

/** 先校验表单，再用 ConfirmDialog 显示具体对象、数量与后果。 */
function requestStockConfirm(): void {
  if (!Number.isInteger(stockDelta.value) || stockDelta.value === 0) {
    actionError.value = "库存调整必须是整数且不能为 0。";
    return;
  }
  if (stockReason.value.trim().length < 6) {
    actionError.value = "请填写至少 6 个字的调整理由，例如「补货入库，供应商单号 A-1024」。";
    return;
  }
  actionError.value = "";
  stockConfirmOpen.value = true;
}

async function submitStock(payload?: { reason: string }): Promise<void> {
  const row = stockTarget.value;
  if (!row) return;
  const reason = stockReason.value.trim();
  if (!Number.isInteger(stockDelta.value) || stockDelta.value === 0 || reason.length < 6) {
    stockConfirmOpen.value = false;
    requestStockConfirm();
    return;
  }
  pending.value = true;
  actionError.value = "";
  try {
    await adminApi.adjustStock(row.id, { delta: stockDelta.value, reason: payload?.reason ? `${reason}｜确认理由：${payload.reason}` : reason });
    toast.success("库存已调整", `SKU ${text(row.code)} 库存变化 ${stockDelta.value > 0 ? "+" : ""}${stockDelta.value}。`);
    stockConfirmOpen.value = false;
    stockTarget.value = null;
    await load();
  } catch (caught) {
    actionError.value = actionErrorMessage(caught, "库存调整失败，请稍后重试。").message;
    stockConfirmOpen.value = false;
  } finally {
    pending.value = false;
  }
}

const stockPreview = computed(() => {
  const current = typeof stockTarget.value?.stock_on_hand === "number" ? stockTarget.value.stock_on_hand : null;
  if (current === null) return "现有库存未知，服务端会以数据库当前值计算";
  return `${current} → ${current + stockDelta.value}`;
});

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Catalog"
      title="商品与库存"
      description="维护商品、SKU、售价与库存。价格与库存的权威值在服务端；这里的每一次库存变化都会带理由写入审计。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
        <AppButton size="sm" :to="'/admin/products/new'">新建商品</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="订单已付金额不可修改"
      description="订单的已付金额只能通过真实收款或原路退款变化，后台不提供直接改写入口；发现金额异常请走退款或对账流程。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="load">
      <FieldRow label="商品关键词" for="product-q">
        <input id="product-q" v-model="search" type="search" class="field-base" placeholder="例如 龙尾 或 paw" />
      </FieldRow>
      <FieldRow label="状态" for="product-status">
        <select id="product-status" v-model="statusFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="PUBLISHED">已上架</option>
          <option value="DRAFT">草稿</option>
          <option value="ARCHIVED">已下架</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; statusFilter = ''; load()">清空</AppButton>
      </div>
    </form>

    <DataTable
      :columns="columns"
      :rows="state.data.value.items"
      :loading="state.loading.value"
      :error="state.error.value"
      :has-more="Boolean(state.data.value.has_more)"
      empty-title="还没有商品"
      empty-description="上架前需要先完成图片授权、SKU 与库存核对；不要用占位商品填充。"
      @retry="load"
    >
      <template #cell-title="{ row }">
        <RouterLink :to="`/admin/products/${row.id}`" class="link-underline font-medium hover:text-amber-strong">
          {{ text(row.title) }}
        </RouterLink>
        <p class="mt-1 font-mono text-xs text-ink-faint">{{ text(row.slug) }}</p>
      </template>
      <template #cell-price_from_cents="{ row }">
        <span class="tabular-nums">{{ formatMoneyExact(row.price_from_cents as number) }}</span>
      </template>
      <template #cell-sku_count="{ row }">
        <span class="tabular-nums">{{ text(row.sku_count, "0") }}</span>
      </template>
      <template #cell-updated_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.updated_at as string) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" :to="`/admin/products/${row.id}`">编辑商品与 SKU</AppButton>
      </template>
    </DataTable>

    <div class="grid gap-6 lg:grid-cols-[1.5fr_1fr]">
      <SurfaceCard>
        <h2 class="font-display text-lg">低库存 SKU</h2>
        <p class="mt-1 text-xs text-ink-faint">按后台阈值提示补货；系统不会自动改库存，需要员工填写理由后调整。</p>
        <div class="mt-4">
          <DataTable
            :columns="skuColumns"
            :rows="lowStock"
            :loading="state.loading.value"
            :error="lowStockError"
            dense
            empty-title="没有低于阈值的 SKU"
            empty-description="所有 SKU 的库存都高于各自阈值。"
            @retry="load"
          >
            <template #cell-code="{ row }">
              <span class="font-mono text-xs">{{ text(row.code) }}</span>
            </template>
            <template #cell-price_cents="{ row }">
              <span class="tabular-nums">{{ formatMoneyExact(row.price_cents as number) }}</span>
            </template>
            <template #cell-stock_on_hand="{ row }">
              <StatusBadge tone="wine" :label="`${text(row.stock_on_hand, '0')} 件`" />
            </template>
            <template #actions="{ row }">
              <AppButton size="sm" @click="openStock(row)">调整库存</AppButton>
            </template>
          </DataTable>
        </div>
      </SurfaceCard>

      <ModuleCard title="商品统计" :status="stats ? '实时' : '未就绪'">
        <div v-if="statsError" class="space-y-3">
          <p class="text-sm text-ink-muted">{{ statsError.description }} {{ statsError.retryHint }}</p>
          <AppButton size="sm" variant="secondary" @click="load">重试</AppButton>
        </div>
        <FieldList v-else :items="statsItems" :columns="1" />
      </ModuleCard>
    </div>

    <!-- 库存调整：危险动作，必须显示对象、数量与后果并填写理由 -->
    <ConfirmDialog
      :model-value="stockConfirmOpen"
      title="调整 SKU 库存"
      :target="stockTarget ? `SKU ${text(stockTarget.code)} · ${text(stockTarget.spec)}` : ''"
      :change="`库存变化 ${stockDelta > 0 ? '+' : ''}${stockDelta} 件`"
      :amount="stockPreview"
      consequence="库存变化会立刻影响下单可售数量；负向调整可能让商品变为售罄。调整会写入审计记录，事后可追溯。"
      :confirm-label="`确认把库存调整为 ${stockPreview}`"
      tone="danger"
      :pending="pending"
      @update:model-value="(value: boolean) => { if (!value) stockConfirmOpen = false; }"
      @confirm="submitStock"
    />

    <SurfaceCard v-if="stockTarget" class="border-wine">
      <h2 class="font-display text-lg">库存调整：{{ text(stockTarget.code) }}</h2>
      <FieldList
        class="mt-3"
        :items="[
          { label: '规格', value: text(stockTarget.spec) },
          { label: '现有库存', value: text(stockTarget.stock_on_hand) },
          { label: '低库存阈值', value: text(stockTarget.low_stock_threshold) },
          { label: '售价', value: formatMoneyExact(stockTarget.price_cents as number) },
        ]"
      />
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <FieldRow label="变化数量" required for="stock-delta" hint="正数入库，负数出库；不能为 0">
          <input id="stock-delta" v-model.number="stockDelta" type="number" step="1" class="field-base tabular-nums" />
        </FieldRow>
        <FieldRow label="调整理由" required for="stock-reason" hint="例如：补货入库，供应商单号 A-1024">
          <input id="stock-reason" v-model="stockReason" type="text" class="field-base" />
        </FieldRow>
      </div>
      <p class="mt-3 text-sm text-ink-muted">调整后库存预览：{{ stockPreview }}</p>
      <p v-if="actionError" class="mt-3 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ actionError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton
          variant="danger"
          :disabled="stockDelta === 0 || stockReason.trim().length < 6"
          :disabled-reason="stockDelta === 0 ? '变化数量不能为 0' : '理由至少 6 个字'"
          @click="requestStockConfirm"
        >
          提交库存调整
        </AppButton>
        <AppButton variant="quiet" @click="stockTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>
  </div>
</template>
