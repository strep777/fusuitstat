<script setup lang="ts">
/**
 * 危险动作确认对话框。
 *
 * 约束（11 文档）：后台危险动作必须显示**具体对象、金额/数量与后果**，不能只有泛用「确定」；
 * 退款批准、积分调整批准、员工权限变更、账号停用、库存调整、发布与下架都必须填写理由。
 * 键盘：Esc 关闭并把焦点还给原控件；打开时焦点圈定在对话框内。
 */
import { computed, ref, watch } from "vue";

import { AppButton, FieldRow } from "@/shared/ui/base";
import { useFocusTrap } from "@/shared/lib/a11y";

const open = defineModel<boolean>({ required: true });

const props = withDefaults(
  defineProps<{
    title: string;
    /** 具体对象，例如「订单 SO-2026-0007」「员工 张工」。 */
    target: string;
    /** 当前状态与将要发生的状态变化。 */
    change: string;
    /** 后果说明：不可撤销、通知谁、影响哪些记录。 */
    consequence: string;
    /** 金额/数量：已格式化的字符串，例如「¥1,280.00」「-3 件」。 */
    amount?: string;
    confirmLabel: string;
    /** 破坏性动作用 danger；发布等中性但重要的动作用 primary。 */
    tone?: "danger" | "primary";
    reasonLabel?: string;
    reasonMin?: number;
    reasonPlaceholder?: string;
    /** 高风险动作要求的二次输入，例如输入订单号。 */
    requireTyped?: string;
    typedLabel?: string;
    pending?: boolean;
    disabledReason?: string;
  }>(),
  {
    amount: "",
    tone: "danger",
    reasonLabel: "操作理由（会写入审计记录）",
    reasonMin: 6,
    reasonPlaceholder: "说明依据，例如工单号、沟通结论、渠道查单结果",
    requireTyped: "",
    typedLabel: "",
    pending: false,
    disabledReason: "",
  },
);

const emit = defineEmits<{
  (event: "confirm", payload: { reason: string; typed: string }): void;
  (event: "cancel"): void;
}>();

const reason = ref("");
const typed = ref("");
const panelRef = ref<HTMLElement | null>(null);
const reasonInputRef = ref<HTMLTextAreaElement | null>(null);
let previousActive: HTMLElement | null = null;

const trap = useFocusTrap(panelRef, open);

const typedMatches = computed(() => !props.requireTyped || typed.value.trim() === props.requireTyped.trim());
const reasonOk = computed(() => reason.value.trim().length >= props.reasonMin);
const canConfirm = computed(() => reasonOk.value && typedMatches.value && !props.pending && !props.disabledReason);

const disabledReason = computed(() => {
  if (props.disabledReason) return props.disabledReason;
  if (!reasonOk.value) return `请先填写至少 ${props.reasonMin} 个字的理由`;
  if (!typedMatches.value) return `请输入「${props.requireTyped}」以确认`;
  return "";
});

watch(open, (value) => {
  if (value) {
    previousActive = document.activeElement as HTMLElement | null;
    reason.value = "";
    typed.value = "";
    trap.activate();
    requestAnimationFrame(() => reasonInputRef.value?.focus());
    document.addEventListener("keydown", onKeydown);
  } else {
    document.removeEventListener("keydown", onKeydown);
    previousActive?.focus();
    previousActive = null;
    emit("cancel");
  }
});

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && !props.pending) {
    event.stopPropagation();
    open.value = false;
  }
}

function submit(): void {
  if (!canConfirm.value) return;
  emit("confirm", { reason: reason.value.trim(), typed: typed.value.trim() });
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open"
      class="fixed inset-0 z-50 flex items-end justify-center bg-night/50 p-0 sm:items-center sm:p-6"
      @click.self="!pending && (open = false)"
    >
      <div
        ref="panelRef"
        role="alertdialog"
        aria-modal="true"
        :aria-label="title"
        class="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-t-lg border border-line bg-surface p-6 shadow-lift sm:rounded-lg"
      >
        <p class="eyebrow">{{ tone === "danger" ? "危险动作确认" : "重要动作确认" }}</p>
        <h2 class="mt-2 font-display text-xl">{{ title }}</h2>

        <dl class="mt-5 space-y-3 rounded-md border border-line bg-surface-muted p-4 text-sm">
          <div class="flex flex-wrap items-baseline gap-2">
            <dt class="shrink-0 text-ink-faint">操作对象</dt>
            <dd class="font-medium text-ink" data-testid="confirm-target">{{ target }}</dd>
          </div>
          <div class="flex flex-wrap items-baseline gap-2">
            <dt class="shrink-0 text-ink-faint">状态变化</dt>
            <dd class="text-ink-soft">{{ change }}</dd>
          </div>
          <div v-if="amount" class="flex flex-wrap items-baseline gap-2">
            <dt class="shrink-0 text-ink-faint">金额 / 数量</dt>
            <dd class="font-display text-lg tabular-nums text-ink" data-testid="confirm-amount">{{ amount }}</dd>
          </div>
          <div class="flex flex-wrap items-baseline gap-2">
            <dt class="shrink-0 text-ink-faint">后果</dt>
            <dd class="text-ink-soft" data-testid="confirm-consequence">{{ consequence }}</dd>
          </div>
        </dl>

        <div class="mt-5 space-y-4">
          <FieldRow :label="reasonLabel" :required="true" for="confirm-reason">
            <textarea
              id="confirm-reason"
              ref="reasonInputRef"
              v-model="reason"
              rows="3"
              class="field-base resize-y"
              :placeholder="reasonPlaceholder"
            />
            <p id="confirm-reason-hint" class="text-xs text-ink-faint">
              已填写 {{ reason.trim().length }} 字，至少 {{ reasonMin }} 字。理由会写入审计记录，事后可追溯。
            </p>
          </FieldRow>

          <FieldRow v-if="requireTyped" :label="typedLabel || `请输入「${requireTyped}」确认`" :required="true">
            <input v-model="typed" type="text" class="field-base font-mono" :placeholder="requireTyped" />
          </FieldRow>
        </div>

        <div class="mt-6 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-end">
          <p v-if="disabledReason" class="text-xs text-ink-faint sm:mr-auto">{{ disabledReason }}</p>
          <AppButton variant="secondary" :disabled="pending" @click="open = false">取消，不做任何修改</AppButton>
          <AppButton
            :variant="tone === 'danger' ? 'danger' : 'primary'"
            :disabled="!canConfirm"
            :loading="pending"
            :disabled-reason="disabledReason"
            @click="submit"
          >
            {{ confirmLabel }}
          </AppButton>
        </div>

        <p class="mt-4 text-xs text-ink-faint">
          对话框只是二次确认；服务端仍会按权限码与状态机逐次校验，界面隐藏不能代替授权。
        </p>
      </div>
    </div>
  </Teleport>
</template>
