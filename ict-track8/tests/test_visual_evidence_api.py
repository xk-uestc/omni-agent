"""Registered, pinned page/crop assets over HTTP, with no external model calls."""
import hashlib
from io import BytesIO
import threading
from urllib.parse import parse_qs, urlencode, urlsplit

import fitz
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.knowledge_store import KnowledgeStore


def pdf_bytes(text='Registered source evidence', width=400, height=300):
    with fitz.open() as document:
        page = document.new_page(width=width, height=height)
        page.insert_text((50, 90), text)
        return document.tobytes()


@pytest.fixture
def registered(tmp_path, monkeypatch):
    from backend import app as app_module
    store = KnowledgeStore(tmp_path)
    raw = pdf_bytes()
    record = store.ingest(raw, document_id='report', title='Report', modality='pdf', filename='report.pdf')
    monkeypatch.setattr(app_module, 'knowledge_store', store)
    monkeypatch.setattr(app_module, 'DOCUMENT_PARSE_SLOTS', threading.BoundedSemaphore(1))
    return TestClient(app_module.app), store, record, app_module


def request_manifest(client, record, **kwargs):
    return client.post('/api/v1/knowledge/documents/report/visual-evidence',
                       json={'page_no': 1, 'expected_source_sha256': record['sha256'], **kwargs})


@pytest.mark.parametrize('crop', [None, [40.25, 70.5, 200.75, 120.25]])
def test_manifest_and_png_are_pinned_byte_and_coordinate_consistent(registered, crop):
    client, _, record, _ = registered
    response = request_manifest(client, record, crop_display_pt=crop)
    assert response.status_code == 200
    manifest = response.json()
    assert manifest['document_id'] == 'report' and manifest['source_sha256'] == record['sha256']
    assert manifest['asset_kind'] == ('crop' if crop else 'page')
    assert 'png_bytes' not in manifest and 'image_base64' not in manifest
    assert manifest['status'] == 'rendered_provenance_only'
    image = client.get(manifest['png_uri'])
    assert image.status_code == 200 and image.headers['content-type'] == 'image/png'
    assert hashlib.sha256(image.content).hexdigest() == manifest['render_sha256']
    assert image.headers['x-source-sha256'] == record['sha256']
    assert image.headers['x-render-sha256'] == manifest['render_sha256']
    assert image.headers['etag'] == f'"{manifest["render_sha256"]}"'
    assert image.headers['cache-control'] == 'no-store'
    with Image.open(BytesIO(image.content)) as png:
        assert list(png.size) == manifest['size_px']
    for word in manifest['native_words']:
        mapped = fitz.Rect(word['bbox_display_pt']) * fitz.Matrix(*manifest['mappings']['display_to_asset_px'])
        assert list(mapped) == pytest.approx(word['bbox_asset_px'])


def test_changed_source_or_render_hash_rejects_manifest_uri(registered):
    client, store, record, _ = registered
    manifest = request_manifest(client, record).json()
    parts = urlsplit(manifest['png_uri'])
    parameters = {name: values[0] for name, values in parse_qs(parts.query).items()}
    parameters['render_sha256'] = '0' * 64
    assert client.get(parts.path + '?' + urlencode(parameters)).status_code == 409
    store.ingest(pdf_bytes('A replacement version'), document_id='report', title='Report', modality='pdf', filename='report.pdf')
    assert client.get(manifest['png_uri']).status_code == 409
    assert request_manifest(client, record).status_code == 409


def test_unknown_and_tampered_sources_are_distinguished(registered):
    client, store, record, _ = registered
    response = client.post('/api/v1/knowledge/documents/missing/visual-evidence',
                           json={'page_no': 1, 'expected_source_sha256': record['sha256']})
    assert response.status_code == 404
    (store.assets / record['asset']).write_bytes(b'tampered')
    response = request_manifest(client, record)
    assert response.status_code == 409
    assert response.json()['detail']['code'] == 'evidence_integrity_failed'


@pytest.mark.parametrize('kwargs', [
    {'page_no': 0}, {'page_no': True}, {'page_no': '1'}, {'page_no': 1001},
    {'expected_source_sha256': 'bad'}, {'crop_display_pt': [0, 0, 10]},
    {'crop_display_pt': [0, 0, 10, 10, 10]}, {'crop_display_pt': ['0', 0, 10, 10]},
    {'crop_display_pt': [False, 0, 10, 10]}, {'url': 'https://example.com/private.pdf'},
    {'path': 'C:/private.pdf'}, {'max_pixels': 1000000000}, {'render_scale': 100},
])
def test_manifest_rejects_bad_types_and_unregistered_input_parameters(registered, kwargs):
    client, _, record, _ = registered
    assert request_manifest(client, record, **kwargs).status_code == 422


@pytest.mark.parametrize('kwargs', [
    {'page_no': 2}, {'crop_display_pt': [-1, 0, 10, 10]},
    {'crop_display_pt': [10, 10, 0, 0]}, {'crop_display_pt': [0, 0, 401, 300]},
])
def test_manifest_rejects_page_and_crop_bounds(registered, kwargs):
    client, _, record, _ = registered
    assert request_manifest(client, record, **kwargs).status_code == 400


def test_png_requires_manifest_pins_and_valid_crop_json(registered):
    client, _, record, _ = registered
    uri = '/api/v1/knowledge/documents/report/pages/1/visual.png'
    assert client.get(uri).status_code == 422
    parameters = {'source_sha256': record['sha256'], 'render_sha256': '0' * 64}
    for invalid in ['https://example.com/a.pdf', '[0,0,NaN,10]', '[0,0,Infinity,10]', '[true,0,10,10]']:
        assert client.get(uri, params={**parameters, 'crop_display_pt': invalid}).status_code == 422


def test_parse_concurrency_is_shared_and_slots_release_after_failures(registered):
    client, _, record, module = registered
    slots = module.DOCUMENT_PARSE_SLOTS
    assert slots.acquire(blocking=False)
    try:
        response = request_manifest(client, record)
        assert response.status_code == 429
        assert response.json()['detail']['code'] == 'visual_render_busy'
    finally:
        slots.release()
    assert request_manifest(client, record, page_no=2).status_code == 400
    assert request_manifest(client, record).status_code == 200
    assert slots.acquire(blocking=False)
    slots.release()


def test_non_pdf_registered_document_is_not_rendered(registered):
    client, store, _, _ = registered
    record = store.ingest(b'plain text', document_id='report', title='Report', modality='txt', filename='report.txt')
    assert request_manifest(client, record).status_code == 400


def test_pixel_budget_applies_before_rasterization_and_slot_is_released(registered):
    client, store, _, module = registered
    record = store.ingest(pdf_bytes(width=2000, height=2000), document_id='report', title='Report', modality='pdf', filename='large.pdf')
    response = request_manifest(client, record)
    assert response.status_code == 400 and 'budget' in response.json()['detail']['message']
    assert module.DOCUMENT_PARSE_SLOTS.acquire(blocking=False)
    module.DOCUMENT_PARSE_SLOTS.release()


def test_png_and_manifest_share_existing_production_authorization(registered, monkeypatch):
    client, _, record, module = registered
    manifest = request_manifest(client, record).json()
    monkeypatch.setattr(module, 'IS_PRODUCTION', True)
    monkeypatch.setattr(module, 'API_TOKEN', 'local-test-token')
    assert request_manifest(client, record).status_code == 401
    assert client.get(manifest['png_uri']).status_code == 401
    assert client.get(manifest['png_uri'], headers={'Authorization': 'Bearer local-test-token'}).status_code == 200


def test_png_rendering_also_respects_shared_parse_slots(registered):
    client, _, record, module = registered
    manifest = request_manifest(client, record).json()
    slots = module.DOCUMENT_PARSE_SLOTS
    assert slots.acquire(blocking=False)
    try:
        assert client.get(manifest['png_uri']).status_code == 429
    finally:
        slots.release()
    assert client.get(manifest['png_uri']).status_code == 200
