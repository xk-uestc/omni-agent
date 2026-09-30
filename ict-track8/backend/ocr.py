"""可审计的 OCR 执行边界。

文档质量模块只负责判断是否需要 OCR；本模块才允许调用外部 OCR 或本地
Tesseract，并把每次预处理、置信度和失败原因保留在结果中。没有配置执行器
时不会伪造文本。
"""

from __future__ import annotations

import base64
import binascii
import os
import re
import shutil
from dataclasses import asdict, dataclass
from io import BytesIO
from typing import Any, Protocol

import requests
from PIL import Image

from .image_quality import ImageEnhancer, ImageQualityAnalyzer
from .config import env_float


class OcrExecutionError(RuntimeError):
    """OCR 执行器不可用或返回了不符合契约的结果。"""


@dataclass(frozen=True)
class OcrResponse:
    text: str
    confidence: float | None
    metadata: dict[str, Any]


class OcrExecutor(Protocol):
    name: str

    def execute(self, image_bytes: bytes, *, language: str) -> OcrResponse:
        ...


class HttpOcrExecutor:
    """调用外部 OCR 服务，服务端只接收图片和语言，不接收会话密钥。"""

    name = "http"

    def __init__(self, base_url: str, token: str = "", *, timeout: float = 12.0, session: Any | None = None):
        value = str(base_url or "").strip().rstrip("/")
        if not value.startswith(("http://", "https://")):
            raise ValueError("OCR URL 必须使用 http 或 https")
        if timeout <= 0:
            raise ValueError("OCR timeout 必须大于 0")
        self.base_url = value
        self.token = str(token or "")
        self.timeout = float(timeout)
        self.session = session or requests.Session()

    def execute(self, image_bytes: bytes, *, language: str) -> OcrResponse:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            response = self.session.post(
                f"{self.base_url}/ocr",
                json={"image_base64": base64.b64encode(image_bytes).decode("ascii"), "language": language},
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            suffix = f"（HTTP {status}）" if status else ""
            raise OcrExecutionError(f"HTTP OCR 请求失败{suffix}") from exc
        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            raise OcrExecutionError("HTTP OCR 返回的 JSON 无法解析") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("text"), str):
            raise OcrExecutionError("HTTP OCR 响应缺少 text 字段")
        confidence = _confidence(payload.get("confidence"))
        return OcrResponse(payload["text"].strip(), confidence, {"provider": payload.get("provider")})

    def health(self) -> dict[str, Any]:
        return {"ready": True, "probe": "not_run", "provider": self.name}


class TesseractOcrExecutor:
    """可选的本地 Tesseract 执行器；依赖和二进制缺失时显式失败。"""

    name = "tesseract"

    def __init__(self, timeout: float = 20.0):
        # 单次 Tesseract 调用的墙钟上限，防止大图阻塞请求线程
        self.timeout = float(timeout)

    def health(self) -> dict[str, Any]:
        binary = shutil.which("tesseract")
        if not binary:
            return {"ready": False, "provider": self.name, "detail": "tesseract 二进制未找到"}
        try:
            import pytesseract

            version = str(pytesseract.get_tesseract_version()).splitlines()[0].strip()
        except Exception as exc:
            return {"ready": False, "provider": self.name, "detail": "Tesseract 版本探针失败"}
        return {"ready": True, "provider": self.name, "binary": binary, "version": version}

    def execute(self, image_bytes: bytes, *, language: str) -> OcrResponse:
        try:
            import pytesseract
            from pytesseract import Output
        except ImportError as exc:
            raise OcrExecutionError("未安装 pytesseract，无法启用 Tesseract OCR") from exc
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                data = pytesseract.image_to_data(image, lang=language, output_type=Output.DICT, timeout=self.timeout)
        except Exception as exc:
            raise OcrExecutionError("Tesseract OCR 执行失败，请确认语言包和 tesseract 二进制可用") from exc
        texts: list[str] = []
        scores: list[float] = []
        for text, raw_conf in zip(data.get("text", []), data.get("conf", [])):
            value = str(text or "").strip()
            if not value:
                continue
            texts.append(value)
            try:
                parsed = float(raw_conf)
            except (TypeError, ValueError):
                continue
            if parsed >= 0:
                scores.append(parsed / 100.0)
        return OcrResponse(" ".join(texts), round(sum(scores) / len(scores), 4) if scores else None, {})


@dataclass(frozen=True)
class OcrAttempt:
    attempt: int
    transforms: tuple[str, ...]
    status: str
    text_length: int
    confidence: float | None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OcrPipelineResult:
    status: str
    executor: str
    text: str
    confidence: float | None
    selected_transforms: tuple[str, ...]
    attempts: tuple[OcrAttempt, ...]
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executor": self.executor,
            "text": self.text,
            "confidence": self.confidence,
            "selected_transforms": list(self.selected_transforms),
            "attempts": [item.to_dict() for item in self.attempts],
            "warnings": list(self.warnings),
        }


class OcrPipeline:
    CONFIDENCE_THRESHOLD = 0.8

    def __init__(
        self,
        executor: OcrExecutor,
        *,
        analyzer: ImageQualityAnalyzer | None = None,
        enhancer: ImageEnhancer | None = None,
    ):
        if executor is None:
            raise ValueError("OCR executor 不能为空")
        self.executor = executor
        self.analyzer = analyzer or ImageQualityAnalyzer()
        self.enhancer = enhancer or ImageEnhancer()

    def run(self, image_bytes: bytes, *, language: str = "eng", max_attempts: int = 3) -> OcrPipelineResult:
        if not image_bytes:
            raise ValueError("图片不能为空")
        if not re.fullmatch(r"[A-Za-z0-9_+\-]{2,32}", language or ""):
            raise ValueError("language 只允许字母、数字、下划线、加号和连字符")
        if not 1 <= int(max_attempts) <= 3:
            raise ValueError("max_attempts 必须在 1 到 3 之间")
        quality = self.analyzer.analyze(image_bytes)
        recommended = tuple(quality.recommended_transforms)
        sequences: list[tuple[str, ...]] = [()]
        for size in range(1, min(len(recommended), int(max_attempts) - 1) + 1):
            sequences.append(recommended[:size])
        while len(sequences) < int(max_attempts):
            sequences.append(recommended)

        attempts: list[OcrAttempt] = []
        best = OcrResponse("", None, {})
        best_transforms: tuple[str, ...] = ()
        warnings: list[str] = []
        for index, transforms in enumerate(sequences[: int(max_attempts)], start=1):
            try:
                enhanced = self.enhancer.enhance(image_bytes, transforms=transforms)
                response = self.executor.execute(enhanced.image_bytes, language=language)
                if response.confidence is not None and (best.confidence is None or response.confidence > best.confidence):
                    best, best_transforms = response, transforms
                elif not best.text and response.text:
                    best, best_transforms = response, transforms
                accepted = bool(response.text.strip()) and (
                    response.confidence is None or response.confidence >= self.CONFIDENCE_THRESHOLD
                )
                attempts.append(
                    OcrAttempt(index, transforms, "accepted" if accepted else "low_confidence", len(response.text), response.confidence)
                )
                if accepted:
                    return OcrPipelineResult("ok", self.executor.name, response.text, response.confidence, transforms, tuple(attempts), tuple(warnings))
            except (OcrExecutionError, ValueError) as exc:
                attempts.append(OcrAttempt(index, transforms, "failed", 0, None, str(exc)))
                warnings.append(str(exc))
        status = "low_confidence" if best.text else "failed"
        return OcrPipelineResult(status, self.executor.name, best.text, best.confidence, best_transforms, tuple(attempts), tuple(dict.fromkeys(warnings)))

    def health(self) -> dict[str, Any]:
        probe = getattr(self.executor, "health", None)
        if callable(probe):
            result = probe()
            if isinstance(result, dict):
                return {"configured": True, **result}
        return {"configured": True, "ready": True, "provider": self.executor.name, "probe": "not_available"}


def _confidence(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed > 1.0:
        parsed /= 100.0
    if not 0.0 <= parsed <= 1.0:
        raise OcrExecutionError("OCR confidence 超出 0 到 1 范围")
    return round(parsed, 4)


def build_ocr_pipeline(warnings: list[str] | None = None) -> OcrPipeline | None:
    url = os.getenv("ICT8_OCR_URL", "").strip()
    if url:
        try:
            return OcrPipeline(
                HttpOcrExecutor(
                    url,
                    os.getenv("ICT8_OCR_TOKEN", ""),
                    timeout=env_float("ICT8_OCR_TIMEOUT", 12.0, minimum=0.1, maximum=120.0, warnings=warnings),
                )
            )
        except ValueError:
            if warnings is not None:
                warnings.append("ICT8_OCR_URL: invalid; OCR disabled")
            return None
    if os.getenv("ICT8_OCR_ENGINE", "").strip().lower() == "tesseract":
        return OcrPipeline(TesseractOcrExecutor())
    return None
