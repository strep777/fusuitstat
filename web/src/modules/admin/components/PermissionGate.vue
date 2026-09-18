<script setup lang="ts">
/**
 * 权限门（显示层）。
 *
 * 重要：UI 隐藏不能代替授权。这里只决定「是否渲染入口」，服务端仍会逐动作校验权限码。
 * 权限码来自登录响应的 `permissions[]`（见 `adminAuth` store）。
 */
import { computed } from "vue";

import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";

const props = withDefaults(
  defineProps<{
    /** 需要的权限码；默认全部满足才渲染。 */
    code: string | string[];
    /** any：满足任一即可（用于「有读或写权限都能看到」的区块）。 */
    mode?: "all" | "any";
    /** 不渲染入口时显示说明，而不是静默消失。 */
    showMissing?: boolean;
    subject?: string;
  }>(),
  { mode: "all", showMissing: true, subject: "" },
);

const auth = useAdminAuthStore();

const codes = computed(() => (Array.isArray(props.code) ? props.code : [props.code]));
const granted = computed(() =>
  props.mode === "any" ? auth.canAny(codes.value) : auth.canAll(codes.value),
);
</script>

<template>
  <slot v-if="granted" />
  <template v-else>
    <slot name="fallback">
      <div
        v-if="showMissing"
        class="rounded-lg border border-dashed border-line-strong bg-surface-muted px-5 py-4 text-sm"
        role="status"
      >
        <p class="font-medium text-ink-soft">
          {{ subject ? `${subject}：` : "" }}当前账号没有这项权限，入口已隐藏
        </p>
        <p class="mt-1 text-xs text-ink-faint">
          需要的权限码：<span class="font-mono">{{ codes.join("、") }}</span>
        </p>
        <p class="mt-1 text-xs text-ink-faint">
          界面隐藏不能代替授权：即使直接调用接口，服务端仍会逐动作校验权限码并记录审计。
        </p>
      </div>
    </slot>
  </template>
</template>
