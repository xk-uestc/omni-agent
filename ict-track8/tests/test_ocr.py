from __future__ import annotations

import base64
from dataclasses import dataclass

from fastapi.testclient import TestClient

import backend.app as app_module
from backend.ocr import OcrPipeline, OcrResponse, build_ocr_pipeline, infer_ocr_table_layout


@dataclass
class FakeQuality:
    recommended_transforms: tuple[str, ...] = ("grayscale_normalize", "upscale")


class FakeAnalyzer:
    def analyze(self, _image_bytes):
        return FakeQuality()


class FakeEnhancer:
    def __init__(self):
        self.calls = []

    def enhance(self, image_bytes, *, transforms, rotation_degrees=0):
        self.calls.append(tuple(transforms))
        return type("Enhanced", (), {"image_bytes": image_bytes})()


class FakeExecutor:
    name = "fake"

    def __init__(self):
        self.calls = 0

    def execute(self, _image_bytes, *, language):
        self.calls += 1
        assert language == "chi_sim+eng"
        if self.calls == 1:
            return OcrResponse("模糊结果", 0.42, {})
        return OcrResponse("清晰结果", 0.96, {})


def test_ocr_pipeline_preserves_retry_evidence():
    enhancer = FakeEnhancer()
    executor = FakeExecutor()
    result = OcrPipeline(executor, analyzer=FakeAnalyzer(), enhancer=enhancer).run(
        b"image", language="chi_sim+eng", max_attempts=3
    )
    assert result.status == "ok"
    assert result.text == "清晰结果"
    assert result.selected_transforms == ("grayscale_normalize",)
    assert [attempt.status for attempt in result.attempts] == ["low_confidence", "accepted"]
    assert enhancer.calls == [(), ("grayscale_normalize",)]


def test_ocr_pipeline_health_is_read_only_and_describes_executor():
    pipeline = OcrPipeline(FakeExecutor(), analyzer=FakeAnalyzer(), enhancer=FakeEnhancer())
    health = pipeline.health()
    assert health["configured"] is True
    assert health["ready"] is True
    assert health["provider"] == "fake"


def test_invalid_ocr_url_disables_executor_without_import_failure(monkeypatch):
    warnings: list[str] = []
    monkeypatch.setenv("ICT8_OCR_URL", "ftp://not-supported")
    assert build_ocr_pipeline(warnings) is None
    assert warnings == ["ICT8_OCR_URL: invalid; OCR disabled"]


def test_quality_retries_do_not_replace_better_baseline_with_last_attempt():
    class PoorQuality(FakeAnalyzer):
        def analyze(self, _image_bytes):
            return type('Quality', (), {'quality_score': 0.4, 'recommended_transforms': ('upscale', 'contrast')})()

    class RegressingExecutor:
        name = 'fake'
        def __init__(self):
            self.responses = iter([OcrResponse('一般工单24小时', .98, {'selected': 'baseline'}),
                                   OcrResponse('一工单24小时', .94, {}), OcrResponse('一工单24小时', .95, {})])
        def execute(self, _image_bytes, *, language):
            return next(self.responses)

    result = OcrPipeline(RegressingExecutor(), analyzer=PoorQuality(), enhancer=FakeEnhancer()).run(b'image')
    assert len(result.attempts) == 3
    assert result.text == '一般工单24小时'
    assert result.selected_transforms == ()
    assert result.metadata['selected'] == 'baseline'


def test_orientation_evidence_triggers_one_bounded_correction_pass():
    class OrientationExecutor:
        name = 'orientation-fixture'
        def __init__(self):
            self.calls = 0
        def execute(self, _image_bytes, *, language):
            self.calls += 1
            if self.calls == 1:
                return OcrResponse('倾斜文本', .65, {
                    'orientation': {'status': 'estimated', 'correction_ccw_degrees': 90},
                })
            return OcrResponse('校正文本', .96, {
                'orientation': {'status': 'estimated', 'correction_ccw_degrees': 0},
            })

    enhancer = FakeEnhancer()
    executor = OrientationExecutor()
    result = OcrPipeline(executor, analyzer=FakeAnalyzer(), enhancer=enhancer).run(
        b'image', language='chi_sim+eng', max_attempts=2)
    assert executor.calls == 2
    assert result.text == '校正文本'
    assert result.selected_transforms == ('grayscale_normalize', 'rotate_to_upright')
    assert enhancer.calls == [(), ('grayscale_normalize', 'rotate_to_upright')]
    assert len(result.attempts) == executor.calls == 2


def test_ocr_table_layout_is_candidate_only_and_preserves_raggedness():
    regions = [
        {'text': '指标', 'bbox': [0, 0, 40, 20], 'confidence': .95},
        {'text': '值', 'bbox': [80, 0, 110, 20], 'confidence': .9},
        {'text': '销售额', 'bbox': [0, 30, 60, 50], 'confidence': .9},
        {'text': '42', 'bbox': [80, 30, 100, 50], 'confidence': .92},
    ]
    layout = infer_ocr_table_layout(regions)
    assert layout['status'] == 'candidate'
    assert layout['verification'] == 'ocr_geometry_candidate_only'
    assert layout['row_count'] == 2 and layout['column_count'] == 2
    assert layout['rows'][1][1]['text'] == '42'


def test_real_enhancer_rotation_sign_and_executor_budget():
    from io import BytesIO
    from PIL import Image
    from backend.image_quality import ImageEnhancer
    output = BytesIO()
    Image.new('RGB', (70, 30), 'white').save(output, format='PNG')
    class Executor:
        name = 'budget-test'
        calls = 0
        def execute(self, data, *, language):
            self.calls += 1
            with Image.open(BytesIO(data)) as image:
                assert image.size == ((70, 30) if self.calls == 1 else (30, 70))
            return OcrResponse('reliable baseline' if self.calls == 1 else 'poorer correction',
                .98 if self.calls == 1 else .83,
                {'orientation': {'status': 'estimated', 'correction_ccw_degrees': 90}})
    class RecordingEnhancer(ImageEnhancer):
        angles = []
        def enhance(self, data, **kwargs):
            self.angles.append(kwargs.get('rotation_degrees', 0))
            return super().enhance(data, **kwargs)
    class Quality:
        def analyze(self, _):
            return type('Q', (), {'recommended_transforms': (), 'quality_score': 1})()
    executor, enhancer = Executor(), RecordingEnhancer()
    result = OcrPipeline(executor, analyzer=Quality(), enhancer=enhancer).run(output.getvalue(), max_attempts=2)
    assert executor.calls == len(result.attempts) == 2
    assert enhancer.angles == [0, -90]
    assert result.text == 'reliable baseline' and result.selected_transforms == ()


def test_one_attempt_does_not_spend_extra_call_on_orientation():
    class Executor:
        name = 'one-call'
        calls = 0
        def execute(self, data, *, language):
            self.calls += 1
            return OcrResponse('text', .92,
                {'orientation': {'status': 'estimated', 'correction_ccw_degrees': 180}})
    executor = Executor()
    result = OcrPipeline(executor, analyzer=FakeAnalyzer(), enhancer=FakeEnhancer()).run(b'image', max_attempts=1)
    assert executor.calls == len(result.attempts) == 1
    assert result.selected_transforms == ()


def test_real_quadrilateral_regions_and_prose_are_distinguished():
    regions = [{'text': str(row * 2 + col), 'confidence': .95,
        'bbox': [[col * 100, row * 30], [col * 100 + 40, row * 30],
                 [col * 100 + 40, row * 30 + 20], [col * 100, row * 30 + 20]]}
        for row in range(3) for col in range(2)]
    layout = infer_ocr_table_layout(regions)
    assert layout['status'] == 'candidate' and layout['row_count'] == 3
    ragged = infer_ocr_table_layout(regions + [{'text': 'note', 'confidence': .9, 'bbox': [0, 100, 30, 120]}])
    assert ragged['ragged_row_count'] == 1
    assert infer_ocr_table_layout([{'text': 'line', 'confidence': .9,
        'bbox': [0, row * 30, 200, row * 30 + 20]} for row in range(6)])['status'] == 'insufficient_columns'
    assert infer_ocr_table_layout(regions * 400)['status'] == 'region_budget_exceeded'


def right_aligned_regions():
    return [item for row,width in enumerate([12,12,240,240]) for item in [
        {'text':f'item{row}','confidence':.95,'bbox':[20,row*40,100,row*40+20]},
        {'text':['1','123456','123456789','42'][row],'confidence':.95,
         'bbox':[500-width,row*40,500,row*40+20]}]]


def test_borderless_varying_width_amounts_use_repeated_right_edges():
    result=infer_ocr_table_layout(right_aligned_regions())
    assert result['status']=='candidate'
    assert result['alignment_method']=='repeated_left_or_right_edges_with_global_gutters'
    assert result['column_gutters_px']==[[100,260]]
    assert result['cell_values_verified'] is False
    assert result['rows'][2][1]['text']=='123456789'


def test_borderless_edge_fallback_requires_three_rows_and_actual_gutters():
    regions=right_aligned_regions()
    sparse=regions[:4]
    sparse[3]={**sparse[3],'bbox':[270,40,282,60]}
    assert infer_ocr_table_layout(sparse)['status']=='unbound_columns'
    for index in (1,3,5,7):
        regions[index]['bbox'][0]-=180
    assert infer_ocr_table_layout(regions)['status']=='unbound_columns'


def test_borderless_ragged_observation_does_not_invent_missing_cell():
    regions=right_aligned_regions()+[{'text':'note','confidence':.9,'bbox':[20,170,100,190]}]
    result=infer_ocr_table_layout(regions)
    assert result['status']=='candidate'
    assert result['ragged_row_count']==1
    assert len(result['rows'][-1])==1


def test_mixed_prose_page_has_explicitly_local_numeric_table_scope():
    regions=[]
    for row in range(4):
        y=30+row*35
        regions.extend([{'text':f'Cost {row}','confidence':.9,'bbox':[10,y,90,y+20]},
            {'text':str(10+row),'confidence':.9,'bbox':[170,y,200,y+20]},
            {'text':'This is prose outside the table','confidence':.9,'bbox':[260,y,600,y+20]}])
    regions.extend([{'text':'A wide page title','confidence':.9,'bbox':[10,0,600,20]},
        {'text':'continuing paragraph below','confidence':.9,'bbox':[10,200,600,220]}])
    result=infer_ocr_table_layout(regions)
    assert result['status']=='candidate'
    assert result['scope']=='local_observed_pairs_not_complete_table'
    assert result['row_count']==4 and result['column_count']==2
    assert all('prose' not in cell['text'] for row in result['rows'] for cell in row)
    assert result['cell_values_verified'] is False


def continuation_regions():
    regions=[]
    for i,y in enumerate([0,30,60,200]):
        regions.extend([{'text':f'Cost {i}','confidence':.9,'bbox':[10,y,110,y+20]},
                        {'text':str(100+i),'confidence':.9,'bbox':[180,y,220,y+20]}])
    regions.extend({'text':f'continued label {i}','confidence':.9,'bbox':[10,y,150,y+20]}
                   for i,y in enumerate([85,110,135,160,180]))
    return regions


def test_local_table_continuity_keeps_multiline_observations_separate_from_labels():
    from backend.ocr import _regional_numeric_table
    regions=continuation_regions()
    result=_regional_numeric_table(regions)
    assert result['row_count']==4
    bridge=result['continuity_evidence'][0]
    assert bridge['semantic_assignment_verified'] is False
    assert (bridge['from_amount'],bridge['to_amount'])==('102','103')
    assert len(bridge['observations'])==5
    assert result['rows'][-1][0]['text']=='Cost 3'
    assert all('continued' not in cell['text'] for row in result['rows'] for cell in row)
    assert result['scope']=='local_observed_pairs_not_complete_table'


def test_local_table_blank_gap_and_conflicting_column_do_not_bridge():
    from backend.ocr import _regional_numeric_table
    regions=continuation_regions()
    assert _regional_numeric_table(regions[:8])['row_count']==3
    for box,text in [([10,115,240,135],'paragraph crosses amount column'),
                     ([120,115,145,135],'99'),([40,115,150,135],'different left edge')]:
        altered=regions+[{'text':text,'confidence':.9,'bbox':box}]
        assert _regional_numeric_table(altered)['row_count']==3
    missing=regions[:8]+regions[10:]
    assert _regional_numeric_table(missing)['row_count']==3


def test_local_table_right_side_prose_is_outside_the_continuity_corridor():
    from backend.ocr import _regional_numeric_table
    regions=continuation_regions()+[{'text':'unrelated right column','confidence':.9,
                                   'bbox':[270,115,450,135]}]
    result=_regional_numeric_table(regions)
    assert result['row_count']==4
    assert all('unrelated' not in item['text'] for item in result['continuity_evidence'][0]['observations'])


def test_independent_side_by_side_blocks_and_headings_are_preserved_separately():
    from backend.ocr import _regional_numeric_table
    regions=[]
    for x,offset in [(10,0),(270,100)]:
        for start in [0,110]:
            for i in range(3):
                y=start+i*25
                regions.extend([{'text':f'Cost {offset+i}','confidence':.9,'bbox':[x,y,x+80,y+20]},
                                {'text':str(offset+i+1),'confidence':.9,'bbox':[x+140,y,x+180,y+20]}])
        regions.append({'text':'DEPARTMENT TITLE','confidence':.9,'bbox':[x,85,x+120,103]})
    result=_regional_numeric_table(regions)
    assert len(result['regional_candidates'])==4
    assert all(candidate['row_count']==3 for candidate in result['regional_candidates'])
    assert all('DEPARTMENT' not in cell['text'] for candidate in result['regional_candidates'] for row in candidate['rows'] for cell in row)


def test_right_aligned_labels_and_minor_ocr_row_overlap_still_require_distinct_rows():
    from backend.ocr import _regional_numeric_table
    regions=[]
    for i,left in enumerate([10,30,20]):
        y=i*18
        regions.extend([{'text':f'Cost {i}','confidence':.9,'bbox':[left,y,100,y+20]},
                        {'text':str(i+1),'confidence':.9,'bbox':[140,y,180,y+20]}])
    result=_regional_numeric_table(regions)
    assert result['row_count']==3
    assert len(result['regional_candidates'])==1
    overlapping=[{**item,'bbox':[item['bbox'][0],i//2*5,item['bbox'][2],i//2*5+20]} for i,item in enumerate(regions)]
    assert _regional_numeric_table(overlapping) is None
