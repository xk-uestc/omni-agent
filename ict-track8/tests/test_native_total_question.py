"""Original isolated amount scope, strict independent review and replay."""
from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from backend.knowledge_store import KnowledgeStore, SourceIntegrityError
from backend.native_total_question import CHECKS, _sha, _registries, bind, eligible, replay, route
from backend.native_amount_annotations import extract
from backend.responses_client import GenerationError
import hashlib


QUESTION = 'What is the difference between total Alpha funds and total Beta funds for Orion in FY 2034?'


def pdf(left='$51.20m', right='$18.10m', *, country='Orion', year='2034', duplicate=False):
    with fitz.open() as document:
        page = document.new_page(width=650, height=500)
        page.insert_text((50, 35), country+' original funding statement')
        page.insert_text((50, 60), 'FY '+year)
        for i, (label, value) in enumerate([
                ('TOTAL ALPHA FUNDS:', left), ('TOTAL ALPHA FUNDS:' if duplicate else 'TOTAL BETA FUNDS:', right)]):
            y = 130+i*130
            page.insert_text((50, y), label, fontsize=10)
            page.insert_text((340, y), value, fontsize=10)
        return document.tobytes()


class Client:
    model = 'gpt-6-luna'; reasoning = 'medium'
    def __init__(self, *, reject_check=None, reject_value=False, hook=None, operation='absolute_difference'):
        self.reject_check, self.reject_value, self.hook, self.operation = reject_check, reject_value, hook, operation
        self.calls = 0; self.audit = {}; self.contexts = []
    def generate(self, instructions, context, schema, **options):
        self.calls += 1
        self.contexts.append(deepcopy(context))
        self.audit = {'status':'completed','http_status':200,'model_verified':True,
            'model':self.model,'reasoning':self.reasoning,'response_model':self.model}
        if options['name'] == 'native_total_selection':
            annotations = context['original_sources'][0]['annotations']
            value = {'abstain':False,'operation':self.operation,
                'annotation_ids':[a['annotation_id'] for a in annotations]}
        elif options['name'] == 'native_total_independent_review':
            value = {key:True for key in CHECKS}
            if self.reject_check:
                value[self.reject_check] = self.reject_value
        else:
            raise AssertionError(options['name'])
        if self.hook:
            self.hook(self.calls, context)
        return value


def environment(tmp_path, *, client=None, left='$51.20m', right='$18.10m', duplicate=False):
    store = KnowledgeStore(tmp_path/'knowledge')
    store.ingest(pdf(left, right, duplicate=duplicate), document_id='orion', title='Original', modality='pdf', filename='amounts.pdf')
    store.generator = SimpleNamespace(client=client or Client())
    return store, [SimpleNamespace(metadata={'document_id':'orion'})]


def test_complete_production_question_uses_sparse_amounts_without_a_fake_table(tmp_path):
    store, _ = environment(tmp_path)
    result = store.answer(QUESTION, document_id='orion')
    assert result['status'] == 'ok' and result['answer'] == '$33.10m'
    assert result['answer_mode'] == 'native_total_model_reviewed'
    assert result['computation']['scale'] == '1000000'
    assert result['answer_scope']['table_identity_verified'] is False
    assert store.generator.client.calls == 2
    assert replay(store, result)
    assert {event.get('tool') for event in result['trace']} >= {'document.amount.read','calculate'}
    assert all(c['metadata']['source_sha256'] == result['native_total_proof']['sources'][0]['sha256'] for c in result['citations'])


@pytest.mark.parametrize('check', CHECKS)
@pytest.mark.parametrize('value', [False, 1, 'true', None])
def test_every_independent_review_check_is_strict_and_cannot_fall_through(tmp_path, check, value):
    store, hits = environment(tmp_path, client=Client(reject_check=check, reject_value=value))
    result, trace = route(store, QUESTION, hits)
    assert result['status'] == 'insufficient_evidence'
    assert trace['reason'] == 'native_total_independent_review_rejected'
    assert store.generator.client.calls == 2


def test_question_direction_is_preserved_with_negative_difference(tmp_path):
    client = Client(operation='difference')
    store, hits = environment(tmp_path, client=client, left='$18.10m', right='$51.20m')
    result, _ = route(store, 'Subtract total Beta funds from total Alpha funds for Orion FY 2034.', hits)
    assert result['answer'] == '$-33.10m' and replay(store, result)


def test_same_symbol_different_literal_multiplier_is_not_normalized(tmp_path):
    store, hits = environment(tmp_path, right='$18.10bn')
    result, trace = route(store, QUESTION, hits)
    assert result is None and trace['reason'] == 'native_total_literal_unit_or_scale_mismatch'
    assert store.generator.client.calls == 1


def test_duplicate_labels_never_choose_an_arbitrary_annotation(tmp_path):
    store, hits = environment(tmp_path, duplicate=True)
    result, trace = route(store, QUESTION, hits)
    assert result is None and trace['reason'] == 'native_total_duplicate_label_requires_scope'


def test_model_context_mutation_is_not_accepted(tmp_path):
    client = Client(hook=lambda call, context: context['original_sources'][0].update(complete_original_page_text='changed') if call == 1 else None)
    store, hits = environment(tmp_path, client=client)
    result, trace = route(store, QUESTION, hits)
    assert result['status'] == 'insufficient_evidence' and client.calls == 1
    assert trace['reason'] == 'native_total_selection_or_context_invalid'


@pytest.mark.parametrize('call', [1, 2])
def test_original_replacement_during_model_call_raises_integrity_error(tmp_path, call):
    client = Client()
    store, hits = environment(tmp_path, client=client)
    path, _ = store.original('orion')
    client.hook = lambda n, context: path.write_bytes(b'changed') if n == call else None
    with pytest.raises(SourceIntegrityError):
        route(store, QUESTION, hits)


@pytest.mark.parametrize('field', ['answer','computation','answer_scope','citations','selection'])
def test_tampered_receipts_do_not_replay_even_with_rehashed_proof(tmp_path, field):
    store, hits = environment(tmp_path)
    result, _ = route(store, QUESTION, hits)
    if field == 'answer':
        result['answer'] = '$99m'
    elif field == 'computation':
        result['computation']['numeric_result'] = '99'
    elif field == 'answer_scope':
        result['answer_scope']['scale'] = '1000000000'
    elif field == 'citations':
        result['citations'][0]['metadata']['page_no'] = 2
    else:
        result['native_total_proof']['selection']['annotation_ids'].reverse()
        result['native_total_proof_sha256'] = _sha(result['native_total_proof'])
    assert not replay(store, result)


@pytest.mark.parametrize('question', [
    'Did total profit increase, and by how much?', 'Which totals are highest?',
    'What percentage of total allocations was donated?', 'Who manages the programme?',
])
def test_other_question_shapes_retain_existing_routes(question):
    assert not eligible(question)


def test_cross_country_annotations_are_never_combined(tmp_path):
    store, _ = environment(tmp_path)
    store.ingest(pdf(country='Lyra'), document_id='lyra', title='Other', modality='pdf', filename='other.pdf')
    sources = [{'document_id':d['document_id'],'sha256':d['sha256']} for d in store.list_documents()]
    registries = _registries(store, sources, None)
    plan = {'abstain':False,'operation':'absolute_difference',
        'annotation_ids':[registries[0]['annotations'][0]['annotation_id'],registries[1]['annotations'][1]['annotation_id']]}
    with pytest.raises(ValueError, match='cross_page_or_source'):
        bind(plan, registries, QUESTION)


def test_identical_pdf_uploads_keep_globally_unique_source_bound_ids(tmp_path):
    store, _ = environment(tmp_path)
    original, _ = store.original('orion')
    store.ingest(original.read_bytes(), document_id='copy', title='Copy', modality='pdf', filename='copy.pdf')
    sources = [{'document_id':d['document_id'],'sha256':d['sha256']} for d in store.list_documents()]
    registries = _registries(store, sources, None)
    ids = [a['annotation_id'] for r in registries for a in r['annotations']]
    assert len(ids) == len(set(ids)) == 4
    assert registries[0]['source_sha256'] == registries[1]['source_sha256']


@pytest.mark.parametrize('amount', ['<$51.20m', '$51.20m*'])
def test_censored_or_qualified_amount_is_not_a_plain_numeric_annotation(amount):
    raw = pdf(left=amount)
    registry = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert not any(a['label'] == 'TOTAL ALPHA FUNDS:' for a in registry['annotations'])


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_declared_rotation_preserves_original_native_coordinates(rotation):
    with fitz.open(stream=pdf(), filetype='pdf') as document:
        document[0].set_rotation(rotation)
        raw = document.tobytes()
    registry = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert registry['page_rotation'] == rotation and len(registry['annotations']) == 2
    assert registry['annotations'][0]['amount_bbox_pt'][0] == pytest.approx(340)


@pytest.mark.parametrize('status', [401, 403, 500])
def test_provider_failure_cannot_become_a_successful_amount(tmp_path, status):
    def fail(call, context):
        raise GenerationError('synthetic provider error', status=status)
    client = Client(hook=fail)
    store, hits = environment(tmp_path, client=client)
    if status in (401, 403):
        with pytest.raises(GenerationError):
            route(store, QUESTION, hits)
    else:
        result, trace = route(store, QUESTION, hits)
        assert result['status'] == 'insufficient_evidence'
        assert trace['reason'] == 'native_total_provider_unavailable'
    assert client.calls == 1


def test_fake_declared_page_count_cannot_hide_an_unscanned_page(tmp_path):
    store, _ = environment(tmp_path)
    sources = [{'document_id':d['document_id'],'sha256':d['sha256']} for d in store.list_documents()]
    document = store.document
    def wrong_metadata(identifier):
        record = deepcopy(document(identifier))
        record['stats']['page_count'] = 2
        return record
    store.document = wrong_metadata
    with pytest.raises(ValueError, match='source_page_count_invalid'):
        _registries(store, sources, None)


def test_chinese_total_labels_keep_own_original_amount():
    with fitz.open() as document:
        page = document.new_page()
        for index, (label, value) in enumerate([('阿尔法基金总额：', '$51.20m'), ('总计贝塔基金：', '$18.10m')]):
            page.insert_text((50, 100+index*130), label, fontname='china-s', fontsize=10)
            page.insert_text((340, 100+index*130), value, fontsize=10)
        raw = document.tobytes()
    registry = extract(raw, page_no=1, expected_source_sha256=hashlib.sha256(raw).hexdigest())
    assert [a['raw_value'] for a in registry['annotations']] == ['$51.20m','$18.10m']
