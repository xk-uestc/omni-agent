"""Actual local SSE deadline and worker ownership; no upstream service."""
import threading
from fastapi.testclient import TestClient


def test_timeout_reports_transport_phase_and_does_not_claim_worker_cancelled(monkeypatch):
    import backend.app as module
    from backend.omni_agent import OmniAgent
    release=threading.Event();finished=threading.Event()
    def delayed(self,*args,**kwargs):
        release.wait(2)
        finished.set()
        return {'status':'ok','trace':[]}
    monkeypatch.setattr(OmniAgent,'query',delayed)
    monkeypatch.setattr(module,'STREAM_TIMEOUT_SECONDS',.02)
    monkeypatch.setattr(module,'STREAM_SLOTS',threading.BoundedSemaphore(1))
    try:
        response=TestClient(module.app).post('/api/v1/omni/query/stream',json={'question':'服务延迟测试'})
        assert response.status_code==200 and '"code": "timeout"' in response.text
        assert '"phase": "transport"' in response.text and '"worker_cancelled": false' in response.text
        assert not finished.is_set()
        assert not module.STREAM_SLOTS.acquire(blocking=False)
    finally:
        release.set()
        assert finished.wait(1)
    assert module.STREAM_SLOTS.acquire(timeout=1)
    module.STREAM_SLOTS.release()
