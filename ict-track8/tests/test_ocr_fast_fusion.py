from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest
from backend.ocr_fast_fusion import (
    apply_fast_reviews, crop_node, decide_fast_review, plan_fast_reviews,
    retry_atomic_regions, vertical_groups,
    is_upright_table_column,
)


def request(text="81", kind="line", confidence=.7):
    return dict(text=text, kind=kind, confidence=confidence, members=[0], bbox=[0,0,30,10])


def readers(text, confidence=.99):
    return dict(text=text, confidence=confidence, sha256="crop", error=None)


def decide(req, text):
    return decide_fast_review(req, readers(text), readers(text), crop_sha256="crop")


@pytest.mark.parametrize("original,new", [("-20","20"),("2,298","2.298"),("2","121"),("1.25万元","1.35万元")])
def test_preserves_sign_format_digit_count_and_units(original,new):
    assert not decide(request(original),new)["accepted"]


def test_crop_identity_confidence_and_existing_correct_value():
    assert decide(request(),"61")["accepted"]
    assert not decide(request(confidence=.99),"61")["accepted"]
    assert not decide(request(),"81")["accepted"]
    bad=readers("61");bad["sha256"]="other"
    assert not decide_fast_review(request(),bad,readers("61"),crop_sha256="crop")["accepted"]
    assert not decide_fast_review(request(),readers("61",.85),readers("61"),crop_sha256="crop")["accepted"]


def test_missing_ink_requires_one_complete_high_confidence_number():
    assert decide(request("",kind="missing"),"1")["accepted"]
    assert not decide(request("",kind="missing"),"ITEM 1")["accepted"]
    assert not decide_fast_review(request("",kind="missing"),readers("1",.96),readers("1"),crop_sha256="crop")["accepted"]


def test_narrow_upright_digit_gains_actual_side_context_without_mutating_request():
    req=request("1");req.update(bbox=[20,0,25,20],polygon=[[20,0],[25,0],[25,20],[20,20]])
    before=deepcopy(req);out=crop_node(req)
    assert out["bbox"]==[12.5,0,32.5,20] and "polygon" not in out
    assert req==before
    req["kind"]="vertical"
    assert crop_node(req)==req


def test_budget_and_normal_table_column_not_joined_as_vertical_id():
    nodes=[dict(text=str(i),bbox=[50,i*40,60,i*40+15],confidence=.7) for i in range(12)]
    assert vertical_groups(nodes,page_size=(300,600))==[]
    plan=plan_fast_reviews(nodes,maximum=3)
    assert len(plan["requests"])==3 and len(plan["deferred"])==9


def test_apply_retains_source_geometry_and_input_and_ignores_rejected():
    first=[dict(text="81",bbox=[0,0,30,10],confidence=.7,words=[dict(text="81",bbox=[2,0,25,10])])]
    before=deepcopy(first);req=request()
    review=dict(request=req,rapid=readers("61"),paddle=readers("61"),decision=decide(req,"61"))
    out=apply_fast_reviews(first,[review])
    assert out[0]["text"]=="61" and out[0]["words"][0]["bbox"]==[2,0,25,10]
    assert first==before
    review["decision"]["accepted"]=False
    assert apply_fast_reviews(first,[review])==first


def test_atomic_retry_requires_observed_character_geometry():
    first=[dict(text="81 /3",bbox=[0,0,60,10],confidence=.7,
                words=[dict(text="81",bbox=[0,0,20,10]),dict(text="3",bbox=[50,0,60,10])])]
    req=request("81 /3")
    review=dict(request=req,rapid=readers("61 /3",.9),paddle=readers("61 /3",.9),decision=dict(accepted=False))
    retries=retry_atomic_regions(first,[review])
    assert len(retries)==1 and retries[0]["bbox"]==[0,0,20,10]
    first[0].pop("words")
    assert retry_atomic_regions(first,[review])==[]


def test_runner_rejects_missing_reordered_or_failed_reader_output():
    path=Path(__file__).resolve().parents[2]/"tools/ocr_fast_fusion_trial.py"
    import sys
    sys.path.insert(0,str(path.parent))
    spec=importlib.util.spec_from_file_location("fast_trial_test",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    req=[dict(id="1",sha256="crop")]
    for records in ([],[dict(id="2",sha256="crop")],[dict(id="1",sha256="crop",error="failed")]):
        with pytest.raises(RuntimeError):module.checked_records(dict(records=records),req)


def test_added_digit_location_is_actual_reviewed_crop_not_unpadded_ink():
    req=request("",kind="missing");req.update(members=[],bbox=[10,10,15,30])
    geometry=dict(source_polygon_px=[[1,9],[24,9],[24,31],[1,31]],source_sha256="original")
    review=dict(request=req,rapid=readers("1"),paddle=readers("1"),decision=decide(req,"1"),source_geometry=geometry)
    node=apply_fast_reviews([],[review])[0]
    assert node["bbox"]==[1,9,24,31] and node["ink_proposal_bbox"]==[10,10,15,30]
    assert node["source_geometry"]["source_sha256"]=="original"


def test_real_table_zero_column_is_not_merged_into_rotated_identifier():
    nodes=[]
    for y in (50,80,110):
        nodes.extend([dict(text="0",bbox=[190,y,208,y+25],confidence=.999),
                      dict(text="ITEM",bbox=[40,y,110,y+25],confidence=.99)])
    assert is_upright_table_column(nodes,[0,2,4])
    assert vertical_groups(nodes,page_size=(220,300))==[]


def test_high_confidence_line_cannot_be_rewritten_and_does_not_waste_review():
    n=dict(text="TOTAL 1 43.636",bbox=[0,0,200,25],confidence=.999)
    assert plan_fast_reviews([n])["requests"]==[]
    req=request(n["text"],confidence=.999)
    assert not decide(req,"TOTAL 1 42.636")["accepted"]
