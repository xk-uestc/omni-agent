"""赛题八结构化问数最小 HTTP 服务。"""

from __future__ import annotations

import base64
import binascii
import hmac
import os
import json
import queue
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .cross_source import CrossSourceAgent, JsonDocumentRetriever
from .clarification import ClarificationResolver, ClarificationSelection
from .document_analysis import DocumentAnalyzer, PageSignal
from .formula_binding import FormulaBinder, ParameterEvidence
from .knowledge_store import KnowledgeStore
from .dependency_agent import DependencyAgent
from .image_quality import ImageEnhancer, ImageQualityAnalyzer
from .pdf_ingest import PdfIngestor
from .chunk_cleaning import DocumentChunker
from .ocr import build_ocr_pipeline
from .config import env_float, env_int
from .nl2sql.engine import Nl2SqlEngine
from .retrieval_adapter import HttpManualRetriever
from .nl2sql.model_contract import HttpModelPlanProvider
from .nl2sql.responses_provider import ResponsesModelPlanProvider
from .nl2sql.annotation import validate_schema_annotations
from .production_audit import to_production_audit
from .session import ConversationStore
from .nl2sql.seed import initialize_database
from .nl2sql.security import SqlSafetyError


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "demo_sales.sqlite"
DEFAULT_DOCUMENTS = ROOT / "data" / "knowledge_documents.json"
DATABASE_PATH = Path(os.getenv("ICT8_DB_PATH", str(DEFAULT_DATABASE)))
ALIASES_PATH = os.getenv("ICT8_SCHEMA_ALIASES", "").strip() or None
CONFIG_WARNINGS: list[str] = []
RUNTIME_ENV = os.getenv("ICT8_ENV", "development").strip().lower() or "development"
PRODUCTION_ENVIRONMENTS = {"production", "prod"}
IS_PRODUCTION = RUNTIME_ENV in PRODUCTION_ENVIRONMENTS


def _validate_upstream_url(name: str, value: str) -> None:
    """生产环境禁止把远程模型/检索请求发到明文 HTTP。"""

    if not IS_PRODUCTION or not value:
        return
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not host:
        raise RuntimeError(f"{name} 在生产环境必须是包含主机名的 HTTP(S) URL")
    if parsed.scheme == "https":
        return
    if parsed.scheme == "http" and host in {"127.0.0.1", "localhost", "::1"}:
        CONFIG_WARNINGS.append(f"{name}: production allows loopback HTTP only")
        return
    raise RuntimeError(f"{name} 在生产环境必须使用 HTTPS（仅允许回环地址使用 HTTP）")


def _require_production_token(name: str, url: str, token: str) -> None:
    if IS_PRODUCTION and url and not token:
        raise RuntimeError(f"{name} 已配置但缺少对应 token，生产模式拒绝启动")


planner_url = os.getenv("ICT8_PLAN_URL", "").strip()
planner_token = os.getenv("ICT8_PLAN_TOKEN", "").strip()
_validate_upstream_url("ICT8_PLAN_URL", planner_url)
_require_production_token("ICT8_PLAN_URL", planner_url, planner_token)
model_plan_provider = None
planner_source = "rules"
if planner_url and planner_token:
    model_plan_provider = HttpModelPlanProvider(
        planner_url,
        planner_token,
        timeout=env_float("ICT8_PLAN_TIMEOUT", 8.0, minimum=0.1, maximum=60.0, warnings=CONFIG_WARNINGS),
        max_retries=env_int("ICT8_PLAN_RETRIES", 1, minimum=0, maximum=2, warnings=CONFIG_WARNINGS),
    )
    planner_source = "http_model_with_rules_fallback"
elif planner_url:
    CONFIG_WARNINGS.append("ICT8_PLAN_URL: token missing; model planner disabled")
if os.getenv("ICT8_PLAN_PROVIDER", "").strip().lower() == "responses":
    responses_url = os.getenv("ICT8_OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    responses_token = os.getenv("ICT8_OPENAI_API_KEY", "").strip() or os.getenv("OPENAI_API_KEY", "").strip()
    responses_model = os.getenv("ICT8_OPENAI_MODEL", "").strip()
    _validate_upstream_url("ICT8_OPENAI_BASE_URL", responses_url)
    _require_production_token("ICT8_OPENAI_BASE_URL", responses_url, responses_token)
    if responses_token and responses_model:
        model_plan_provider = ResponsesModelPlanProvider(responses_url, responses_token, model=responses_model,
            reasoning_effort=os.getenv("ICT8_OPENAI_REASONING", "medium").strip(),
            timeout=env_float("ICT8_PLAN_TIMEOUT", 45.0, minimum=0.1, maximum=60.0, warnings=CONFIG_WARNINGS),
            max_retries=env_int("ICT8_PLAN_RETRIES", 1, minimum=0, maximum=2, warnings=CONFIG_WARNINGS))
        planner_source = "responses_model_with_validated_rules_fallback"
    else:
        CONFIG_WARNINGS.append("responses planner: model or API key missing; disabled")
        model_plan_provider, planner_source = None, "rules"
if not DATABASE_PATH.exists():
    # 只有默认演示库允许自动创建；显式配置的业务库缺失时必须启动失败，
    # 避免生产误配置后静默对外提供演示数据。
    if DATABASE_PATH.resolve() == DEFAULT_DATABASE.resolve() or os.getenv("ICT8_DEMO_MODE", "").strip() == "1":
        initialize_database(DATABASE_PATH)
    else:
        raise RuntimeError(f"ICT8_DB_PATH 指向的数据库不存在: {DATABASE_PATH.name}")

engine = Nl2SqlEngine(
    DATABASE_PATH,
    aliases_path=ALIASES_PATH,
    model_plan_provider=model_plan_provider,
    model_min_confidence=env_float("ICT8_PLAN_MIN_CONFIDENCE", 0.5, minimum=0.0, maximum=1.0, warnings=CONFIG_WARNINGS),
)
if isinstance(model_plan_provider, ResponsesModelPlanProvider):
    model_plan_provider.catalog = engine.metric_catalog
    model_plan_provider.reference_date = engine.reference_date
STREAM_TIMEOUT_SECONDS = env_float("ICT8_STREAM_TIMEOUT", 60.0, minimum=1.0, maximum=600.0, warnings=CONFIG_WARNINGS)
STREAM_SLOTS = threading.BoundedSemaphore(env_int("ICT8_STREAM_CONCURRENCY", 16, minimum=1, maximum=256, warnings=CONFIG_WARNINGS))
DOCUMENT_PARSE_SLOTS = threading.BoundedSemaphore(env_int("ICT8_DOCUMENT_PARSE_CONCURRENCY", 4, minimum=1, maximum=64, warnings=CONFIG_WARNINGS))
manual_url = os.getenv("ICT8_MANUAL_RETRIEVER_URL", "").strip()
manual_token = os.getenv("ICT8_MANUAL_RETRIEVER_TOKEN", "").strip()
_validate_upstream_url("ICT8_MANUAL_RETRIEVER_URL", manual_url)
_require_production_token("ICT8_MANUAL_RETRIEVER_URL", manual_url, manual_token)
if manual_url and manual_token:
    document_retriever = HttpManualRetriever(
        manual_url,
        manual_token,
        timeout=env_float(
            "ICT8_MANUAL_RETRIEVER_TIMEOUT",
            8.0,
            minimum=0.1,
            maximum=60.0,
            warnings=CONFIG_WARNINGS,
        ),
        max_retries=env_int(
            "ICT8_MANUAL_RETRIEVER_RETRIES",
            1,
            minimum=0,
            maximum=3,
            warnings=CONFIG_WARNINGS,
        ),
    )
    document_source = "vnext_http"
else:
    document_retriever = JsonDocumentRetriever.from_file(DEFAULT_DOCUMENTS)
    document_source = "local_json"
agent = CrossSourceAgent(engine, document_retriever)
session_db = os.getenv("ICT8_SESSION_DB", "").strip() or None
conversation_store = ConversationStore(storage_path=session_db)
document_analyzer = DocumentAnalyzer()
image_quality_analyzer = ImageQualityAnalyzer()
image_enhancer = ImageEnhancer()
pdf_ingestor = PdfIngestor(document_analyzer)
document_chunker = DocumentChunker()
ocr_pipeline = build_ocr_pipeline(CONFIG_WARNINGS)
embedder = None
dense_path = os.getenv('ICT8_DENSE_MODEL_PATH', '').strip()
if dense_path:
    from .dense_retrieval import LocalBgeEmbedder
    embedder = LocalBgeEmbedder(dense_path)
generation_client = None
if os.getenv('ICT8_GENERATION_PROVIDER', '').lower() == 'responses':
    from .responses_client import StructuredResponses
    generation_client = StructuredResponses(os.getenv('ICT8_OPENAI_BASE_URL', 'https://api.openai.com/v1'),
        os.getenv('ICT8_OPENAI_API_KEY', '') or os.getenv('OPENAI_API_KEY', ''),
        model=os.getenv('ICT8_OPENAI_MODEL', ''), reasoning=os.getenv('ICT8_OPENAI_REASONING', 'medium'))
generator = None
if generation_client:
    from .grounded_generation import GroundedGenerator
    generator = GroundedGenerator(generation_client)
knowledge_store = KnowledgeStore(os.getenv("ICT8_KNOWLEDGE_ROOT", str(ROOT / "runtime/knowledge")), ocr_pipeline=ocr_pipeline, embedder=embedder, generator=generator)
if os.getenv("ICT8_KNOWLEDGE_ROOT", "").strip():
    document_retriever = knowledge_store
    document_source = "independent_corpus"
    agent = CrossSourceAgent(engine, document_retriever)
clarification_resolver = ClarificationResolver()
app = FastAPI(title="ICT Track 8 Structured QA", version="0.1.0")
allowed_origins = [
    item.strip()
    for item in os.getenv("ICT8_CORS_ORIGINS", "http://127.0.0.1:8021,http://localhost:8021").split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    # 生产 API 使用 Bearer 鉴权；必须显式放行预检中的 Authorization，
    # 否则浏览器请求会在到达鉴权中间件前被 CORS 拦截。
    allow_headers=["Content-Type", "Authorization"],
)

API_MAX_BODY_BYTES = env_int(
    "ICT8_MAX_BODY_BYTES",
    32 * 1024 * 1024,
    minimum=1 * 1024,
    maximum=64 * 1024 * 1024,
    warnings=CONFIG_WARNINGS,
)
API_TOKEN = os.getenv("ICT8_API_TOKEN", "").strip()


@app.middleware("http")
async def request_security(request: Request, call_next):
    """统一生产鉴权和请求体限制；开发模式保持原有本地 demo 行为。"""

    is_health = request.url.path == "/health"
    if IS_PRODUCTION and not is_health and request.method != "OPTIONS":
        if not API_TOKEN:
            return JSONResponse(
                status_code=503,
                content={"detail": {"code": "api_auth_not_configured", "message": "生产 API 未配置 ICT8_API_TOKEN"}},
            )
        authorization = request.headers.get("authorization", "")
        expected = f"Bearer {API_TOKEN}"
        if not hmac.compare_digest(authorization, expected):
            return JSONResponse(
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
                content={"detail": {"code": "unauthorized", "message": "需要有效的 Bearer token"}},
            )

    if request.method in {"POST", "PUT", "PATCH"}:
        declared = request.headers.get("content-length")
        try:
            if declared is not None and int(declared) > API_MAX_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"detail": {"code": "request_too_large", "message": "请求体超过大小限制"}},
                )
        except ValueError:
            return JSONResponse(
                status_code=400,
                content={"detail": {"code": "invalid_content_length", "message": "Content-Length 无效"}},
            )
        body = await request.body()
        if len(body) > API_MAX_BODY_BYTES:
            return JSONResponse(
                status_code=413,
                content={"detail": {"code": "request_too_large", "message": "请求体超过大小限制"}},
            )

    return await call_next(request)


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    max_rows: int = Field(default=100, ge=1, le=100)


class AgentQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k_documents: int = Field(default=4, ge=1, le=10)
    session_id: str | None = Field(default=None, max_length=128)
    use_context: bool = True


class PageSignalRequest(BaseModel):
    page_no: int = Field(ge=1)
    text: str = ""
    ocr_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    rotation_degrees: float = Field(default=0.0, ge=-360.0, le=360.0)
    skew_degrees: float = Field(default=0.0, ge=-90.0, le=90.0)
    blur_score: float | None = Field(default=None, ge=0.0, le=1.0)
    scanned: bool = False


class DocumentAnalysisRequest(BaseModel):
    document_id: str = Field(default="document", min_length=1, max_length=200)
    text: str = Field(default="", max_length=500_000)
    pages: list[PageSignalRequest] = Field(default_factory=list, max_length=500)


class FormulaParameterRequest(BaseModel):
    value: float = Field(allow_inf_nan=False)
    source_uri: str = Field(min_length=1, max_length=500)
    locator: str = Field(min_length=1, max_length=500)
    unit: str = Field(default="unknown", max_length=64)


class FormulaCalculationRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=300)
    parameters: dict[str, FormulaParameterRequest] = Field(default_factory=dict, max_length=32)
    formula_source: str = Field(min_length=1, max_length=500)
    formula_locator: str = Field(min_length=1, max_length=500)


class KnowledgeIngestRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    modality: str = Field(pattern=r"^(pdf|docx|xlsx|txt|md|image)$")
    filename: str = Field(min_length=1, max_length=200)
    file_base64: str = Field(min_length=1, max_length=28_000_000)
    language: str = Field(default="eng", min_length=2, max_length=32)


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=10)


class DependencyQueryRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(min_length=1, max_length=16)


class ImageQualityRequest(BaseModel):
    image_base64: str = Field(min_length=1, max_length=12_000_000)


class ImageEnhancementRequest(ImageQualityRequest):
    transforms: list[str] = Field(default_factory=list, max_length=8)
    rotation_degrees: float = Field(default=0.0, ge=-45.0, le=45.0)
    crop_box: list[int] | None = Field(default=None, min_length=4, max_length=4)
    max_dimension: int = Field(default=2400, ge=1, le=6000)


class PdfAnalysisRequest(BaseModel):
    document_id: str = Field(default="document.pdf", min_length=1, max_length=200)
    pdf_base64: str = Field(min_length=1, max_length=28_000_000)


class ChunkPreviewRequest(BaseModel):
    document_id: str = Field(default="document", min_length=1, max_length=200)
    modality: str = Field(pattern=r"^(pdf|docx|xlsx|image)$")
    file_base64: str = Field(min_length=1, max_length=28_000_000)
    language: str = Field(default="eng", min_length=2, max_length=32)


class OcrRequest(ImageQualityRequest):
    language: str = Field(default="eng", min_length=2, max_length=32)
    max_attempts: int = Field(default=3, ge=1, le=3)


class ClarificationRequest(BaseModel):
    original_question: str = Field(min_length=1, max_length=1000)
    clarification_code: str = Field(min_length=1, max_length=80)
    selected_value: str = Field(min_length=1, max_length=120)
    selected_label: str | None = Field(default=None, max_length=120)
    session_id: str | None = Field(default=None, max_length=128)
    top_k_documents: int = Field(default=4, ge=1, le=10)


class OmniRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=128)
    reset_context: bool = False


@app.post('/api/v1/omni/query')
def omni_query(request: OmniRequest):
    from .omni_agent import OmniAgent
    try:
        return OmniAgent(engine, knowledge_store, conversation_store, generation_client).query(
            request.question, session_id=request.session_id, reset_context=request.reset_context)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "ict-track8-nl2sql",
        "database": DATABASE_PATH.name,
        "document_source": document_source,
        "planner_source": planner_source,
        "retrieval": knowledge_store.retrieval_health(),
        "generation": {'mode': 'responses' if generator else 'attributed_extracts', 'model': generation_client.model if generation_client else None},
        "configuration_warnings": list(CONFIG_WARNINGS),
    }


@app.get("/api/v1/nl2sql/schema")
def schema() -> dict[str, object]:
    try:
        return engine.schema()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/knowledge/documents")
def knowledge_documents():
    return {"documents": knowledge_store.list_documents()}


@app.get('/api/v1/specification')
def official_specification():
    path = ROOT.parent/'specification/official-track8.pdf'
    if not path.exists():
        raise HTTPException(status_code=404, detail='当前部署未包含赛题文档')
    return FileResponse(path, media_type='application/pdf', content_disposition_type='inline')


@app.get('/api/v1/capabilities')
def capabilities():
    path = ROOT.parent/'docs/coverage.json'
    if not path.exists():
        return {'items': [], 'status': 'not_available'}
    result = json.loads(path.read_text(encoding='utf-8'))
    # This is a saved diagnostic, not a live health guarantee or a credential file.
    probe = ROOT.parent/'docs/MODEL_API_PROBE.json'
    if probe.exists():
        report = json.loads(probe.read_text(encoding='utf-8'))
        result['last_model_probe'] = {key: report.get(key) for key in ('created_at', 'model', 'status', 'checks')}
    return result


@app.post("/api/v1/knowledge/ingest")
def knowledge_ingest(request: KnowledgeIngestRequest):
    if not DOCUMENT_PARSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="文档解析繁忙，请稍后重试")
    try:
        raw = base64.b64decode(request.file_base64, validate=True)
        return knowledge_store.ingest(raw, document_id=request.document_id, title=request.title,
            modality=request.modality, filename=request.filename, language=request.language)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail=str(exc)[:200]) from exc
    finally:
        DOCUMENT_PARSE_SLOTS.release()


@app.get("/api/v1/knowledge/documents/{document_id}")
def knowledge_document(document_id: str):
    try:
        return knowledge_store.document(document_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文档不存在") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/knowledge/documents/{document_id}/original")
def knowledge_original(document_id: str):
    try:
        path, filename = knowledge_store.original(document_id)
        return FileResponse(path, filename=filename, content_disposition_type="inline")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文档不存在") from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail="原文件完整性检查失败") from exc


@app.post("/api/v1/knowledge/query")
def knowledge_query(request: KnowledgeQueryRequest):
    try:
        return knowledge_store.answer(request.question, top_k=request.top_k)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/fusion/execute")
def execute_dependency_plan(request: DependencyQueryRequest):
    try:
        return DependencyAgent(engine, knowledge_store).run(request.tasks)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_dependency_plan", "message": str(exc)[:200]}) from exc


@app.get("/api/v1/nl2sql/schema/annotation-report")
def schema_annotation_report() -> dict[str, object]:
    if not ALIASES_PATH:
        return {"status": "not_configured", "message": "未配置 ICT8_SCHEMA_ALIASES"}
    try:
        with engine._connect() as connection:
            tables = engine.introspector.introspect(connection)
        return validate_schema_annotations(tables, ALIASES_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.post("/api/v1/nl2sql/query")
def query(request: QueryRequest) -> dict[str, object]:
    try:
        # 与 /agent/query 共用同一引擎（同一规划器、模型门控和缓存），避免两个入口答案不一致
        result = engine.answer(request.question, max_rows=request.max_rows)
        return result.to_dict()
    except SqlSafetyError as exc:
        raise HTTPException(status_code=422, detail={"code": "sql_rejected_or_limited", "message": "查询超出安全执行限制"}) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_request", "message": str(exc)[:200]}) from exc


@app.post("/api/v1/agent/query")
def agent_query(request: AgentQueryRequest) -> dict[str, object]:
    try:
        session_id = ConversationStore.validate_id(request.session_id) if request.session_id else None
        turns = conversation_store.context(session_id) if session_id and request.use_context else ()
        context = (turns[-1].effective_question,) if turns else ()
        result = agent.answer(
            request.question,
            top_k_documents=request.top_k_documents,
            context_questions=context,
        )
        if session_id:
            conversation_store.remember(
                session_id,
                question=request.question,
                effective_question=str(result.get("effective_question") or request.question),
            )
        result["session_id"] = session_id
        return result
    except SqlSafetyError as exc:
        raise HTTPException(status_code=422, detail={"code": "sql_rejected_or_limited", "message": "查询超出安全执行限制"}) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_request", "message": str(exc)[:200]}) from exc


@app.post("/api/v1/agent/query/stream")
def agent_query_stream(request: AgentQueryRequest) -> StreamingResponse:
    """以 SSE 顺序发送真实阶段 trace，最后发送完整 done 快照。"""
    try:
        session_id = ConversationStore.validate_id(request.session_id) if request.session_id else None
        turns = conversation_store.context(session_id) if session_id and request.use_context else ()
        context = (turns[-1].effective_question,) if turns else ()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


    events: queue.Queue[tuple[str, object]] = queue.Queue()
    result_holder: dict[str, object] = {}
    trace_history: list[dict[str, Any]] = []

    def publish(item: dict[str, Any]) -> None:
        trace_history.append(item)
        events.put(("trace", item))
        if item.get("stage") == "evidence_fusion":
            retrieval_stage = next(
                (entry for entry in trace_history if entry.get("stage") == "document_retrieval"),
                {},
            )
            audit_status = "partial" if retrieval_stage.get("status") == "degraded" else "ok"
            events.put(("audit", to_production_audit(trace_history, status=audit_status)))

    def worker() -> None:
        try:
            result_holder["result"] = agent.answer(
                request.question,
                top_k_documents=request.top_k_documents,
                context_questions=context,
                trace_callback=publish,
            )
        except Exception as exc:  # headers 已发送，错误作为 SSE 事件返回（不透传内部异常文本）
            result_holder["error"] = {"code": type(exc).__name__, "message": "查询执行失败"}
        finally:
            events.put(("complete", None))
            STREAM_SLOTS.release()

    if not STREAM_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail={"code": "stream_busy", "message": "并发流式请求过多，请稍后重试"})
    threading.Thread(target=worker, name="ict8-agent-stream", daemon=True).start()

    def generate():
        deadline = time.monotonic() + STREAM_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield f"event: error\ndata: {json.dumps({'detail': {'code': 'timeout', 'message': '查询超时'}}, ensure_ascii=False)}\n\n"
                return
            try:
                kind, payload = events.get(timeout=min(10.0, remaining))
            except queue.Empty:
                yield ": keep-alive\n\n"  # SSE 注释行作为心跳，防止代理断开
                continue
            if kind == "trace":
                yield f"event: trace\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                continue
            if kind == "audit":
                yield f"event: audit\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                continue
            if "error" in result_holder:
                yield f"event: error\ndata: {json.dumps({'detail': result_holder['error']}, ensure_ascii=False)}\n\n"
                return
            result = result_holder["result"]
            if session_id:
                conversation_store.remember(
                    session_id,
                    question=request.question,
                    effective_question=str(result.get("effective_question") or request.question),
                )
            result["session_id"] = session_id
            yield f"event: done\ndata: {json.dumps(result, ensure_ascii=False)}\n\n"
            return

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/v1/documents/analyze")
def analyze_document(request: DocumentAnalysisRequest) -> dict[str, object]:
    pages = tuple(PageSignal(**page.model_dump()) for page in request.pages)
    result = document_analyzer.analyze(request.text, document_id=request.document_id, pages=pages)
    return result.to_dict()


@app.post("/api/v1/documents/formulas/calculate")
def calculate_document_formula(request: FormulaCalculationRequest) -> dict[str, object]:
    try:
        return FormulaBinder().calculate(request.expression,
            {name: ParameterEvidence(**value.model_dump()) for name, value in request.parameters.items()},
            formula_source=request.formula_source, formula_locator=request.formula_locator)
    except (ValueError, SyntaxError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_formula_binding", "message": str(exc)[:200]}) from exc


@app.post("/api/v1/documents/image-quality")
def image_quality(request: ImageQualityRequest) -> dict[str, object]:
    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 不是有效的 Base64") from exc
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片超过 8MB 限制")
    try:
        return image_quality_analyzer.analyze(raw).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/image-enhance")
def image_enhance(request: ImageEnhancementRequest) -> dict[str, object]:
    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 不是有效的 Base64") from exc
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片超过 8MB 限制")
    try:
        crop_box = tuple(request.crop_box) if request.crop_box is not None else None
        result = image_enhancer.enhance(
            raw,
            transforms=tuple(request.transforms),
            rotation_degrees=request.rotation_degrees,
            crop_box=crop_box,
            max_dimension=request.max_dimension,
        )
        if len(result.image_bytes) > 12 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="增强后的图片超过 12MB 限制")
        return {
            **result.to_dict(),
            "image_base64": base64.b64encode(result.image_bytes).decode("ascii"),
            "ocr_executed": False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/nl2sql/clarify")
def clarify(request: ClarificationRequest) -> dict[str, object]:
    try:
        session_id = ConversationStore.validate_id(request.session_id) if request.session_id else None
        enriched_question = clarification_resolver.apply(
            request.original_question,
            ClarificationSelection(
                code=request.clarification_code,
                value=request.selected_value,
                label=request.selected_label,
            ),
        )
        # 澄清选择已经是用户对原问题的补全，不再把旧轮次再次拼接进来。
        # 直接走同一条跨源编排链，确保 SQL、文档证据和完整 trace 一次返回。
        result = agent.answer(enriched_question, top_k_documents=request.top_k_documents)
        if session_id:
            conversation_store.remember(
                session_id,
                question=request.original_question,
                effective_question=str(result.get("effective_question") or enriched_question),
            )
        result["session_id"] = session_id
        return {"enriched_question": enriched_question, "result": result, "session_id": session_id}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/pdf-analyze")
def pdf_analyze(request: PdfAnalysisRequest) -> dict[str, object]:
    try:
        raw = base64.b64decode(request.pdf_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="pdf_base64 不是有效的 Base64") from exc
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="PDF 超过 20MB 限制")
    try:
        return pdf_ingestor.analyze(raw, document_id=request.document_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/ocr")
def ocr(request: OcrRequest) -> dict[str, object]:
    if ocr_pipeline is None:
        raise HTTPException(status_code=503, detail="OCR 执行器未配置；请设置 ICT8_OCR_URL 或 ICT8_OCR_ENGINE=tesseract")
    try:
        raw = base64.b64decode(request.image_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="image_base64 不是有效的 Base64") from exc
    if len(raw) > 8 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图片超过 8MB 限制")
    try:
        return ocr_pipeline.run(raw, language=request.language, max_attempts=request.max_attempts).to_dict()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/documents/chunks-preview")
def chunks_preview(request: ChunkPreviewRequest) -> dict[str, Any]:
    try:
        raw = base64.b64decode(request.file_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="file_base64 不是有效的 Base64") from exc
    if len(raw) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文档超过 20MB 限制")
    if not DOCUMENT_PARSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="文档解析并发已满，请稍后重试")
    try:
        if request.modality == "pdf":
            result = document_chunker.parse_pdf(raw, document_id=request.document_id, ocr_pipeline=ocr_pipeline, language=request.language)
        elif request.modality == "docx":
            result = document_chunker.parse_docx(
                raw,
                document_id=request.document_id,
                ocr_pipeline=ocr_pipeline,
                language=request.language,
            )
        elif request.modality == "xlsx":
            result = document_chunker.parse_xlsx(raw, document_id=request.document_id)
        else:
            result = document_chunker.parse_image(raw, document_id=request.document_id, ocr_pipeline=ocr_pipeline, language=request.language)
    except ValueError as exc:
        message = str(exc)
        status = 413 if "超过上限" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    finally:
        DOCUMENT_PARSE_SLOTS.release()
    return result.to_dict()


@app.get("/api/v1/documents/ocr/health")
def ocr_health() -> dict[str, object]:
    if ocr_pipeline is None:
        return {"configured": False, "ready": False, "status": "disabled", "warnings": list(CONFIG_WARNINGS)}
    return {"status": "configured", **ocr_pipeline.health()}
