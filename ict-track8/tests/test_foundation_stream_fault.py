"""Actual local SSE deadline and worker ownership; no upstream service."""
import threading
import pytest
from fastapi import HTTPException


class LocalStreamingResponse:
    def __init__(self, content, **kwargs): self.body_iterator=content


def _consume_until_timeout(response):
    output=''
    for chunk in response.body_iterator:
        output+=chunk.decode() if isinstance(chunk,bytes) else str(chunk)
        if '"code": "timeout"' in output:break
    return output


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
    monkeypatch.setattr(module,'StreamingResponse',LocalStreamingResponse)
    try:
        response=module.omni_query_stream(module.OmniRequest(question='服务延迟测试'))
        text=_consume_until_timeout(response)
        assert '"phase": "transport"' in text and '"worker_cancelled": false' in text
        assert not finished.is_set()
        assert not module.STREAM_SLOTS.acquire(blocking=False)
    finally:
        release.set()
        assert finished.wait(1)
    assert module.STREAM_SLOTS.acquire(timeout=1)
    module.STREAM_SLOTS.release()


def test_agent_stream_timeout_reports_worker_state_and_releases_slot_after_completion(monkeypatch):
    import backend.app as module
    release=threading.Event();finished=threading.Event()
    def delayed(*args,**kwargs):
        release.wait(2)
        finished.set()
        return {'status':'ok','trace':[]}
    monkeypatch.setattr(module.agent,'answer',delayed)
    monkeypatch.setattr(module,'STREAM_TIMEOUT_SECONDS',.02)
    monkeypatch.setattr(module,'STREAM_SLOTS',threading.BoundedSemaphore(1))
    monkeypatch.setattr(module,'StreamingResponse',LocalStreamingResponse)
    try:
        response=module.agent_query_stream(module.AgentQueryRequest(question='服务延迟测试'))
        text=_consume_until_timeout(response)
        assert '"worker_cancelled": false' in text
        assert '"worker_state": "running"' in text
        assert not finished.is_set()
        assert not module.STREAM_SLOTS.acquire(blocking=False)
    finally:
        release.set()
        assert finished.wait(1)
    assert module.STREAM_SLOTS.acquire(timeout=1)
    module.STREAM_SLOTS.release()


def test_agent_stream_thread_start_failure_returns_its_slot(monkeypatch):
    import backend.app as module
    class FailedThread:
        def __init__(self,*args,**kwargs): pass
        def start(self): raise RuntimeError('synthetic thread start failure')
    slots=threading.BoundedSemaphore(1)
    monkeypatch.setattr(module,'STREAM_SLOTS',slots)
    monkeypatch.setattr(module.threading,'Thread',FailedThread)
    request=module.AgentQueryRequest(question='启动失败测试')
    with pytest.raises(HTTPException) as error:
        module.agent_query_stream(request)
    assert error.value.status_code==503
    assert slots.acquire(blocking=False)
