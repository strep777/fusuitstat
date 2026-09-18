<script setup lang="ts">
/**
 * 作品相册灯箱。
 *
 * 规则（11 文档）：灯箱有焦点圈定、Esc 关闭、关闭后焦点还给触发控件；
 * 缩略图裁切不改变完整图——灯箱始终加载原图并按原始比例展示。
 * 键盘：Esc 关闭、←/→ 切换、Tab 在对话框内循环；所有按钮都有文字标签，不用图标猜意思。
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";

import type { MediaImage } from "@/modules/content/api/content";
import { useFocusTrap } from "@/shared/lib/a11y";

const props = withDefaults(
  defineProps<{
    images: MediaImage[];
    open: boolean;
    index?: number;
    /** 作品名，用于缺省替代文本 */
    title?: string;
  }>(),
  { index: 0, title: "" },
);

const emit = defineEmits<{
  close: [];
  "update:index": [value: number];
}>();

const overlay = ref<HTMLElement | null>(null);
const closeButton = ref<HTMLButtonElement | null>(null);
/** 打开灯箱前的焦点位置：关闭后必须还回去 */
const trigger = ref<HTMLElement | null>(null);

const active = computed<boolean>({
  get: () => props.open,
  set: (value) => {
    if (!value) emit("close");
  },
});

const trap = useFocusTrap(overlay, active);

const total = computed(() => props.images.length);
const currentIndex = computed(() => {
  if (total.value === 0) return 0;
  return Math.min(Math.max(props.index, 0), total.value - 1);
});
const current = computed<MediaImage | null>(() => props.images[currentIndex.value] ?? null);

function altFor(image: MediaImage, position: number): string {
  const description = image.alt?.trim();
  if (description) return description;
  return props.title ? `${props.title} 相册第 ${position} 张` : "";
}

function go(step: number): void {
  if (total.value <= 1) return;
  emit("update:index", (currentIndex.value + step + total.value) % total.value);
}

function onKeydown(event: KeyboardEvent): void {
  if (!props.open) return;
  if (event.key === "ArrowRight") {
    event.preventDefault();
    go(1);
  } else if (event.key === "ArrowLeft") {
    event.preventDefault();
    go(-1);
  }
}

function release(): void {
  document.removeEventListener("keydown", onKeydown);
  document.body.style.overflow = "";
}

watch(
  () => props.open,
  (open) => {
    if (!open) {
      release();
      trigger.value?.focus();
      trigger.value = null;
      return;
    }
    trigger.value = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    document.addEventListener("keydown", onKeydown);
    // 打开期间锁住背景滚动，关闭时恢复（不改变 layout 样式表）
    document.body.style.overflow = "hidden";
    void nextTick(() => {
      trap.activate();
      closeButton.value?.focus();
    });
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  release();
  trigger.value?.focus();
});
</script>

<template>
  <div
    v-if="open && current"
    ref="overlay"
    class="fixed inset-0 z-50 flex flex-col bg-night/95 text-canvas"
    role="dialog"
    aria-modal="true"
    :aria-label="title ? `${title} 作品相册` : '作品相册'"
    @click.self="emit('close')"
  >
    <div class="flex items-center justify-between gap-4 px-4 py-3 sm:px-6">
      <p class="text-sm tabular-nums text-canvas/70">
        第 {{ currentIndex + 1 }} / {{ total }} 张
      </p>
      <button
        ref="closeButton"
        type="button"
        class="inline-flex min-h-[44px] items-center gap-2 rounded-sm border border-night-line px-4 text-sm text-canvas transition-colors hover:border-amber-ink hover:text-amber-ink"
        @click="emit('close')"
      >
        关闭
        <span class="text-xs text-canvas/50" aria-hidden="true">Esc</span>
      </button>
    </div>

    <div class="flex min-h-0 flex-1 items-center justify-center gap-2 px-4 pb-4 sm:gap-4 sm:px-6">
      <button
        v-if="total > 1"
        type="button"
        class="inline-flex min-h-[44px] shrink-0 items-center rounded-sm border border-night-line px-3 text-sm text-canvas transition-colors hover:border-amber-ink hover:text-amber-ink"
        @click="go(-1)"
      >
        上一张
      </button>

      <img
        :src="current.src"
        :width="current.width"
        :height="current.height"
        :alt="altFor(current, currentIndex + 1)"
        decoding="async"
        class="max-h-full min-h-0 w-auto max-w-full object-contain"
      />

      <button
        v-if="total > 1"
        type="button"
        class="inline-flex min-h-[44px] shrink-0 items-center rounded-sm border border-night-line px-3 text-sm text-canvas transition-colors hover:border-amber-ink hover:text-amber-ink"
        @click="go(1)"
      >
        下一张
      </button>
    </div>

    <div v-if="total > 1" class="border-t border-night-line px-4 py-3 sm:px-6">
      <ul class="flex gap-3 overflow-x-auto pb-1">
        <li v-for="(image, position) in images" :key="image.id">
          <button
            type="button"
            class="block overflow-hidden rounded-sm border transition-colors"
            :class="position === currentIndex ? 'border-amber-ink' : 'border-night-line hover:border-amber-ink'"
            :aria-label="`查看第 ${position + 1} 张完整图`"
            :aria-current="position === currentIndex ? 'true' : undefined"
            @click="emit('update:index', position)"
          >
            <img
              :src="image.thumb ?? image.src"
              :width="image.width"
              :height="image.height"
              :alt="altFor(image, position + 1)"
              loading="lazy"
              decoding="async"
              class="size-16 object-cover"
            />
          </button>
        </li>
      </ul>
      <p class="mt-2 text-xs text-canvas/50">缩略图为裁切预览，点开显示完整图；也可以用 ← → 切换。</p>
    </div>
  </div>
</template>
