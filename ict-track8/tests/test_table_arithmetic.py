from copy import deepcopy
import pytest
from backend.table_arithmetic import TableArithmeticAgent
from backend.pdf_amount_verification import amount


def table(texts=None):
    texts=texts or [['Parts','$0.10'],['Labor','$0.20'],['TOTAL:','$0.30']]
    return {'column_count':len(texts[0]),'columns':[{'header_path':['Item']},{'header_path':['Amount']}],
        'body_cells':[[{'text':text,'row':i,'column':j,'status':'ocr_observed','rowspan':1,'colspan':1,
            'original_geometry':{'source_image_sha256':'a'*64,'original_polygon_px':[[j,i]]}}
            for j,text in enumerate(row)] for i,row in enumerate(texts)]}


def test_exact_decimal_sum_and_source_cells_do_not_change_observations():
    source=table();before=deepcopy(source);result=TableArithmeticAgent().run(source)
    assert result['status']=='arithmetic_consistent'
    check=result['checks'][0]
    assert check['computed_total']=='0.30' and check['difference']=='0.00'
    assert check['detail_count']==2 and check['total_cell']['original_row']==2
    assert check['detail_cells'][0]['original_geometry']==source['body_cells'][0][1]['original_geometry']
    assert not result['cell_values_verified'] and not result['table_completeness_verified'] and source==before


def test_mismatch_reports_signed_difference_without_repair():
    source=table([['Parts','$10'],['Labor','$20'],['合计','$25']])
    result=TableArithmeticAgent().run(source)
    assert result['status']=='conflict' and result['checks'][0]['difference']=='-5'
    assert source['body_cells'][-1][1]['text']=='$25'


def test_negative_detail_and_repeated_labels_are_preserved_not_deduplicated():
    result=TableArithmeticAgent().run(table([['Parts','$10'],['Parts','$10'],['Refund','($5)'],['总计','$15']]))
    assert result['status']=='arithmetic_consistent' and result['checks'][0]['detail_count']==3


@pytest.mark.parametrize('texts,reason',[
    ([['A','$10'],['小计','$10'],['TOTAL','$20']],'subtotal_or_carry_forward_present'),
    ([['A','$10'],['Total parts','$10'],['TOTAL','$20']],'subtotal_or_carry_forward_present'),
    ([['A','$10'],['TOTAL','$10'],['B','$20']],'ambiguous_total_scope'),
    ([['A','$10'],['TOTAL','$10'],['合计','$10']],'ambiguous_total_scope'),
    ([['','$10'],['TOTAL','$10']],'missing_detail_label')])
def test_scope_ambiguity_rejects_without_computation(texts,reason):
    result=TableArithmeticAgent().run(table(texts));assert result['reason']==reason and result['checks']==[]


@pytest.mark.parametrize('value',['','$2.000','0','—','$1,00'])
def test_missing_or_ambiguous_amount_is_never_zero(value):
    source=table([['A',value],['TOTAL','$0']]);result=TableArithmeticAgent().run(source)
    assert result['status']=='unavailable' and result['checks'][0]['reason']=='amount_missing_or_ambiguous'
    assert result['checks'][0]['problem_cells'][0]['text']==value


def test_mixed_currency_and_unit_multiplier_are_not_assumed():
    result=TableArithmeticAgent().run(table([['A','USD 10'],['TOTAL','$10']]))
    assert result['checks'][0]['reason']=='mixed_or_unspecified_currency_identity'
    source=table();source['columns'][1]['header_path']=['Amount in thousands']
    assert TableArithmeticAgent().run(source)['reason']=='unit_scale_requires_confirmation'


def test_multi_amount_columns_checked_independently_and_any_failure_remains_visible():
    result=TableArithmeticAgent().run(table([['A','$10','€10'],['B','$20','€20'],['TOTAL','$30','€25']]))
    assert result['status']=='conflict'
    assert [c['status'] for c in result['checks']]==['arithmetic_consistent','conflict']


def test_merged_cells_and_missing_total_do_not_guess_scope():
    source=table();source['body_cells'][0][0]['rowspan']=2
    assert TableArithmeticAgent().run(source)['reason']=='merged_body_cells_require_review'
    assert TableArithmeticAgent().run(table([['A','$1'],['B','$2']]))['status']=='not_applicable'


def test_large_monetary_values_are_not_rounded_by_default_decimal_context():
    digits='1234567890'*8
    assert amount('($'+digits+'.12)')['value']=='-'+digits+'.12'
    result=TableArithmeticAgent().run(table([['A','$'+digits+'.12'],['B','-$'+digits+'.12'],['TOTAL','$0.00']]))
    assert result['status']=='arithmetic_consistent' and result['checks'][0]['computed_total']=='0.00'


def test_single_ambiguity_has_unconfirmed_total_constraint_candidate_not_repaired_value():
    source=table([['A','$98,000'],['B','$2.000'],['TOTAL','$100,000']])
    report=TableArithmeticAgent().run(source);candidate=report['checks'][0]['review_candidate']
    assert report['status']=='unavailable'
    assert candidate['candidate_amount']=='2000' and candidate['known_detail_sum']=='98000'
    assert candidate['confirmed'] is False and candidate['ocr_value_replaced'] is False
    assert source['body_cells'][1][1]['text']=='$2.000'


@pytest.mark.parametrize('texts',[
    [['A',''],['B',''],['TOTAL','$10']],
    [['A','$10'],['TOTAL','']],
    [['A','USD 10'],['B',''],['TOTAL','$10']]])
def test_multiple_unknowns_unknown_total_or_mixed_currency_have_no_candidate(texts):
    result=TableArithmeticAgent().run(table(texts))
    assert result['status']=='unavailable' and all('review_candidate' not in check for check in result['checks'])
