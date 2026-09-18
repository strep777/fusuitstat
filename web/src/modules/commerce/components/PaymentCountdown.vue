<script setup lang="ts">
/**
 * 付款到期倒计时。
 *
 * 规则（06/11 文档）：付款到期只表示「可以继续尝试支付的窗口」，到期不等于支付失败或订单取消；
 * 因此组件只输出剩余时间文案，结果判定由父级根据服务端查单状态决定。
 * 倒计时每秒刷新，卸载时清理定时器；到期只发出一次 `expired` 事件。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { formatCountdown, isExpired } from "@/shared/lib/format";

const props = withDefaults(
  defineProps<{
    /** 服务端返回的付款到期时间。 */
    deadline: string | number | null | undefined;
    label?: string;
    /** 到期后的说明；不得写成“已取消”。 */
    expiredHint?: string;
  }>(),
  {
    label: "付款剩余时间",
    expiredHint: "付款窗口已到期。付款结果以服务端查单为准，请重新查询。",
  },
);

const emit = defineEmits<{ (event: "expired"): void }>();

const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;
let announced = false;

function tick(): void {
  now.value = Date.now();
  if (!announced && expired.value) {
    announced = true;
    emit("expired");
  }
}

const expired = computed(() => isExpired(props.deadline, now.value));
const text = computed(() => formatCountdown(props.deadline, now.value));
const hasDeadline = computed(
  () => props.deadline !== null && props.deadline !== undefined && props.deadline !== "",
);

onMounted(() => {
  timer = setInterval(tick, 1000);
  tick();
});

onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
  timer = null;
});

// 服务端换发新的到期时间时重新播报
watch(
  () => props.deadline,
  () => {
    announced = false;
    now.value = Date.now();
  },
);
</script>

<template>
  <p
    class="flex flex-wrap items-baseline gap-2 text-sm"
    role="timer"
    :aria-label="`${label}：${hasDeadline ? text : '服务端未返回到期时间'}`"
  >
    <span class="text-ink-muted">{{ label }}</span>
    <span v-if="hasDeadline" class="font-display text-lg tabular-nums" :class="expired ? 'text-wine' : 'text-ink'">
      {{ text }}
    </span>
    <span v-else class="text-ink-faint">服务端未返回到期时间</span>
  </p>
  <p v-if="expired && hasDeadline" class="mt-2 rounded-sm bg-wine-soft px-3 py-2 text-xs text-wine" role="status">
    {{ expiredHint }}
  </p>
</template>
