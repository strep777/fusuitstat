/**
 * 品牌素材集中映射（11 文档：使用 asset-manifest 集中映射，组件不写 Windows 路径）。
 *
 * 原图位于 `素材/`，不被移动或覆盖；`web/scripts/derive-assets.mjs` 按本清单生成
 * `public/assets/brand/` 下的派生图。组件只引用这里的路径。
 *
 * 授权（C26）：加载素材 77_双曜弈兽、积分素材 62_弈环双尾、Logo 35_逐星绒龙，
 * 三者商业使用与加工授权由工作室确认并保留原图与来源记录。
 */

export interface BrandAsset {
  /** 代码标识 */
  readonly key: string;
  /** 原始素材相对项目根的路径（只读，不修改） */
  readonly source: string;
  /** 运行时路径（派生图） */
  readonly path: string;
  /** 原始 SHA256（_调研证据/assets.json） */
  readonly sha256: string;
  readonly sourceSize: readonly [number, number];
  readonly width: number;
  readonly height: number;
  readonly format: "png" | "webp";
  /** 用途说明，供无障碍 alt 文案与运营核对 */
  readonly purpose: string;
  readonly alt: string;
  /** 是否含透明通道；深色背景需检查对比度 */
  readonly hasAlpha: boolean;
}

export const brandAssets = {
  /** 路由初次加载、长表单提交、关键区块加载 */
  brandLoader: {
    key: "brandLoader",
    source: "素材/加载/77_双曜弈兽.png",
    path: "/assets/brand/loader-dual-star-512.webp",
    sha256: "46912c03330accbe06a048366fb72bab37d55afcefadbff6f68809514db7af9b",
    sourceSize: [1254, 1254],
    width: 512,
    height: 512,
    format: "webp",
    purpose: "加载指示：路由初次加载、长表单提交、关键区块加载",
    alt: "弈星纪元加载图案：双曜弈兽",
    hasAlpha: true,
  },
  /** 积分（星绒币）图标 */
  pointsIcon: {
    key: "pointsIcon",
    source: "素材/积分点/62_弈环双尾.png",
    path: "/assets/brand/points-twin-tail-96.webp",
    sha256: "e7199c6db2863dafa41ed7b1da6e6c0cd850e3c9192458e277466107f0214d47",
    sourceSize: [1254, 1254],
    width: 96,
    height: 96,
    format: "webp",
    purpose: "星绒币图标：16/20/24px 行内，32/48px 权益卡",
    alt: "星绒币",
    hasAlpha: true,
  },
  /** 主 Logo（深色剪影，用于浅色背景） */
  brandLogo: {
    key: "brandLogo",
    source: "素材/logo/35_逐星绒龙.png",
    path: "/assets/brand/logo-star-dragon-256.webp",
    sha256: "e050ead67e95ce7622c379876a127f887f5cc5c648bf9cc94a910679ff7b5725",
    sourceSize: [1254, 1254],
    width: 256,
    height: 256,
    format: "webp",
    purpose: "主 Logo：浅色导航与浅色页脚",
    alt: "弈星纪元兽装工作室标志：逐星绒龙",
    hasAlpha: true,
  },
  /** 白色 Logo（深色背景） */
  brandLogoLight: {
    key: "brandLogoLight",
    source: "素材/logo/35_逐星绒龙_bai.png",
    path: "/assets/brand/logo-star-dragon-light-256.webp",
    sha256: "7e88faececf14c5f4f89c5a3db07bc751a0a0b35dca4e062f2a5a15236008fb8",
    sourceSize: [1254, 1254],
    width: 256,
    height: 256,
    format: "webp",
    purpose: "白色 Logo：深色首屏与深色页脚",
    alt: "弈星纪元兽装工作室标志（白色版）",
    hasAlpha: true,
  },
} as const satisfies Record<string, BrandAsset>;

export type BrandAssetKey = keyof typeof brandAssets;

/** 派生尺寸清单。加载图与 Logo 生成多种尺寸，避免 1254px 原图反复加载。 */
export const derivativeVariants: Record<BrandAssetKey, readonly number[]> = {
  brandLoader: [128, 256, 512],
  pointsIcon: [24, 48, 96],
  brandLogo: [64, 128, 256],
  brandLogoLight: [64, 128, 256],
};

export function brandPath(key: BrandAssetKey, size?: number): string {
  const asset = brandAssets[key];
  if (!size || size === asset.width) {
    return asset.path;
  }
  const sizes = derivativeVariants[key];
  const chosen = sizes.find((value) => value >= size) ?? asset.width;
  return asset.path.replace(new RegExp(`-${asset.width}\\.`), `-${chosen}.`);
}

/** srcset 字符串，供 <img> 使用（11 文档：作品与品牌图使用 srcset/sizes）。 */
export function brandSrcset(key: BrandAssetKey): string {
  const asset = brandAssets[key];
  return derivativeVariants[key]
    .map((size) => `${asset.path.replace(new RegExp(`-${asset.width}\\.`), `-${size}.`)} ${size}w`)
    .join(", ");
}

export const brandFonts = {
  display: '"Songti SC", "STSong", "Noto Serif SC", Georgia, serif',
} as const;
