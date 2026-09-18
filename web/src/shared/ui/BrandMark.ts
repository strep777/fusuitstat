/**
 * 品牌图标与加载图组件。
 *
 * 规则（11 文档）：组件只通过 manifest 引用素材，不写 Windows 路径；
 * 保留像素比例与透明边缘，不拉伸、不镜像；Logo 在导航使用 32–48px，页脚 40–64px；
 * 加载动画周期 1.6–2 秒，尊重 prefers-reduced-motion。
 */

import { computed, defineComponent, h, type PropType } from "vue";

import { brandAssets, brandSrcset } from "@/shared/lib/brand-assets";

type LogoVariant = "dark" | "light";

export const BrandMark = defineComponent({
  name: "BrandMark",
  props: {
    size: { type: Number, default: 40 },
    variant: { type: String as PropType<LogoVariant>, default: "dark" },
    decorative: { type: Boolean, default: false },
    label: { type: String, default: "" },
  },
  setup(props) {
    const asset = computed(() => (props.variant === "light" ? brandAssets.brandLogoLight : brandAssets.brandLogo));
    return () =>
      h("img", {
        src: asset.value.path,
        srcset: brandSrcset(props.variant === "light" ? "brandLogoLight" : "brandLogo"),
        sizes: `${props.size}px`,
        width: props.size,
        height: props.size,
        alt: props.decorative ? "" : props.label || asset.value.alt,
        "aria-hidden": props.decorative ? "true" : undefined,
        loading: "eager",
        decoding: "async",
        class: "select-none",
        draggable: "false",
      });
  },
});

export const PointsIcon = defineComponent({
  name: "PointsIcon",
  props: {
    size: { type: Number, default: 20 },
    withUnit: { type: Boolean, default: false },
    points: { type: Number, default: null as unknown as number },
    label: { type: String, default: "" },
  },
  setup(props) {
    return () => {
      const icon = h("img", {
        src: brandAssets.pointsIcon.path,
        srcset: brandSrcset("pointsIcon"),
        sizes: `${props.size}px`,
        width: props.size,
        height: props.size,
        alt: "",
        "aria-hidden": "true",
        loading: "lazy",
        decoding: "async",
        class: "inline-block shrink-0",
      });
      const text = props.label || (props.points !== null ? `${props.points} 星绒币` : "星绒币");
      return h(
        "span",
        {
          class: "inline-flex items-center gap-1.5 align-middle",
          // 读屏只读一次（11 文档）
          role: "img",
          "aria-label": text,
        },
        [icon, h("span", { class: "tabular-nums", "aria-hidden": "true" }, text)],
      );
    };
  },
});

/**
 * 双曜弈兽加载图：“加载素材用于路由初次加载、长表单提交和关键区块加载”。
 * 等待少于 150ms 不显示闪烁加载；超过 8 秒给可操作提示。
 */
export const StarLoader = defineComponent({
  name: "StarLoader",
  props: {
    size: { type: Number, default: 96 },
    message: { type: String, default: "正在加载" },
    /** 长任务（支付确认等）显示更明确的说明，不伪造百分比。 */
    hint: { type: String, default: "" },
    inline: { type: Boolean, default: false },
  },
  setup(props) {
    return () =>
      h(
        "div",
        {
          class: props.inline
            ? "inline-flex items-center gap-3"
            : "flex flex-col items-center justify-center gap-3 py-10",
          role: "status",
          "aria-live": "polite",
        },
        [
          h("img", {
            src: brandAssets.brandLoader.path,
            srcset: brandSrcset("brandLoader"),
            sizes: `${props.size}px`,
            width: props.size,
            height: props.size,
            alt: "",
            "aria-hidden": "true",
            class: "animate-breathe",
            decoding: "async",
          }),
          h("div", { class: props.inline ? "text-sm text-ink-muted" : "text-center" }, [
            h("p", { class: "text-sm font-medium text-ink-soft" }, props.message),
            props.hint ? h("p", { class: "mt-1 text-xs text-ink-faint" }, props.hint) : null,
          ]),
        ],
      );
  },
});

export default BrandMark;
