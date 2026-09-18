/**
 * 轻量提示（toast）。用于操作结果反馈；错误提示带可选的恢复入口，
 * 不使用颜色单独表达语义（11 文档：错误贴字段、说明可重试动作）。
 */

import { ref } from "vue";

export type ToastTone = "success" | "error" | "info";

export interface ToastItem {
  id: number;
  tone: ToastTone;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

const items = ref<ToastItem[]>([]);
let sequence = 0;

function push(item: Omit<ToastItem, "id">, timeoutMs = 6000): number {
  const id = ++sequence;
  items.value = [...items.value, { ...item, id }];
  if (timeoutMs > 0) {
    setTimeout(() => dismiss(id), timeoutMs);
  }
  return id;
}

function dismiss(id: number): void {
  items.value = items.value.filter((item) => item.id !== id);
}

export function useToast() {
  return {
    items,
    dismiss,
    success: (title: string, description?: string) => push({ tone: "success", title, description }),
    info: (title: string, description?: string) => push({ tone: "info", title, description }),
    error: (title: string, description?: string, action?: { label: string; run: () => void }) =>
      push(
        {
          tone: "error",
          title,
          description,
          actionLabel: action?.label,
          onAction: action?.run,
        },
        9000,
      ),
  };
}
