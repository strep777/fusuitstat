<script setup lang="ts">
/**
 * 6 位短信验证码输入。
 *
 * 无障碍与可用性（11 文档）：一次性验证码自动填充 `autocomplete="one-time-code"`、
 * `inputmode="numeric"`、支持粘贴整段验证码、逐格退格、保留前导零，
 * 读屏只读一个输入框，视觉上的六格是 `aria-hidden` 的展示层。
 */
import { computed, nextTick, ref, useId, watch } from "vue";

const props = withDefaults(
  defineProps<{
    modelValue: string;
    length?: number;
    label?: string;
    hint?: string;
    error?: string;
    disabled?: boolean;
    /** 关联外部 label 的 id；留空时使用组件内部生成的 id。 */
    inputId?: string;
  }>(),
  {
    length: 6,
    label: "短信验证码",
    hint: "",
    error: "",
    disabled: false,
    inputId: "",
  },
);

const emit = defineEmits<{
  "update:modelValue": [value: string];
  complete: [value: string];
}>();

const generatedId = useId();
const fieldId = computed(() => props.inputId || `otp-${generatedId}`);
const errorId = computed(() => `${fieldId.value}-error`);
const hintId = computed(() => `${fieldId.value}-hint`);

const inputRef = ref<HTMLInputElement | null>(null);

const digits = computed<string[]>(() => {
  const chars = props.modelValue.replace(/\D/g, "").slice(0, props.length).split("");
  return Array.from({ length: props.length }, (_, index) => chars[index] ?? "");
});

const activeIndex = computed(() => Math.min(props.modelValue.replace(/\D/g, "").length, props.length - 1));

const describedBy = computed(() => {
  const ids = [props.error ? errorId.value : "", props.hint ? hintId.value : ""].filter(Boolean);
  return ids.length ? ids.join(" ") : undefined;
});

function clean(raw: string): string {
  return raw.replace(/\D/g, "").slice(0, props.length);
}

function commit(raw: string): void {
  const value = clean(raw);
  emit("update:modelValue", value);
  if (value.length === props.length) emit("complete", value);
}

function onInput(event: Event): void {
  const target = event.target as HTMLInputElement;
  const value = clean(target.value);
  // 受控回写：拒绝非数字字符后把显示值拉回干净状态。
  if (target.value !== value) target.value = value;
  commit(value);
}

function onPaste(event: ClipboardEvent): void {
  if (props.disabled) return;
  event.preventDefault();
  const text = event.clipboardData?.getData("text") ?? "";
  commit(text);
  void nextTick(() => inputRef.value?.focus());
}

function onKeydown(event: KeyboardEvent): void {
  if (props.disabled) return;
  if (event.key === "Escape") {
    commit("");
    return;
  }
  if (event.key !== "Backspace" && event.key !== "Delete") return;
  const input = event.target as HTMLInputElement;
  // 选区非空时交给浏览器处理，保持常规编辑行为。
  if (input.selectionStart !== input.selectionEnd) return;
  const caret = input.selectionStart ?? input.value.length;
  const value = clean(input.value);

  // Delete：中间位置只删光标右侧一格，避免一次清空整串。
  if (event.key === "Delete") {
    if (caret < value.length) {
      event.preventDefault();
      commit(value.slice(0, caret) + value.slice(caret + 1));
    }
    return;
  }

  // 退格：先让模型跟上浏览器的删除结果（逐格退格、前导零都按字符串处理），
  // 再把焦点留在验证码输入上，键盘用户不需要重新 Tab 回来。
  void nextTick(() => {
    commit(input.value);
    inputRef.value?.focus();
  });
}

function focus(): void {
  inputRef.value?.focus();
}

/** 清空并聚焦：验证码错误时由父组件调用。 */
function reset(): void {
  commit("");
  void nextTick(() => inputRef.value?.focus());
}

watch(
  () => props.disabled,
  (disabled) => {
    if (!disabled) void nextTick(() => inputRef.value?.focus());
  },
);

defineExpose({ focus, reset });
</script>

<template>
  <div class="space-y-1.5">
    <label :for="fieldId" class="block text-sm font-medium text-ink-soft">
      {{ label }}
      <span class="ml-2 font-normal text-ink-faint">{{ length }} 位数字</span>
    </label>

    <div
      class="relative flex items-center gap-2"
      :class="disabled ? 'cursor-not-allowed opacity-60' : 'cursor-text'"
      @click="focus()"
    >
      <!-- 视觉格：纯展示，读屏忽略 -->
      <span
        v-for="(digit, index) in digits"
        :key="index"
        aria-hidden="true"
        class="flex h-12 w-10 items-center justify-center rounded-sm border bg-surface font-mono text-lg tabular-nums sm:w-11"
        :class="[
          error ? 'border-wine text-wine' : 'border-line-strong text-ink',
          index === activeIndex && !disabled ? 'border-amber ring-2 ring-amber-soft' : '',
          digit ? '' : 'text-ink-faint',
        ]"
      >
        {{ digit || "·" }}
      </span>

      <input
        :id="fieldId"
        ref="inputRef"
        class="absolute inset-0 h-full w-full cursor-text bg-transparent text-transparent caret-transparent opacity-0 focus:outline-none"
        type="text"
        inputmode="numeric"
        autocomplete="one-time-code"
        autocapitalize="off"
        autocorrect="off"
        spellcheck="false"
        :maxlength="length"
        :value="modelValue"
        :disabled="disabled"
        :aria-invalid="error ? 'true' : undefined"
        :aria-describedby="describedBy"
        :aria-label="label"
        @input="onInput"
        @paste="onPaste"
        @keydown="onKeydown"
      />
    </div>

    <p v-if="hint" :id="hintId" class="text-xs text-ink-faint">{{ hint }}</p>
    <p v-if="error" :id="errorId" class="text-xs font-medium text-wine" role="alert">{{ error }}</p>
  </div>
</template>
