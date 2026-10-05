from copy import deepcopy
import pytest
from backend.scanned_table_structure import ScannedTableStructureAgent


def metadata():
    texts=[['Region','Sales',''],['','2024','2025'],['East','10','20'],['West','','30']]
    cells=[[{'row':r,'column':c,'rowspan':1,'colspan':1,'status':'ocr_observed',
             'text':text,'observations':[{'text':text}],
             'original_geometry':{'original_polygon_px':[[c,r]],'original_bbox_eligible':True}}
            for c,text in enumerate(row)] for r,row in enumerate(texts)]
    cells[0][0]['rowspan']=2
    cells[1][0].update(status='covered_by_merged_cell',anchor=[0,0])
    cells[0][1]['colspan']=2
    cells[0][2].update(status='covered_by_merged_cell',anchor=[0,1])
    return {'source_image_sha256':'a'*64,'scanned_grids':{'tables':[{
        'status':'layout_observed','row_count':4,'column_count':3,'cells':cells,
        'unbound_region_indices':[7]}]}}


def test_hierarchical_headers_preserve_sources_and_blank_body():
    source=metadata();before=deepcopy(source)
    result=ScannedTableStructureAgent().run(source,header_rows=2)
    assert result['status']=='structure_observed'
    assert [c['header_path'] for c in result['columns']]==[['Region'],['Sales','2024'],['Sales','2025']]
    assert result['columns'][1]['header_cells'][0]['original_geometry']==source['scanned_grids']['tables'][0]['cells'][0][1]['original_geometry']
    assert result['body_cells'][1][1]['text']==''
    assert result['unbound_region_indices']==[7]
    assert result['ready_for_sql_import'] is False
    assert result['cell_values_verified'] is False
    assert result['header_meanings_verified'] is False
    result['body_cells'][0][1]['text']='changed'
    assert source==before


def test_grid_uses_own_evidence_frame():
    source=metadata()
    outer={'source_image_sha256':'b'*64,'scanned_grids':{'tables':[]},'scanned_table_evidence':source}
    result=ScannedTableStructureAgent().run(outer,header_rows=2)
    assert result['columns'][1]['header_path']==['Sales','2024']


def test_known_sideways_grid_is_not_interpreted_as_upright_headers():
    source=metadata();source['orientation']={'status':'estimated','rotation_ccw_degrees':90}
    assert ScannedTableStructureAgent().run(source,header_rows=2)['reason']=='grid_requires_upright_frame'


def test_upright_grid_priority_precedes_coverage_and_does_not_trust_contradictory_correction():
    from backend.scanned_grid_selection import ScannedGridSelectionAgent
    agent=ScannedGridSelectionAgent();correction={'triggered':True,'correction_ccw_degrees':-90}
    sideways={'orientation':{'status':'estimated','rotation_ccw_degrees':90}}
    corrected={'orientation':{'status':'undetermined'}}
    upright={'orientation':{'status':'estimated','rotation_ccw_degrees':0}}
    old,_=agent.score(sideways,[{'text':'x'}]*10,.99)
    new,basis=agent.score(corrected,[{'text':'x'},{'text':''}],.92,correction)
    assert new>old and basis=='corrected_from_reliable_prior_text_direction'
    assert agent.score(upright,[{'text':'x'}],.9)[0]>new
    assert agent.orientation_priority(sideways,correction)[0]==0


def test_observed_axis_grid_does_not_receive_noisy_small_ocr_skew():
    from backend.scanned_grid_selection import ScannedGridSelectionAgent
    orientation={'rotation_ccw_degrees':90,'correction_ccw_degrees':-88.019,'skew_ccw_degrees':-1.981}
    correction,basis=ScannedGridSelectionAgent.correction(orientation,{'status':'observed','tables':[{}]})
    assert correction==-90 and basis=='observed_axis_aligned_grid_and_text_direction'
    assert ScannedGridSelectionAgent.correction(orientation,{'status':'no_complete_rectangular_grid'})[0]==-88.019
    orientation['skew_ccw_degrees']=5
    assert ScannedGridSelectionAgent.correction(orientation,{'status':'observed','tables':[{}]})[0]==-88.019


@pytest.mark.parametrize('mutation,reason',[
    (lambda t:t['cells'][1][0].update(anchor=[0,1]),'merged_anchor_mismatch'),
    (lambda t:t['cells'][0][1].update(colspan=4),'invalid_cell_span'),
    (lambda t:t['cells'][0][1].update(colspan=1),'uncovered_grid_positions'),
    (lambda t:t['cells'][2][1].update(rowspan=2),'overlapping_cell_spans'),
    (lambda t:t['cells'][3][2].update(column=0),'cell_position_mismatch'),
])
def test_malformed_geometry_is_not_flattened(mutation,reason):
    source=metadata();mutation(source['scanned_grids']['tables'][0])
    assert ScannedTableStructureAgent().run(source,header_rows=2)['reason']==reason


def test_boundary_cannot_cut_merged_header():
    assert ScannedTableStructureAgent().run(metadata(),header_rows=1)['reason']=='header_boundary_crosses_merged_cell'


def test_empty_or_duplicate_header_remains_unresolved():
    source=metadata();source['scanned_grids']['tables'][0]['cells'][1][2]['text']='2024'
    result=ScannedTableStructureAgent().run(source,header_rows=2)
    assert result['columns'][1]['status']=='ambiguous_duplicate_path'
    source['scanned_grids']['tables'][0]['cells'][1][2]['text']=''
    result=ScannedTableStructureAgent().run(source,header_rows=2)
    assert result['columns'][2]['status']=='needs_review'


@pytest.mark.parametrize('count',[True,-1,6,4])
def test_invalid_header_rows_do_not_invent_structure(count):
    assert ScannedTableStructureAgent().run(metadata(),header_rows=count)['status']=='unavailable'


def test_zero_header_rows_preserves_every_data_row_without_inventing_column_names():
    result=ScannedTableStructureAgent().run(metadata(),header_rows=0)
    assert len(result['body_cells'])==4
    assert result['body_cells'][0][0]['text']=='Region'
    assert all(c['header_path']==[] and c['status']=='no_header_selected' for c in result['columns'])


def test_ocr_endpoint_explicit_header_selection(monkeypatch):
    import base64
    from fastapi.testclient import TestClient
    from backend import app as app_module
    class Result:
        def to_dict(self):return {'metadata':metadata()}
    class Pipeline:
        def run(self,*args,**kwargs):return Result()
    monkeypatch.setattr(app_module,'ocr_pipeline',Pipeline())
    client=TestClient(app_module.app)
    response=client.post('/api/v1/documents/ocr',json={'image_base64':base64.b64encode(b'image').decode(),'table_header_rows':2})
    assert response.status_code==200
    assert response.json()['metadata']['table_structure']['columns'][1]['header_path']==['Sales','2024']
    assert client.post('/api/v1/documents/ocr',json={'image_base64':'aQ==','table_header_rows':6}).status_code==422
