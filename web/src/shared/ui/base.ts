/**
 * 基础控件：按钮、标签、卡片、徽章、空态、错误态与骨架屏。
 *
 * 约束（11 文档）：按钮触控范围至少 44×44px；不能靠颜色单独表达售罄/已付款/错误；
 * 禁用按钮旁说明原因；每个数据区域都有 loading/empty/error/forbidden/not-found/success 状态。
 * 视觉 v2：胶囊按钮、圆润无衬线标题、珊瑚红主色，深色主题共用品牌按钮色。
 */

import { computed, defineComponent, h, type PropType, type VNodeChild } from "vue";
import { RouterLink } from "vue-router";

import { StarLoader } from "@/shared/ui/BrandMark";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "quiet";
export type ButtonSize = "sm" | "md" | "lg";

const VARIANT_CLASS: Record<ButtonVariant, string> = {
  primary:
    "bg-brand text-white border-2 border-ink shadow-[4px_4px_0_var(--color-ink)] hover:bg-brand-hover hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
  secondary:
    "bg-surface text-ink border-2 border-ink shadow-[4px_4px_0_var(--color-ink)] hover:bg-lemon-soft hover:-translate-y-0.5 active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
  ghost: "bg-transparent text-ink-soft border-2 border-transparent hover:border-ink hover:bg-lemon-soft",
  danger:
    "bg-wine text-white border-2 border-ink shadow-[4px_4px_0_var(--color-ink)] hover:opacity-90 active:translate-x-[2px] active:translate-y-[2px] active:shadow-none",
  quiet: "bg-canvas-sunk text-ink-soft border-2 border-transparent hover:border-ink hover:bg-lemon-soft",
};

const SIZE_CLASS: Record<ButtonSize, string> = {
  sm: "min-h-[44px] px-4 text-sm",
  md: "min-h-[46px] px-6 text-sm",
  lg: "min-h-[54px] px-8 text-base",
};

export const AppButton = defineComponent({
  name: "AppButton",
  props: {
    variant: { type: String as PropType<ButtonVariant>, default: "primary" },
    size: { type: String as PropType<ButtonSize>, default: "md" },
    type: { type: String as PropType<"button" | "submit" | "reset">, default: "button" },
    to: { type: String, default: "" },
    href: { type: String, default: "" },
    disabled: { type: Boolean, default: false },
    loading: { type: Boolean, default: false },
    block: { type: Boolean, default: false },
    /** 禁用原因：必须显示在按钮旁，而不是只有泛用“确定” */
    disabledReason: { type: String, default: "" },
    ariaLabel: { type: String, default: "" },
  },
  emits: ["click"],
  setup(props, { slots, emit }) {
    const classes = computed(() => [
      "inline-flex items-center justify-center gap-2 rounded-full font-semibold transition-all duration-150",
      "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2",
      VARIANT_CLASS[props.variant],
      SIZE_CLASS[props.size],
      props.block ? "w-full" : "",
      props.disabled || props.loading ? "cursor-not-allowed opacity-60" : "",
    ]);

    return () => {
      const content: VNodeChild[] = [];
      if (props.loading) {
        content.push(h(StarLoader, { size: 18, inline: true, message: "" }));
      }
      content.push(slots.default ? slots.default() : null);

      const common = {
        class: classes.value,
        "aria-disabled": props.disabled || props.loading ? "true" : undefined,
        "aria-label": props.ariaLabel || undefined,
        title: props.disabled && props.disabledReason ? props.disabledReason : undefined,
      };

      let node: VNodeChild;
      if (props.to && !props.disabled) {
        node = h(RouterLink, { to: props.to, class: classes.value, "aria-label": props.ariaLabel }, () => content);
      } else if (props.href && !props.disabled) {
        node = h(
          "a",
          { ...common, href: props.href, rel: "noopener noreferrer", target: "_blank" },
          content,
        );
      } else {
        node = h(
          "button",
          {
            ...common,
            type: props.type,
            disabled: props.disabled || props.loading,
            onClick: (event: MouseEvent) => {
              if (props.disabled || props.loading) {
                event.preventDefault();
                return;
              }
              emit("click", event);
            },
          },
          content,
        );
      }

      if (props.disabled && props.disabledReason) {
        return h("span", { class: "inline-flex flex-col gap-1" }, [
          node,
          h("span", { class: "text-xs text-ink-faint" }, props.disabledReason),
        ]);
      }
      return node;
    };
  },
});

export type BadgeTone = "neutral" | "amber" | "jade" | "wine" | "night" | "grape" | "sky" | "lemon";

const BADGE_TONE: Record<BadgeTone, string> = {
  neutral: "bg-surface-muted text-ink-muted border-line-strong",
  amber: "bg-amber-soft text-amber-ink border-amber/30",
  jade: "bg-jade-soft text-jade border-jade/30",
  wine: "bg-wine-soft text-wine border-wine/30",
  night: "bg-night text-canvas border-night-line",
  grape: "bg-grape-soft text-grape border-grape/30",
  sky: "bg-sky-soft text-sky border-sky/30",
  lemon: "bg-lemon-soft text-amber-ink border-lemon/40",
};

export const StatusBadge = defineComponent({
  name: "StatusBadge",
  props: {
    tone: { type: String as PropType<BadgeTone>, default: "neutral" },
    /** 文本必须自解释状态，不依赖颜色（11 文档） */
    label: { type: String, required: true },
    icon: { type: String, default: "" },
  },
  setup(props) {
    return () =>
      h(
        "span",
        {
          class: [
            "inline-flex items-center gap-1 rounded-full border-2 px-2.5 py-1 font-mono text-[11px] font-bold uppercase tracking-wide",
            BADGE_TONE[props.tone],
          ],
        },
        [props.icon ? h("span", { "aria-hidden": "true" }, props.icon) : null, props.label],
      );
  },
});

export const SurfaceCard = defineComponent({
  name: "SurfaceCard",
  props: {
    as: { type: String, default: "section" },
    padded: { type: Boolean, default: true },
    bordered: { type: Boolean, default: true },
    interactive: { type: Boolean, default: false },
  },
  setup(props, { slots }) {
    return () =>
      h(
        props.as,
        {
          class: [
            "rounded-lg bg-surface",
            props.bordered ? "border-2 border-ink" : "",
            props.padded ? "p-5 sm:p-6" : "",
            props.interactive
              ? "transition-all duration-200 hover:-translate-y-1 hover:border-ink hover:shadow-[6px_6px_0_var(--color-ink)] focus-within:shadow-[6px_6px_0_var(--color-ink)]"
              : "shadow-quiet",
          ],
        },
        slots.default?.(),
      );
  },
});

export const SectionHeading = defineComponent({
  name: "SectionHeading",
  props: {
    eyebrow: { type: String, default: "" },
    title: { type: String, required: true },
    description: { type: String, default: "" },
    /** 标题层级。模板里写成 `level="3"` 时 Vue 传字符串，这里统一归一化。 */
    level: { type: [Number, String] as PropType<1 | 2 | 3 | "1" | "2" | "3">, default: 2 },
    align: { type: String as PropType<"left" | "center">, default: "left" },
  },
  setup(props, { slots }) {
    const resolvedLevel = computed<1 | 2 | 3>(() => {
      const value = Number(props.level);
      return value === 1 || value === 3 ? value : 2;
    });
    return () =>
      h("header", { class: props.align === "center" ? "text-center" : "" }, [
        props.eyebrow ? h("p", { class: "eyebrow mb-3" }, props.eyebrow) : null,
        h(
          `h${resolvedLevel.value}`,
          {
            class:
              resolvedLevel.value === 1
                ? "font-display text-4xl leading-[1.04] tracking-[-0.04em] sm:text-5xl lg:text-7xl"
                : resolvedLevel.value === 3
                  ? "font-display text-xl leading-snug sm:text-2xl"
                  : "font-display text-3xl leading-[1.08] tracking-[-0.03em] sm:text-4xl lg:text-5xl",
          },
          props.title,
        ),
        props.description
          ? h(
              "p",
              {
                class: [
                  "mt-3 text-base leading-relaxed text-ink-muted",
                  props.align === "center" ? "mx-auto max-w-2xl" : "max-w-2xl",
                ],
              },
              props.description,
            )
          : null,
        slots.actions ? h("div", { class: "mt-6 flex flex-wrap gap-3" }, slots.actions()) : null,
      ]);
  },
});

export const EmptyState = defineComponent({
  name: "EmptyState",
  props: {
    title: { type: String, required: true },
    description: { type: String, default: "" },
    tone: { type: String as PropType<"empty" | "error" | "forbidden" | "not-found">, default: "empty" },
  },
  setup(props, { slots }) {
    const marker: Record<string, string> = {
      empty: "暂无内容",
      error: "加载失败",
      forbidden: "无权访问",
      "not-found": "页面不存在",
    };
    return () =>
      h(
        "div",
        {
          class: "flex flex-col items-center gap-3 rounded-2xl border border-dashed border-line-strong bg-surface-muted px-6 py-12 text-center",
          role: props.tone === "error" ? "alert" : "status",
        },
        [
          h("p", { class: "eyebrow" }, marker[props.tone] ?? ""),
          h("p", { class: "font-display text-xl font-bold" }, props.title),
          props.description ? h("p", { class: "max-w-md text-sm text-ink-muted" }, props.description) : null,
          slots.default ? h("div", { class: "mt-2 flex flex-wrap justify-center gap-3" }, slots.default()) : null,
        ],
      );
  },
});

export const SkeletonBlock = defineComponent({
  name: "SkeletonBlock",
  props: {
    lines: { type: Number, default: 3 },
    /** 局部列表优先骨架屏，避免全页每次闪动 */
    ariaLabel: { type: String, default: "正在加载内容" },
  },
  setup(props) {
    return () =>
      h(
        "div",
        { class: "space-y-3", role: "status", "aria-label": props.ariaLabel },
        Array.from({ length: props.lines }, (_, index) =>
          h("div", {
            class: "h-4 animate-pulse rounded-md bg-canvas-sunk",
            style: { width: index === props.lines - 1 ? "62%" : "100%" },
          }),
        ),
      );
  },
});

export const FieldRow = defineComponent({
  name: "FieldRow",
  props: {
    label: { type: String, required: true },
    for: { type: String, default: "" },
    required: { type: Boolean, default: false },
    hint: { type: String, default: "" },
    error: { type: String, default: "" },
  },
  setup(props, { slots }) {
    return () =>
      h("div", { class: "space-y-1.5" }, [
        h(
          "label",
          { class: "flex items-baseline gap-2 text-sm font-semibold text-ink-soft", for: props.for || undefined },
          [props.label, props.required ? h("span", { class: "text-wine text-xs" }, "必填") : null],
        ),
        props.hint ? h("p", { class: "text-xs text-ink-faint" }, props.hint) : null,
        slots.default?.(),
        // 错误贴字段（11 文档）
        props.error
          ? h("p", { class: "text-xs font-medium text-wine", role: "alert" }, props.error)
          : null,
      ]);
  },
});

export const Breadcrumbs = defineComponent({
  name: "AppBreadcrumbs",
  props: {
    items: { type: Array as PropType<Array<{ label: string; to?: string }>>, required: true },
  },
  setup(props) {
    return () =>
      h(
        "nav",
        { "aria-label": "面包屑", class: "text-xs text-ink-faint" },
        h(
          "ol",
          { class: "flex flex-wrap items-center gap-2" },
          props.items.map((item, index) =>
            h("li", { class: "flex items-center gap-2" }, [
              item.to && index !== props.items.length - 1
                ? h(RouterLink, { to: item.to, class: "link-underline hover:text-amber-strong" }, () => item.label)
                : h("span", { "aria-current": index === props.items.length - 1 ? "page" : undefined }, item.label),
              index !== props.items.length - 1 ? h("span", { "aria-hidden": "true" }, "/") : null,
            ]),
          ),
        ),
      );
  },
});
