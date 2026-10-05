from decimal import Decimal
from backend.ocr_table_fusion import quantities, atomic_review_nodes, plan_atomic_reviews, reconcile_table_review, plan_expert_recovery


def test_locale_is_explicit_and_literal_is_retained():
    assert quantities("60.000")[0]["value"]=="60.000"
    q=quantities("60.000",number_locale="id-ID")[0]
    assert q["value"]=="60000" and q["text"]=="60.000"
    assert q["number_reason"]=="explicit_id_ID_thousands_dot"
    assert quantities("2.00",number_locale="id-ID")[0]["value"]=="2.00"
    assert quantities("12.37",number_locale="id-ID")[0]["value"]=="12.37"
    assert quantities("-60.000",number_locale="id-ID")[0]["value"]=="-60000"


def test_locale_scale_is_not_used_to_alter_observed_characters():
    q=quantities("￥1.25万元",number_locale="id-ID")[0]
    assert Decimal(q["scaled_value"])==12500
    assert quantities("6.12 5",number_locale="id-ID")[1]["value"]=="5"


def test_atomic_review_uses_real_alignment_and_preserves_source():
    line={"text":"TOTAL 5.60 2","bbox":[0,0,200,20],"confidence":.7,
          "words":[{"text":"TOTAL","bbox":[0,0,50,20]}, {"text":"5.60","bbox":[70,0,110,20]},
                   {"text":"2","bbox":[150,0,160,20]}]}
    plan=plan_atomic_reviews([line])
    assert len(plan["requests"])==2
    assert plan["nodes"][0]["bbox"]==[70,0,110,20]
    assert plan["nodes"][1]["bbox"]==[150,0,160,20]
    assert plan["nodes"][0]["parent_node_index"]==0
    assert line["text"]=="TOTAL 5.60 2"


def test_atomic_review_does_not_invent_character_boxes_or_exceed_budget():
    line={"text":"TOTAL 5.60 2","bbox":[0,0,200,20],"confidence":.3}
    assert plan_atomic_reviews([line])["requests"]==[]
    lines=[dict(line,text="10.00",words=[{"text":"10.00","bbox":[0,0,20,20]}]) for _ in range(20)]
    plan=plan_atomic_reviews(lines,maximum=3)
    assert len(plan["requests"])==3 and len(plan["deferred"])==17


def test_low_confidence_digit_repair_does_not_relax_separator_or_sign_guard():
    def obs(text):
        return [{"engine":e,"text":text,"confidence":.98,"crop_sha256":"s"} for e in ("rapid","paddle")]
    context={"observed_character_extent":True,"original_numeric_confidence":.8}
    assert reconcile_table_review("81",obs("61"),crop_sha256="s",context=context)["status"]=="corrected"
    for original,proposed in [("(2,298)","(2.298)"),("2","121"),("-20","20")]:
        assert reconcile_table_review(original,obs(proposed),crop_sha256="s",context=context)["status"]=="needs_review"
    assert reconcile_table_review("81",obs("61"),crop_sha256="s",context={**context,"original_numeric_confidence":.99})["status"]=="needs_review"


def test_expert_proposals_require_source_identity_and_do_not_overwrite_existing_number():
    first=[{"text":str(i),"bbox":[100,20*i,110,20*i+10],"confidence":.99} for i in range(6)]
    expert=[{"text":"012345","bbox":[98,0,112,110],"confidence":.99}]
    result=plan_expert_recovery(first,expert,source_sha256="a",expert_source_sha256="a")
    assert result["gate"]["vertical_fragmentation"] and len(result["proposals"])==1
    assert len(result["proposals"][0]["replace_token_indices"])==6
    assert plan_expert_recovery(first,expert,source_sha256="a",expert_source_sha256="b")["proposals"]==[]
    first.append({"text":"999999","bbox":[98,0,112,110],"confidence":.99})
    # A normal, valid whole number inside a fragmented proposal is a conflict.
    assert plan_expert_recovery(first,expert,source_sha256="a",expert_source_sha256="a")["proposals"]==[]
