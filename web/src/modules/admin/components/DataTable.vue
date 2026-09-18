<script setup lang="ts" generic="Row extends Record<string, unknown> = Record<string, unknown>">
/**
 * 轻量数据表格（无第三方依赖）。
 *
 * 约束（11 文档）：每个数据区域都有 loading / empty / error / forbidden / not-found 状态；
 * 局部列表用骨架屏而不是整页加载图；行内操作可 Tab 聚焦；禁用动作旁说明原因。
 *
 * 用法：
 *   <DataTable :columns="columns" :rows="rows" :loading="loading" :error="errorView" …>
 *     <template #cell-status="{ row }">…</template>
 *     <template #actions="{ row }">…</template>
 *   </DataTable>
 */
import { computed, ref } from "vue";

import { AppButton, EmptyState, SkeletonBlock, StatusBadge } from "@/shared/ui/base";
import type { BadgeTone } from "@/shared/ui/base";
import { failureTone } from "@/modules/admin/api/admin";
import type { FailureView } from "@/modules/admin/api/admin";

export interface TableColumn {
  key: string;
  label: string;
  align?: "left" | "right" | "center";
  width?: string;
  /** 表头补充说明，例如「金额为整数分，展示时换算」。 */
  hint?: string;
  /** 移动端隐藏的低优先级列。 */
  hideOnMobile?: boolean;
}

export type FailureTone = "empty" | "error" | "forbidden" | "not-found";

const props = withDefaults(
  defineProps<{
    columns: TableColumn[];
    rows: Row[];
    rowKey?: string;
    loading?: boolean;
    error?: FailureView | null;
    /** 权限不足：显示说明与所需权限码，不显示任何数据。 */
    forbidden?: { title: string; description: string; codes?: string[] } | null;
    emptyTitle?: string;
    emptyDescription?: string;
    /** 「该模块接口尚未就绪」等既非空也非错误的诚实状态。 */
    notice?: { title: string; description: string } | null;
    caption?: string;
    page?: number;
    nextCursor?: string | null;
    hasMore?: boolean;
    pageSize?: number;
    canPrev?: boolean;
    dense?: boolean;
  }>(),
  {
    rowKey: "id",
    loading: false,
    error: null,
    forbidden: null,
    emptyTitle: "暂无记录",
    emptyDescription: "当前筛选条件下没有数据。可以调整筛选或稍后再看。",
    notice: null,
    caption: "",
    page: 1,
    nextCursor: null,
    hasMore: false,
    pageSize: 20,
    canPrev: false,
    dense: false,
  },
);

defineSlots<
  {
    /** 行内操作：必须可 Tab 聚焦。 */
    actions?: (props: { row: Row; index: number }) => unknown;
  } & {
    [key: `cell-${string}`]: (props: { row: Row; value: unknown; column: TableColumn }) => unknown;
  }
>();

const emit = defineEmits<{
  (event: "prev"): void;
  (event: "next"): void;
  (event: "retry"): void;
}>();

const tableRef = ref<HTMLTableElement | null>(null);

const skeletonRows = computed(() => 5);
/**
 * `error` 由 `describeFailure` 产出，`kind` 已经区分 403 / 404 / 网络失败。
 * 这里按 kind 选择 EmptyState 的语义色调，避免把「无权限」「记录不存在」渲染成普通错误。
 * `data-state` 同时暴露给测试与无障碍检查，用来断言当前是哪个状态而不是猜文案。
 */
const errorTone = computed(() => failureTone(props.error));
const showEmpty = computed(
  () => !props.loading && !props.error && !props.forbidden && props.rows.length === 0 && !props.notice,
);
/** 当前展示的是哪个状态：供无障碍提示与测试断言使用，不靠文案猜。 */
const stateName = computed(() => {
  if (props.loading) return "loading";
  if (props.error) return props.error.kind;
  if (props.forbidden) return "forbidden";
  if (props.notice) return "not-ready";
  if (showEmpty.value) return "empty";
  return "success";
});

function keyOf(row: Record<string, unknown>, index: number): string {
  const value = row[props.rowKey];
  return typeof value === "string" || typeof value === "number" ? String(value) : `row-${index}`;
}

function raw(row: Record<string, unknown>, column: TableColumn): unknown {
  return row[column.key];
}

const ALIGN_CLASS: Record<string, string> = {
  left: "text-left",
  right: "text-right",
  center: "text-center",
};

function alignClass(column: TableColumn): string {
  return ALIGN_CLASS[column.align ?? "left"] ?? "text-left";
}

/** 状态徽章的语气由状态文本决定；颜色只是辅助，文字自解释。 */
const STATUS_TONE: Record<string, BadgeTone> = {
  已发布: "jade",
  已上架: "jade",
  已完成: "jade",
  已批准: "jade",
  已发货: "jade",
  一致: "jade",
  通过: "jade",
  草稿: "neutral",
  待审核: "amber",
  待付款: "amber",
  待处理: "amber",
  待审: "amber",
  处理中: "amber",
  补件中: "amber",
  已下架: "neutral",
  已归档: "neutral",
  已关闭: "neutral",
  已拒绝: "wine",
  失败: "wine",
  死信: "wine",
  异常: "wine",
  差异: "wine",
  已过期: "wine",
};

function toneFor(value: string): BadgeTone {
  return STATUS_TONE[value] ?? "neutral";
}

/** 已登记状态词才渲染徽章，其余按纯文本显示，避免把普通文本误标成状态。 */
function statusLabel(value: unknown): string {
  return typeof value === "string" && STATUS_TONE[value] ? value : "";
}

function focusFirstRowAction(): void {
  const first = tableRef.value?.querySelector<HTMLElement>("tbody [data-row-action]");
  first?.focus();
}
</script>

<template>
  <div class="space-y-4" :data-state="stateName">
    <div v-if="loading" class="space-y-3 rounded-lg border border-line bg-surface p-5" role="status" aria-live="polite">
      <SkeletonBlock :lines="2" aria-label="正在加载列表" />
      <div class="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <div v-for="cell in skeletonRows" :key="cell" class="h-8 animate-pulse rounded-xs bg-canvas-sunk" />
      </div>
      <span class="sr-only">正在加载，请稍候</span>
    </div>

    <EmptyState
      v-else-if="error"
      :tone="errorTone"
      :title="error.title"
      :description="`${error.description} ${error.retryHint}`"
    >
      <AppButton size="sm" @click="emit('retry')">重试</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      tone="forbidden"
      :title="forbidden.title"
      :description="forbidden.description"
    >
      <p v-if="forbidden.codes?.length" class="text-xs text-ink-faint">
        需要的权限码：{{ forbidden.codes.join("、") }}
      </p>
      <p class="text-xs text-ink-faint">服务端仍会逐动作校验权限；界面隐藏只是提示，不是授权。</p>
    </EmptyState>

    <EmptyState
      v-else-if="notice"
      :title="notice.title"
      :description="notice.description"
    >
      <AppButton size="sm" variant="secondary" @click="emit('retry')">重试</AppButton>
    </EmptyState>

    <EmptyState v-else-if="showEmpty" :title="emptyTitle" :description="emptyDescription">
      <AppButton size="sm" variant="secondary" @click="emit('retry')">重新加载</AppButton>
    </EmptyState>

    <template v-else>
      <div class="overflow-x-auto rounded-lg border border-line bg-surface">
        <table ref="tableRef" class="w-full border-collapse text-sm">
          <caption class="sr-only">{{ caption || "运营后台数据列表" }}</caption>
          <thead>
            <tr class="border-b border-line bg-surface-muted text-xs text-ink-muted">
              <th
                v-for="column in columns"
                :key="column.key"
                scope="col"
                :class="[
                  'px-4 py-3 font-medium',
                  alignClass(column),
                  column.hideOnMobile ? 'hidden lg:table-cell' : '',
                ]"
                :style="column.width ? { width: column.width } : undefined"
              >
                {{ column.label }}
                <span v-if="column.hint" class="block font-normal text-ink-faint">{{ column.hint }}</span>
              </th>
              <th scope="col" class="px-4 py-3 text-right font-medium">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="(row, index) in rows"
              :key="keyOf(row, index)"
              class="border-b border-line last:border-b-0 hover:bg-surface-muted/60 focus-within:bg-surface-muted/60"
            >
              <td
                v-for="column in columns"
                :key="column.key"
                :class="[
                  props.dense ? 'px-4 py-2' : 'px-4 py-3',
                  alignClass(column),
                  'align-top text-ink-soft',
                  column.hideOnMobile ? 'hidden lg:table-cell' : '',
                ]"
              >
                <slot :name="`cell-${column.key}`" :row="row" :value="raw(row, column)" :column="column">
                  <StatusBadge
                    v-if="statusLabel(raw(row, column))"
                    :tone="toneFor(statusLabel(raw(row, column)))"
                    :label="statusLabel(raw(row, column))"
                  />
                  <span v-else-if="raw(row, column) === null || raw(row, column) === undefined || raw(row, column) === ''">
                    —
                  </span>
                  <span v-else class="tabular-nums">{{ String(raw(row, column)) }}</span>
                </slot>
              </td>
              <td class="px-4 py-3 text-right align-top">
                <div class="flex flex-wrap items-center justify-end gap-2">
                  <slot name="actions" :row="row" :index="index" />
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="flex flex-wrap items-center justify-between gap-3 text-xs text-ink-muted">
        <p>
          第 {{ page }} 页 · 每页最多 {{ pageSize }} 条
          <span v-if="nextCursor" class="ml-2 text-ink-faint">游标分页：只能顺序翻页，不会跳过新记录</span>
        </p>
        <div class="flex items-center gap-2">
          <AppButton size="sm" variant="secondary" :disabled="!canPrev" disabled-reason="已经是第一页" @click="emit('prev')">
            上一页
          </AppButton>
          <AppButton size="sm" variant="secondary" :disabled="!hasMore" disabled-reason="没有更多记录了" @click="emit('next')">
            下一页
          </AppButton>
          <button
            v-if="rows.length"
            type="button"
            class="rounded-xs px-2 py-1 text-ink-faint underline decoration-line-strong underline-offset-2 hover:text-amber-strong"
            @click="focusFirstRowAction"
          >
            跳到行操作
          </button>
        </div>
      </div>
    </template>
  </div>
</template>
