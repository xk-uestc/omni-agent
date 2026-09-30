"""输入图片的轻量质量探针，不执行 OCR，也不把质量判断冒充语义识别。"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any

from PIL import Image, ImageFilter, ImageOps

# Image.open 是惰性的：在解码前按尺寸拒绝解压炸弹（默认 4000 万像素，约 6300×6300）
MAX_PIXELS = 40_000_000


@dataclass(frozen=True)
class ImageQualityReport:
    width: int
    height: int
    mode: str
    exif_orientation: int | None
    brightness: float
    contrast: float
    edge_energy: float
    resolution_score: float
    quality_score: float
    issues: tuple[dict[str, Any], ...]
    recommended_transforms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnhancedImage:
    image_bytes: bytes
    width: int
    height: int
    format: str
    applied_transforms: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "width": self.width,
            "height": self.height,
            "format": self.format,
            "applied_transforms": list(self.applied_transforms),
            "byte_count": len(self.image_bytes),
        }


def _otsu_threshold(histogram: list[int]) -> int:
    """Otsu 类间方差最大化阈值（替代原先的全局均值阈值）。"""

    counts = histogram[:256]
    total = sum(counts) or 1
    sum_all = sum(index * count for index, count in enumerate(counts))
    weight_bg = sum_bg = 0.0
    best, threshold = -1.0, 127
    for index, count in enumerate(counts):
        weight_bg += count
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += index * count
        mean_bg, mean_fg = sum_bg / weight_bg, (sum_all - sum_bg) / weight_fg
        between = weight_bg * weight_fg * (mean_bg - mean_fg) ** 2
        if between > best:
            best, threshold = between, index
    return threshold


class ImageEnhancer:
    """为 OCR 准备图片的确定性预处理器，不执行 OCR 或语义推断。"""

    ALLOWED_TRANSFORMS = frozenset(
        {
            "exif_transpose",
            "rotate_to_upright",
            "crop",
            "grayscale_normalize",
            "adaptive_threshold",
            "sharpen",
            "upscale",
            # 以下三项此前会被 ImageQualityAnalyzer 推荐却不在白名单中，导致重试 100% 失败
            "brighten",
            "reduce_highlights",
            "contrast",
        }
    )

    def enhance(
        self,
        image_bytes: bytes,
        *,
        transforms: tuple[str, ...] = (),
        rotation_degrees: float = 0.0,
        crop_box: tuple[int, int, int, int] | None = None,
        max_dimension: int = 2400,
    ) -> EnhancedImage:
        if not image_bytes:
            raise ValueError("图片不能为空")
        unknown = sorted(set(transforms) - self.ALLOWED_TRANSFORMS)
        if unknown:
            raise ValueError(f"不支持的图片变换: {', '.join(unknown)}")
        if not 1 <= max_dimension <= 6000:
            raise ValueError("max_dimension 必须在 1 到 6000 之间")
        try:
            with Image.open(BytesIO(image_bytes)) as source:
                if source.width * source.height > MAX_PIXELS:
                    raise ValueError("图片像素数超过安全上限")
                image = ImageOps.exif_transpose(source).convert("RGB")
        except Exception as exc:
            raise ValueError("图片格式无法解析") from exc

        requested = tuple(dict.fromkeys(transforms))
        applied: list[str] = []
        if "exif_transpose" in requested:
            applied.append("exif_transpose")
        if "rotate_to_upright" in requested and abs(rotation_degrees) > 0.01:
            image = image.rotate(-rotation_degrees, expand=True, resample=Image.Resampling.BICUBIC)
            applied.append("rotate_to_upright")
        if "crop" in requested:
            if crop_box is None:
                raise ValueError("crop 变换需要 crop_box")
            left, top, right, bottom = crop_box
            if not (0 <= left < right <= image.width and 0 <= top < bottom <= image.height):
                raise ValueError("crop_box 超出图片边界")
            image = image.crop(crop_box)
            applied.append("crop")
        if "brighten" in requested:
            # gamma < 1 提亮暗部，不放大已饱和的高光
            image = ImageOps.grayscale(image).point(lambda p: round(255 * (p / 255) ** 0.6)).convert("RGB")
            applied.append("brighten")
        if "reduce_highlights" in requested:
            image = ImageOps.grayscale(image).point(lambda p: round(255 * (p / 255) ** 1.8)).convert("RGB")
            applied.append("reduce_highlights")
        if "contrast" in requested or "grayscale_normalize" in requested:
            image = ImageOps.autocontrast(ImageOps.grayscale(image), cutoff=1).convert("RGB")
            applied.append("contrast" if "contrast" in requested else "grayscale_normalize")
            if "contrast" in requested and "grayscale_normalize" in requested:
                applied.append("grayscale_normalize")
        if "adaptive_threshold" in requested:
            gray = ImageOps.grayscale(image)
            threshold = _otsu_threshold(gray.histogram())
            image = gray.point(lambda pixel: 255 if pixel >= threshold else 0).convert("RGB")
            applied.append("adaptive_threshold")
        if "sharpen" in requested:
            image = image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=140, threshold=3))
            applied.append("sharpen")
        if "upscale" in requested:
            longest = max(image.width, image.height)
            if longest < max_dimension:
                scale = max_dimension / max(1, longest)
                image = image.resize(
                    (max(1, round(image.width * scale)), max(1, round(image.height * scale))),
                    Image.Resampling.LANCZOS,
                )
            applied.append("upscale")
        output = BytesIO()
        image.save(output, format="PNG", optimize=True)
        return EnhancedImage(
            image_bytes=output.getvalue(),
            width=image.width,
            height=image.height,
            format="PNG",
            applied_transforms=tuple(applied),
        )


class ImageQualityAnalyzer:
    def analyze(self, image_bytes: bytes) -> ImageQualityReport:
        if not image_bytes:
            raise ValueError("图片不能为空")
        try:
            with Image.open(BytesIO(image_bytes)) as original:
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError("图片像素数超过安全上限")
                image = ImageOps.exif_transpose(original).convert("L")
                width, height = image.size
                mode = original.mode
                exif_orientation = self._orientation(original)
                pixels = list(image.resize((min(256, width), min(256, height))).getdata())
        except Exception as exc:
            raise ValueError("图片格式无法解析") from exc
        if not pixels:
            raise ValueError("图片没有像素")
        brightness = sum(pixels) / len(pixels) / 255
        variance = sum((pixel / 255 - brightness) ** 2 for pixel in pixels) / len(pixels)
        contrast = variance**0.5
        edge_energy = self._edge_energy(image)
        resolution_score = min(1.0, (width * height) / (1280 * 960))
        score = 0.35 * resolution_score + 0.25 * min(1.0, contrast / 0.28) + 0.4 * min(1.0, edge_energy / 0.12)
        issues: list[dict[str, Any]] = []
        transforms: list[str] = []
        if min(width, height) < 640:
            issues.append({"code": "low_resolution", "severity": "warning", "message": "短边分辨率偏低"})
            transforms.append("upscale")
        if brightness < 0.18:
            issues.append({"code": "underexposed", "severity": "warning", "message": "图像偏暗"})
            transforms.append("brighten")
        elif brightness > 0.88:
            issues.append({"code": "overexposed", "severity": "warning", "message": "图像偏亮"})
            transforms.append("reduce_highlights")
        if contrast < 0.08:
            issues.append({"code": "low_contrast", "severity": "warning", "message": "对比度偏低"})
            transforms.append("contrast")
        if edge_energy < 0.035:
            issues.append({"code": "blurred", "severity": "warning", "message": "边缘能量偏低，可能模糊"})
            transforms.append("sharpen")
        if exif_orientation not in (None, 1):
            issues.append({"code": "exif_rotation", "severity": "info", "message": "EXIF 指示需要方向校正", "orientation": exif_orientation})
            transforms.append("exif_transpose")
        return ImageQualityReport(
            width=width,
            height=height,
            mode=mode,
            exif_orientation=exif_orientation,
            brightness=round(brightness, 4),
            contrast=round(contrast, 4),
            edge_energy=round(edge_energy, 4),
            resolution_score=round(resolution_score, 4),
            quality_score=round(max(0.0, min(1.0, score)), 4),
            issues=tuple(issues),
            recommended_transforms=tuple(dict.fromkeys(transforms)),
        )

    @staticmethod
    def _orientation(image: Image.Image) -> int | None:
        try:
            exif = image.getexif()
            value = exif.get(274)
            return int(value) if value is not None else None
        except (AttributeError, TypeError, ValueError):
            return None

    @staticmethod
    def _edge_energy(image: Image.Image) -> float:
        sample = image.resize((min(256, image.width), min(256, image.height)))
        pixels = list(sample.getdata())
        width, height = sample.size
        if width < 2 or height < 2:
            return 0.0
        differences = []
        for row in range(height - 1):
            offset = row * width
            for column in range(width - 1):
                current = pixels[offset + column]
                differences.append(abs(current - pixels[offset + column + 1]) / 255)
                differences.append(abs(current - pixels[offset + width + column]) / 255)
        return sum(differences) / len(differences)
