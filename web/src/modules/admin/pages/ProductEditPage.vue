<script setup lang="ts">
/**
 * 商品编辑：商品字段、SKU 列表、库存调整、上下架。
 *
 * 约束（08 文档）：库存调整必须显示对象与数量并填写理由；价格与库存以服务端为准；
 * 这里没有修改订单已付金额的入口。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  failureTone,
  text,
  usePageData,
  type Dict,
  type ProductRecord,
  type SkuRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, EmptyState, FieldRow, SectionHeading, SkeletonBlock, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact, formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const auth = useAdminAuthStore();
const toast = useToast();

const productId = computed(() => String(route.params.id ?? ""));
const isNew = computed(() => route.name === "admin-product-new");

const form = ref({
  title: "",
  slug: "",
  summary: "",
  status: "DRAFT",
  currency: "CNY",
  kind: "stock_goods",
  category: "",
  service_kind: "",
  requires_approval: false,
});

const version = ref(0);
const skus = ref<SkuRecord[]>([]);
const skusError = ref("");
const saveError = ref("");
const dirty = ref(false);

const state = usePageData<ProductRecord | null>(
  async () => (isNew.value ? null : adminApi.productDetail(productId.value)),
  null,
  "商品详情",
  () => auth.handleSessionExpired(),
);

const skuColumns: TableColumn[] = [
  { key: "code", label: "SKU 编码" },
  { key: "spec", label: "规格" },
  { key: "price_cents", label: "售价", width: "120px" },
  { key: "stock_on_hand", label: "库存", width: "100px" },
  { key: "low_stock_threshold", label: "阈值", width: "90px" },
  { key: "active", label: "可售", width: "90px" },
];

const newSku = ref({ code: "", spec: "", price_cents: 0, stock_on_hand: 0, low_stock_threshold: 3 });
const skuPending = ref(false);

const stockTarget = ref<SkuRecord | null>(null);
const stockDelta = ref(0);
const stockReason = ref("");
const stockConfirmOpen = ref(false);
const stockError = ref("");
const stockPending = ref(false);

const savedItems = computed(() => [
  { label: "商品编号", value: text(productId.value) },
  { label: "状态", value: text(form.value.status) },
  { label: "版本", value: version.value },
  { label: "slug", value: text(form.value.slug) },
]);

function fill(record: ProductRecord): void {
  form.value.title = String(record.title ?? "");
  form.value.slug = String(record.slug ?? "");
  form.value.summary = String((record as Dict).summary ?? "");
  form.value.status = String(record.status ?? "DRAFT");
  form.value.currency = String(record.currency ?? "CNY");
  form.value.kind = String(record.kind ?? "stock_goods");
  form.value.category = String(record.category ?? "");
  form.value.service_kind = String(record.service_kind ?? "");
  form.value.requires_approval = record.requires_approval === true;
  version.value = typeof record.version === "number" ? record.version : 0;
  dirty.value = false;
}

/** SKU 通常随商品详情一起返回；接口未提供时如实说明，不编造 SKU。 */
function fillSkus(record: ProductRecord): void {
  const raw = (record as Dict).skus;
  if (Array.isArray(raw)) {
    skus.value = raw as SkuRecord[];
    skusError.value = "";
    return;
  }
  skus.value = [];
  skusError.value = "该商品详情接口没有返回 SKU 列表；新建 SKU 后可以在这里核对，但不会显示伪造数据。";
}

async function load(): Promise<void> {
  await state.load();
  if (state.data.value) {
    fill(state.data.value);
    fillSkus(state.data.value);
  }
}

async function saveProduct(): Promise<void> {
  saveError.value = "";
  if (!form.value.title.trim() || !form.value.slug.trim()) {
    saveError.value = "商品名称与 slug 是必填项。";
    return;
  }
  state.saving.value = true;
  try {
    if (isNew.value) {
      const created = await adminApi.createProduct({
        title: form.value.title.trim(),
        slug: form.value.slug.trim(),
        summary: form.value.summary,
        status: form.value.status,
        kind: form.value.kind,
        category: form.value.category || null,
        service_kind: form.value.service_kind || null,
        requires_approval: form.value.requires_approval,
      });
      toast.success("商品草稿已创建", "接着添加 SKU 与库存，再上架。");
      await router.replace({ name: "admin-product-edit", params: { id: created.id } });
      await load();
      return;
    }
    const updated = await adminApi.updateProduct(productId.value, {
      title: form.value.title.trim(),
      slug: form.value.slug.trim(),
      summary: form.value.summary,
      status: form.value.status,
      category: form.value.category || null,
      service_kind: form.value.service_kind || null,
      requires_approval: form.value.requires_approval,
      version: version.value,
    });
    fill(updated);
    toast.success("商品已保存", "保存不会影响已下单订单的价格快照。");
  } catch (caught) {
    const view = actionErrorMessage(caught, "保存失败，请稍后重试。");
    saveError.value = view.message;
  } finally {
    state.saving.value = false;
  }
}

async function addSku(): Promise<void> {
  skuPending.value = true;
  skusError.value = "";
  try {
    await adminApi.createSku(productId.value, {
      code: newSku.value.code.trim(),
      spec: newSku.value.spec.trim(),
      price_cents: Math.round(newSku.value.price_cents),
      stock_on_hand: Math.round(newSku.value.stock_on_hand),
      low_stock_threshold: Math.round(newSku.value.low_stock_threshold),
    });
    toast.success("SKU 已创建", "库存为初始值，后续调整会单独记录审计。");
    newSku.value = { code: "", spec: "", price_cents: 0, stock_on_hand: 0, low_stock_threshold: 3 };
    await load();
  } catch (caught) {
    skusError.value = actionErrorMessage(caught, "创建 SKU 失败，请稍后重试。").message;
  } finally {
    skuPending.value = false;
  }
}

function openStock(row: SkuRecord): void {
  stockTarget.value = row;
  stockDelta.value = 0;
  stockReason.value = "";
  stockError.value = "";
}

function requestStockConfirm(): void {
  if (!Number.isInteger(stockDelta.value) || stockDelta.value === 0) {
    stockError.value = "库存变化必须是整数且不能为 0。";
    return;
  }
  if (stockReason.value.trim().length < 6) {
    stockError.value = "请填写至少 6 个字的调整理由。";
    return;
  }
  stockError.value = "";
  stockConfirmOpen.value = true;
}

async function submitStock(): Promise<void> {
  const row = stockTarget.value;
  if (!row) return;
  stockPending.value = true;
  try {
    await adminApi.adjustStock(row.id, { delta: stockDelta.value, reason: stockReason.value.trim() });
    toast.success("库存已调整", `SKU ${text(row.code)} 变化 ${stockDelta.value > 0 ? "+" : ""}${stockDelta.value}。`);
    stockConfirmOpen.value = false;
    stockTarget.value = null;
    await load();
  } catch (caught) {
    stockError.value = actionErrorMessage(caught, "库存调整失败，请稍后重试。").message;
    stockConfirmOpen.value = false;
  } finally {
    stockPending.value = false;
  }
}

const stockPreview = computed(() => {
  const current = typeof stockTarget.value?.stock_on_hand === "number" ? stockTarget.value.stock_on_hand : null;
  return current === null ? "现有库存未知（以服务端当前值为准）" : `${current} → ${current + stockDelta.value}`;
});

watch(
  () => [
    form.value.title,
    form.value.slug,
    form.value.summary,
    form.value.status,
    form.value.kind,
    form.value.category,
    form.value.service_kind,
    form.value.requires_approval,
  ],
  () => {
    dirty.value = true;
  },
);

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      :eyebrow="isNew ? 'Catalog · New' : 'Catalog · Edit'"
      :title="isNew ? '新建商品' : `编辑商品：${text(form.title, '未命名商品')}`"
      description="商品保存与上架是分开的动作；SKU 的售价与库存以服务端为准，结算时会重新核对价格。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
        <AppButton size="sm" variant="secondary" to="/admin/products">返回商品列表</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      tone="warn"
      title="订单已付金额不可修改"
      description="后台只调整商品与 SKU 的当前售价，不改动已生成订单的金额快照；金额变化必须通过收款或原路退款完成。"
    />

    <div v-if="state.loading.value" class="space-y-4">
      <SkeletonBlock :lines="6" aria-label="正在加载商品详情" />
    </div>

    <EmptyState
      v-else-if="state.error.value"
      :tone="failureTone(state.error.value)"
      :title="state.error.value.title"
      :description="`${state.error.value.description} ${state.error.value.retryHint}`"
    >
      <AppButton size="sm" @click="load">重试</AppButton>
      <AppButton size="sm" variant="secondary" to="/admin/products">返回列表</AppButton>
    </EmptyState>

    <div v-else class="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
      <div class="space-y-6">
        <SurfaceCard>
          <SectionHeading level="2" title="商品信息" description="slug 决定公开地址；摘要用于列表与分享卡片。" />
          <form class="mt-5 space-y-4" @submit.prevent="saveProduct">
            <FieldRow label="商品名称" required for="p-title">
              <input id="p-title" v-model="form.title" type="text" class="field-base" maxlength="120" />
            </FieldRow>
            <FieldRow label="slug" required for="p-slug" hint="只允许小写字母、数字与连字符">
              <input id="p-slug" v-model="form.slug" type="text" class="field-base font-mono" />
            </FieldRow>
            <FieldRow label="摘要" for="p-summary">
              <textarea id="p-summary" v-model="form.summary" rows="2" class="field-base resize-y" maxlength="300" />
            </FieldRow>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="类型" for="p-kind" :hint="isNew ? '创建后不可修改' : '类型创建后不可修改'">
                <select id="p-kind" v-model="form.kind" class="field-base" :disabled="!isNew">
                  <option value="stock_goods">现货商品</option>
                  <option value="exclusive_character">角色汤</option>
                  <option value="commission">委托服务</option>
                  <option value="service">服务</option>
                </select>
              </FieldRow>
              <FieldRow label="分类" for="p-category" hint="决定出现在商城、道具还是周边">
                <select id="p-category" v-model="form.category" class="field-base">
                  <option value="">不归类</option>
                  <option value="stock">现货</option>
                  <option value="accessory">配件</option>
                  <option value="props">道具</option>
                  <option value="merch">周边</option>
                  <option value="service">服务</option>
                  <option value="character">角色</option>
                </select>
              </FieldRow>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="服务类型" for="p-service-kind" hint="洗毛套餐必须选择「洗毛」">
                <select id="p-service-kind" v-model="form.service_kind" class="field-base">
                  <option value="">不适用</option>
                  <option value="cleaning">洗毛</option>
                  <option value="repair">修补</option>
                  <option value="accessory">配件制作</option>
                  <option value="fitting">试穿调整</option>
                </select>
              </FieldRow>
              <FieldRow label="购买方式" for="p-approval" hint="洗毛与限量掉落需要先审批">
                <label class="flex min-h-11 items-center gap-2 rounded-sm border-2 border-ink/10 px-3 text-sm">
                  <input id="p-approval" v-model="form.requires_approval" type="checkbox" class="size-4 accent-brand" />
                  需要先申请审批
                </label>
              </FieldRow>
            </div>
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="币种" for="p-currency">
                <select id="p-currency" v-model="form.currency" class="field-base">
                  <option value="CNY">人民币 CNY</option>
                  <option value="USD">美元 USD</option>
                </select>
              </FieldRow>
              <FieldRow label="状态" for="p-status" hint="上架前必须完成图片授权与 SKU 库存核对">
                <select id="p-status" v-model="form.status" class="field-base">
                  <option value="DRAFT">草稿</option>
                  <option value="PUBLISHED">已上架</option>
                  <option value="ARCHIVED">已下架</option>
                </select>
              </FieldRow>
            </div>
            <p v-if="saveError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ saveError }}
            </p>
            <div class="flex flex-wrap items-center gap-3">
              <AppButton type="submit" :loading="state.saving.value">保存商品</AppButton>
              <span v-if="dirty" class="text-xs text-ink-faint">有未保存的修改</span>
            </div>
          </form>
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading level="2" title="SKU 与库存" description="每个 SKU 独立记录售价与库存；库存变化都会写入审计。" />
          <p v-if="skusError" class="mt-4 rounded-md border border-line bg-surface-muted px-4 py-3 text-sm text-ink-muted">
            {{ skusError }}
          </p>
          <div class="mt-4">
            <DataTable
              :columns="skuColumns"
              :rows="skus"
              :loading="state.loading.value"
              dense
              empty-title="还没有 SKU"
              empty-description="先添加至少一个 SKU，商品才有可售规格。"
              @retry="load"
            >
              <template #cell-code="{ row }">
                <span class="font-mono text-xs">{{ text(row.code) }}</span>
              </template>
              <template #cell-price_cents="{ row }">
                <span class="tabular-nums">{{ formatMoneyExact(row.price_cents as number) }}</span>
              </template>
              <template #cell-stock_on_hand="{ row }">
                <span class="tabular-nums">{{ text(row.stock_on_hand, "0") }}</span>
              </template>
              <template #cell-active="{ row }">
                <span>{{ row.active === false ? "已停用" : "可售" }}</span>
              </template>
              <template #actions="{ row }">
                <AppButton size="sm" variant="secondary" @click="openStock(row)">调整库存</AppButton>
              </template>
            </DataTable>
          </div>

          <fieldset class="mt-6 rounded-md border border-line p-4">
            <legend class="px-1 text-sm font-medium text-ink-soft">新增 SKU</legend>
            <div class="mt-4 grid gap-4 sm:grid-cols-2">
              <FieldRow label="SKU 编码" required for="sku-code">
                <input id="sku-code" v-model="newSku.code" type="text" class="field-base font-mono" />
              </FieldRow>
              <FieldRow label="规格" required for="sku-spec" hint="例如：全装 / 半装 / 单头">
                <input id="sku-spec" v-model="newSku.spec" type="text" class="field-base" />
              </FieldRow>
              <FieldRow label="售价（分）" required for="sku-price" hint="金额一律用整数分，例如 128000 表示 ¥1,280.00">
                <input id="sku-price" v-model.number="newSku.price_cents" type="number" min="0" step="1" class="field-base tabular-nums" />
              </FieldRow>
              <FieldRow label="初始库存" required for="sku-stock">
                <input id="sku-stock" v-model.number="newSku.stock_on_hand" type="number" min="0" step="1" class="field-base tabular-nums" />
              </FieldRow>
              <FieldRow label="低库存阈值" for="sku-threshold">
                <input id="sku-threshold" v-model.number="newSku.low_stock_threshold" type="number" min="0" step="1" class="field-base tabular-nums" />
              </FieldRow>
            </div>
            <div class="mt-4">
              <AppButton
                :loading="skuPending"
                :disabled="!newSku.code.trim() || !newSku.spec.trim()"
                :disabled-reason="!newSku.code.trim() || !newSku.spec.trim() ? 'SKU 编码与规格必须先填写' : ''"
                @click="addSku"
              >
                添加 SKU
              </AppButton>
            </div>
          </fieldset>
        </SurfaceCard>
      </div>

      <div class="space-y-6">
        <ModuleCard title="当前商品" status="只读摘要">
          <FieldList :items="savedItems" :columns="1" />
          <p class="mt-3 text-xs text-ink-faint">
            服务端保存时间：{{ formatDateTime(state.data.value?.updated_at as string) }}
          </p>
        </ModuleCard>

        <SurfaceCard v-if="stockTarget">
          <h2 class="font-display text-lg">库存调整：{{ text(stockTarget.code) }}</h2>
          <p class="mt-2 text-sm text-ink-muted">
            操作对象：SKU {{ text(stockTarget.code) }}（{{ text(stockTarget.spec) }}）；当前库存
            {{ text(stockTarget.stock_on_hand) }} 件。调整会立刻影响可售数量。
          </p>
          <div class="mt-4 space-y-4">
            <FieldRow label="变化数量" required for="edit-stock-delta">
              <input id="edit-stock-delta" v-model.number="stockDelta" type="number" step="1" class="field-base tabular-nums" />
            </FieldRow>
            <FieldRow label="调整理由" required for="edit-stock-reason" hint="例如：退货返库，售后单 AS-2026-0031">
              <input id="edit-stock-reason" v-model="stockReason" type="text" class="field-base" />
            </FieldRow>
            <p class="text-sm text-ink-soft">调整后预览：{{ stockPreview }}</p>
            <p v-if="stockError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ stockError }}
            </p>
            <div class="flex flex-wrap gap-3">
              <AppButton variant="danger" @click="requestStockConfirm">提交库存调整</AppButton>
              <AppButton variant="quiet" @click="stockTarget = null">取消</AppButton>
            </div>
          </div>
        </SurfaceCard>

        <SurfaceCard v-else>
          <h2 class="font-display text-lg">库存调整</h2>
          <p class="mt-2 text-sm text-ink-muted">在左侧 SKU 列表选择一行，填写变化数量与理由后提交。</p>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="stockConfirmOpen"
      title="调整 SKU 库存"
      :target="stockTarget ? `SKU ${text(stockTarget.code)} · ${text(stockTarget.spec)}` : ''"
      :change="`库存变化 ${stockDelta > 0 ? '+' : ''}${stockDelta} 件`"
      :amount="stockPreview"
      consequence="库存变化会立刻影响下单可售数量；负向调整可能让商品变为售罄。调整会写入审计记录与理由。"
      confirm-label="确认调整库存"
      tone="danger"
      :pending="stockPending"
      @confirm="submitStock"
    />
  </div>
</template>
