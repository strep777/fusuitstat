/**
 * 无障碍与常用组合式工具。
 */

import { onBeforeUnmount, onMounted, ref, type Ref } from "vue";

/** 媒体查询。用于响应式布局与减弱动效。 */
export function useMediaQuery(query: string): Ref<boolean> {
  const matches = ref(false);
  let mql: MediaQueryList | null = null;
  const handler = (event: MediaQueryListEvent | MediaQueryList) => {
    matches.value = event.matches;
  };
  onMounted(() => {
    if (typeof matchMedia !== "function") return;
    mql = matchMedia(query);
    handler(mql);
    mql.addEventListener("change", handler);
  });
  onBeforeUnmount(() => {
    mql?.removeEventListener("change", handler);
  });
  return matches;
}

/** 焦点圈定：对话框打开时把焦点移入，关闭后还回原控件（11 文档）。 */
export function useFocusTrap(containerRef: Ref<HTMLElement | null>, active: Ref<boolean>) {
  let previous: HTMLElement | null = null;

  function focusables(): HTMLElement[] {
    const root = containerRef.value;
    if (!root) return [];
    return Array.from(
      root.querySelectorAll<HTMLElement>(
        'a[href], button:not([disabled]), textarea, input, select, [tabindex]:not([tabindex="-1"])',
      ),
    ).filter((element) => element.offsetParent !== null);
  }

  function onKeydown(event: KeyboardEvent): void {
    if (event.key !== "Tab") return;
    const list = focusables();
    if (list.length === 0) return;
    const first = list[0]!;
    const last = list[list.length - 1]!;
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  }

  function onEscape(event: KeyboardEvent): void {
    if (event.key === "Escape") {
      active.value = false;
    }
  }

  onMounted(() => {
    document.addEventListener("keydown", onKeydown);
    document.addEventListener("keydown", onEscape);
  });
  onBeforeUnmount(() => {
    document.removeEventListener("keydown", onKeydown);
    document.removeEventListener("keydown", onEscape);
    previous?.focus();
  });

  return {
    activate() {
      previous = document.activeElement as HTMLElement | null;
      requestAnimationFrame(() => focusables()[0]?.focus());
    },
  };
}

/** 延迟显示加载态：等待少于 150ms 不显示闪烁加载（11 文档）。 */
export function useDelayedFlag(source: Ref<boolean>, delayMs = 150): Ref<boolean> {
  const visible = ref(false);
  let handle: ReturnType<typeof setTimeout> | null = null;
  const stop = (value: boolean) => {
    if (handle) {
      clearTimeout(handle);
      handle = null;
    }
    if (!value) {
      visible.value = false;
      return;
    }
    handle = setTimeout(() => {
      visible.value = true;
    }, delayMs);
  };
  const watcher = () => stop(source.value);
  onMounted(() => {
    const timer = setInterval(watcher, 50);
    onBeforeUnmount(() => clearInterval(timer));
  });
  return visible;
}

/** 超时提示：超过 8 秒给可操作提示，不让用户无限等待。 */
export function useSlowHint(source: Ref<boolean>, afterMs = 8000): Ref<boolean> {
  const slow = ref(false);
  let handle: ReturnType<typeof setTimeout> | null = null;
  const timer = setInterval(() => {
    if (source.value && !handle && !slow.value) {
      handle = setTimeout(() => {
        slow.value = true;
      }, afterMs);
    }
    if (!source.value) {
      if (handle) clearTimeout(handle);
      handle = null;
      slow.value = false;
    }
  }, 100);
  onBeforeUnmount(() => {
    clearInterval(timer);
    if (handle) clearTimeout(handle);
  });
  return slow;
}

/** 图片替代文本规则：描述角色与角度；纯装饰为空（11 文档）。 */
export function decorativeAlt(isDecorative: boolean, description: string): string {
  return isDecorative ? "" : description;
}
