#!/usr/bin/env bash
set -euo pipefail
RUN_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$RUN_DIR/../.." && pwd)"
cd "$PROJECT_DIR"
export PYTHONPATH="$PROJECT_DIR/ict-track8"
export ICT8_DB_PATH="$RUN_DIR/demo.sqlite"
export ICT8_METRIC_CATALOG="$RUN_DIR/metric_catalog.json"
export ICT8_KNOWLEDGE_ROOT="$RUN_DIR/knowledge"
export ICT8_SESSION_DB="$RUN_DIR/sessions.sqlite"
export ICT8_ENV=development
export ICT8_OCR_ENGINE=rapidocr
export ICT8_OCR_URL=''
export ICT8_DENSE_MODEL_PATH=''
export ICT8_PDF_BACKEND=native
export ICT8_MEMORY_ENABLED=0
export ICT8_PLAN_PROVIDER=''
export ICT8_PLAN_URL=''
export ICT8_GENERATION_PROVIDER=''
export ICT8_MANUAL_RETRIEVER_URL=''
export ICT8_OPENAI_FALLBACKS='[]'
unset OPENAI_API_KEY ICT8_OPENAI_API_KEY ICT8_PLAN_TOKEN DEEPSEEK_API_KEY
exec "${OMNI_RUN_PYTHON:-$PROJECT_DIR/.venv/bin/python}" -m uvicorn asgi:app --app-dir "$RUN_DIR" --host 0.0.0.0 --port 8030
