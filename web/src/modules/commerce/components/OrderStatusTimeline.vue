<script setup lang="ts">
/**
 * 订单/款项状态时间线。
 *
 * 规则（11 文档）：不能靠颜色单独表达状态，因此每一步都输出「已到达 / 当前 / 未到达 / 已中止」文字，
 * 并用 `aria-current="step"` 标注当前步骤。取消是终态分支，不再渲染后续步骤为可达。
 */
import { computed } from "vue";

import { StatusBadge, type BadgeTone } from "@/shared/ui/base";

const props = withDefaults(
  defineProps<{
    /** 当前状态码，使用服务端返回值原文。 */
    status: string;
    /** 有序步骤：取消/关闭这类终态分支放在 `terminal`。 */
    steps: Array<{ code: string; label: string }>;
    /** 终态说明（如 CANCELLED）：给出后不再展示 steps 的进度判定。 */
    terminal?: { code: string; label: string; description: string } | null;
    title?: string;
    note?: string;
  }>(),
  { terminal: null, title: "", note: "" },
);

const TERMINAL_TONE: Record<string, BadgeTone> = {
  CANCELLED: "neutral",
  CLOSED: "neutral",
  REJECTED: "wine",
  FAILED: "wine",
  UNKNOWN: "amber",
};

const currentIndex = computed(() => props.steps.findIndex((step) => step.code === props.status));

const isTerminal = computed(
  () => props.terminal !== null && props.terminal !== undefined && props.terminal.code === props.status,
);

const currentLabel = computed(() => {
  const found = props.steps.find((step) => step.code === props.status);
  return found?.label ?? props.status;
});

const marker = computed(() => {
  if (isTerminal.value) return "状态已结束";
  if (currentIndex.value < 0) return "状态以服务端返回为准";
  return `当前步骤：${currentLabel.value}`;
});

function stepState(index: number): "done" | "current" | "todo" {
  if (isTerminal.value) return "todo";
  if (currentIndex.value < 0) return "todo";
  if (index < currentIndex.value) return "done";
  if (index === currentIndex.value) return "current";
  return "todo";
}

const STEP_TEXT: Record<"done" | "current" | "todo", string> = {
  done: "已完成",
  current: "当前",
  todo: "未到达",
};

const STEP_CLASS: Record<"done" | "current" | "todo", string> = {
  done: "border-jade bg-jade-soft text-jade",
  current: "border-amber bg-amber-soft text-amber-strong",
  todo: "border-line bg-surface-muted text-ink-faint",
};
</script>

<template>
  <div>
    <div v-if="title" class="flex flex-wrap items-center justify-between gap-3">
      <h3 class="font-display text-base">{{ title }}</h3>
      <StatusBadge :tone="isTerminal ? (TERMINAL_TONE[status] ?? 'neutral') : 'amber'" :label="marker" />
    </div>

    <div v-if="isTerminal && terminal" class="mt-3 rounded-lg border border-line bg-surface-muted p-4">
      <p class="text-sm font-medium text-ink-soft">{{ terminal.label }}</p>
      <p class="mt-1 text-sm text-ink-muted">{{ terminal.description }}</p>
    </div>

    <ol v-else class="mt-4 space-y-3" role="list">
      <li v-for="(step, index) in steps" :key="step.code" class="flex items-start gap-3">
        <span
          class="mt-0.5 inline-flex size-6 shrink-0 items-center justify-center rounded-full border text-xs font-medium"
          :class="STEP_CLASS[stepState(index)]"
          aria-hidden="true"
        >
          {{ index + 1 }}
        </span>
        <div class="min-w-0">
          <p
            class="text-sm"
            :class="stepState(index) === 'todo' ? 'text-ink-faint' : 'font-medium text-ink-soft'"
            :aria-current="stepState(index) === 'current' ? 'step' : undefined"
          >
            {{ step.label }}
          </p>
          <p class="text-xs text-ink-faint">
            {{ STEP_TEXT[stepState(index)] }}
            <span v-if="stepState(index) === 'current'" class="sr-only">（当前状态）</span>
          </p>
        </div>
      </li>
    </ol>

    <p v-if="note" class="mt-4 text-xs text-ink-faint">{{ note }}</p>
  </div>
</template>
