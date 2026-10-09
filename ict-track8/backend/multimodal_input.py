"""Validated, request-scoped image input for multimodal Omni turns."""
from __future__ import annotations

import base64
import binascii
import hashlib
import re
import warnings
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps


_DATA_URL = re.compile(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/]+={0,2})$")
_FORMATS = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}
MAX_IMAGES = 3
MAX_IMAGE_BYTES = 6 * 1024 * 1024
MAX_TOTAL_IMAGE_BYTES = 12 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_000_000
MAX_NORMALIZED_IMAGE_BYTES = 8 * 1024 * 1024
MAX_TOTAL_NORMALIZED_BYTES = 18 * 1024 * 1024


@dataclass(frozen=True)
class ChatImageInput:
    data: bytes
    mime_type: str
    sha256: str
    width: int
    height: int

    @property
    def size_px(self):
        return [self.width, self.height]


def decode_chat_images(data_urls: list[str] | tuple[str, ...]) -> list[ChatImageInput]:
    if not isinstance(data_urls, (list, tuple)) or len(data_urls) > MAX_IMAGES:
        raise ValueError("最多上传3张图片")
    images = []
    total_raw = 0
    total_normalized = 0
    for value in data_urls:
        if not isinstance(value, str) or len(value) > 8_400_000:
            raise ValueError("图片编码无效或超过大小限制")
        match = _DATA_URL.fullmatch(value)
        if match is None:
            raise ValueError("仅支持 PNG、JPEG 或 WebP 图片")
        mime_type, encoded = match.groups()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("图片编码无效") from exc
        total_raw += len(raw)
        if not raw or len(raw) > MAX_IMAGE_BYTES or total_raw > MAX_TOTAL_IMAGE_BYTES:
            raise ValueError("图片总大小超过限制")
        expected_format = _FORMATS[mime_type]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(BytesIO(raw)) as source:
                    if source.format != expected_format or getattr(source, "n_frames", 1) != 1:
                        raise ValueError("图片格式与内容不一致，或包含动画帧")
                    width, height = source.size
                    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
                        raise ValueError("图片像素数超过限制")
                    source.verify()
                with Image.open(BytesIO(raw)) as source:
                    source.load()
                    normalized = ImageOps.exif_transpose(source)
                    width, height = normalized.size
                    if expected_format == "JPEG" and normalized.mode not in {"RGB", "L"}:
                        normalized = normalized.convert("RGB")
                    elif expected_format == "WEBP" and normalized.mode not in {"RGB", "RGBA"}:
                        normalized = normalized.convert("RGB")
                    output = BytesIO()
                    options = {"quality": 92} if expected_format in {"JPEG", "WEBP"} else {"compress_level": 1}
                    normalized.save(output, format=expected_format, **options)
                    safe_bytes = output.getvalue()
        except ValueError:
            raise
        except (OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("图片文件损坏或像素数超过限制") from exc
        total_normalized += len(safe_bytes)
        if len(safe_bytes) > MAX_NORMALIZED_IMAGE_BYTES or total_normalized > MAX_TOTAL_NORMALIZED_BYTES:
            raise ValueError("图片解码后的大小超过限制")
        images.append(ChatImageInput(safe_bytes, mime_type, hashlib.sha256(safe_bytes).hexdigest(), width, height))
    return images
