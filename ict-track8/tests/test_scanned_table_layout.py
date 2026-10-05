from io import BytesIO
from PIL import Image, ImageDraw
from backend.scanned_table_layout import extract_scanned_grids, recover_grid_cells,recover_borderless_rows


def scan():
    image = Image.new('RGB', (400, 260), 'white')
    draw = ImageDraw.Draw(image)
    for x in (30, 190, 370):
        draw.line((x, 30, x, 230), fill='black', width=3)
    for y in (30, 100, 170, 230):
        draw.line((30, y, 370, y), fill='black', width=3)
    stream = BytesIO()
    image.save(stream, format='PNG')
    return stream.getvalue()


def region(text, left, top, right, bottom):
    return {'text': text, 'confidence': .92,
            'bbox': [[left, top], [right, top], [right, bottom], [left, bottom]]}


def test_repeated_short_glyph_stems_never_become_ruled_column_boundaries():
    with Image.open(BytesIO(scan())) as image:
        draw=ImageDraw.Draw(image)
        # Repeated stems meet the global projection threshold but never join
        # the horizontal ruling at either end of any complete row.
        for y in (45,115,185):draw.line((100,y,100,y+35),fill='black',width=3)
        output=BytesIO();image.save(output,format='PNG')
    tables=extract_scanned_grids(output.getvalue(),[])['tables']
    assert len(tables)==1 and tables[0]['column_count']==2 and tables[0]['row_count']==3


def test_scanned_cells_retain_empty_cells_and_exact_observation_binding():
    result = extract_scanned_grids(scan(), [region('Metric', 50, 50, 110, 70),
        region('42', 220, 120, 245, 140)])
    assert result['status'] == 'observed'
    table = result['tables'][0]
    assert table['row_count'] == 3 and table['column_count'] == 2
    assert table['cells'][0][0]['text'] == 'Metric'
    assert table['cells'][1][1]['text'] == '42'
    assert table['cells'][2][1]['status'] == 'empty_or_unrecognized'
    assert table['cell_values_verified'] is False


def test_cross_column_ocr_box_is_never_guessed_into_a_cell():
    result = extract_scanned_grids(scan(), [region('cross boundary', 120, 50, 240, 70)])
    table = result['tables'][0]
    assert table['unbound_region_indices'] == [0]
    assert not any(cell['text'] for row in table['cells'] for cell in row)


def test_blank_and_borderless_page_does_not_invent_grid():
    stream = BytesIO()
    Image.new('RGB', (400, 260), 'white').save(stream, format='PNG')
    assert extract_scanned_grids(stream.getvalue(), [])['status'] == 'no_complete_rectangular_grid'


def test_cell_recovery_is_bounded_and_does_not_overwrite_existing_observation():
    raw = scan()
    layout = extract_scanned_grids(raw, [region('existing', 50, 50, 110, 70)])
    calls = []
    def recognize(data):
        calls.append(data)
        return [{'text': '47,000', 'confidence': .95}]
    result = recover_grid_cells(raw, layout, recognize, max_cells=2)
    table = result['tables'][0]
    assert len(calls) == result['cell_recovery']['calls'] == 2
    assert table['cells'][0][0]['text'] == 'existing'
    assert table['cells'][0][1]['text'] == '47,000'
    assert table['cells'][0][1]['crop_observations'][0]['crop_sha256']
    assert table['cells'][1][1]['status'] == 'empty_or_unrecognized'
    assert table['cell_values_verified'] is False


def test_cell_recovery_keeps_failed_cells_unresolved():
    def fail(_):
        raise RuntimeError('OCR unavailable')
    result = recover_grid_cells(scan(), extract_scanned_grids(scan(), []), fail, max_cells=1)
    assert result['cell_recovery']['failures'] == 1
    assert not any(cell['text'] for row in result['tables'][0]['cells'] for cell in row)


def merged_scan(kind):
    image = Image.new('RGB', (400, 260), 'white')
    draw = ImageDraw.Draw(image)
    for x in (30,370):
        draw.line((x,30,x,230), fill='black', width=3)
    for y in (30,230):
        draw.line((30,y,370,y), fill='black', width=3)
    draw.line((30,170,370,170), fill='black', width=3)
    draw.line((190,100,190,230), fill='black', width=3)
    if kind == 'colspan':
        draw.line((30,100,370,100), fill='black', width=3)
    elif kind == 'rowspan':
        draw.line((190,30,190,100), fill='black', width=3)
        draw.line((190,100,370,100), fill='black', width=3)
    elif kind == 'nonrectangular':
        draw.line((190,100,370,100), fill='black', width=3)
    elif kind == 'broken':
        draw.line((30,100,370,100), fill='black', width=3)
        draw.line((190,30,190,60), fill='black', width=3)
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def test_merged_header_colspan_binds_one_observation_to_anchor():
    layout = extract_scanned_grids(merged_scan('colspan'), [region('Annual report',80,50,300,70)])
    table = layout['tables'][0]
    assert table['row_count'] == 3 and table['column_count'] == 2
    assert table['merged_cell_count'] == 1
    assert table['cells'][0][0]['colspan'] == 2
    assert table['cells'][0][0]['text'] == 'Annual report'
    assert table['cells'][0][1]['status'] == 'covered_by_merged_cell'
    recovered = recover_grid_cells(merged_scan('colspan'), layout,
        lambda _: [{'text':'crop', 'confidence':.9}], max_cells=8)
    assert recovered['cell_recovery']['calls'] == 4


def test_vertical_merge_keeps_explicit_rowspan_and_covered_cell():
    layout = extract_scanned_grids(merged_scan('rowspan'), [region('Quarter',50,50,110,140)])
    table = layout['tables'][0]
    assert table['cells'][0][0]['rowspan'] == 2
    assert table['cells'][0][0]['text'] == 'Quarter'
    assert table['cells'][1][0]['anchor'] == [0,0]


def test_broken_or_nonrectangular_grid_does_not_invent_merged_cells():
    for kind in ('broken', 'nonrectangular'):
        layout = extract_scanned_grids(merged_scan(kind), [])
        assert layout['status'] == 'no_complete_rectangular_grid'
        assert layout['tables'] == []


def test_borderless_recovery_reprojects_crop_boxes_and_respects_budget():
    layout={'status':'candidate','alignment_method':'repeated_left_or_right_edges_with_global_gutters',
        'column_count':2,'rows':[
            [{'bbox':[40,40,80,60]}],
            [{'bbox':[40,100,80,120]},{'bbox':[200,100,250,120]}],
            [{'bbox':[40,160,80,180]},{'bbox':[200,160,250,180]}]]}
    def recognize(data):
        return [region('1',20,20,40,40)]
    extra,audit=recover_borderless_rows(scan(),layout,[],recognize,max_cells=1)
    assert audit['calls']==1 and audit['recovered_regions']==1
    assert extra[0]['bbox']==[[202,38],[212,38],[212,48],[202,48]]
    assert extra[0]['observation_origin']=='borderless_cell_crop_ocr'
    assert len(extra[0]['crop_sha256'])==64
    assert recover_borderless_rows(scan(),layout,[],recognize,max_cells=0)[1]['calls']==0
    assert recover_borderless_rows(scan(),layout,[],recognize,max_seconds=0)[1]['calls']==0


def test_borderless_crop_failure_and_outside_box_never_invent_values():
    layout={'status':'candidate','alignment_method':'repeated_left_or_right_edges_with_global_gutters',
        'column_count':2,'rows':[[{'bbox':[40,40,80,60]}],
            [{'bbox':[40,100,80,120]},{'bbox':[200,100,250,120]}],
            [{'bbox':[40,160,80,180]},{'bbox':[200,160,250,180]}]]}
    def failure(data):raise RuntimeError('unavailable')
    assert recover_borderless_rows(scan(),layout,[],failure)[1]['recovered_regions']==0
    assert recover_borderless_rows(scan(),layout,[],lambda data:[region('outside',-50,-50,500,500)])[1]['recovered_regions']==0
