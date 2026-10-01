"""Actual PDF native geometry fixtures; no model, official answers or GT."""
import hashlib

import fitz
import pytest

from backend.visual_charts import extract_pdf_charts, query_chart_fact


def chart_pdf(*, years=(2021,2023,2025), values=((80,60,30),(70,55,45)), unit=None,
              missing_label=None, conflicting_label=False, missing_point=False,
              duplicate_legend=False, same_color=False, nonlinear_axis=False,
              dual_axis=False, rotation=0, caption_year=False, percent=False):
    doc=fitz.open()
    page=doc.new_page(width=380,height=330)
    page.draw_rect(fitz.Rect(80,80,300,240),color=(0,0,0))
    page.insert_text((135,55),'Rates of service activity',fontsize=10)
    if unit: page.insert_text((42,73),unit,fontsize=8)
    for tick in (0,50,100):
        y=240-tick*1.6
        displayed=40 if nonlinear_axis and tick==50 else tick
        page.insert_text((44,y+3.104),str(displayed)+('%' if percent else ''),fontsize=8)
        if dual_axis: page.insert_text((310,y+3.104),str(tick),fontsize=8)
    centers=[110+i*160/(len(years)-1) for i in range(len(years))]
    for x,year in zip(centers,years):
        word=str(year);width=fitz.get_text_length(word,fontsize=8)
        page.insert_text((x-width/2,255),word,fontsize=8)
    if caption_year: page.insert_text((110,273),'Source year: '+str(years[-1]),fontsize=8)
    colors=[(1,0,0),(0,0,1),(0,.6,0),(.6,0,.6)]
    names=['Alpha','Beta','Gamma','Delta']
    for series_no,series_values in enumerate(values):
        color=colors[0] if same_color else colors[series_no]
        legend_x=95+series_no*55
        page.insert_text((legend_x,85),names[series_no],fontsize=8)
        width=fitz.get_text_length(names[series_no],fontsize=8)
        page.draw_line((legend_x+width/2-7,94),(legend_x+width/2+7,94),color=color)
        if duplicate_legend and series_no==0:
            page.draw_line((legend_x+5,105),(legend_x+19,105),color=color)
        points=[fitz.Point(x,240-value*1.6) for x,value in zip(centers,series_values)]
        page.draw_polyline(points[:-1] if missing_point and series_no==0 else points,color=color)
        for point_no,(point,value) in enumerate(zip(points,series_values)):
            if missing_label==(series_no,point_no): continue
            text=str(value)+('%' if percent else '')
            page.insert_text((point.x-fitz.get_text_length(text,fontsize=8)/2,point.y-4),text,fontsize=8)
            if conflicting_label and series_no==0 and point_no==1:
                page.insert_text((point.x-fitz.get_text_length(text,fontsize=8)/2,point.y-4),text,fontsize=8)
    page.set_rotation(rotation)
    raw=doc.tobytes()
    doc.close()
    return raw


def extract(raw):
    return extract_pdf_charts(raw,page_no=1,expected_source_sha256=hashlib.sha256(raw).hexdigest())


def assert_rejected(report,reason):
    assert not report['charts'] and not report['facts']
    assert reason in {r['reason'] for r in report['rejected_charts']}


def test_native_series_year_labels_and_unknown_units_are_explicit():
    raw=chart_pdf()
    report=extract(raw)
    assert len(report['charts'])==1
    assert len(report['facts'])==6
    result=query_chart_fact('What was the number of Beta in 2023?',report)
    assert result['status']=='verified'
    assert result['fact']['raw_value']=='55'
    assert result['unit']=='unknown'
    assert result['value_kind']=='native_annotation_only'
    assert result['calculator_input_eligible'] is False
    assert result['scope']['title_context'][0]['text']=='Rates of service activity'
    assert len(result['scope']['legend_scope'])==2
    assert result['fact']['source_sha256']==hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('years,values',[
    ((1996,1998),((25,75),)),
    ((2031,2032,2033,2034,2035,2036),((90,80,70,60,50,40),)),
    ((2005,2008,2011),((85,75,65),(60,50,40),(35,25,15),(12,10,5))),
    ((2021,2023,2025),((50,50,50),)),
])
def test_variable_series_periods_and_flat_series(years,values):
    report=extract(chart_pdf(years=years,values=values))
    assert len(report['charts'])==1,report['rejected_charts']
    assert len(report['facts'])==len(years)*len(values)
    assert report['charts'][0]['years']==list(years)


def test_declared_axis_unit_and_numeric_labels_not_axis_rounded_values():
    report=extract(chart_pdf(unit='Count',values=((81.25,61.75,31.5),)))
    result=query_chart_fact('What is the value of Alpha in 2023?',report)
    assert result['status']=='verified'
    assert result['fact']['raw_value']=='61.75'
    assert result['unit']=='count'
    assert result['fact']['numeric_value']=='61.75'
    assert result['calculator_input_eligible'] is False


def test_percent_axis_preserved_without_scale_conversion():
    report=extract(chart_pdf(percent=True,values=((80,60,30),)))
    result=query_chart_fact('What is the value of Alpha in 2021?',report)
    assert result['status']=='verified'
    assert result['unit']=='percent'
    assert result['fact']['raw_value']=='80%'
    assert result['fact']['numeric_value']=='80'


def test_native_axis_corroboration_cannot_replace_missing_annotation():
    assert_rejected(extract(chart_pdf(missing_label=(0,1))),'point_annotation_missing_conflicting_or_ambiguous')


def test_rejection_retains_unverified_chart_scope_for_routing():
    report=extract(chart_pdf(missing_label=(0,1)))
    scope=report['rejected_charts'][0]['scope_text_candidates_unverified']
    assert any(item['text']=='Rates of service activity' for item in scope)
    assert any(item['text']=='Alpha' for item in scope)
    assert report['rejected_charts'][0]['calculator_input_eligible'] is False


def test_annotation_outside_plot_is_not_invented_from_axis():
    assert_rejected(extract(chart_pdf(values=((95,85,75),))),'point_annotation_missing_conflicting_or_ambiguous')


def test_conflicting_overlapping_labels_abstain():
    assert_rejected(extract(chart_pdf(conflicting_label=True)),'point_annotation_missing_conflicting_or_ambiguous')


def test_missing_period_point_abstains():
    assert_rejected(extract(chart_pdf(missing_point=True)),'series_period_points_incomplete_or_ambiguous')


def test_multiple_legend_matches_abstain():
    assert_rejected(extract(chart_pdf(duplicate_legend=True)),'legend_swatch_missing_or_ambiguous')


def test_duplicate_series_color_abstains():
    assert_rejected(extract(chart_pdf(same_color=True)),'series_style_not_unique_or_over_budget')


def test_nonlinear_axis_abstains():
    assert_rejected(extract(chart_pdf(nonlinear_axis=True)),'nonlinear_or_inconsistent_y_axis')


def test_dual_axis_abstains():
    assert_rejected(extract(chart_pdf(dual_axis=True)),'possible_dual_y_axis')


def test_duplicate_year_abstains():
    assert_rejected(extract(chart_pdf(years=(2021,2021,2025))),'year_slots_missing_ambiguous_or_unordered')


def test_caption_date_is_not_an_axis_year():
    report=extract(chart_pdf(caption_year=True))
    assert len(report['charts'])==1
    assert report['charts'][0]['years']==[2021,2023,2025]


def test_first_raw_numeric_threshold_uses_complete_displayed_periods():
    report=extract(chart_pdf(values=((80,60,30),)))
    result=query_chart_fact('What is the first year Alpha is below 70?',report)
    assert result['status']=='verified' and result['fact']['year']==2023
    assert result['threshold_domain']=='raw_annotation_numeric'
    assert result['comparison_scope']=='all_displayed_chart_periods_only'
    crossing=query_chart_fact('In which year did Alpha drop below 50?',report)
    assert crossing['status']=='verified' and crossing['fact']['year']==2025


def test_multiple_below_periods_need_explicit_first():
    report=extract(chart_pdf(values=((80,60,30),)))
    assert query_chart_fact('Which year is Alpha below 70?',report)['clarification_code']=='threshold_period_ambiguous_without_first'


def test_crossing_requires_previous_displayed_period():
    report=extract(chart_pdf(values=((20,60,30),)))
    assert query_chart_fact('In which year did Alpha drop below 50?',report)['clarification_code']=='crossing_predecessor_outside_chart_scope'


@pytest.mark.parametrize('question',[
    'What is Alpha in 2023 dollars?', 'What percentage is Alpha in 2023?',
    'What is Alpha in 2023 for the northern region?', 'What is Alpha and Beta in 2023?',
    'What is Alpha in 2022?', 'What is the first year Alpha is below $70?',
])
def test_unbound_unit_region_series_or_period_abstains(question):
    assert query_chart_fact(question,extract(chart_pdf()))['status']=='incomplete'


def test_rotation_maps_evidence_to_display_coordinates():
    report=extract(chart_pdf(rotation=90))
    assert len(report['charts'])==1
    assert report['facts'][0]['bbox_display_pt'][0]>=0
    assert report['facts'][0]['point_display_pt']!=[110,112]


def test_source_digest_and_page_are_checked():
    raw=chart_pdf()
    with pytest.raises(ValueError,match='sha256'):
        extract_pdf_charts(raw,page_no=1,expected_source_sha256='0'*64)
    with pytest.raises(ValueError,match='bounds'):
        extract_pdf_charts(raw,page_no=2)


def test_raster_and_unframed_pages_do_not_become_native_chart_facts():
    doc=fitz.open();page=doc.new_page();page.insert_text((50,50),'Chart picture without native paths')
    raw=doc.tobytes();doc.close()
    report=extract(raw)
    assert report['charts']==[] and report['facts']==[]
