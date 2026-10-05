from copy import deepcopy

import pytest
from PIL import Image,ImageDraw
from backend.ocr_gap_fusion import plan_detection_gaps,expert_gap_requests,merge_confirmed_gap_nodes,decide_detected_number,field_gap_route
from backend.ocr_table_fusion import area


def node(text="10",box=(10,10,30,25),confidence=.99):
    return dict(text=text,bbox=list(box),confidence=confidence)


def test_blank_page_never_calls_expert():
    plan=plan_detection_gaps(Image.new("RGB",(400,500),"white"),[])
    assert plan["regions"]==[] and plan["area_fraction"]==0


def test_coverage_deficit_budget_is_based_on_pixels_not_values():
    image=Image.new("RGB",(400,400),"white");draw=ImageDraw.Draw(image)
    for y in (50,90,130,170):
        draw.text((20,y),"ITEM",fill="black")
        draw.text((250,y),"100.00",fill=(160,160,160))
    nodes=[node("ITEM",(18,y-2,70,y+13)) for y in (50,90,130,170)]
    plan=plan_detection_gaps(image,nodes,maximum=1,area_budget=.25,automatic=False)
    assert len(plan["regions"])<=1 and sum(area(r["bbox"]) for r in plan["regions"])<=400*400*.25
    changed=[dict(n,text="entirely different reference-free words") for n in nodes]
    changed_plan=plan_detection_gaps(image,changed,maximum=1,area_budget=.25,automatic=False)
    assert changed_plan["regions"]==plan["regions"] and changed_plan["area_fraction"]==plan["area_fraction"]
    assert plan_detection_gaps(image,nodes,maximum=0)["regions"]==[]
    with pytest.raises(ValueError):plan_detection_gaps(image,nodes,area_budget=1.1)


def test_expert_candidates_skip_existing_duplicate_and_low_confidence_boxes():
    existing=[node()];detections=[node(box=(9,9,31,26)),node(box=(70,70,90,90)),
        node(box=(71,71,91,91)),node(box=(100,100,120,120),confidence=.3)]
    out=expert_gap_requests(detections,existing)
    assert len(out)==1 and out[0]["bbox"]==[70,70,90,90]
    assert out[0]["members"]==[] and out[0]["text"]==""


def test_new_evidence_cannot_overwrite_trusted_field_or_duplicate_it():
    first=[node("81")];before=deepcopy(first)
    out=merge_confirmed_gap_nodes(first,[node("61"),node("1",(70,70,90,90)),node("LABEL",(100,100,150,120)),node("1",(71,71,91,91))])
    assert [n["text"] for n in out]==["81","1"] and first==before


def test_expert_detector_is_location_support_not_a_vote_or_permission_to_guess():
    req=dict(detector=dict(confidence=.8));reader=dict(text="43.636",confidence=.95,sha256="crop",error=None)
    assert decide_detected_number(req,reader,reader,crop_sha256="crop")["accepted"]
    for other in (dict(reader,text="43,636"),dict(reader,text="1 43.636"),dict(reader,sha256="other"),dict(reader,confidence=.91)):
        assert not decide_detected_number(req,reader,other,crop_sha256="crop")["accepted"]
    assert not decide_detected_number(dict(detector=dict(confidence=.4)),reader,reader,crop_sha256="crop")["accepted"]


def test_amount_label_gap_routes_without_answers_and_known_values_skip_expert():
    nodes=[node("TOTAL",(10,50,90,70)),node("TAX 10.00 %",(10,90,120,110))]
    assert field_gap_route(nodes)["triggered"]
    nodes += [node("60000",(200,50,270,70)),node("6000",(200,90,270,110))]
    assert not field_gap_route(nodes)["triggered"]
    image=Image.new("RGB",(400,400),"white")
    assert plan_detection_gaps(image,nodes)["regions"]==[]
