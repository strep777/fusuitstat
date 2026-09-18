<script setup lang="ts">
/**
 * 发送验证码倒计时按钮。
 *
 * 规则（10 文档）：重发间隔由服务端 `retry_after` 决定，前端只做展示与防连点；
 * 倒计时期间按钮禁用，并在按钮旁给出原因「倒计时 N 秒」（11 文档：禁用按钮旁说明原因）。
 */
import { computed, onBeforeUnmount, ref } from "vue";

import { AppButton } from "@/shared/ui/base";

const props = withDefaults(
  defineProps<{
    /** 空闲态文案。 */
    label?: string;
    /** 倒计时中的文案模板，`{seconds}` 会被替换为剩余秒数。 */
    countingTemplate?: string;
    seconds?: number;
    disabled?: boolean;
    /** 按钮自身不可用时的原因（与倒计时原因分开显示）。 */
    disabledReason?: string;
    loading?: boolean;
    size?: "sm" | "md" | "lg";
  }>(),
  {
    label: "获取验证码",
    countingTemplate: "{seconds} 秒后可重发",
    seconds: 0,
    disabled: false,
    disabledReason: "",
    loading: false,
    size: "md",
  },
);

const emit = defineEmits<{ send: [] }>();

const remaining = ref(Math.max(0, Math.floor(props.seconds)));
let handle: ReturnType<typeof setInterval> | null = null;

const counting = computed(() => remaining.value > 0);

const text = computed(() =>
  counting.value
    ? props.countingTemplate.replace("{seconds}", String(remaining.value))
    : props.disabled
      ? "暂不可发送"
      : props.label,
);

/** 禁用原因：倒计时优先展示剩余秒数。 */
const reason = computed(() => {
  if (counting.value) return `倒计时 ${remaining.value} 秒，结束后可重新发送。`;
  return props.disabled ? props.disabledReason : "";
});

const blocked = computed(() => props.disabled || props.loading || counting.value);

function stop(): void {
  if (handle !== null) {
    clearInterval(handle);
    handle = null;
  }
}

/** 发送成功后由父组件用服务端 `retry_after` 启动倒计时。 */
function start(seconds: number): void {
  stop();
  const total = Number.isFinite(seconds) ? Math.max(0, Math.floor(seconds)) : 0;
  remaining.value = total;
  if (total <= 0) return;
  handle = setInterval(() => {
    remaining.value = Math.max(0, remaining.value - 1);
    if (remaining.value === 0) stop();
  }, 1000);
}

onBeforeUnmount(stop);

defineExpose({ start, stop });
</script>

<template>
  <div class="inline-flex flex-col gap-1">
    <AppButton
      type="button"
      variant="secondary"
      :size="size"
      :disabled="blocked"
      :loading="loading"
      :aria-label="counting ? `重新发送验证码，剩余 ${remaining} 秒` : label"
      @click="emit('send')"
    >
      {{ text }}
    </AppButton>
    <span v-if="reason" class="text-xs text-ink-faint" aria-live="polite">{{ reason }}</span>
  </div>
</template>
