"""Experimental table evidence fusion; upstream structures propose, pixels decide.

No reference answers, sample names or arithmetic targets enter decisions. Structure
agreement is NOT counted as OCR reader agreement, especially SLANet vs its ONNX port.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from decimal import Decimal
import re
import unicodedata

from .ocr_region_fusion import amount_key

TOKEN = re.compile(r"(?<![\w.,])(?P<literal>\(?[$¥￥]?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?)(?P<unit>万元|亿元|百万|million|billion|元|m|bn)?(?![\w.,])", re.I)


def quantities(text, *, context="", number_locale=None):
    """Keep mantissa, multiplier and unit separate. Unknown scale stays unknown."""
    text = unicodedata.normalize("NFKC", str(text)).replace("−", "-")
    result = []
    for match in TOKEN.finditer(text):
        key = amount_key(match["literal"])
        if key is None:
            continue
        unit = match["unit"] or ""
        scale = {"元": "1", "万元": "10000", "亿元": "100000000", "百万": "1000000",
                 "million": "1000000", "billion": "1000000000"}.get(unit.lower())
        reason = "explicit_unit" if scale else "unit_not_present"
        if unit.lower() in {"m", "bn"}:
            # Short symbols are ambiguous without explicit document context.
            pattern=r"\bmillions?\b|百万" if unit.lower()=="m" else r"\bbillions?\b|十亿"
            if re.search(pattern, context, re.I):
                scale = "1000000" if unit.lower() == "m" else "1000000000"
                reason = "short_unit_with_explicit_document_scale"
            else:
                reason = "ambiguous_short_unit_requires_context"
        if not unit:
            scale = "1"
        value=key[1]
        number_reason="default_decimal_dot_grouped_comma"
        # A document-level locale is an explicit input, never guessed from a
        # model majority or a convenient reference answer. Keep the literal.
        if number_locale=="id-ID":
            raw=match["literal"].lstrip("$¥￥")
            if re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+",raw):
                value=Decimal(raw.replace(".",""))
                number_reason="explicit_id_ID_thousands_dot"
        result.append({"text": match["literal"], "surface": match.group(), "span": list(match.span()),
                       "value": str(value), "currency_symbol": key[0], "unit_raw": unit,
                       "scale": scale, "scaled_value": str(value*Decimal(scale)) if scale else None,
                       "unit_reason": reason, "raw_line": text,"number_locale":number_locale,"number_reason":number_reason})
    return result


def area(box):
    return max(0., box[2]-box[0])*max(0., box[3]-box[1])


def overlap(a, b):
    return area([max(a[0],b[0]),max(a[1],b[1]),min(a[2],b[2]),min(a[3],b[3])])


def numeric_nodes(nodes, *, number_locale=None):
    """Shared normalization for ALL baselines. Never invent a character position."""
    output = []
    context = "\n".join(n.get("text", "") for n in nodes)
    for index, node in enumerate(nodes):
        text = node.get("text", "")
        for quantity in quantities(text, context=context, number_locale=number_locale):
            selected = []
            cursor = 0
            for word in node.get("words", []):
                start = text.find(word["text"], cursor)
                if start < 0:
                    continue
                end = start+len(word["text"])
                cursor = end
                if start < quantity["span"][1] and end > quantity["span"][0]:
                    selected.append(word)
            new = deepcopy(node)
            new.update(text=quantity["text"], quantity=quantity, parent_node_index=index,
                       location_kind="ocr_line_extent")
            if selected:
                boxes = [w["bbox"] for w in selected]
                new["bbox"] = [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
                x0,y0,x1,y1 = new["bbox"]
                new["polygon"] = [[x0,y0],[x1,y0],[x1,y1],[x0,y1]]
                new["location_kind"] = "ctc_character_alignment_not_independent_detector"
                new.pop("source_geometry", None)
            output.append(new)
    return output


def atomic_review_nodes(nodes):
    """Use observed CTC token extents to review quantities inside merged lines.

    Does not proportionally divide a line by string length. Unaligned tokens
    stay line-sized and are not eligible for this additional crop route.
    """
    output=numeric_nodes(nodes)
    for n in output:
        n["text"]=n["quantity"]["surface"]
        n.pop("words",None)
    return output


def plan_atomic_reviews(nodes, cells=(), *, maximum=12):
    tokens=bind_structures(atomic_review_nodes(nodes),cells)
    plan=plan_table_reviews(tokens,maximum=10000)
    selected={r["node_index"]:r for r in plan["requests"]}
    for i,n in enumerate(tokens):
        # Low confidence single-digit counts were excluded by the old table
        # planner. Read their actual aligned pixels; never infer from totals.
        if (i not in selected and re.fullmatch(r"\d",n["text"]) and n.get("confidence",1)<.94
                and n["location_kind"]=="ctc_character_alignment_not_independent_detector"):
            selected[i]={"node_index":i,"original_text":n["text"],"reasons":["uncertain_aligned_single_digit"],
                         "priority":2-float(n["confidence"]),"grouped_row":False,"decimal_row":False,"currency_column":False}
    eligible=[r for i,r in selected.items() if tokens[i]["location_kind"]=="ctc_character_alignment_not_independent_detector"]
    eligible.sort(key=lambda r:r["priority"],reverse=True)
    for r in eligible:
        r["original_numeric_confidence"]=float(tokens[r["node_index"]].get("confidence",1))
        r["observed_character_extent"]=True
    return {"nodes":tokens,"requests":eligible[:maximum],"deferred":eligible[maximum:],"maximum":maximum}


def structural_cells(record, paddle_record=None):
    output = []
    rapid = record.get("rapid_table", {})
    boxes = (rapid.get("cell_bboxes") or [[]])[0]
    logic = (rapid.get("logic_points") or [[]])[0]
    for index, bbox in enumerate(boxes):
        if len(bbox) == 8:
            xs,ys = bbox[::2],bbox[1::2]
            bbox = [min(xs),min(ys),max(xs),max(ys)]
        if len(bbox) == 4 and area(bbox)>0:
            output.append({"engine": "rapid-table", "family": "slanet-plus", "table": 0, "bbox": bbox,
                           "logic": logic[index] if index<len(logic) else None})
    for t, table in enumerate(record.get("img2table", {}).get("tables", [])):
        for cell in table["cells"]:
            output.append({"engine": "img2table", "family": "opencv_geometry", "table": t,
                           "bbox": cell["bbox"], "logic": [cell["row"],cell["row"],cell["column"],cell["column"]]})
    if paddle_record and not paddle_record.get("error"):
        for table in paddle_record.get("predictions", []):
            for index, bbox in enumerate(table.get("bbox", [])):
                if len(bbox) == 8:
                    bbox = [min(bbox[::2]),min(bbox[1::2]),max(bbox[::2]),max(bbox[1::2])]
                if len(bbox) == 4 and area(bbox)>0:
                    output.append({"engine": "paddle-slanet", "family": "slanet-plus", "table": 0,
                                   "bbox": bbox, "logic": None})
    return output


def bind_structures(nodes, cells):
    result = []
    for index, node in enumerate(nodes):
        supports = []
        bbox = node["bbox"]
        for cell in cells:
            cb = cell["bbox"]
            # Whole-page and multi-row boxes cannot corroborate a specific cell.
            if overlap(bbox,cb)/max(1.,area(bbox)) >= .6 and cb[3]-cb[1] <= 3.5*(bbox[3]-bbox[1]):
                supports.append(cell)
        families = sorted({s["family"] for s in supports})
        agreement = any(a["family"]!=b["family"] and overlap(a["bbox"],b["bbox"])/max(1.,min(area(a["bbox"]),area(b["bbox"])))>=.6
                        for a in supports for b in supports)
        new = deepcopy(node)
        new.update(structure_support=supports, structure_families=families,
                   structure_status="cross_family_cell_support" if agreement else "conflicting_structure_candidates" if len(families)>1 else "single_family_candidate" if families else "unresolved_structure")
        result.append(new)
    return result


def table_context(quantity_nodes, text_nodes):
    """Observed same-row labels and model row/column indices, never business truth."""
    output=deepcopy(quantity_nodes)
    for q in output:
        bbox=q["bbox"]
        cy=(bbox[1]+bbox[3])/2
        labels=[]
        for index,n in enumerate(text_nodes):
            nb=n["bbox"]
            if nb[2]>bbox[0]+3 or abs((nb[1]+nb[3])/2-cy)>max(nb[3]-nb[1],bbox[3]-bbox[1])*.65:
                continue
            if not n.get("text") or len(n["text"])>100 or quantities(n["text"]):continue
            labels.append({"text":n["text"],"bbox":nb,"text_node_index":index,"basis":"observed_same_row_left_neighbour"})
        quantity=q["quantity"]
        if quantity["span"][0]>0:
            prefix=quantity["raw_line"][:quantity["span"][0]].strip()
            if prefix and re.search(r"[A-Za-z\u4e00-\u9fff]",prefix):
                labels.append({"text":prefix,"text_node_index":q["parent_node_index"],"basis":"same_ocr_line_prefix","bbox":None})
        q["row_label_candidates"]=sorted(labels,key=lambda x: (x["bbox"] or bbox)[0],reverse=True)[:3]
        supports=q.get("structure_support",[])
        usable=[s for s in supports if s.get("logic") is not None]
        chosen=min(usable,key=lambda s:area(s["bbox"])) if usable else None
        q["table_cell_candidate"]={"engine":chosen["engine"],"table":chosen["table"],"logic":chosen["logic"],"bbox":chosen["bbox"],
                                   "status":"upstream_structure_candidate_not_verified_truth"} if chosen else None
    return output


def row_peers(nodes, index):
    bbox = nodes[index]["bbox"]
    cy=(bbox[1]+bbox[3])/2
    height=bbox[3]-bbox[1]
    return [n for j,n in enumerate(nodes) if j != index and abs((n["bbox"][1]+n["bbox"][3])/2-cy) <= max(height,n["bbox"][3]-n["bbox"][1])*.7]


def plan_table_reviews(nodes, *, maximum=18):
    requests=[]
    for index,node in enumerate(nodes):
        text=node.get("text", "")
        if not re.search(r"\d", text):
            continue
        # Long sentences and dates are not table numeric cells.
        if len(text)>48 or re.search(r"\d{4}[-/]\d{1,2}[-/]",text):
            continue
        qs=quantities(text)
        if not qs and len(re.sub(r"[^\d]", "", text))<2:
            continue
        peers=row_peers(nodes,index)
        if re.fullmatch(r"\d",text.strip()):continue
        if re.fullmatch(r"(?:19|20)\d{2}",text.strip()) and any(re.fullmatch(r"(?:19|20)\d{2}",p.get("text","").strip()) for p in peers):continue
        if re.search(r"[A-Za-z]{3,}",text) and not re.search(r"[$¥￥]",text):continue
        if re.search(r"[A-Za-z]{3,}",text) and qs and all(q["span"][0]>0 for q in qs):continue
        grouped=sum(bool(re.search(r"\d,\d{3}(?:\D|$)",n.get("text", ""))) for n in peers)
        decimal_peers=sum(bool(re.search(r"\d\.\d{1,2}(?:\D|$)",n.get("text", ""))) for n in peers)
        bbox=node["bbox"]
        currency_column=sum(bool(re.match(r"[$¥￥]",n.get("text", ""))) for j,n in enumerate(nodes) if j!=index
                            and abs(n["bbox"][2]-bbox[2])<max(8.,(bbox[2]-bbox[0])*.35))>=3
        reasons=[]
        if float(node.get("confidence",1))<.94: reasons.append("low_numeric_confidence_including_plain_integer")
        if re.search(r"\d[A-Za-z]\d",text): reasons.append("letters_inside_numeric_cell")
        if re.search(r"\d\.\d{3}(?:\D|$)",text) and grouped>=2: reasons.append("decimal_vs_grouped_row_conflict")
        if not qs and re.search(r"\d",text): reasons.append("unparsed_numeric_cell")
        if len(qs)==1 and qs[0]["span"] != [0,len(text)] and len(text)<24: reasons.append("extra_symbols_or_merged_numeric_region")
        if re.fullmatch(r"\d{3,}",text.strip()) and decimal_peers>=2: reasons.append("missing_decimal_in_decimal_row")
        if currency_column and re.fullmatch(r"\d{1,3}[. ]\d{2}",text.strip()):reasons.append("currency_symbol_missing_in_currency_column")
        if reasons:
            structural_priority=.25 if node.get("structure_status")=="cross_family_cell_support" else 0.
            requests.append({"node_index":index,"original_text":text,"reasons":reasons,"grouped_row":grouped>=2,"decimal_row":decimal_peers>=2,
                             "currency_column":currency_column,"structure_status":node.get("structure_status"),
                             "priority":len(reasons)+1-float(node.get("confidence",1))+structural_priority
                                 +(2 if any(r in reasons for r in ("decimal_vs_grouped_row_conflict","missing_decimal_in_decimal_row","currency_symbol_missing_in_currency_column")) else 0)})
    requests.sort(key=lambda r:r["priority"],reverse=True)
    return {"requests":requests[:maximum],"deferred":requests[maximum:],"maximum":maximum}


def reconcile_table_review(original_text, observations, *, crop_sha256, context):
    base={"status":"needs_review","original_text":original_text,"selected_text":original_text,"observations":observations,
          "crop_sha256":crop_sha256,"reason":"no_unique_crop_agreement"}
    grouped=defaultdict(dict)
    for obs in observations:
        qs=quantities(obs.get("text", ""))
        if obs.get("crop_sha256")!=crop_sha256 or obs.get("error") or len(qs)!=1:
            continue
        q=qs[0]
        # Readers must see a single quantity, not a number extracted from prose.
        if len(obs["text"].strip())-len(q["surface"])>2:
            continue
        minimum=.5 if obs["engine"]=="tesseract" else .85
        if obs.get("confidence",0)<minimum: continue
        key=amount_key(q["text"])
        grouped[key][obs["engine"]]=obs
    candidates=[(k,v) for k,v in grouped.items() if len(v)>=2]
    if len(candidates)!=1:return base
    key,readers=candidates[0]
    if any(o.get("confidence",0)>=.95 for k,v in grouped.items() if k!=key for o in v.values()):
        return dict(base,reason="confident_crop_reader_conflict")
    old=amount_key(original_text)
    preferred=readers.get("paddle") or next(iter(readers.values()))
    proposed=quantities(preferred["text"])[0]["text"]
    if old and old!=key:
        # Accept a changed valid number only in a matching observed row format.
        grouped_format=bool(re.search(r"\d,\d{3}",proposed))
        decimal_format=bool(re.search(r"\d\.\d{1,2}(?:\D|$)",proposed))
        restored_currency=bool(context.get("currency_column") and not old[0] and key[0])
        # Permit a low-confidence digit substitution on a real aligned crop,
        # with two high-confidence neural readers and unchanged punctuation.
        # This cannot turn (2,298) into (2.298), alter sign, or insert digits.
        same_format=re.sub(r"\d","#",original_text)==re.sub(r"\d","#",proposed)
        neural_support=all(e in readers and readers[e].get("confidence",0)>=.92 for e in ("rapid","paddle"))
        uncertain_digit_change=bool(context.get("observed_character_extent") and context.get("original_numeric_confidence",1)<.9
                                   and same_format and neural_support)
        if not ((context.get("grouped_row") and grouped_format) or (context.get("decimal_row") and decimal_format) or restored_currency or uncertain_digit_change):
            return dict(base,reason="valid_number_change_without_matching_row_context",proposed_text=proposed)
    # A unit suffix is not dropped by recognition-only replacement.
    original_q=quantities(original_text)
    if any(q["unit_raw"] for q in original_q):
        return dict(base,reason="unit_bearing_literal_preserved_for_structured_extraction")
    return dict(base,status="confirmed" if old==key else "corrected",selected_text=proposed,
                reason="same_crop_reader_agreement_and_observed_row_format",supporting_engines=sorted(readers),
                reader_independence="different_reader_pipelines_not_statistically_independent")


def expert_recovery_gate(nodes):
    """Detect sparse uncertainty and vertical character fragmentation locally."""
    quality=page_quality(nodes)
    digits=[n for n in nodes if re.fullmatch(r"\d",n.get("text", ""))]
    chains=[]
    for anchor in digits:
        b=anchor["bbox"];cx=(b[0]+b[2])/2;w=max(3,b[2]-b[0]);h=max(3,b[3]-b[1])
        aligned=sorted([n for n in digits if abs((n["bbox"][0]+n["bbox"][2])/2-cx)<=w*.8],key=lambda n:n["bbox"][1])
        chain=[]
        for n in aligned:
            if chain and n["bbox"][1]-chain[-1]["bbox"][3]>h*2.5:
                if len(chain)>=4:chains.append(chain)
                chain=[]
            chain.append(n)
        if len(chain)>=4:chains.append(chain)
    sparse=quality["numeric_regions"]>=4 and quality["uncertain_fraction"]>=.15
    return {"eligible":bool(sparse or chains),"sparse_uncertainty":sparse,
            "vertical_fragmentation":bool(chains),"quality":quality}


def plan_expert_recovery(first, expert, *, source_sha256, expert_source_sha256, maximum=8):
    """Expert detections propose new or fragmented regions; crops must confirm.

    Existing valid quantities are not overwritten by a full-page vote. This
    returns proposals only, not extra answer candidates to inflate recall.
    """
    gate=expert_recovery_gate(first)
    if not gate["eligible"] or source_sha256!=expert_source_sha256:
        return {"gate":gate,"proposals":[]}
    quantities_first=numeric_nodes(first)
    proposals=[]
    for n in expert:
        text=n.get("text","").strip();qs=quantities(text)
        if len(qs)!=1 or qs[0]["span"]!=[0,len(text)] or n.get("confidence",0)<.90:continue
        b=n["bbox"]
        nearby=[(i,q) for i,q in enumerate(quantities_first) if overlap(q["bbox"],b)/max(1.,area(q["bbox"]))>=.5]
        fragments=[i for i,q in nearby if re.fullmatch(r"\d",q["text"]) and len(q["quantity"]["raw_line"].strip())==1]
        fragmented=len(fragments)>=4 and len(re.sub(r"\D","",text))>=4 and b[3]-b[1]>2*(b[2]-b[0])
        if fragmented and len(fragments)!=len(nearby):continue
        if nearby and not fragmented:continue
        # A high-confidence nonnumeric first read in the same region is an
        # explicit conflict, not evidence of a missing field.
        conflicting=[f for f in first if not quantities(f.get("text","")) and f.get("confidence",0)>=.94
                     and overlap(f["bbox"],b)/max(1.,min(area(f["bbox"]),area(b)))>=.5]
        if conflicting:continue
        proposals.append({"node":deepcopy(n),"replace_token_indices":fragments if fragmented else [],
                          "reason":"vertical_character_fragments" if fragmented else "uncovered_numeric_detection"})
    return {"gate":gate,"proposals":proposals[:maximum],"deferred":max(0,len(proposals)-maximum)}


def page_quality(nodes):
    numeric=[n for n in nodes if re.search(r"\d",n.get("text","")) and len(n.get("text",""))<35]
    uncertain=sum(float(n.get("confidence",0))<.94 for n in numeric)
    reliable=sum(len(quantities(n["text"]))==1 and float(n.get("confidence",0))>=.94 for n in numeric)
    return {"numeric_regions":len(numeric),"uncertain_regions":uncertain,
            "uncertain_fraction":uncertain/max(1,len(numeric)),"reliable_quantity_regions":reliable,
            "confidence_is_not_calibrated_accuracy":True}


def select_page_reader(first_nodes, local_nodes, expert_nodes, *, source_sha256, expert_source_sha256):
    """Quality-gated expensive expert, never choose using reference accuracy.

    Cross-engine confidence is only a routing heuristic; retain both observations.
    Thresholds are experimental and explicitly evaluated as a development policy.
    """
    first=page_quality(first_nodes)
    expert=page_quality(expert_nodes)
    eligible=first["numeric_regions"]>=20 and first["uncertain_fraction"]>=.15
    gain=expert["reliable_quantity_regions"]-first["reliable_quantity_regions"]
    selected=bool(eligible and source_sha256==expert_source_sha256 and expert_nodes
                  and gain>=max(3,first["reliable_quantity_regions"]*.08)
                  and expert["numeric_regions"]>=first["numeric_regions"]*.85
                  and expert["uncertain_fraction"]<=first["uncertain_fraction"]+.02)
    return {"selected":"paddle_full" if selected else "local_fusion","nodes":deepcopy(expert_nodes if selected else local_nodes),
            "reason":"dense_uncertain_page_with_more_reliable_expert_regions" if selected else "expert_not_eligible_or_no_observed_quality_gain",
            "expert_requested_by_quality":eligible,"first_quality":first,"expert_quality":expert,
            "policy_scope":"experimental_confidence_routing_not_accuracy_or_truth_certificate"}
