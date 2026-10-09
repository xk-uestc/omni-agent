"""Serve this project's API and frontend on one independent port."""
from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8030)
    parser.add_argument('--with-model', action='store_true', help='读取 runtime 模型配置及备用 Responses 服务')
    parser.add_argument('--model', default='gpt-6-luna')
    parser.add_argument('--diagnostic-stack-after', type=int, choices=range(10, 601),
                        help='仅排障：在指定秒数后向服务日志写线程调用栈，不包含局部变量')
    args = parser.parse_args()
    if args.diagnostic_stack_after:
        import faulthandler
        faulthandler.dump_traceback_later(args.diagnostic_stack_after, repeat=False)
    if args.with_model:
        os.environ.setdefault('ICT8_FAST_SQL', '1')
        from model_runtime import enable_local_model
        enable_local_model(args.model)
        # Several bounded upstream calls can form one dependent request.
        # Preserve explicit operator settings; allow their progress stream to
        # outlive the single-call transport timeout in the model demo.
        os.environ.setdefault('ICT8_STREAM_TIMEOUT', '300')
    os.environ.setdefault('ICT8_KNOWLEDGE_ROOT', str(ROOT / 'runtime/knowledge'))
    os.environ.setdefault('ICT8_OCR_ENGINE', 'rapidocr')
    os.environ.setdefault('ICT8_SESSION_DB', str(ROOT / 'runtime/sessions.sqlite'))
    dense_model = ROOT / 'models/bge-small-zh-v1.5'
    if (dense_model / 'ASSET_MANIFEST.json').exists():
        os.environ.setdefault('ICT8_DENSE_MODEL_PATH', str(dense_model))
    (ROOT / 'runtime').mkdir(exist_ok=True)
    knowledge_database = Path(os.environ['ICT8_KNOWLEDGE_ROOT']) / 'knowledge.sqlite'
    if not knowledge_database.exists():
        subprocess.run([sys.executable, str(ROOT / 'tools/create_sample_corpus.py'), '--ingest'], cwd=ROOT, check=True)
    from fastapi.staticfiles import StaticFiles
    from backend.app import app
    import uvicorn
    app.mount('/', StaticFiles(directory=ROOT / 'ict-track8/frontend', html=True), name='frontend')
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
