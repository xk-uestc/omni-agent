"""Serve this project's API and frontend on one independent port."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ict-track8'))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8030)
    args = parser.parse_args()
    from fastapi.staticfiles import StaticFiles
    from backend.app import app
    import uvicorn
    app.mount('/', StaticFiles(directory=ROOT / 'ict-track8/frontend', html=True), name='frontend')
    uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
