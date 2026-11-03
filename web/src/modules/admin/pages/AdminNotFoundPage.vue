<script setup lang="ts">
/**
 * 后台 404：路径不存在时给出返回概览与退出登录的出口，不自动跳转。
 * 若当前员工连概览权限都没有，就退回登录页，避免把人留在死循环里。
 */
import { computed } from "vue";
import { useRouter } from "vue-router";

import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, EmptyState } from "@/shared/ui/base";

const auth = useAdminAuthStore();
const router = useRouter();

const canOpenDashboard = computed(() => auth.can(ADMIN_PERMISSIONS.dashboard));

async function backToDashboard(): Promise<void> {
  await router.replace({ name: "admin-dashboard" });
}

async function signOut(): Promise<void> {
  await auth.logout();
  await router.replace({ name: "admin-login" });
}
</script>

<template>
  <div class="space-y-6">
    <EmptyState
      tone="not-found"
      title="这个后台路径不存在"
      description="链接可能已经失效，或者对应的模块还没有上线。这里不会自动跳转，你可以自己选择下一步。"
    >
      <AppButton v-if="canOpenDashboard" size="sm" @click="backToDashboard">返回运营概览</AppButton>
      <AppButton size="sm" variant="secondary" @click="signOut">退出登录</AppButton>
    </EmptyState>

    <p class="text-xs text-ink-faint">
      当前账号角色：{{ auth.roleLabel }}。若你认为这个路径应该存在，请把上面的地址与出错时间告诉管理员，
      审计记录里会保留请求痕迹。
    </p>
  </div>
</template>

<script lang="ts">
/**
 * 后台页面公共区块（具名导出）。
 *
 * 为什么放在这个文件里：本项目只允许创建约定的文件清单，公共区块不单独建文件；
 * 具名导出必须写在普通 `<script>` 块里，配合 `<script setup>` 一起使用。
 * 使用方式：`import { AdminPageHeader, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";`
 */
import { defineComponent, h, type PropType, type VNodeChild } from "vue";

import { SurfaceCard } from "@/shared/ui/base";

const NOTICE_CLASS: Record<string, string> = {
  info: "border-ink bg-surface-muted text-ink-soft",
  warn: "border-amber bg-amber-soft text-amber-strong",
  danger: "border-wine bg-wine-soft text-wine",
};

const NOTICE_MARK: Record<string, string> = { info: "说明", warn: "注意", danger: "禁止" };

/** 页面顶部的说明条：把「服务端仍会逐动作校验」「禁止修改已付金额」这类硬约束写在界面上。 */
export const InlineNotice = defineComponent({
  name: "InlineNotice",
  props: {
    tone: { type: String as PropType<"info" | "warn" | "danger">, default: "info" },
    title: { type: String, required: true },
    description: { type: String, default: "" },
  },
  setup(props, { slots }) {
    return () =>
      h(
        "div",
        {
          class: ["border-2 border-l-[6px] px-4 py-3 text-sm", NOTICE_CLASS[props.tone] ?? NOTICE_CLASS.info],
          role: "note",
        },
        [
          h("p", { class: "font-mono text-[10px] font-bold uppercase tracking-[0.16em]" }, `[${NOTICE_MARK[props.tone] ?? "说明"}] ${props.title}`),
          props.description ? h("p", { class: "mt-1 text-xs leading-relaxed" }, props.description) : null,
          slots.default ? h("div", { class: "mt-3 flex flex-wrap gap-3" }, slots.default()) : null,
        ],
      );
  },
});

/** 页面标题区：标题、说明、右侧动作。 */
export const AdminPageHeader = defineComponent({
  name: "AdminPageHeader",
  props: {
    title: { type: String, required: true },
    description: { type: String, default: "" },
    eyebrow: { type: String, default: "" },
  },
  setup(props, { slots }) {
    return () =>
      h("header", { class: "flex flex-col gap-4 border-b-2 border-ink pb-5 lg:flex-row lg:items-end lg:justify-between" }, [
        h("div", {}, [
          props.eyebrow ? h("p", { class: "font-mono text-[10px] font-bold uppercase tracking-[0.2em] text-brand-strong" }, props.eyebrow) : null,
          h("h1", { class: "mt-2 font-display text-3xl font-bold tracking-[-0.03em] sm:text-4xl" }, props.title),
          props.description ? h("p", { class: "mt-2 max-w-3xl text-sm leading-relaxed text-ink-muted" }, props.description) : null,
        ]),
        slots.actions ? h("div", { class: "flex flex-wrap items-center gap-3" }, slots.actions()) : null,
      ]);
  },
});

/** 只读字段展示：详情页统一「字段名 → 值」，缺值显示 — 而不是留空。 */
export const FieldList = defineComponent({
  name: "FieldList",
  props: {
    items: {
      type: Array as PropType<Array<{ label: string; value: string | number | null | undefined }>>,
      required: true,
    },
    columns: { type: Number, default: 2 },
  },
  setup(props) {
    return () =>
      h(
        "dl",
        {
          class: [
            "grid gap-x-6 gap-y-3 text-sm",
            props.columns >= 3 ? "sm:grid-cols-3" : props.columns === 2 ? "sm:grid-cols-2" : "",
          ],
        },
        props.items.map((item) =>
          h("div", { class: "min-w-0" }, [
            h("dt", { class: "font-mono text-[10px] uppercase tracking-wide text-ink-faint" }, item.label),
            h(
              "dd",
              { class: "mt-0.5 break-words text-ink-soft" },
              item.value === null || item.value === undefined || item.value === "" ? "—" : String(item.value),
            ),
          ]),
        ),
      );
  },
});

/** 区块卡片：标题 + 状态 + 内容，用于把「模块未就绪」和真实数据区分开。 */
export const ModuleCard = defineComponent({
  name: "ModuleCard",
  props: {
    title: { type: String, required: true },
    status: { type: String, default: "" },
  },
  setup(props, { slots }) {
    return () =>
      h(SurfaceCard, {}, () => [
        h("div", { class: "flex items-baseline justify-between gap-3" }, [
          h("h2", { class: "font-display text-lg" }, props.title),
          props.status ? h("span", { class: "eyebrow" }, props.status) : null,
        ]),
        h("div", { class: "mt-4" }, slots.default?.() ?? []),
      ]);
  },
});

/** 过滤/搜索表单容器：字段与按钮分两行，键盘顺序与视觉顺序一致。 */
export const FilterBar = defineComponent({
  name: "FilterBar",
  props: { description: { type: String, default: "" } },
  setup(props, { slots }) {
    const children: VNodeChild[] = [slots.default?.() ?? []];
    if (props.description) {
      children.push(h("p", { class: "text-xs text-ink-faint" }, props.description));
    }
    return () =>
      h(
        "div",
        { class: "grid gap-4 border-2 border-ink bg-surface p-5 shadow-[4px_4px_0_var(--color-ink)] sm:grid-cols-2 lg:grid-cols-4" },
        children,
      );
  },
});
</script>
