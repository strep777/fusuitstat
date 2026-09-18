"""品牌素材派生脚本（一次性构建步骤，产物提交进仓库）。

输入：``素材/`` 下的原图。脚本先校验 SHA256，不符即停止，绝不修改原图。
输出：``web/public/assets/brand/`` 下的 WebP 派生图、favicon 与 OG 封面。

规则（11 文档）：保留像素比例与透明边缘，不拉伸、不镜像、不自动重绘成其他角色；
只生成尺寸派生图。组件通过 ``web/src/shared/lib/brand-assets.ts`` 的 manifest 引用。

用法：``uv run python -m app.cli derive-assets``
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from app.platform.config import PROJECT_ROOT


@dataclass(frozen=True)
class OriginalAsset:
    source: str
    slug: str
    sha256: str
    sizes: tuple[int, ...]
    is_primary_logo: bool = False
    extra: dict[str, tuple[int, ...]] = field(default_factory=dict)


MANIFEST: tuple[OriginalAsset, ...] = (
    OriginalAsset(
        source="素材/加载/77_双曜弈兽.png",
        slug="loader-dual-star",
        sha256="46912c03330accbe06a048366fb72bab37d55afcefadbff6f68809514db7af9b",
        sizes=(128, 256, 512),
    ),
    OriginalAsset(
        source="素材/积分点/62_弈环双尾.png",
        slug="points-twin-tail",
        sha256="e7199c6db2863dafa41ed7b1da6e6c0cd850e3c9192458e277466107f0214d47",
        sizes=(24, 48, 96),
    ),
    OriginalAsset(
        source="素材/logo/35_逐星绒龙.png",
        slug="logo-star-dragon",
        sha256="e050ead67e95ce7622c379876a127f887f5cc5c648bf9cc94a910679ff7b5725",
        sizes=(64, 128, 256),
        is_primary_logo=True,
    ),
    OriginalAsset(
        source="素材/logo/35_逐星绒龙_bai.png",
        slug="logo-star-dragon-light",
        sha256="7e88faececf14c5f4f89c5a3db07bc751a0a0b35dca4e062f2a5a15236008fb8",
        sizes=(64, 128, 256),
    ),
)

CANVAS_RGB = (250, 248, 243)


class AssetDerivationError(RuntimeError):
    """派生失败。任何不一致都停止，不产出半成品。"""


def _verify(asset: OriginalAsset) -> Path:
    absolute = PROJECT_ROOT / asset.source
    if not absolute.is_file():
        raise AssetDerivationError(f"原图不存在：{asset.source}")
    digest = hashlib.sha256(absolute.read_bytes()).hexdigest()
    if digest != asset.sha256:
        raise AssetDerivationError(
            f"原图哈希不一致，停止派生以保护素材：{asset.source}\n  期望 {asset.sha256}\n  实际 {digest}"
        )
    return absolute


def _square(asset: OriginalAsset, image: Image.Image) -> Image.Image:
    if image.width != image.height:
        raise AssetDerivationError(f"品牌素材应为正方形，实际 {image.width}x{image.height}：{asset.source}")
    return image


def derive_all(out_dir: Path | None = None) -> list[str]:
    target_dir = out_dir or (PROJECT_ROOT / "web" / "public" / "assets" / "brand")
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []

    for asset in MANIFEST:
        absolute = _verify(asset)
        with Image.open(absolute) as raw:
            image = _square(asset, raw.convert("RGBA"))
            for size in asset.sizes:
                resized = image.resize((size, size), Image.Resampling.LANCZOS)
                target = target_dir / f"{asset.slug}-{size}.webp"
                resized.save(target, format="WEBP", quality=92, method=6, exact=True)
                written.append(str(target.relative_to(PROJECT_ROOT)))

            if asset.is_primary_logo:
                for size in (32, 64, 192, 512):
                    resized = image.resize((size, size), Image.Resampling.LANCZOS)
                    target = target_dir / f"favicon-{size}.png"
                    resized.save(target, format="PNG", optimize=True)
                    written.append(str(target.relative_to(PROJECT_ROOT)))

                app_icon = image.resize((192, 192), Image.Resampling.LANCZOS)
                app_icon_path = target_dir / "app-icon-192.png"
                app_icon.save(app_icon_path, format="PNG", optimize=True)
                written.append(str(app_icon_path.relative_to(PROJECT_ROOT)))

                cover = Image.new("RGBA", (1200, 630), (*CANVAS_RGB, 255))
                logo = image.resize((420, 420), Image.Resampling.LANCZOS)
                cover.alpha_composite(logo, (390, 105))
                cover_path = target_dir / "og-cover.png"
                cover.convert("RGB").save(cover_path, format="PNG", optimize=True)
                written.append(str(cover_path.relative_to(PROJECT_ROOT)))

    record = {
        "generatedBy": "server/app/assets/derive.py",
        "originalsPreserved": True,
        "files": sorted(written),
    }
    (target_dir / "manifest.generated.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return sorted(written)
