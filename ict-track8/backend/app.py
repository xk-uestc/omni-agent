"""赛题八结构化问数最小 HTTP 服务。"""

from __future__ import annotations

import base64
import binascii
import hmac
import hashlib
import os
import json
import sqlite3
import queue
import threading
import time
from pathlib import Path
from typing import Any, Annotated
from urllib.parse import urlsplit, urlencode, quote

from fastapi import FastAPI, HTTPException, Query, Path as ApiPath
from fastapi import Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict

from .cross_source import CrossSourceAgent, JsonDocumentRetriever
from .clarification import ClarificationResolver, ClarificationSelection
from .formula_binding import FormulaBinder, ParameterEvidence
from .knowledge_store import KnowledgeStore, SourceIntegrityError, SourceRevisionError
from .visual_work_budget import VisualWorkBusy
from .dependency_agent import DependencyAgent
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
from .nl2sql.seed import initialize_database, initialize_rich_demo_data
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
            http_headers=json.loads(os.getenv('ICT8_OPENAI_HEADERS', '{}')),
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
if DATABASE_PATH.resolve() == DEFAULT_DATABASE.resolve():
    initialize_rich_demo_data(DATABASE_PATH)

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
document_chunker = DocumentChunker()
ocr_pipeline = build_ocr_pipeline(CONFIG_WARNINGS)
embedder = None
dense_path = os.getenv('ICT8_DENSE_MODEL_PATH', '').strip()
if dense_path:
    from .dense_retrieval import LocalBgeEmbedder
    embedder = LocalBgeEmbedder(dense_path)
generation_client = None
if os.getenv('ICT8_GENERATION_PROVIDER', '').lower() == 'responses':
    from .responses_client import configured_responses_client
    generation_client = configured_responses_client()
generator = None
if generation_client:
    from .grounded_generation import GroundedGenerator
    generator = GroundedGenerator(generation_client)
knowledge_store = KnowledgeStore(os.getenv("ICT8_KNOWLEDGE_ROOT", str(ROOT / "runtime/knowledge")), ocr_pipeline=ocr_pipeline, embedder=embedder, generator=generator)
if os.getenv("ICT8_KNOWLEDGE_ROOT", "").strip():
    document_retriever = knowledge_store
    document_source = "independent_corpus"
    agent = CrossSourceAgent(engine, document_retriever)
from .memory.core import MemoryCore
from .memory.adapter import MemoryAdapter
memory_core = MemoryCore.from_env()
memory_adapter = MemoryAdapter(memory_core, engine, knowledge_store,
    database_source=os.getenv('ICT8_MEMORY_DATABASE_SOURCE', 'database'),
    formation_enabled=os.getenv('ICT8_MEMORY_FORMATION_ENABLED','0')=='1') if memory_core.enabled else None
from .memory.experience import ExperienceSelector
experience_selector = ExperienceSelector(memory_adapter) if memory_adapter is not None and os.getenv('ICT8_TASK_EXPERIENCE_ENABLED','0')=='1' else None
clarification_resolver = ClarificationResolver()
app = FastAPI(title="ICT Track 8 Structured QA", version="0.1.0")


@app.on_event("startup")
def warm_retrieval_encoder() -> None:
    knowledge_store.warm_dense_index()


@app.exception_handler(VisualWorkBusy)
async def visual_work_busy_handler(request: Request, exc: VisualWorkBusy):
    return JSONResponse(status_code=429, headers={'Retry-After': '2'}, content={'detail': {
        'code': 'visual_render_busy', 'message': '视觉处理并发已满，请稍后重试'}})


@app.exception_handler(sqlite3.DatabaseError)
@app.exception_handler(OSError)
async def storage_unavailable_handler(request: Request, exc: Exception):
    # Do not replace persistent state with a new in-memory identity on failure.
    # The caller can retry after the actual resource has been repaired.
    return JSONResponse(status_code=503,headers={'Retry-After':'2'},content={'detail':{
        'code':'storage_unavailable','message':'数据存储暂不可用，请恢复文件或解除数据库锁后重试。'}})
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
    complete_results: bool = False


class AgentQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k_documents: int = Field(default=4, ge=1, le=10)
    session_id: str | None = Field(default=None, max_length=128)
    use_context: bool = True
    complete_results: bool = False


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


class ExcelTableSelection(BaseModel):
    sheet_name: str = Field(min_length=1, max_length=31)
    range: str = Field(pattern=r'^[A-Z]{1,3}[1-9]\d{0,6}:[A-Z]{1,3}[1-9]\d{0,6}$')
    header_rows: int = Field(ge=0, le=8, strict=True)


class KnowledgeIngestRequest(BaseModel):
    document_id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=200)
    modality: str = Field(pattern=r"^(pdf|docx|xlsx|txt|md|image)$")
    filename: str = Field(min_length=1, max_length=200)
    file_base64: str = Field(min_length=1, max_length=28_000_000)
    language: str = Field(default="eng", min_length=2, max_length=32)
    excel_tables: list[ExcelTableSelection] = Field(default_factory=list, max_length=64)


class KnowledgeQueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    top_k: int = Field(default=4, ge=1, le=10)
    document_id: str | None = Field(default=None, min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$')
    page_no: int | None = Field(default=None, ge=1, le=1000, strict=True)


class VisualEvidenceRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    page_no: int = Field(strict=True, ge=1, le=1000)
    expected_source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    crop_display_pt: tuple[Annotated[float, Field(strict=True, allow_inf_nan=False)],
                           Annotated[float, Field(strict=True, allow_inf_nan=False)],
                           Annotated[float, Field(strict=True, allow_inf_nan=False)],
                           Annotated[float, Field(strict=True, allow_inf_nan=False)]] | None = None


class VisualTableQueryRequest(VisualEvidenceRequest):
    question: str = Field(min_length=1, max_length=1000)


class DependencyQueryRequest(BaseModel):
    tasks: list[dict[str, Any]] = Field(min_length=1, max_length=16)


class PdfTableHeaderSelection(BaseModel):
    page_no: int = Field(ge=1, le=1000)
    table_index: int = Field(default=0, ge=0, le=31)
    header_rows: int = Field(ge=0, le=5)


class PdfTableLink(BaseModel):
    from_page: int = Field(ge=1,le=1000,strict=True)
    from_table: int = Field(ge=0,le=31,strict=True)
    to_page: int = Field(ge=1,le=1000,strict=True)
    to_table: int = Field(ge=0,le=31,strict=True)


class ChunkPreviewRequest(BaseModel):
    document_id: str = Field(default="document", min_length=1, max_length=200)
    modality: str = Field(pattern=r"^(pdf|docx|xlsx|image)$")
    file_base64: str = Field(min_length=1, max_length=28_000_000)
    language: str = Field(default="eng", min_length=2, max_length=32)
    pdf_table_headers: list[PdfTableHeaderSelection] = Field(default_factory=list, max_length=8)
    pdf_table_links: list[PdfTableLink] = Field(default_factory=list, max_length=7)
    excel_tables: list[ExcelTableSelection] = Field(default_factory=list, max_length=64)


class ClarificationRequest(BaseModel):
    original_question: str = Field(min_length=1, max_length=1000)
    clarification_code: str = Field(min_length=1, max_length=80)
    selected_value: str = Field(min_length=1, max_length=120)
    selected_label: str | None = Field(default=None, max_length=120)
    selected_time: str | None = Field(default=None, max_length=32)
    session_id: str | None = Field(default=None, max_length=128)
    top_k_documents: int = Field(default=4, ge=1, le=10)
    complete_results: bool = False


class OmniRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    session_id: str | None = Field(default=None, max_length=128)
    reset_context: bool = False
    complete_results: bool = False
    images: list[Annotated[str, Field(max_length=8_400_000)]] = Field(default_factory=list, max_length=3)


@app.post('/api/v1/omni/query')
def omni_query(request: OmniRequest):
    from .omni_agent import OmniAgent
    from .multimodal_input import decode_chat_images
    try:
        image_attachments = decode_chat_images(request.images)
        return OmniAgent(engine, knowledge_store, conversation_store, generation_client, memory=memory_adapter, experience=experience_selector).query(
            request.question, session_id=request.session_id, reset_context=request.reset_context,
            complete_results=request.complete_results, image_attachments=image_attachments or None)
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409,detail={'code':'evidence_integrity_failed','message':str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc


@app.post('/api/v1/omni/query/stream')
def omni_query_stream(request: OmniRequest):
    """Stream actual unified-agent calls, then the unchanged query snapshot."""
    from .omni_agent import OmniAgent
    from .multimodal_input import decode_chat_images
    try:
        if request.session_id:
            ConversationStore.validate_id(request.session_id)
        if not request.question.strip():
            raise ValueError('问题为空或过长')
        image_attachments = decode_chat_images(request.images)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc
    if not STREAM_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail={'code': 'stream_busy', 'message': '并发流式请求过多，请稍后重试'})
    events = queue.Queue()
    def worker():
        try:
            response = OmniAgent(engine, knowledge_store, conversation_store, generation_client, memory=memory_adapter, experience=experience_selector).query(
                request.question, session_id=request.session_id, reset_context=request.reset_context,
                complete_results=request.complete_results,
                image_attachments=image_attachments or None,
                trace_callback=lambda event: events.put(('trace', event)))
            events.put(('done', response))
        except SourceIntegrityError:
            events.put(('error', {'detail': {'code': 'evidence_integrity_failed',
                                             'message': '来源已变化或原件完整性检查失败'}}))
        except ValueError as exc:
            message = str(exc)[:200] if image_attachments else '请求或查询条件未通过检查'
            events.put(('error', {'detail': {'code': 'invalid_query', 'message': message}}))
        except Exception:
            events.put(('error', {'detail': {'code': 'query_failed', 'message': '查询执行失败'}}))
        finally:
            STREAM_SLOTS.release()
    try:
        threading.Thread(target=worker, name='ict8-omni-stream', daemon=True).start()
    except Exception:
        STREAM_SLOTS.release()
        raise
    def generate():
        deadline = time.monotonic() + STREAM_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield 'event: error\ndata: ' + json.dumps(
                    {'detail': {'code': 'timeout', 'message': '查询超时，后台执行可能仍在继续'}}, ensure_ascii=False) + '\n\n'
                return
            try:
                kind, payload = events.get(timeout=min(10.0, remaining))
            except queue.Empty:
                yield ': keep-alive\n\n'
                continue
            yield f'event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'
            if kind in {'done', 'error'}:
                return
    return StreamingResponse(generate(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/v1/omni/clarify')
def omni_clarify(request: ClarificationRequest):
    """Validate the offered selection and retain the unified conversation state."""
    try:
        with conversation_store.turn(request.session_id):
            return _omni_clarify_turn(request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)[:200]) from exc


def _omni_clarify_turn(request: ClarificationRequest):
    from .omni_agent import OmniAgent
    try:
        pending={}
        if request.session_id:
            history = conversation_store.context(request.session_id)
            pending = (history[-1].state or {}) if history else {}
            if pending.get('pending_question') != request.original_question:
                raise HTTPException(status_code=409,detail='该澄清已过期，请重新提交问题。')
            if pending.get('route') == 'sql':
                from .pending_source_validation import PendingSourceValidationAgent
                if not PendingSourceValidationAgent(engine).matches(pending):
                    raise HTTPException(status_code=409,detail='数据源或业务映射已经更新，请重新提交完整问题。')
        offered = engine.extract_required_intent(request.original_question)
        if not offered.clarification or offered.clarification_code != request.clarification_code:
            raise HTTPException(status_code=400,detail='澄清类型与当前问题不匹配。')
        selected = next((option for option in offered.clarification_options
                         if option['value']==request.selected_value),None)
        if selected is None:
            raise HTTPException(status_code=400,detail='请选择服务端提供的有效选项。')
        question = clarification_resolver.apply(request.original_question,ClarificationSelection(
            code=request.clarification_code,value=selected['value'],label=selected.get('label'),time_value=request.selected_time))
        # A user-confirmed choice already fixes a slot. Do not send it back to
        # the top-level model for another rewrite that could discard the hint.
        return OmniAgent(engine,knowledge_store,conversation_store).query(question,session_id=request.session_id,
            complete_results=request.complete_results,
            _confirmed_comparison_scope=question if pending.get('comparison_pending_query') else None,
            _confirmed_pending_turn_id=history[-1].turn_id if request.session_id and pending.get('route')=='sql' else None)
    except ValueError as exc:
        raise HTTPException(status_code=400,detail=str(exc)[:200]) from exc


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "ict-track8-nl2sql",
        "database": DATABASE_PATH.name,
        "document_source": document_source,
        "planner_source": planner_source,
        "retrieval": knowledge_store.retrieval_health(),
        "generation": {'mode': 'responses' if generator else 'attributed_extracts',
            'model': generation_client.model if generation_client else None,
            'provider_count': len(generation_client.providers) if hasattr(generation_client, 'providers') else int(generation_client is not None),
            'hedge_delay_ms': round(generation_client.hedge_delay * 1000) if hasattr(generation_client, 'hedge_delay') else None},
        "configuration_warnings": list(CONFIG_WARNINGS),
    }


@app.get("/api/v1/nl2sql/schema")
def schema() -> dict[str, object]:
    try:
        return engine.schema()
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get("/api/v1/data-sources")
def data_sources() -> dict[str, object]:
    with knowledge_store.connect() as connection:
        document_count = connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        chunk_count = connection.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    return {"documents": {"name": "knowledge.sqlite", "document_count": document_count,
                           "chunk_count": chunk_count, "status": "connected"}}


@app.get('/api/v1/nl2sql/tables/{table_name}/preview')
def source_table_preview(table_name: str,
                         expected_source_revision: str = Query(pattern=r'^[a-f0-9]{64}$'),
                         limit: int = Query(default=10, ge=1, le=100),
                         offset: int = Query(default=0, ge=0, le=1000000)):
    from .nl2sql.security import execute_read_only
    try:
        with engine.consistent_reads(), engine._connect() as connection:
            tables, _, revision = engine._snapshot_for(connection)
            digest = hashlib.sha256(repr(revision).encode()).hexdigest()
            if digest != expected_source_revision:
                raise HTTPException(status_code=409, detail='数据库已更新，请重新查询后再定位数据表。')
            table = next((item for item in tables if item.name == table_name), None)
            if table is None:
                raise HTTPException(status_code=404, detail='数据表不存在。')
            quoted = '"' + table.name.replace('"', '""') + '"'
            order = ', '.join('"'+column.name.replace('"','""')+'"' for column in table.columns if column.primary_key)
            order = order or ', '.join('"'+column.name.replace('"','""')+'"' for column in table.columns)
            columns, rows = execute_read_only(connection, f'SELECT * FROM {quoted} ORDER BY {order} LIMIT ? OFFSET ?', (limit,offset),
                max_rows=limit, max_steps=engine.max_steps, max_seconds=engine.max_seconds)
            # Binary fields are represented by size; raw bytes are not JSON.
            rows = [{key: {'binary_bytes':len(value)} if isinstance(value, bytes) else value
                     for key, value in row.items()} for row in rows]
            return {'database':engine.database_path.name, 'table':table.name, 'source_revision':digest,
                'schema':table.to_dict(), 'columns':list(columns), 'rows':rows, 'limit':limit, 'offset':offset,
                'scope':'source_table_preview_not_filtered_query_result'}
    except (OSError, sqlite3.DatabaseError, SqlSafetyError):
        raise HTTPException(status_code=503, detail='当前数据表暂不可读取。')


@app.get('/api/v1/knowledge/documents/{document_id}/chunks/{chunk_id}')
def knowledge_chunk_source(document_id: str, chunk_id: str,
                           expected_source_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
    try:
        knowledge_store.verify_source(document_id, expected_sha256=expected_source_sha256)
        with knowledge_store.connect() as connection:
            row = connection.execute('SELECT rowid,payload FROM chunks WHERE document_id=? AND chunk_id=?',
                                     (document_id, chunk_id)).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail='引用的chunk已移除，请重新查询。')
            before = connection.execute('SELECT payload FROM chunks WHERE document_id=? AND rowid<? ORDER BY rowid DESC LIMIT 1',
                                        (document_id,row[0])).fetchone()
            after = connection.execute('SELECT payload FROM chunks WHERE document_id=? AND rowid>? ORDER BY rowid LIMIT 1',
                                       (document_id,row[0])).fetchone()
            chunks = [json.loads(item[0]) for item in (before, (row[1],), after) if item]
        knowledge_store.verify_source(document_id, expected_sha256=expected_source_sha256)
        return {'document_id':document_id, 'source_sha256':expected_source_sha256,
                'selected_chunk_id':chunk_id, 'chunks':chunks}
    except SourceRevisionError:
        raise HTTPException(status_code=409, detail='资料版本已变化，请重新查询后再定位。')
    except (KeyError, ValueError, SourceIntegrityError):
        raise HTTPException(status_code=409, detail='当前资料无法核验，请重新查询。')


class TableLocatorRequest(BaseModel):
    expected_source_revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    filters: list[dict] = Field(default_factory=list, max_length=32)


@app.post('/api/v1/nl2sql/tables/{table_name}/locate')
def locate_table_rows(table_name: str, request: TableLocatorRequest):
    from .nl2sql.metric_compiler import MetricCompiler, MetricPlanError, quote
    from .nl2sql.models import FilterSpec
    from .nl2sql.security import execute_read_only
    try:
        with engine.consistent_reads(), engine._connect() as connection:
            tables, _, revision = engine._snapshot_for(connection)
            digest = hashlib.sha256(repr(revision).encode()).hexdigest()
            if digest != request.expected_source_revision:
                raise HTTPException(status_code=409, detail='数据库版本已变化，请重新查询。')
            table = next((item for item in tables if item.name == table_name), None)
            if table is None:
                raise HTTPException(status_code=404, detail='数据表不存在。')
            filters = [FilterSpec(column=item['column'], operator=item['operator'], value=item.get('value'),
                source_text='', explanation='', table=item.get('table') or table_name) for item in request.filters]
            if any(item.table != table_name for item in filters):
                raise HTTPException(status_code=400, detail='跨表条件请查看来源 SQL，当前原表定位不推断关联行。')
            clauses, params = MetricCompiler(None)._filters(filters, {table_name:table})
            ordering = ', '.join(quote(column.name) for column in table.columns if column.primary_key)
            ordering = ordering or ', '.join(quote(column.name) for column in table.columns)
            numbered = f'SELECT *, ROW_NUMBER() OVER (ORDER BY {ordering}) - 1 AS "__source_offset" FROM {quote(table_name)}'
            where = ' AND '.join(clauses) or '1=1'
            sql = f'WITH "__source_rows" AS ({numbered}) SELECT "__source_offset" FROM "__source_rows" AS {quote(table_name)} WHERE {where} ORDER BY "__source_offset" LIMIT 1'
            _, rows = execute_read_only(connection, sql, tuple(params), max_rows=1,
                max_steps=engine.max_steps, max_seconds=engine.max_seconds)
            return {'source_revision':digest, 'table':table_name,
                'first_offset':rows[0]['__source_offset'] if rows else None,
                'scope':'original_table_filter_navigation_not_result_lineage'}
    except (KeyError, TypeError, ValueError, MetricPlanError):
        raise HTTPException(status_code=400, detail='定位条件无效，请重新查询。')


class SourceLocationRequest(BaseModel):
    expected_source_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    quote: str = Field(min_length=1, max_length=12000)
    page_no: int | None = Field(default=None, ge=1, le=1000)


@app.post('/api/v1/knowledge/documents/{document_id}/source-location')
def locate_source_quote(document_id: str, request: SourceLocationRequest):
    from .source_provenance import locate_quote_chunks
    try:
        knowledge_store.verify_source(document_id, expected_sha256=request.expected_source_sha256)
        with knowledge_store.connect() as connection:
            records = connection.execute('SELECT payload FROM chunks WHERE document_id=? ORDER BY rowid',
                                         (document_id,)).fetchall()
        chunks = [json.loads(row[0]) for row in records]
        if request.page_no is not None:
            chunks = [chunk for chunk in chunks if chunk.get('page_no') == request.page_no]
        selected = locate_quote_chunks(request.quote, chunks)
        knowledge_store.verify_source(document_id, expected_sha256=request.expected_source_sha256)
        return {'document_id':document_id, 'source_sha256':request.expected_source_sha256,
            'matched_chunk_ids':[chunk['chunk_id'] for chunk in selected], 'chunks':selected,
            'match_basis':'NFKC_and_whitespace_for_source_navigation',
            'page_no':request.page_no, 'status':'literal_chunk_match' if selected else 'chunk_match_unavailable'}
    except (KeyError, ValueError, SourceIntegrityError):
        raise HTTPException(status_code=409, detail='资料已变化或无法核验，请重新查询。')


@app.get("/api/v1/knowledge/documents")
def knowledge_documents():
    return {"documents": knowledge_store.list_documents()}


@app.get('/api/v1/knowledge/raysource-images/{image_id}')
def raysource_sample_image(image_id: str):
    from .raysource_media import image_path
    path = image_path(image_id)
    if path is None:
        raise HTTPException(status_code=404, detail='手册图片不存在')
    return FileResponse(path, media_type='image/jpeg', headers={'Cache-Control': 'public, max-age=86400'})


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
        if request.excel_tables and request.modality != 'xlsx':
            raise ValueError('Excel表区域仅可用于xlsx文件')
        return knowledge_store.ingest(raw, document_id=request.document_id, title=request.title,
            modality=request.modality, filename=request.filename, language=request.language,
            excel_tables=[item.model_dump() for item in request.excel_tables] or None)
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
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={'code':'evidence_integrity_failed','message':str(exc)}) from exc
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
        return knowledge_store.answer(request.question, top_k=request.top_k,
            **({'document_id': request.document_id} if request.document_id is not None else {}),
            **({'page_no': request.page_no} if request.page_no is not None else {}))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='指定资料不存在') from exc
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={'code':'evidence_integrity_failed','message':str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/api/v1/knowledge/query/stream')
def knowledge_query_stream(request: KnowledgeQueryRequest) -> StreamingResponse:
    """Send the deterministic retrieval audit before the final answer snapshot."""
    if not STREAM_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429,
            detail={'code': 'stream_busy', 'message': '并发流式请求过多，请稍后重试'})
    events: queue.Queue[tuple[str, object]] = queue.Queue()

    def worker():
        try:
            result = knowledge_store.answer(request.question, top_k=request.top_k,
                **({'document_id': request.document_id} if request.document_id is not None else {}),
                **({'page_no': request.page_no} if request.page_no is not None else {}),
                audit_callback=lambda audit: events.put(('audit', audit)),
                progress_callback=lambda item: events.put(('trace', item)))
            events.put(('done', result))
        except SourceIntegrityError:
            events.put(('error', {'detail': {'code': 'evidence_integrity_failed',
                                             'message': '来源已变化或原件完整性检查失败'}}))
        except KeyError:
            events.put(('error', {'detail': {'code': 'document_not_found',
                                             'message': '指定资料不存在'}}))
        except ValueError as exc:
            events.put(('error', {'detail': {'code': 'invalid_query',
                                             'message': str(exc)[:200]}}))
        except Exception:
            events.put(('error', {'detail': {'code': 'query_failed',
                                             'message': '文档问答执行失败'}}))
        finally:
            STREAM_SLOTS.release()

    try:
        threading.Thread(target=worker, name='ict8-knowledge-stream', daemon=True).start()
    except Exception:
        STREAM_SLOTS.release()
        raise

    def generate():
        deadline = time.monotonic() + STREAM_TIMEOUT_SECONDS
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                yield 'event: error\ndata: ' + json.dumps(
                    {'detail': {'code': 'timeout', 'message': '文档问答超时，后台执行可能仍在继续'}},
                    ensure_ascii=False) + '\n\n'
                return
            try:
                kind, payload = events.get(timeout=min(10.0, remaining))
            except queue.Empty:
                yield ': keep-alive\n\n'
                continue
            yield f'event: {kind}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n'
            if kind in {'done', 'error'}:
                return

    return StreamingResponse(generate(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


def _registered_visual_asset(document_id, request):
    if not DOCUMENT_PARSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail={'code': 'visual_render_busy',
                                                     'message': '文档解析并发已满，请稍后重试'})
    try:
        return knowledge_store.visual_asset(document_id, page_no=request.page_no,
            expected_source_sha256=request.expected_source_sha256, crop_display_pt=request.crop_display_pt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='文档不存在') from exc
    except SourceRevisionError as exc:
        raise HTTPException(status_code=409, detail={'code': 'evidence_source_changed', 'message': str(exc)}) from exc
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={'code': 'evidence_integrity_failed', 'message': str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={'code': 'invalid_visual_evidence', 'message': str(exc)[:200]}) from exc
    finally:
        DOCUMENT_PARSE_SLOTS.release()


@app.post('/api/v1/knowledge/documents/{document_id}/visual-evidence')
def visual_evidence_manifest(document_id: str, request: VisualEvidenceRequest):
    asset = _registered_visual_asset(document_id, request)
    manifest = dict(asset.manifest)
    parameters = {'source_sha256': request.expected_source_sha256, 'render_sha256': manifest['render_sha256']}
    if request.crop_display_pt is not None:
        parameters['crop_display_pt'] = json.dumps(request.crop_display_pt, separators=(',', ':'))
    manifest['png_uri'] = (f'/api/v1/knowledge/documents/{quote(document_id, safe="")}/pages/{request.page_no}/visual.png?'
                           + urlencode(parameters))
    return manifest


@app.post('/api/v1/knowledge/documents/{document_id}/visual-table-query')
def visual_table_query(document_id: str, request: VisualTableQueryRequest):
    from .responses_client import GenerationError
    if knowledge_store.generator is None:
        raise HTTPException(status_code=503, detail={'code': 'visual_model_unconfigured', 'message': '尚未配置指定视觉模型'})
    if not DOCUMENT_PARSE_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail={'code': 'visual_render_busy', 'message': '视觉处理并发已满'})
    try:
        return knowledge_store.visual_table_answer(document_id, question=request.question, page_no=request.page_no,
            expected_source_sha256=request.expected_source_sha256, crop_display_pt=request.crop_display_pt)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail='文档不存在') from exc
    except SourceIntegrityError as exc:
        raise HTTPException(status_code=409, detail={'code': 'evidence_integrity_failed', 'message': str(exc)}) from exc
    except GenerationError as exc:
        raise HTTPException(status_code=503, detail={'code': 'visual_model_unavailable', 'message': '视觉模型未返回可核验选择'}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={'code': 'invalid_visual_query', 'message': str(exc)[:200]}) from exc
    finally:
        DOCUMENT_PARSE_SLOTS.release()


@app.get('/api/v1/knowledge/documents/{document_id}/pages/{page_no}/visual.png')
def visual_evidence_png(document_id: str, page_no: Annotated[int, ApiPath(ge=1, le=1000)],
                        source_sha256: Annotated[str, Query(pattern=r'^[a-f0-9]{64}$')],
                        render_sha256: Annotated[str, Query(pattern=r'^[a-f0-9]{64}$')],
                        crop_display_pt: Annotated[str | None, Query(max_length=160)] = None):
    try:
        request = VisualEvidenceRequest(page_no=page_no, expected_source_sha256=source_sha256,
            crop_display_pt=json.loads(crop_display_pt) if crop_display_pt is not None else None)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={'code': 'invalid_visual_parameters',
                                                     'message': '页码、SHA256或裁剪坐标无效'}) from exc
    asset = _registered_visual_asset(document_id, request)
    if hashlib.sha256(asset.png_bytes).hexdigest() != render_sha256 or asset.manifest['render_sha256'] != render_sha256:
        raise HTTPException(status_code=409, detail={'code': 'visual_render_changed',
                                                     'message': '页图与证据清单哈希不一致，请重新取得清单'})
    return Response(content=asset.png_bytes, media_type='image/png', headers={
        'Cache-Control': 'no-store', 'ETag': f'"{render_sha256}"',
        'X-Source-SHA256': source_sha256, 'X-Render-SHA256': render_sha256,
        'X-Content-Type-Options': 'nosniff'})


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
        result = engine.answer(request.question, max_rows=request.max_rows,
                               complete_results=request.complete_results)
        return result.to_dict()
    except SqlSafetyError as exc:
        raise HTTPException(status_code=422, detail={"code": "sql_rejected_or_limited", "message": "查询超出安全执行限制"}) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={"code": "bad_request", "message": str(exc)[:200]}) from exc


@app.get('/api/v1/nl2sql/results/{artifact_id}')
def complete_sql_result_page(artifact_id: str, offset: int = Query(default=0, ge=0),
                             page_size: int = Query(default=100, ge=1, le=100),
                             query_sha256: str | None = Query(default=None, max_length=64),
                             binding_sha256: str = Query(min_length=64, max_length=64)):
    try:
        return engine.complete_result_page(artifact_id, offset=offset, page_size=page_size,
                                           expected_query_sha256=query_sha256,
                                           expected_binding_sha256=binding_sha256)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail={'code': 'result_artifact_not_found'}) from exc
    except SqlSafetyError as exc:
        raise HTTPException(status_code=409, detail={'code': 'result_artifact_stale_or_invalid'}) from exc
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail={'code': 'invalid_result_artifact_request'}) from exc


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
            **({"complete_results": True} if request.complete_results else {}),
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
                **({"complete_results": True} if request.complete_results else {}),
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


@app.post("/api/v1/documents/formulas/calculate")
def calculate_document_formula(request: FormulaCalculationRequest) -> dict[str, object]:
    try:
        return FormulaBinder().calculate(request.expression,
            {name: ParameterEvidence(**value.model_dump()) for name, value in request.parameters.items()},
            formula_source=request.formula_source, formula_locator=request.formula_locator)
    except (ValueError, SyntaxError, OverflowError) as exc:
        raise HTTPException(status_code=422, detail={"code": "invalid_formula_binding", "message": str(exc)[:200]}) from exc


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
                time_value=request.selected_time,
            ),
        )
        # 澄清选择已经是用户对原问题的补全，不再把旧轮次再次拼接进来。
        # 直接走同一条跨源编排链，确保 SQL、文档证据和完整 trace 一次返回。
        result = agent.answer(enriched_question, top_k_documents=request.top_k_documents,
                              **({"complete_results": True} if request.complete_results else {}))
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


@app.post("/api/v1/documents/chunks-preview")
def chunks_preview(request: ChunkPreviewRequest) -> dict[str, Any]:
    if request.excel_tables and request.modality != 'xlsx':
        raise HTTPException(status_code=400,detail='Excel表区域仅可用于xlsx文件')
    if (request.pdf_table_headers or request.pdf_table_links) and request.modality != 'pdf':
        raise HTTPException(status_code=400, detail='逐页表头选择仅适用于PDF')
    if request.pdf_table_links:
        try:
            from .pdf_table_continuity import PdfTableContinuityAgent
            PdfTableContinuityAgent.validate_links([item.model_dump() for item in request.pdf_table_links],
                [(item.page_no,item.table_index) for item in request.pdf_table_headers])
        except ValueError as exc:
            raise HTTPException(status_code=400,detail=str(exc)) from exc
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
            result = document_chunker.parse_xlsx(raw, document_id=request.document_id,
                excel_tables=[item.model_dump() for item in request.excel_tables] or None)
        else:
            result = document_chunker.parse_image(raw, document_id=request.document_id, ocr_pipeline=ocr_pipeline, language=request.language)
        response=result.to_dict()
        if request.pdf_table_headers:
            from .pdf_table_preview import PdfTablePreviewAgent
            response['table_structures']=PdfTablePreviewAgent().run(raw,
                [item.model_dump() for item in request.pdf_table_headers],
                ocr_pipeline=ocr_pipeline,chunks=result.chunks,language=request.language)
            from .pdf_table_continuity import PdfTableContinuityAgent
            response['table_continuity']=PdfTableContinuityAgent().run(response['table_structures'],
                [item.model_dump() for item in request.pdf_table_links])
    except ValueError as exc:
        message = str(exc)
        status = 413 if "超过上限" in message else 400
        raise HTTPException(status_code=status, detail=message) from exc
    finally:
        DOCUMENT_PARSE_SLOTS.release()
    return response


@app.get("/api/v1/documents/ocr/health")
def ocr_health() -> dict[str, object]:
    if ocr_pipeline is None:
        return {"configured": False, "ready": False, "status": "disabled", "warnings": list(CONFIG_WARNINGS)}
    return {"status": "configured", **ocr_pipeline.health()}
