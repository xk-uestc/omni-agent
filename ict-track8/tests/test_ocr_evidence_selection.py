from copy import deepcopy
import pytest
from backend.ocr_evidence_selection import OcrEvidenceSelectionAgent


def candidate(attempt,confidence,label='Democratlc Reform',amount='$5.49',shift=0,source='a'*64):
    rows=[[{'text':text,'confidence':confidence,'original_geometry':{
        'original_bbox_eligible':True,'source_sha256':source,'coordinate_scope':'source_image_stored_pixel_edges',
        'bbox_px':[10+column*140+shift,10,100+column*140+shift,30]}}
        for column,text in enumerate([label,amount])]]
    region={'scope':'local_observed_pairs_not_complete_table','rows':rows}
    return {'source_attempt':attempt,'coordinate_frame':{'sha256':str(attempt)*64},
        'selected_transforms':['sharpen'] if attempt==2 else [],
        'original_pixel_mapping':{'status':'mapped','source':{'sha256':source}},
        'table_layout':{**deepcopy(region),'regional_candidates':[region],'sparse_observations':[]}}


def test_equivalent_region_improves_label_and_keeps_actual_attempt_provenance_and_coverage():
    coverage=candidate(2,.9);original=candidate(1,.99,'Democratic Reform');before=deepcopy(coverage)
    coverage['table_layout']['sparse_observations']=[{'scope':'locally_reobserved_label_amount_pair','is_table':False,'rows':[]}]
    result=OcrEvidenceSelectionAgent().select(coverage,[original,coverage])
    region=result['table_layout']['regional_candidates'][0]
    assert region['rows'][0][0]['text']=='Democratic Reform'
    assert region['observation_provenance']['source_attempt']==1
    assert region['observation_provenance']['coordinate_frame']==original['coordinate_frame']
    assert result['table_layout']['rows'][0][0]['text']=='Democratic Reform'
    assert len(result['table_layout']['sparse_observations'])==1
    assert result['region_selection']['replaced_regions']==1
    assert coverage['table_layout']['regional_candidates']==before['table_layout']['regional_candidates']


@pytest.mark.parametrize('changes',[{'amount':'$5.50'},{'shift':80},{'source':'b'*64},{'confidence':float('nan')}])
def test_different_amount_position_source_or_invalid_confidence_cannot_replace(changes):
    coverage=candidate(2,.9);alternative=candidate(1,.99,'Democratic Reform',**changes) if 'confidence' not in changes else candidate(1,changes['confidence'],'Democratic Reform')
    result=OcrEvidenceSelectionAgent().select(coverage,[alternative])
    assert result['table_layout']['rows'][0][0]['text']=='Democratlc Reform'
    assert result['region_selection']['replaced_regions']==0


def test_duplicate_matches_do_not_arbitrarily_select_another_region():
    coverage=candidate(2,.9);alternative=candidate(1,.99,'Democratic Reform')
    alternative['table_layout']['regional_candidates']*=2
    result=OcrEvidenceSelectionAgent().select(coverage,[alternative])
    assert result['region_selection']['replaced_regions']==0


def test_pipeline_preserves_additional_coverage_and_uses_better_label_with_own_frame():
    from io import BytesIO
    import hashlib
    from PIL import Image
    from backend.ocr import OcrPipeline,OcrResponse
    class Quality:
        def analyze(self,data):return type('Q',(),{'quality_score':0.,'recommended_transforms':('grayscale_normalize',)})()
    class Executor:
        name='region_selection_fixture'
        def __init__(self):self.calls=0
        def execute(self,data,*,language):
            self.calls+=1;attempt=self.calls
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            current=candidate(attempt,.99 if attempt==1 else .9,'Democratic Reform' if attempt==1 else 'Democratlc Reform')
            layout=current['table_layout'];layout.update(status='candidate',row_count=1,column_count=2,ragged_row_count=0)
            for region in [layout,*layout['regional_candidates']]:
                for row in region['rows']:
                    for cell in row:cell['bbox']=cell.pop('original_geometry')['bbox_px']
            if attempt==2:
                layout['sparse_observations']=[{'status':'candidate','scope':'locally_reobserved_label_amount_pair',
                    'is_table':False,'rows':[[{'text':'Total','confidence':.9,'bbox':[10,50,100,70]},
                                             {'text':'$22.56','confidence':.9,'bbox':[150,50,240,70]}]]}]
            return OcrResponse('text',.99 if attempt==1 else .9,{'table_layout':layout,
                'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,'sha256':hashlib.sha256(data).hexdigest()}})
    output=BytesIO();Image.new('RGB',(260,100),'white').save(output,format='PNG')
    result=OcrPipeline(Executor(),analyzer=Quality()).run(output.getvalue(),max_attempts=2)
    evidence=result.metadata['borderless_table_evidence']
    assert evidence['source_attempt']==2 and evidence['region_selection']['replaced_regions']==1
    assert evidence['table_layout']['rows'][0][0]['text']=='Democratic Reform'
    assert evidence['table_layout']['regional_candidates'][0]['observation_provenance']['source_attempt']==1
    assert evidence['table_layout']['sparse_observations'][0]['rows'][0][1]['text']=='$22.56'


def sparse_candidate(attempt,amount='$22.56',offset=60,source='a'*64):
    value=candidate(attempt,.95,source=source)
    value['original_pixel_mapping']['source']['size_px']=[500,200]
    pair=deepcopy(value['table_layout']['regional_candidates'][0])
    pair.update(scope='locally_reobserved_label_amount_pair',is_table=False)
    pair['rows'][0][1]['text']=amount
    for cell in pair['rows'][0]:
        cell['original_geometry']['bbox_px'][1]+=offset
        cell['original_geometry']['bbox_px'][3]+=offset
    value['table_layout']['sparse_observations']=[pair]
    return value


def test_disjoint_pair_is_preserved_with_its_own_frame_without_changing_table_rows():
    baseline=sparse_candidate(1)
    coverage=sparse_candidate(2)
    coverage['table_layout']['sparse_observations']=[]
    before=deepcopy(coverage)
    result=OcrEvidenceSelectionAgent().select(coverage,[baseline,coverage])
    added=result['table_layout']['sparse_observations']
    assert len(added)==1 and added[0]['rows'][0][1]['text']=='$22.56'
    assert added[0]['observation_provenance']['source_attempt']==1
    assert added[0]['observation_provenance']['coordinate_frame']==baseline['coordinate_frame']
    assert added[0]['is_table'] is False and added[0]['cell_values_verified'] is False
    assert result['table_layout']['rows']==before['table_layout']['rows']
    assert result['region_selection']['supplemented_independent_pairs']==1
    assert coverage==before


@pytest.mark.parametrize('change',['conflict','overlap','foreign','outside','missing_frame','nan_confidence','bad_shape'])
def test_unsafe_supplement_does_not_enter_selected_evidence(change):
    original=sparse_candidate(1)
    selected=sparse_candidate(2);selected['table_layout']['sparse_observations']=[]
    attempts=[original,selected]
    pair=original['table_layout']['sparse_observations'][0]
    if change=='conflict':attempts.insert(1,sparse_candidate(3,amount='$22.57'))
    if change=='overlap':original=sparse_candidate(1,offset=0);attempts[0]=original
    if change=='foreign':attempts[0]=sparse_candidate(1,source='b'*64)
    if change=='outside':pair['rows'][0][0]['original_geometry']['bbox_px'][2]=600
    if change=='missing_frame':original['coordinate_frame']={}
    if change=='nan_confidence':pair['rows'][0][1]['confidence']=float('nan')
    if change=='bad_shape':pair['rows'][0].pop()
    result=OcrEvidenceSelectionAgent().select(selected,attempts)
    assert result['region_selection']['supplemented_independent_pairs']==0
    assert result['table_layout']['sparse_observations']==[]


def test_repeated_same_pair_is_added_only_once_and_budget_is_preserved():
    original=sparse_candidate(1);duplicate=sparse_candidate(3)
    selected=sparse_candidate(2);selected['table_layout']['sparse_observations']=[]
    result=OcrEvidenceSelectionAgent().select(selected,[original,duplicate,selected])
    assert result['region_selection']['supplemented_independent_pairs']==1
    assert len(result['table_layout']['sparse_observations'])==1
