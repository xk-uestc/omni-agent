from backend.ocr import _regional_numeric_table


def pairs(ys):
    return [cell for i,y in enumerate(ys) for cell in [
        {'text':f'Item {i}','confidence':.9,'bbox':[10,y,100,y+20]},
        {'text':f'{10+i}.00','confidence':.9,'bbox':[160,y,200,y+20]}]]


def test_short_blocks_preserve_observations_without_claiming_a_table():
    result=_regional_numeric_table(pairs([0,30,60,300,500,530]))
    assert result['row_count']==3
    sparse=result['sparse_observations']
    assert [block['row_count'] for block in sparse]==[1,2]
    assert all(block['is_table'] is False and block['cell_values_verified'] is False for block in sparse)
    assert all(block['numeric_column_observation_count']==6 for block in sparse)
    assert sparse[0]['rows'][0][1]['text']=='13.00'


def test_sparse_only_page_requires_repeated_numeric_column_and_never_invents_table_rows():
    result=_regional_numeric_table(pairs([0,300,600]))
    assert result['is_table'] is False and result['row_count']==0 and result['rows']==[]
    assert len(result['sparse_observations'])==3
    assert _regional_numeric_table(pairs([0,300])) is None
    regions=pairs([0,300,600])
    for index,cell in enumerate(regions[1::2]):
        cell['bbox']=[160+index*100,cell['bbox'][1],200+index*100,cell['bbox'][3]]
    assert _regional_numeric_table(regions) is None


def test_overlapping_rows_are_not_repackaged_as_sparse_observations():
    assert _regional_numeric_table(pairs([0,5,10])) is None


def test_sparse_only_candidate_survives_pipeline_selection_and_keeps_source_binding():
    from io import BytesIO
    import hashlib
    from PIL import Image
    from backend.ocr import OcrPipeline,OcrResponse
    class Executor:
        name='sparse_observation_fixture'
        def execute(self,data,*,language):
            with Image.open(BytesIO(data)) as image:size=list(image.size)
            return OcrResponse('three short entries',.95,{
                'coordinate_frame':{'scope':'ocr_executor_input','size_px':size,'sha256':hashlib.sha256(data).hexdigest()},
                'table_layout':_regional_numeric_table(pairs([0,300,600]))})
    output=BytesIO();Image.new('RGB',(260,680),'white').save(output,format='PNG')
    data=output.getvalue()
    result=OcrPipeline(Executor()).run(data,max_attempts=1)
    evidence=result.metadata['borderless_table_evidence']
    assert evidence['table_layout']['is_table'] is False
    assert evidence['original_pixel_mapping']['source']['sha256']==hashlib.sha256(data).hexdigest()
    cells=[cell for region in evidence['table_layout']['sparse_observations'] for row in region['rows'] for cell in row]
    assert len(cells)==6 and all(cell['original_geometry']['original_bbox_eligible'] for cell in cells)
