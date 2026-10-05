from copy import deepcopy
from decimal import Decimal

from backend.ocr_table_fusion import (bind_structures, numeric_nodes, plan_table_reviews,
                                     quantities, reconcile_table_review, table_context, select_page_reader)


def n(text, x=100, y=100, confidence=.99):
    return {"text":text,"bbox":[x,y,x+90,y+20],"confidence":confidence}


def obs(text, engines=("rapid","paddle"), sha="crop"):
    return [{"engine":e,"text":text,"confidence":.99,"crop_sha256":sha} for e in engines]


def test_money_unit_cannot_silently_become_dollars_or_metres():
    q=quantities("TOTAL USAID $43.54m")[0]
    assert q["value"]=="43.54" and q["unit_raw"]=="m"
    assert q["scaled_value"] is None
    assert quantities("$43.54m",context="Funding in millions")[0]["scaled_value"]=="43540000.00"
    assert quantities("采购费用 ￥1.25万元")[0]["scaled_value"]=="12500.00"
    assert quantities("$43.54m",context="Other figures in billions")[0]["scaled_value"] is None


def test_quantity_parser_never_joins_neighbouring_numbers_or_partial_grouping():
    assert [Decimal(q["value"]) for q in quantities("6.12 5")]==[Decimal("6.12"),Decimal("5")]
    assert quantities("1,00")==[]
    assert quantities("(2,298)")[0]["value"]=="-2298"
    assert quantities("100L1")==[]


def test_character_alignment_is_used_without_fabricating_substring_positions():
    node=n("TOTAL $5.60")
    node["words"]=[{"text":"TOTAL","bbox":[0,100,50,120]}, {"text":"$5.60","bbox":[65,100,100,120]}]
    q=numeric_nodes([node])[0]
    assert q["bbox"]==[65,100,100,120]
    assert q["location_kind"]=="ctc_character_alignment_not_independent_detector"
    q=numeric_nodes([n("TOTAL $5.60")])[0]
    assert q["location_kind"]=="ocr_line_extent"


def test_low_score_plain_integer_and_high_score_format_conflict_both_route():
    nodes=[n("105589",confidence=.68),n("30.691",x=250),n("10,976",x=400),n("40,739",x=550)]
    plan=plan_table_reviews(nodes)
    assert {r["node_index"] for r in plan["requests"]}=={0,1}
    assert "decimal_vs_grouped_row_conflict" in next(r for r in plan["requests"] if r["node_index"]==1)["reasons"]


def test_true_decimal_row_is_not_changed_into_thousands():
    nodes=[n("12.37"),n("6.12",x=250),n("27.72",x=400),n("37.02",x=550)]
    assert plan_table_reviews(nodes)["requests"]==[]
    decision=reconcile_table_review("12.37",obs("1,237"),crop_sha256="crop",context={"decimal_row":True})
    assert decision["status"]=="needs_review"


def test_correlated_structure_models_cannot_supply_two_family_support():
    cells=[{"engine":"rapid-table","family":"slanet-plus","bbox":[90,90,200,130]},
           {"engine":"paddle-slanet","family":"slanet-plus","bbox":[90,90,200,130]}]
    nodes=[n("100.00")];before=deepcopy(nodes)
    assert bind_structures(nodes,cells)[0]["structure_status"]=="single_family_candidate"
    assert nodes==before
    cells.append({"engine":"img2table","family":"opencv_geometry","bbox":[90,90,200,130]})
    assert bind_structures(nodes,cells)[0]["structure_status"]=="cross_family_cell_support"


def test_whole_page_structure_cannot_validate_a_numeric_cell():
    cells=[{"engine":"rapid-table","family":"slanet-plus","bbox":[0,0,1000,1000]}]
    assert bind_structures([n("100.00")],cells)[0]["structure_status"]=="unresolved_structure"


def test_crop_identity_and_reader_deduplication_are_mandatory():
    assert reconcile_table_review("S100,000",obs("$100,000",sha="other"),crop_sha256="crop",context={})["status"]=="needs_review"
    assert reconcile_table_review("S100,000",obs("$100,000",engines=("paddle","paddle")),crop_sha256="crop",context={})["status"]=="needs_review"


def test_legal_numeric_changes_require_observed_row_format_and_pixel_readers():
    decision=reconcile_table_review("30.691",obs("30,691"),crop_sha256="crop",context={"grouped_row":True})
    assert decision["status"]=="corrected"
    assert reconcile_table_review("30.691",obs("30,691"),crop_sha256="crop",context={})["status"]=="needs_review"
    assert reconcile_table_review("(2,298)",obs("(2.298)",engines=("rapid","paddle","tesseract")),crop_sha256="crop",context={"grouped_row":True})["status"]=="needs_review"


def test_unit_bearing_literal_is_not_replaced_with_unitless_reader_output():
    assert reconcile_table_review("$43.54m",obs("$43.54"),crop_sha256="crop",context={})["status"]=="needs_review"


def test_same_row_labels_remain_traceable_candidates_not_semantic_truth():
    texts=[n("项目甲",x=0),n("项目乙",x=0,y=150),n("￥100.00",x=200)]
    output=table_context(numeric_nodes(texts),texts)
    assert [l["text"] for l in output[0]["row_label_candidates"]]==["项目甲"]
    assert output[0]["row_label_candidates"][0]["text_node_index"]==0
    assert output[0]["table_cell_candidate"] is None


def test_dense_low_quality_page_can_select_expert_without_reference_answers():
    first=[n(str(1000+i),y=30*i,confidence=.7 if i<8 else .99) for i in range(30)]
    expert=[n(str(2000+i),y=30*i,confidence=.99) for i in range(30)]
    route=select_page_reader(first,first,expert,source_sha256="a",expert_source_sha256="a")
    assert route["selected"]=="paddle_full"
    assert select_page_reader(first,first,expert,source_sha256="a",expert_source_sha256="b")["selected"]=="local_fusion"


def test_expensive_expert_cannot_override_a_clean_page_or_a_worse_expert():
    clean=[n(str(i),y=30*i) for i in range(30)]
    bad=[n(str(i),y=30*i,confidence=.6) for i in range(30)]
    assert select_page_reader(clean,clean,clean,source_sha256="a",expert_source_sha256="a")["selected"]=="local_fusion"
    assert select_page_reader(bad,bad,bad,source_sha256="a",expert_source_sha256="a")["selected"]=="local_fusion"
