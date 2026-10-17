"""Optional local MinerU backend, isolated from application dependencies/models."""
from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import tempfile
import time
from pathlib import Path


class LocalMinerUPdfBackend:
    def __init__(self, executable, model_home, *, page_budget=40, timeout_seconds=300):
        self.executable = Path(executable).resolve(strict=True)
        self.model_home = Path(model_home).resolve(strict=True)
        if not self.executable.is_file() or not os.access(self.executable, os.X_OK):
            raise ValueError('invalid_pdf_backend_executable')
        if not 1 <= page_budget <= 100 or not 1 <= timeout_seconds <= 600:
            raise ValueError('invalid_pdf_backend_budget')
        self.page_budget, self.timeout_seconds = page_budget, timeout_seconds

    def parse(self, raw):
        import fitz
        from .structured_document import normalize_mineru
        if not raw or len(raw) > 20 * 1024 * 1024:
            raise ValueError('pdf_bytes_budget')
        with fitz.open(stream=raw, filetype='pdf') as pdf:
            if pdf.is_encrypted or not 1 <= len(pdf) <= self.page_budget:
                raise ValueError('structured_pdf_page_budget_exhausted_or_encrypted')
            # Engines do not share a coordinate convention for shifted/rotated
            # pages. Refuse until their geometry can be verified, never guess.
            if any(p.rotation or abs(p.cropbox.x0) > .1 or abs(p.cropbox.y0) > .1 for p in pdf):
                raise ValueError('structured_pdf_unsupported_page_geometry')
            geometry = [(float(p.rect.width), float(p.rect.height)) for p in pdf]
        start = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix='omni-pdf-') as directory:
            root = Path(directory)
            source, output = root/'original.pdf', root/'parsed'
            source.write_bytes(raw)
            cmd = [str(self.executable), 'parse', str(source), '--pages', f'1-{len(geometry)}',
                   '--tier', 'basic', '--ocr-mode', 'auto', '--format', 'middle_json', '--output', str(output)]
            env = {**os.environ, 'MINERU_HOME': str(self.model_home), 'HF_HUB_OFFLINE': '1',
                   'OMP_NUM_THREADS': '4', 'HF_HUB_DISABLE_XET': '1'}
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    env=env, start_new_session=True)
            try:
                code = proc.wait(timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired as exc:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait()
                raise ValueError('structured_pdf_timeout') from exc
            if code:
                raise ValueError('structured_pdf_parser_failed')
            files = list(output.rglob('*.json'))
            if len(files) != 1 or files[0].is_symlink() or files[0].stat().st_size > 8 * 1024 * 1024:
                raise ValueError('structured_pdf_output_budget_or_ambiguity')
            structure = normalize_mineru(json.loads(files[0].read_text()), geometry, hashlib.sha256(raw).hexdigest())
        structure['cost'] = {'seconds': round(time.perf_counter()-start, 4), 'pages_read': len(geometry),
                             'page_budget': self.page_budget, 'timeout_seconds': self.timeout_seconds,
                             'backend': 'local_cpu_onnx', 'remote_inference': False}
        return structure


def configured_pdf_backend():
    """Configuration belongs to the server, never to document/user instructions."""
    name = os.getenv('ICT8_PDF_BACKEND', 'native').strip().lower()
    if name == 'native':
        return None
    if name != 'mineru':
        raise ValueError('unsupported_configured_pdf_backend')
    return LocalMinerUPdfBackend(os.environ['ICT8_MINERU_EXECUTABLE'], os.environ['ICT8_MINERU_HOME'],
        page_budget=int(os.getenv('ICT8_STRUCTURED_PDF_PAGE_BUDGET', '40')),
        timeout_seconds=int(os.getenv('ICT8_STRUCTURED_PDF_TIMEOUT', '300')))
