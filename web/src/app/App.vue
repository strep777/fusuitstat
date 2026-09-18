<script setup lang="ts">
/**
 * 应用根组件：全局提示（toast）与路由视图。
 * 提示条用 role=status/alert，使读屏可感知；不使用颜色单独表达错误。
 */
import { computed } from "vue";
import { RouterView } from "vue-router";

import { useToast } from "@/shared/lib/toast";

const toast = useToast();
const visible = computed(() => toast.items.value.length > 0);
</script>

<template>
  <RouterView />
  <div
    v-if="visible"
    class="pointer-events-none fixed inset-x-0 bottom-4 z-50 flex flex-col items-center gap-2 px-4"
    aria-live="polite"
  >
    <div
      v-for="item in toast.items.value"
      :key="item.id"
      class="pointer-events-auto w-full max-w-lg rounded-md border bg-surface px-4 py-3 text-sm shadow-lift"
      :class="{
        'border-jade text-ink': item.tone === 'success',
        'border-wine text-ink': item.tone === 'error',
        'border-line-strong text-ink': item.tone === 'info',
      }"
      :role="item.tone === 'error' ? 'alert' : 'status'"
    >
      <div class="flex items-start gap-3">
        <span class="mt-0.5 shrink-0 text-xs font-semibold" aria-hidden="true">
          {{ item.tone === "success" ? "已完成" : item.tone === "error" ? "未完成" : "提示" }}
        </span>
        <div class="flex-1">
          <p class="font-medium">{{ item.title }}</p>
          <p v-if="item.description" class="mt-1 text-ink-muted">{{ item.description }}</p>
          <button
            v-if="item.actionLabel && item.onAction"
            type="button"
            class="mt-2 text-xs font-medium text-amber-strong link-underline"
            @click="item.onAction()"
          >
            {{ item.actionLabel }}
          </button>
        </div>
        <button
          type="button"
          class="shrink-0 text-ink-faint hover:text-ink"
          aria-label="关闭提示"
          @click="toast.dismiss(item.id)"
        >
          ✕
        </button>
      </div>
    </div>
  </div>
</template>
