from copy import deepcopy
import pytest
from backend.ocr import _regional_numeric_table


def observations():
    valid=[]
    for i,y in enumerate([0,30,60]):
        valid.extend([{'text':f'Program {i}','confidence':.95,'bbox':[10,y,100,y+16]},
            {'text':f'${i+1}.00','confidence':.95,'bbox':[170,y,210,y+16]}])
    valid.extend([{'text':'TOTAL FSA FUNDS BUDGETED:','confidence':.95,'bbox':[10,200,153,216]},
        {'text':'$29.03m','confidence':.95,'bbox':[160,200,210,222]}])
    return valid


def test_close_currency_pair_is_preserved_separately_without_inventing_table_or_total():
    valid=observations();before=deepcopy(valid)
    layout=_regional_numeric_table(valid)
    assert valid==before and layout['row_count']==3
    paired=[region for region in layout['sparse_observations'] if region['scope']=='close_same_line_label_amount_pair']
    assert len(paired)==1
    pair=paired[0]
    assert pair['is_table'] is False and pair['cell_values_verified'] is False
    assert pair['observed_gap_px']==7 and pair['numeric_column_observation_count']==4
    assert pair['rows'][0][1]['text']=='$29.03m'


@pytest.mark.parametrize('condition',['overlap','misaligned','low_confidence','ambiguous','different_numeric_column'])
def test_conflicting_close_boxes_never_become_an_observed_pair(condition):
    valid=observations()
    if condition=='overlap':valid[-2]['bbox'][2]=165
    elif condition=='misaligned':valid[-2]['bbox'][1:4:2]=[210,226]
    elif condition=='low_confidence':valid[-2]['confidence']=.5
    elif condition=='ambiguous':valid.append({**deepcopy(valid[-2]),'text':'Competing label'})
    elif condition=='different_numeric_column':valid[-1]['bbox']=[300,200,350,222]
    layout=_regional_numeric_table(valid)
    assert not [region for region in layout.get('sparse_observations',[]) if region['scope']=='close_same_line_label_amount_pair']
