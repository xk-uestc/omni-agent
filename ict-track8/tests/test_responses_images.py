"""Image transport contracts; actual visual capability is tested separately."""
from copy import deepcopy
import json
from io import BytesIO

import pytest
from reportlab.pdfgen import canvas

from backend.responses_client import GenerationError, StructuredResponses
from backend.visual_evidence import VisualAsset, render_pdf_evidence


def client():
    class Response:
        status_code = 200
        payload = {'model': 'gpt-6-luna', 'status': 'completed', 'output': [
            {'type': 'message', 'content': [{'type': 'output_text', 'text': '{}'}]}]}
        content = json.dumps(payload).encode()

        def json(self):
            return self.payload

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.calls = []

        def post(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    return StructuredResponses('https://example.com/v1', 'test-only-token', model='gpt-6-luna', session=Session())


def asset():
    stream = BytesIO()
    pdf = canvas.Canvas(stream, pagesize=(200, 160))
    pdf.drawString(20, 100, 'Private native content')
    pdf.save()
    return render_pdf_evidence(stream.getvalue(), page_no=1)


def test_image_input_uses_multimodal_contract_and_safe_audit():
    c, image = client(), asset()
    c.generate('Read image', {'question': 'Read visible content'}, {}, image_attachments=[image])
    body = c.session.calls[0][1]['json']
    assert body['model'] == 'gpt-6-luna' and body['reasoning']['effort'] == 'medium'
    content = body['input'][0]['content']
    assert content[-1]['type'] == 'input_image'
    assert content[-1]['image_url'].startswith('data:image/png;base64,')
    assert 'Private native content' not in '\n'.join(x.get('text', '') for x in content)
    assert c.audit['image_count'] == 1
    assert c.audit['image_evidence'][0]['render_sha256'] == image.manifest['render_sha256']
    assert 'base64' not in json.dumps(c.audit_history)
    audit = c.audit_history
    audit[0]['image_evidence'][0]['render_sha256'] = 'mutated'
    assert c.audit['image_evidence'][0]['render_sha256'] != 'mutated'


@pytest.mark.parametrize('mutation', ['hash', 'dimensions', 'bytes', 'page', 'schema'])
def test_image_integrity_failure_never_sends_request(mutation):
    c, image = client(), asset()
    metadata = deepcopy(image.manifest)
    key, value = {'hash': ('render_sha256', '0' * 64), 'dimensions': ('size_px', [1, 1]),
                  'bytes': ('png_byte_count', 1), 'page': ('page_no', True),
                  'schema': ('schema_version', 'unknown')}[mutation]
    metadata[key] = value
    with pytest.raises(GenerationError):
        c.generate('', {}, {}, image_attachments=[VisualAsset(image.png_bytes, metadata)])
    assert not c.session.calls
    assert c.audit['input_validation'] == 'visual_asset_rejected'


@pytest.mark.parametrize('images', [[], ['https://example.com/image.png'], ['D:/private.png'], [object()]])
def test_remote_and_arbitrary_inputs_are_not_image_attachments(images):
    c = client()
    with pytest.raises(GenerationError):
        c.generate('', {}, {}, image_attachments=images)
    assert not c.session.calls


def test_image_count_is_bounded():
    c, image = client(), asset()
    with pytest.raises(GenerationError):
        c.generate('', {}, {}, image_attachments=[image] * 3)
    assert not c.session.calls


def test_text_only_request_retains_string_contract():
    c = client()
    c.generate('', {'sample': 'text'}, {})
    assert c.session.calls[0][1]['json']['input'] == '{"sample": "text"}'
    assert 'image_count' not in c.audit
