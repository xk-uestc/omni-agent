from copy import deepcopy
import base64
import pytest
from backend.pdf_table_preview import PdfTablePreviewAgent
from backend.pdf_table_continuity import PdfTableContinuityAgent
from tests.test_pdf_table_preview import pdf,FakePipeline


def link(p=1,t=0,next_table=0):return {'from_page':p,'from_table':t,'to_page':p+1,'to_table':next_table}


def structures(headers=(1,1,1)):
    raw=pdf(pages=len(headers))
    selected=[{'page_no':index+1,'table_index':0,'header_rows':header} for index,header in enumerate(headers)]
    return PdfTablePreviewAgent().run(raw,selected,ocr_pipeline=FakePipeline())


def test_repeated_header_chain_preserves_all_page_rows_and_does_not_mutate():
    tables=structures();before=deepcopy(tables)
    result=PdfTableContinuityAgent().run(tables,[link(2),link(1)])
    assert result['automatic_join'] is False
    assembly=result['assemblies'][0]
    assert assembly['status']=='assembled_preview' and assembly['row_count']==6
    assert [row['page_no'] for row in assembly['rows']]==[1,1,2,2,3,3]
    assert [row['cells'][1]['text'] for row in assembly['rows']]==['10','20']*3
    assert all(row['cells'][0]['original_geometry']['pdf_geometry']['page_no']==row['page_no'] for row in assembly['rows'])
    assert assembly['cell_values_verified'] is False and assembly['ready_for_sql_import'] is False
    assert tables==before


def test_candidate_only_never_stitches_without_explicit_confirmation():
    result=PdfTableContinuityAgent().run(structures((1,1)))
    assert result['assemblies']==[] and result['candidates'][0]['status']=='needs_confirmation'


def test_no_repeat_header_keeps_first_row_as_data_and_inherits_first_columns():
    result=PdfTableContinuityAgent().run(structures((1,0)),[link()])
    assembly=result['assemblies'][0]
    assert assembly['row_count']==5
    assert assembly['rows'][2]['original_row']==0
    assert assembly['rows'][2]['cells'][0]['text']=='Year'
    assert assembly['columns'][0]['header_path']==['Year']


@pytest.mark.parametrize('mutation,reason',[
    ('header','header_paths_changed'),('hash','different_pdf_sources'),('coordinate','incomplete_or_unlocated_table'),
    ('duplicate','ambiguous_header_paths'),('malformed','incomplete_or_unlocated_table')])
def test_incompatible_tables_reject_entire_assembly_without_partial_rows(mutation,reason):
    tables=structures((1,1))
    if mutation=='header':tables[1]['columns'][1]['header_path']=['Cost']
    if mutation=='hash':
        tables[1]['source_pdf_sha256']='b'*64
        for cell in [c for row in tables[1]['body_cells'] for c in row]+[c for col in tables[1]['columns'] for c in col['header_cells']]:
            cell['original_geometry']['pdf_geometry']['source_pdf_sha256']='b'*64
    if mutation=='coordinate':tables[1]['body_cells'][0][0]['original_geometry']['pdf_geometry']['page_no']=1
    if mutation=='duplicate':tables[1]['columns'][1]['header_path']=['Year']
    if mutation=='malformed':tables[1]['columns'][1]['header_path']=[None]
    result=PdfTableContinuityAgent().run(tables,[link()])
    assert result['assemblies'][0]['status']=='rejected'
    assert result['assemblies'][0]['reason']==reason and result['assemblies'][0]['rows']==[]


@pytest.mark.parametrize('links',[[link(),link()],
    [{'from_page':1,'from_table':0,'to_page':3,'to_table':0}],
    [link(1,0,1)],[{'from_page':True,'from_table':0,'to_page':2,'to_table':0}],
    [link(),link(1,0,1)]])
def test_invalid_duplicate_missing_nonadjacent_and_branch_links_reject(links):
    with pytest.raises(ValueError):PdfTableContinuityAgent().run(structures(),links)


def test_api_returns_sidecar_keeps_native_text_and_rejects_bad_links_before_ocr(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import app as module
    pipeline=FakePipeline();monkeypatch.setattr(module,'ocr_pipeline',pipeline)
    client=TestClient(module.app)
    payload={'document_id':'continuity','modality':'pdf','file_base64':base64.b64encode(pdf()).decode(),
        'pdf_table_headers':[{'page_no':p,'table_index':0,'header_rows':1} for p in (1,2)],'pdf_table_links':[link()]}
    response=client.post('/api/v1/documents/chunks-preview',json=payload)
    assert response.status_code==200
    result=response.json();assert result['table_continuity']['assemblies'][0]['row_count']==4
    assert 'Native original evidence' in '\n'.join(chunk['text'] for chunk in result['chunks'])
    pipeline.calls.clear();payload['pdf_table_links'][0]['to_page']=3
    response=client.post('/api/v1/documents/chunks-preview',json=payload)
    assert response.status_code==400 and pipeline.calls==[] and '相邻' in response.json()['detail']


def test_empty_cells_and_page_local_merged_anchors_are_not_filled_or_reindexed():
    tables=structures((1,1))
    tables[1]['body_cells'][0][0]['rowspan']=2
    tables[1]['body_cells'][1][0].update({'status':'covered_by_merged_cell','anchor':[1,0]})
    tables[1]['body_cells'][0][1]['text']=''
    result=PdfTableContinuityAgent().run(tables,[link()])['assemblies'][0]
    assert result['rows'][2]['cells'][0]['rowspan']==2
    assert result['rows'][3]['cells'][0]['anchor']==[1,0]
    assert result['rows'][2]['cells'][1]['text']==''
    assert result['rows'][3]['original_row']==2


def test_independent_chains_do_not_mix_or_collapse_same_value_rows():
    result=PdfTableContinuityAgent().run(structures((1,1,1,1)),[link(1),link(3)])
    assert len(result['assemblies'])==2
    assert [[row['page_no'] for row in table['rows']] for table in result['assemblies']]==[[1,1,2,2],[3,3,4,4]]


def test_missing_first_page_grid_returns_rejection_instead_of_server_error():
    tables=structures((1,1));tables[0]={'page_no':1,'table_index':0,'source_pdf_sha256':tables[0]['source_pdf_sha256'],
        'status':'unavailable','reason':'selected_grid_unavailable'}
    result=PdfTableContinuityAgent().run(tables,[link()])
    assert result['assemblies'][0]['reason']=='incomplete_or_unlocated_table'
    assert result['assemblies'][0]['rows']==[]


def test_unassigned_observations_preserved_with_explicit_incomplete_warning():
    tables=structures((1,1));tables[1]['unbound_region_indices']=[8]
    joined=PdfTableContinuityAgent().run(tables,[link()])['assemblies'][0]
    assert joined['status']=='assembled_preview' and joined['all_observations_assigned'] is False
    assert joined['segments'][1]['unbound_region_indices']==[8]
    assert any('未归入' in warning for warning in joined['warnings'])
