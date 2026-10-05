"""可审计的 OCR 执行边界。

文档质量模块只负责判断是否需要 OCR；本模块才允许调用外部 OCR 或本地
Tesseract，并把每次预处理、置信度和失败原因保留在结果中。没有配置执行器
时不会伪造文本。
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import os
import re
import shutil
import threading
from dataclasses import asdict, dataclass, field
from io import BytesIO
from typing import Any, Protocol

import requests
from PIL import Image, ImageOps

from .image_quality import MAX_PIXELS, ImageEnhancer, ImageQualityAnalyzer
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
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "executor": self.executor,
            "text": self.text,
            "confidence": self.confidence,
            "selected_transforms": list(self.selected_transforms),
            "attempts": [item.to_dict() for item in self.attempts],
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
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

    @staticmethod
    def _finish(status, executor, response, transforms, attempts, warnings, image_bytes, quality, table_evidence=None,borderless_evidence=None,borderless_attempts=None,page_detection=None):
        from .orientation_quality import orientation_warnings
        metadata = dict(response.metadata)
        metadata['source_image_sha256'] = hashlib.sha256(image_bytes).hexdigest()
        if page_detection is not None:
            metadata['page_boundary_detection']={**page_detection,
                'selected_for_text':'perspective_rectify' in transforms,
                'selected_for_table':'perspective_rectify' in (borderless_evidence or table_evidence or {}).get('selected_transforms',[])}
        if table_evidence is not None:
            # Text and grid candidates can come from different attempts. Keep
            # the grid's own frame and chain; never relabel its bboxes as those
            # of the selected text response.
            metadata['scanned_table_evidence'] = table_evidence
        if borderless_evidence is not None:
            from .ocr_evidence_selection import OcrEvidenceSelectionAgent
            metadata['borderless_table_evidence']=OcrEvidenceSelectionAgent().select(borderless_evidence,borderless_attempts or [])
        if hasattr(quality,'to_dict'):
            metadata['input_quality'] = quality.to_dict()
        if 'orientation' in metadata:
            warnings = [*warnings,*orientation_warnings(metadata['orientation'])]
        if getattr(quality,'exif_orientation',None) not in (None,1):
            warnings = [*warnings,'page_exif_orientation_corrected']
        return OcrPipelineResult(status,executor,response.text,response.confidence,transforms,
                                 tuple(attempts),tuple(dict.fromkeys(warnings)),metadata)

    def run(self, image_bytes: bytes, *, language: str = "eng", max_attempts: int = 3,perspective_quad=None,auto_perspective=True) -> OcrPipelineResult:
        if not image_bytes:
            raise ValueError("图片不能为空")
        if not re.fullmatch(r"[A-Za-z0-9_+\-]{2,32}", language or ""):
            raise ValueError("language 只允许字母、数字、下划线、加号和连字符")
        if not 1 <= int(max_attempts) <= 3:
            raise ValueError("max_attempts 必须在 1 到 3 之间")
        quality = self.analyzer.analyze(image_bytes)
        if type(auto_perspective) is not bool:raise ValueError('auto_perspective must be boolean')
        page_detection=None;automatic_quad=None;page_detector=None
        if auto_perspective and perspective_quad is None and int(max_attempts)>1:
            from .perspective import PageBoundaryDetectionAgent
            page_detector=PageBoundaryDetectionAgent()
            try:
                with Image.open(BytesIO(image_bytes)) as source:
                    page_detection=page_detector.detect(ImageOps.exif_transpose(source))
                page_detection['source_image_sha256']=hashlib.sha256(image_bytes).hexdigest()
            except (ImportError,ValueError,OSError):
                page_detection={'status':'unavailable','reason':'page_detection_unavailable'}
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
        orientation_retry_used = False
        correction_plan = None
        table_evidence = None
        table_score = None
        borderless_evidence=None
        borderless_score=None
        borderless_attempts=[]
        for index in range(1, int(max_attempts) + 1):
            transforms = sequences[index - 1]
            try:
                applied_correction = correction_plan
                perspective_options={}
                if perspective_quad is not None or automatic_quad is not None:
                    transforms=tuple(dict.fromkeys(('perspective_rectify',*transforms)))
                    perspective_options={'perspective_quad':perspective_quad if perspective_quad is not None else automatic_quad}
                    if automatic_quad is not None:
                        perspective_options['perspective_selection']='observed_page_boundary'
                        page_detection={**page_detection,'status':'attempted','rectification_attempt':index}
                if applied_correction is not None:
                    transforms = tuple(dict.fromkeys((*transforms, 'rotate_to_upright')))
                    # Enhancer uses the opposite sign to PIL's CCW convention.
                    enhanced = self.enhancer.enhance(image_bytes, transforms=transforms,
                        rotation_degrees=-applied_correction['correction_ccw_degrees'],**perspective_options)
                else:
                    enhanced = self.enhancer.enhance(image_bytes, transforms=transforms,**perspective_options)
                transforms = tuple(getattr(enhanced,'applied_transforms',transforms))
                response = self.executor.execute(enhanced.image_bytes, language=language)
                from .image_geometry import attach_original_coordinates
                response = OcrResponse(response.text,response.confidence,
                    attach_original_coordinates(response.metadata,getattr(enhanced,'geometry',None),
                                                image_bytes,enhanced.image_bytes))
                automatic_retry=False
                if index==1 and page_detection and page_detection.get('status')=='candidate':
                    if page_detector.retains_observed_text(page_detection,response.metadata):
                        automatic_quad=page_detection['quad_px']
                        page_detection={**page_detection,'status':'scheduled','baseline_text_retained':True,'source_attempt':index}
                        automatic_retry=True
                        correction_plan=None
                        orientation_retry_used=False
                    else:
                        page_detection={**page_detection,'status':'rejected','reason':'baseline_text_outside_page_or_unmapped'}
                if applied_correction is not None:
                    response = OcrResponse(response.text, response.confidence,
                        {**response.metadata, 'orientation_retry': applied_correction})
                grids = response.metadata.get('scanned_grids',{})
                mapping = response.metadata.get('original_pixel_mapping',{})
                layout=response.metadata.get('table_layout',{})
                if isinstance(layout,dict) and layout.get('status')=='candidate' and mapping.get('status')=='mapped':
                    candidates=[*(layout.get('regional_candidates') or [layout]),*layout.get('sparse_observations',[])]
                    cells=[cell for candidate in candidates for row in candidate.get('rows',[]) for cell in row]
                    eligible=all(cell.get('original_geometry',{}).get('original_bbox_eligible') for cell in cells)
                    score=(1-layout.get('ragged_row_count',0)/max(1,layout.get('row_count',0)),len(cells),response.confidence or 0.)
                    if cells and eligible:
                        candidate={'status':'candidate','source_attempt':index,
                            'selection_basis':'complete_row_fraction_then_observation_count_then_ocr_confidence',
                            'selected_transforms':list(transforms),'coordinate_frame':response.metadata.get('coordinate_frame'),
                            'image_geometry':response.metadata.get('image_geometry'),'original_pixel_mapping':mapping,
                            'table_layout':layout,'ocr_values_independently_verified':False}
                        borderless_attempts.append(candidate)
                        if borderless_score is None or score>borderless_score:
                            borderless_score=score
                            borderless_evidence=candidate
                if (isinstance(grids,dict) and grids.get('status')=='observed'
                        and mapping.get('status')=='mapped'):
                    anchors = [cell for table in grids.get('tables',[]) for row in table.get('cells',[]) for cell in row
                               if cell.get('status')!='covered_by_merged_cell']
                    from .scanned_grid_selection import ScannedGridSelectionAgent
                    score,orientation_basis = ScannedGridSelectionAgent().score(
                        response.metadata,anchors,response.confidence,applied_correction)
                    if anchors and (table_score is None or score > table_score):
                        table_score = score
                        table_evidence = {'status':'layout_observed','source_attempt':index,
                            'selection_basis':'upright_direction_then_observed_anchor_coverage_then_count_then_ocr_confidence',
                            'orientation_basis':orientation_basis,
                            'orientation':response.metadata.get('orientation'),
                            'orientation_retry':response.metadata.get('orientation_retry'),
                            'selected_transforms':list(transforms),
                            'coordinate_frame':response.metadata.get('coordinate_frame'),
                            'image_geometry':response.metadata.get('image_geometry'),
                            'original_pixel_mapping':mapping,'scanned_grids':grids,
                            'ocr_values_independently_verified':False}
                # OCR engines can discover page direction only after detecting
                # text boxes. Use that evidence for one bounded second pass;
                # never rotate repeatedly or silently claim correction.
                orientation = response.metadata.get("orientation", {}) if isinstance(response.metadata, dict) else {}
                correction = orientation.get("correction_ccw_degrees") if isinstance(orientation, dict) else None
                if (not automatic_retry and not orientation_retry_used and index < int(max_attempts)
                        and type(correction) in (int, float) and math.isfinite(correction)
                        and 1 <= abs(float(correction)) <= 180
                        and orientation.get("status") == "estimated"):
                    orientation_retry_used = True
                    from .scanned_grid_selection import ScannedGridSelectionAgent
                    selected_correction,correction_basis=ScannedGridSelectionAgent.correction(orientation,grids)
                    correction_plan = {
                        "triggered": True,
                        "correction_ccw_degrees": round(float(selected_correction), 3),
                        "correction_basis":correction_basis,
                        "source_attempt": index,
                    }
                if response.text.strip() and response.confidence is not None and (best.confidence is None or response.confidence > best.confidence):
                    best, best_transforms = response, transforms
                elif not best.text and response.text:
                    best, best_transforms = response, transforms
                accepted = bool(response.text.strip()) and (
                    response.confidence is None or response.confidence >= self.CONFIDENCE_THRESHOLD
                )
                # High recognition confidence can still omit strokes in dark/washed scans.
                # A measured poor image therefore completes the bounded enhancement sequence.
                orientation_retry = applied_correction is None and correction_plan is not None and index < int(max_attempts)
                quality_retry = (getattr(quality, 'quality_score', 1.0) < 0.6 and bool(recommended)
                                 and index < int(max_attempts)) or orientation_retry or automatic_retry
                attempts.append(
                    OcrAttempt(index, transforms, "quality_retry" if accepted and quality_retry else "accepted" if accepted else "low_confidence", len(response.text), response.confidence)
                )
                if accepted and not quality_retry:
                    # Enhancement is a candidate, not permission to discard a better baseline.
                    selected = best if best.text and (best.confidence is None or best.confidence >= self.CONFIDENCE_THRESHOLD) else response
                    selected_transforms = best_transforms if selected is best else transforms
                    return self._finish("ok",self.executor.name,selected,selected_transforms,attempts,warnings,image_bytes,quality,table_evidence,borderless_evidence,borderless_attempts,page_detection)
            except (OcrExecutionError, ValueError) as exc:
                attempts.append(OcrAttempt(index, transforms, "failed", 0, None, str(exc)))
                warnings.append(str(exc))
        status = ("ok" if best.text and (best.confidence is None or best.confidence>=self.CONFIDENCE_THRESHOLD)
                  else "low_confidence" if best.text else "failed")
        return self._finish(status,self.executor.name,best,best_transforms,attempts,warnings,image_bytes,quality,table_evidence,borderless_evidence,borderless_attempts,page_detection)

    def health(self) -> dict[str, Any]:
        probe = getattr(self.executor, "health", None)
        if callable(probe):
            result = probe()
            if isinstance(result, dict):
                return {"configured": True, **result}
        return {"configured": True, "ready": True, "provider": self.executor.name, "probe": "not_available"}


def _regional_numeric_table(valid):
    """Local label/amount candidate in mixed prose pages, not a page table.

    Requires repeated numeric right edges and stable left label edges; keeps
    only observed pairs. No headers, omitted rows or semantic totals inferred.
    """
    numeric=re.compile(r'^[($€£¥₹]?\s*[-+]?\d[\d ,\.]*\s*(?:\)|%|m|bn)?$',re.I)
    numbers=[cell for cell in valid if numeric.fullmatch(cell['text'])]
    if not 3<=len(numbers)<=200:return None
    heights=sorted(cell['bbox'][3]-cell['bbox'][1] for cell in numbers)
    median_height=heights[len(heights)//2]
    tolerance=max(6,median_height*.6)
    def block_boundary(previous,following):
        low=sum(previous[0]['bbox'][1::2])/2+median_height*.45
        high=sum(following[0]['bbox'][1::2])/2-median_height*.45
        left=min(previous[0]['bbox'][0],following[0]['bbox'][0])-tolerance
        right=max(previous[1]['bbox'][2],following[1]['bbox'][2])
        for cell in valid:
            x0,y0,x1,y1=cell['bbox']
            if not low<(y0+y1)/2<high or x1<=left or x0>=right:continue
            text=cell['text']
            # Unpaired subtotal text crossing the numeric column, or an
            # observed uppercase heading, separates local blocks. Neither
            # becomes a fabricated row or a semantically verified header.
            if (cell not in numbers and (x1>=min(previous[1]['bbox'][0],following[1]['bbox'][0])
                    or text==text.upper() and len(re.findall(r'[A-Z]',text))>=3)):
                return True
        return False
    def continuation(previous, following):
        """Observe label-column continuity without assigning text to a value."""
        left=previous[0]['bbox'][0]
        top=previous[0]['bbox'][3]
        bottom=following[0]['bbox'][1]
        right=min(previous[1]['bbox'][0],following[1]['bbox'][0])-12
        if bottom-top>median_height*16:return None
        between=[cell for cell in valid if top<sum(cell['bbox'][1::2])/2<bottom
                 and cell['bbox'][2]>left and cell['bbox'][0]<max(previous[1]['bbox'][2],following[1]['bbox'][2])]
        if not 1<=len(between)<=12:return None
        # A new amount, cross-column paragraph or another left edge breaks
        # continuity. Do not merely select a convenient subset of the gap.
        if any(cell in numbers or abs(cell['bbox'][0]-left)>tolerance
               or cell['bbox'][2]>right for cell in between):return None
        between.sort(key=lambda cell:cell['bbox'][1])
        reached=top
        for cell in between:
            if cell['bbox'][1]-reached>median_height*1.25:return None
            reached=max(reached,cell['bbox'][3])
        if bottom-reached>median_height*1.25:return None
        return {'status':'observed_layout_continuity','semantic_assignment_verified':False,
                'from_amount':previous[1]['text'],'to_amount':following[1]['text'],
                'observations':[dict(cell) for cell in between]}
    groups=[]
    for cell in sorted(numbers,key=lambda c:c['bbox'][2]):
        if groups and cell['bbox'][2]-groups[-1][0]['bbox'][2]<=tolerance:groups[-1].append(cell)
        else:groups.append([cell])
    candidates=[]
    sparse=[]
    from .ocr_pairing import CloseLabelAmountAgent
    close_pairs=CloseLabelAmountAgent().run(valid,groups)
    for group in groups:
        if len(group)<3:continue
        pairs=[]
        for value in group:
            x0,y0,x1,y1=value['bbox'];cy=(y0+y1)/2
            labels=[cell for cell in valid if cell not in numbers and cell['bbox'][2]+12<x0
                and abs((cell['bbox'][1]+cell['bbox'][3])/2-cy)<=max(y1-y0,cell['bbox'][3]-cell['bbox'][1])*.45]
            if not labels:continue
            label=max(labels,key=lambda cell:cell['bbox'][2])
            pairs.append([label,value])
        pairs.sort(key=lambda pair:pair[0]['bbox'][1])
        # Repetition requires actual non-overlapping label/value pairs.
        if len(pairs)<3:continue
        runs=[]
        bridges=[]
        for pair in pairs:
            aligned=runs and (abs(pair[0]['bbox'][0]-runs[-1][0][0]['bbox'][0])<=tolerance
                             or abs(pair[0]['bbox'][2]-runs[-1][0][0]['bbox'][2])<=tolerance)
            if aligned and block_boundary(runs[-1][-1],pair):aligned=False
            close=aligned and pair[0]['bbox'][1]-runs[-1][-1][0]['bbox'][3]<=median_height*4
            bridge=continuation(runs[-1][-1],pair) if aligned and not close else None
            if close or bridge:
                runs[-1].append(pair)
                if bridge:bridges[-1].append(bridge)
            else:
                runs.append([pair])
                bridges.append([])
        for run,continuity in zip(runs,bridges):
            gutter=(max(pair[0]['bbox'][2] for pair in run),min(pair[1]['bbox'][0] for pair in run))
            if gutter[1]-gutter[0]<12:continue
            def distinct_rows(a,b):
                ha=a['bbox'][3]-a['bbox'][1];hb=b['bbox'][3]-b['bbox'][1]
                return (sum(b['bbox'][1::2])-sum(a['bbox'][1::2]))/2>=min(ha,hb)*.55 and a['bbox'][3]-b['bbox'][1]<=max(ha,hb)*.30
            if any(not distinct_rows(run[i][j],run[i+1][j]) for i in range(len(run)-1) for j in (0,1)):continue
            if len(run)<3:
                sparse.append((run,gutter,continuity,len(group)))
            else:candidates.append((run,gutter,continuity))
    if not candidates and not sparse and not close_pairs:return None
    def layout(candidate):
        run,gutter,continuity=candidate
        return {'status':'candidate','verification':'ocr_geometry_candidate_only','cell_values_verified':False,
        'alignment_method':'regional_numeric_right_edge_with_same_line_labels',
        'scope':'local_observed_pairs_not_complete_table','candidate_count':len(candidates),
        'row_count':len(run),'column_count':2,'ragged_row_count':0,
        'confidence':round(sum(cell['confidence'] for pair in run for cell in pair)/(len(run)*2),4),
        'column_gutters_px':[list(gutter)],'rows':[[dict(cell) for cell in pair] for pair in run],
        'continuity_evidence':continuity,
        'limitations':['headers_not_inferred','multiline_labels_not_reconstructed','omitted_rows_not_proven_absent']}
    chosen=sorted(candidates,key=lambda candidate:(candidate[0][0][0]['bbox'][1],candidate[0][0][0]['bbox'][0]))[:16]
    primary=layout(max(chosen,key=lambda value:len(value[0]))) if chosen else {
        'status':'candidate','scope':'sparse_observed_pairs_not_table','is_table':False,
        'row_count':0,'column_count':0,'ragged_row_count':0,'rows':[],
        'cell_values_verified':False,'verification':'ocr_geometry_candidate_only'}
    primary['regional_candidates']=[layout(candidate) for candidate in chosen]
    primary['regional_candidates_truncated']=len(candidates)>16
    primary['sparse_observations']=[{**layout(item[:3]),'is_table':False,
        'scope':'sparse_pairs_with_repeated_numeric_column','numeric_column_observation_count':item[3]}
        for item in sorted(sparse,key=lambda item:(item[0][0][0]['bbox'][1],item[0][0][0]['bbox'][0]))[:32]]
    primary['sparse_observations_truncated']=len(sparse)>32
    existing=primary['sparse_observations']
    primary['sparse_observations']=[*existing,*close_pairs][:32]
    primary['sparse_observations_truncated']=primary['sparse_observations_truncated'] or len(existing)+len(close_pairs)>32
    return primary


def infer_ocr_table_layout(regions: list[dict[str, Any]] | None) -> dict[str, Any]:
    """Build a conservative table *candidate* from OCR boxes.

    This is layout evidence only. It never promotes OCR text to a verified
    cell value; native-grid or visible-cell corroboration remains required for
    factual table answers. Rows are grouped by vertical overlap and columns
    by their left-to-right centers, with a confidence penalty for ragged rows.
    """
    if not isinstance(regions, list) or len(regions) > 2000:
        return {'status': 'region_budget_exceeded' if isinstance(regions, list) else 'insufficient_regions',
                'row_count': 0, 'column_count': 0, 'rows': []}
    valid = []
    for item in regions or []:
        try:
            bbox = item.get("bbox")
            text = str(item.get("text") or "").strip()
            confidence = float(item.get("confidence"))
            if len(bbox) != 4 or not text or len(text) > 4000 or not 0 <= confidence <= 1:
                continue
            if all(isinstance(point, (tuple, list)) and len(point) == 2 for point in bbox):
                points = [[float(value) for value in point] for point in bbox]
                x0, y0 = min(p[0] for p in points), min(p[1] for p in points)
                x1, y1 = max(p[0] for p in points), max(p[1] for p in points)
            else:
                x0, y0, x1, y1 = [float(value) for value in bbox]
            if not all(math.isfinite(value) for value in (x0, y0, x1, y1)):
                continue
            if not (x1 > x0 and y1 > y0):
                continue
            valid.append({"text": text, "bbox": [x0, y0, x1, y1], "confidence": confidence,
                **{key:item[key] for key in ('observation_origin','crop_sha256','crop_bbox_px') if key in item}})
        except (AttributeError, TypeError, ValueError):
            continue
    if len(valid) < 4:
        return {"status": "insufficient_regions", "row_count": 0, "column_count": 0, "rows": []}
    valid.sort(key=lambda item: (item["bbox"][1], item["bbox"][0]))
    rows: list[list[dict[str, Any]]] = []
    for item in valid:
        x0, y0, x1, y1 = item["bbox"]
        cy = (y0 + y1) / 2
        height = y1 - y0
        target = None
        for row in rows:
            centers = [(cell["bbox"][1] + cell["bbox"][3]) / 2 for cell in row]
            median_y = sorted(centers)[len(centers) // 2]
            row_height = sum(cell["bbox"][3] - cell["bbox"][1] for cell in row) / len(row)
            if abs(cy - median_y) <= max(height, row_height) * 0.6:
                target = row
                break
        if target is None:
            target = []
            rows.append(target)
        target.append(item)
    rows = [sorted(row, key=lambda cell: cell["bbox"][0]) for row in rows if row]
    if len(rows) < 2:
        return {"status": "insufficient_rows", "row_count": len(rows), "column_count": 0, "rows": []}
    column_count = max(len(row) for row in rows)
    if column_count < 2:
        return {"status": "insufficient_columns", "row_count": len(rows), "column_count": column_count, "rows": []}
    ragged = sum(1 for row in rows if len(row) != column_count)
    full_rows = [row for row in rows if len(row) == column_count]
    if any(row[column]['bbox'][2]>=row[column+1]['bbox'][0] for row in rows for column in range(len(row)-1)):
        return _regional_numeric_table(valid) or {'status':'unbound_columns','row_count':len(rows),'column_count':column_count,
                'ragged_row_count':ragged,'rows':[]}
    centers = [[(cell['bbox'][0] + cell['bbox'][2]) / 2 for cell in row] for row in full_rows]
    aligned = 0
    if centers:
        anchors = [sorted(row[column] for row in centers)[len(centers) // 2] for column in range(column_count)]
        for row, positions in zip(full_rows, centers):
            if all(abs(value - anchor) <= max(12, (cell['bbox'][2] - cell['bbox'][0]) * .65)
                   for value, anchor, cell in zip(positions, anchors, row)):
                aligned += 1
    alignment_method='center_alignment'
    gutters=[]
    # Variable-length amounts are commonly right aligned. Their centers can
    # drift by hundreds of pixels while the actual column edges stay fixed.
    # Only admit this fallback with repeated rows AND visible global gutters;
    # a pair of prose lines is insufficient evidence for a borderless table.
    if len(full_rows)>=3:
        heights=sorted(cell['bbox'][3]-cell['bbox'][1] for row in full_rows for cell in row)
        tolerance=max(6,heights[len(heights)//2]*.45)
        stable=[]
        for column in range(column_count):
            edges=[[row[column]['bbox'][edge] for row in full_rows] for edge in (0,2)]
            spreads=[max(values)-min(values) for values in edges]
            stable.append(min(spreads)<=tolerance)
        gutters=[(max(row[column]['bbox'][2] for row in full_rows),
                  min(row[column+1]['bbox'][0] for row in full_rows)) for column in range(column_count-1)]
        if all(stable) and all(right-left>=max(12,heights[len(heights)//2]) for left,right in gutters):
            aligned=len(full_rows)
            alignment_method='repeated_left_or_right_edges_with_global_gutters'
    if aligned < 2 or aligned / len(rows) < .7:
        return _regional_numeric_table(valid) or {'status': 'unbound_columns', 'row_count': len(rows), 'column_count': column_count,
                'ragged_row_count': ragged, 'rows': []}
    confidence = sum(cell["confidence"] for row in rows for cell in row) / sum(len(row) for row in rows)
    confidence *= max(0.0, 1.0 - ragged / len(rows))
    return {
        "status": "candidate",
        "verification": "ocr_geometry_candidate_only",
        "alignment_method": alignment_method,
        "column_gutters_px": [list(gap) for gap in gutters] if alignment_method!='center_alignment' else [],
        "cell_values_verified": False,
        "row_count": len(rows),
        "column_count": column_count,
        "confidence": round(confidence, 4),
        "ragged_row_count": ragged,
        "rows": [[dict(cell)
                   for cell in row] for row in rows],
    }


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
    if os.getenv("ICT8_OCR_ENGINE", "").strip().lower() == "rapidocr":
        return OcrPipeline(RapidOcrExecutor())
    return None


class RapidOcrExecutor:
    """Local CPU OCR with packaged Chinese/English ONNX models; no remote service."""
    name = "rapidocr"

    def __init__(self):
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        if self._model is None:
            try:
                from rapidocr_onnxruntime import RapidOCR
                from .orientation_quality import assess_text_orientation
                class AuditedRapidOCR(RapidOCR):
                    # Observe the existing pass before recognition-score filtering.
                    # No second inference, classifier substitution or shared global state.
                    orientation_evidence = None
                    def get_final_res(self, dt_boxes, cls_res, rec_res, det_elapse, cls_elapse, rec_elapse):
                        boxes = dt_boxes.tolist() if hasattr(dt_boxes,'tolist') else dt_boxes
                        self.orientation_evidence = assess_text_orientation(boxes,cls_res,rec_res)
                        return super().get_final_res(dt_boxes,cls_res,rec_res,det_elapse,cls_elapse,rec_elapse)
                self._model = AuditedRapidOCR(intra_op_num_threads=2, inter_op_num_threads=1)
            except Exception as exc:
                raise OcrExecutionError("RapidOCR 模型不可用，请安装独立项目依赖") from exc
        return self._model

    def health(self):
        try:
            with self._lock:
                self._load()
            return {"ready": True, "provider": self.name, "languages": ["Chinese", "English"], "execution": "local_cpu"}
        except OcrExecutionError:
            return {"ready": False, "provider": self.name, "detail": "模型初始化失败"}

    def recognize_line(self,image_bytes):
        with Image.open(BytesIO(image_bytes)) as image:
            if image.width*image.height>1_000_000:raise OcrExecutionError('局部行识别超过像素预算')
        with self._lock:
            found,_=self._load()(image_bytes,use_det=False,use_cls=False,use_rec=True)
        return [{'text':str(row[0]),'confidence':float(row[1])} for row in (found or [])]

    def execute(self, image_bytes: bytes, *, language: str) -> OcrResponse:
        if language not in {"eng", "chi_sim", "chi_sim+eng", "zh", "en"}:
            raise OcrExecutionError("RapidOCR 本配置只支持中文简体/英文；不得冒充其他语言识别")
        try:
            with Image.open(BytesIO(image_bytes)) as image:
                if image.width*image.height>MAX_PIXELS:
                    raise OcrExecutionError('图片像素数超过安全上限')
                input_size = [image.width,image.height]
            with self._lock:
                model = self._load()
                model.orientation_evidence = None
                results, timings = model(image_bytes, use_cls=True)
                orientation = model.orientation_evidence
                if orientation is None:
                    from .orientation_quality import assess_text_orientation
                    orientation = assess_text_orientation([],[],[])
            regions = [{'bbox': [[float(x), float(y)] for x, y in row[0]], 'text': str(row[1]), 'confidence': float(row[2])} for row in (results or [])]
            from .orientation_quality import upright_reading_order
            regions = upright_reading_order(regions,input_size,orientation)
            text = '\n'.join(item['text'] for item in regions)
            total = sum(len(item['text']) for item in regions)
            confidence = sum(item['confidence'] * len(item['text']) for item in regions) / total if total else 0.0
            table_layout = infer_ocr_table_layout(regions)
            from .scanned_table_layout import extract_scanned_grids, recover_grid_cells,recover_borderless_rows,recover_merged_amount_pairs
            try:
                scanned_grids = extract_scanned_grids(image_bytes, regions)
                def recognize_cell(data):
                    with self._lock:
                        found, _ = self._load()(data, use_cls=True)
                    return [{'text': str(row[1]), 'confidence': float(row[2])} for row in (found or [])]
                scanned_grids = recover_grid_cells(image_bytes, scanned_grids, recognize_cell)
                def recognize_borderless(data):
                    with self._lock:found,_=self._load()(data,use_cls=True)
                    return [{'text':str(row[1]),'confidence':float(row[2]),'bbox':row[0]} for row in (found or [])]
                extra,recovery=recover_borderless_rows(image_bytes,table_layout,regions,recognize_borderless)
                if recovery['recovered_regions']:
                    recovered_layout=infer_ocr_table_layout(extra)
                    if (recovered_layout.get('status')=='candidate' and recovered_layout['column_count']==table_layout['column_count']
                        and recovered_layout['row_count']==table_layout['row_count']
                        and recovered_layout['ragged_row_count']<table_layout['ragged_row_count']):
                        table_layout=recovered_layout
                        recovery['accepted']=True
                    else:recovery['accepted']=False
                table_layout['cell_recovery']=recovery
                def recognize_crop_line(data):
                    with self._lock:found,_=self._load()(data,use_det=False,use_cls=False,use_rec=True)
                    return [{'text':str(row[0]),'confidence':float(row[1])} for row in (found or [])]
                merged,merged_audit=recover_merged_amount_pairs(image_bytes,regions,recognize_borderless,
                    recognize_line=recognize_crop_line)
                if merged:
                    if table_layout.get('status')!='candidate':
                        table_layout={'status':'candidate','is_table':False,'scope':'sparse_observed_pairs_not_table',
                            'rows':[],'row_count':0,'column_count':0,'ragged_row_count':0,'cell_values_verified':False}
                    existing=table_layout.get('sparse_observations',[])
                    table_layout['sparse_observations']=[*existing,*merged][:32]
                    table_layout['sparse_observations_truncated']=table_layout.get('sparse_observations_truncated',False) or len(existing)+len(merged)>32
                table_layout['merged_pair_recovery']=merged_audit
            except (ImportError, ValueError, RuntimeError):
                # Optional layout analysis must not erase successful OCR.
                scanned_grids = {'status': 'layout_analysis_unavailable', 'tables': []}
            return OcrResponse(text, confidence, {"provider": self.name, "regions": regions,
                "orientation":orientation,
                "table_layout": table_layout,
                "scanned_grids": scanned_grids,
                "reading_order":"geometric_local_line_intercepts_and_prose_columns_not_semantic_layout" if orientation['status']=='estimated' else 'engine_order_orientation_undetermined',
                "coordinate_frame":{"scope":"ocr_executor_input","size_px":input_size,"sha256":hashlib.sha256(image_bytes).hexdigest()},
                "timings_seconds": [float(t) for t in (timings or [])], "language_model": "Chinese_English"})
        except OcrExecutionError:
            raise
        except Exception as exc:
            raise OcrExecutionError("RapidOCR 本地识别失败") from exc
