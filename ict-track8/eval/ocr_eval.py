"""OCR 预处理链路 A/B 评测（对应中级任务 9 与决赛鲁棒性"模糊扫描件"）。

  python eval/ocr_eval.py --repo <ict-track8 目录> --out ocr.json [--samples 8]

合成"人眼可读"的退化图片（暗光、过曝、低对比、轻度模糊、小字号），记录：
- pipeline_ok_rate     流水线在 3 次尝试内达到置信度阈值的比例
- mean_cer             最终文本相对真值的字符错误率（Levenshtein / 真值长度）
- rejected_attempts    因"推荐变换不在执行白名单"而直接失败的尝试次数
需要本机安装 Tesseract（沙箱仅有 eng 语言包，因此使用英文文本行）；未安装时跳过。
上一轮的退化最重档连人都难以辨认，结论不可靠，这里只保留可读档位。
"""

from __future__ import annotations

import argparse
import io
import json
import random
import statistics
import sys
from pathlib import Path

WORDS = ("revenue", "region", "quarter", "orders", "growth", "margin", "customer", "channel", "report", "total",
         "online", "store", "east", "south", "north", "sales", "amount", "average", "ticket", "policy")


def levenshtein(a: str, b: str) -> int:
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return previous[-1]


def render(text_lines, size=28):
    from PIL import Image, ImageDraw, ImageFont
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    image = Image.new("L", (900, 60 + 48 * len(text_lines)), 255)
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(text_lines):
        draw.text((30, 30 + 48 * index), line, fill=0, font=font)
    return image


def degrade(image, kind):
    from PIL import ImageFilter
    if kind == "dark":
        return image.point(lambda p: int(p * 0.33))
    if kind == "overexposed":
        return image.point(lambda p: int(175 + p * 0.31))
    if kind == "low_contrast":
        return image.point(lambda p: int(118 + p * 0.22))
    if kind == "blur":
        return image.filter(ImageFilter.GaussianBlur(1.3))
    if kind == "small_font":
        return image.resize((image.width // 2, image.height // 2))
    if kind == "dark_blur_small":
        image = image.filter(ImageFilter.GaussianBlur(0.9)).resize((image.width * 5 // 11, image.height * 5 // 11))
        return image.point(lambda p: int(p * 0.28))
    if kind == "washed_small_noise":
        import random as _r
        rng = _r.Random(7)
        image = image.resize((image.width * 5 // 11, image.height * 5 // 11)).point(lambda p: int(165 + p * 0.33))
        pixels = image.load()
        for _ in range(image.width * image.height // 12):
            x, y = rng.randrange(image.width), rng.randrange(image.height)
            pixels[x, y] = max(0, min(255, pixels[x, y] + rng.randint(-60, 60)))
        return image
    if kind == "dim_lowcontrast_blur":
        return image.filter(ImageFilter.GaussianBlur(1.1)).point(lambda p: int(40 + p * 0.25))
    return image


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=8)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--require-executor",
        action="store_true",
        help="执行器不可用时以非零状态退出，供正式 OCR 发布门禁使用",
    )
    args = parser.parse_args()
    sys.path.insert(0, str(args.repo.resolve()))
    from backend.ocr import OcrPipeline, TesseractOcrExecutor  # noqa: WPS433
    executor = TesseractOcrExecutor()
    health = executor.health()
    if not health.get("ready", False):
        reason = health.get("detail", "Tesseract 不可用")
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps({
            "status": "skipped",
            "reason": reason,
            "overall": None,
            "by_kind": {},
            "samples_per_kind": args.samples,
        }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"Tesseract 不可用，已写出跳过报告: {reason}")
        if args.require_executor:
            raise SystemExit(2)
        return
    pipeline = OcrPipeline(executor)
    rng = random.Random(42)
    kinds = ["clean", "dark", "overexposed", "low_contrast", "blur", "small_font",
             "dark_blur_small", "washed_small_noise", "dim_lowcontrast_blur"]
    by_kind = {}
    for kind in kinds:
        oks, cers, rejected, transforms_used, accepted_at = [], [], 0, [], []
        for _ in range(args.samples):
            lines = [" ".join(rng.choice(WORDS) for _ in range(5)) for _ in range(3)]
            buffer = io.BytesIO()
            degrade(render(lines), kind).convert("RGB").save(buffer, format="PNG")
            result = pipeline.run(buffer.getvalue(), language="eng", max_attempts=3).to_dict()
            truth = " ".join(lines).lower()
            predicted = " ".join((result.get("text") or "").split()).lower()
            oks.append(result["status"] == "ok")
            cers.append(levenshtein(predicted, truth) / max(1, len(truth)))
            rejected += sum(1 for a in result.get("attempts", []) if "不支持的图片变换" in (a.get("error") or ""))
            accepted_at.append(next((a["attempt"] for a in result.get("attempts", []) if a.get("status") == "accepted"), None))
            transforms_used.append(list(result.get("transforms") or result.get("applied_transforms") or []))
        by_kind[kind] = {"pipeline_ok_rate": round(sum(oks) / len(oks), 3), "mean_cer": round(statistics.mean(cers), 4),
                         "rejected_attempts": rejected,
                         "accepted_at_attempt": {str(k): accepted_at.count(k) for k in (1, 2, 3, None)}}
        print(f"{kind:13s} ok={by_kind[kind]['pipeline_ok_rate']:.3f} CER={by_kind[kind]['mean_cer']:.4f} rejected={rejected} accepted_at={by_kind[kind]['accepted_at_attempt']}", flush=True)
    overall = {"pipeline_ok_rate": round(statistics.mean(v["pipeline_ok_rate"] for v in by_kind.values()), 4),
               "mean_cer": round(statistics.mean(v["mean_cer"] for v in by_kind.values()), 4),
               "rejected_attempts": sum(v["rejected_attempts"] for v in by_kind.values())}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps({"overall": overall, "by_kind": by_kind, "samples_per_kind": args.samples}, ensure_ascii=False, indent=1), encoding="utf-8")
    print("overall", overall)


if __name__ == "__main__":
    main()
