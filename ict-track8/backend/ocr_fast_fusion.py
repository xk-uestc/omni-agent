"""Rapid detection plus bounded line recognition; no full-page expert required."""
from copy import deepcopy
import re
from .ocr_table_fusion import numeric_nodes, quantities, overlap, area


def is_upright_table_column(nodes, members):
    """Horizontal row companions distinguish a column of zeros from a rotated ID."""
    rows=0
    for i in members:
        n=nodes[i];b=n["bbox"];h=b[3]-b[1];w=b[2]-b[0]
        if w>h*1.05 or not re.fullmatch(r"\d{1,3}",n.get("text","").strip()):continue
        for j,other in enumerate(nodes):
            if j in members or len(other.get("text","").strip())<2:continue
            a=other["bbox"]
            y_overlap=max(0,min(b[3],a[3])-max(b[1],a[1]))
            x_gap=max(a[0]-b[2],b[0]-a[2])
            if y_overlap>.4*min(h,a[3]-a[1]) and x_gap>1.5*h:
                rows+=1;break
    return rows>=2


def vertical_groups(nodes, page_size=None):
    eligible=[i for i,n in enumerate(nodes) if re.fullmatch(r"\d{1,12}",n.get("text","").strip())]
    unseen=set(eligible);groups=[]
    while unseen:
        seed=unseen.pop();component={seed};todo=[seed]
        while todo:
            i=todo.pop();a=nodes[i]["bbox"];aw=a[2]-a[0]
            for j in list(unseen):
                b=nodes[j]["bbox"];bw=b[2]-b[0]
                gap=max(0,max(a[1],b[1])-min(a[3],b[3]))
                edge=bool(page_size and ((a[0]+a[2])/2<page_size[0]*.15 or (a[0]+a[2])/2>page_size[0]*.85))
                if abs((a[0]+a[2]-b[0]-b[2])/2)<=max(aw,bw)*.5 and gap<=min(aw,bw)*(1.8 if edge else .65):
                    unseen.remove(j);component.add(j);todo.append(j)
        if len(component)<2:continue
        if is_upright_table_column(nodes,component):continue
        members=[nodes[i] for i in component]
        boxes=[n["bbox"] for n in members]
        box=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
        broad=sum(b[2]-b[0]>=.9*(b[3]-b[1]) for n,b in zip(members,boxes) if len(n["text"].strip())<=2)
        if box[3]-box[1]>2.5*(box[2]-box[0]) and broad>=2 and sum(len(n["text"]) for n in members)>=5:
            groups.append({"kind":"vertical","members":sorted(component),"bbox":box,"text":"","confidence":0.,"priority":5.})
    return groups


def plan_fast_reviews(nodes, maximum=24, page_size=None):
    groups=vertical_groups(nodes,page_size)
    consumed={i for g in groups for i in g["members"]}
    requests=list(groups)
    for i,n in enumerate(nodes):
        text=n.get("text","")
        if i in consumed or len(text)>40 or not re.search(r"\d",text):continue
        if re.search(r"(?i)phone|fax|\bpage\b",text):continue
        if len(re.findall(r"[A-Za-z]",text))>14:continue
        # Preserve ordinary clean standalone numbers. Review merged/uncertain
        # lines in their full observed box so a slash/table rule is not cropped
        # into an extra digit beside an isolated character.
        qs=quantities(text)
        # The decision gate never rewrites any >=.985 line, so re-reading
        # such lines cannot improve output. Avoid that provably wasted work.
        clean=n.get("confidence",0)>=.985
        if clean:continue
        if re.search(r"\d{1,4}[-/]\d{1,2}[-/]\d{1,4}",text):continue
        if len(text)>40 and len(qs)>2:continue
        requests.append({"kind":"line","members":[i],"bbox":n["bbox"],"polygon":n.get("polygon"),
                         "text":text,"confidence":n.get("confidence",0),
                         "priority":1-n.get("confidence",0)+(1 if len(qs)>1 else 0)})
    requests.sort(key=lambda r:r["priority"],reverse=True)
    return {"requests":requests[:maximum],"deferred":requests[maximum:],"maximum":maximum}


def refine_vertical_regions(image, nodes, plan):
    """Locate edge-column ink geometrically, without a second neural detector."""
    import cv2
    import numpy as np
    gray=np.asarray(image.convert("L"));height,width=gray.shape
    _,ink=cv2.threshold(gray,0,255,cv2.THRESH_BINARY_INV+cv2.THRESH_OTSU)
    proposals=[]
    # Printed vertical marginal identifiers can be entirely missed by a
    # horizontal detector. Morphology only proposes pixels, never their text.
    for left,right in ((0,int(width*.2)),(int(width*.8),width)):
        band=ink[:,left:right]
        connected=cv2.morphologyEx(band,cv2.MORPH_CLOSE,np.ones((max(3,round(width*.018)),1),np.uint8))
        count,_,stats,_=cv2.connectedComponentsWithStats(connected)
        for x,y,w,h,size in stats[1:]:
            if w<4 or w>width*.09 or h<4*w or h>height*.45:continue
            density=float(np.count_nonzero(band[y:y+h,x:x+w]))/max(1,w*h)
            if not .08<density<.65:continue
            b=[int(left+x),int(y),int(left+x+w),int(y+h)]
            members=[i for i,n in enumerate(nodes) if overlap(n["bbox"],b)/max(1,area(n["bbox"]))>.5]
            if any(re.search(r"[A-Za-z]{3,}",nodes[i].get("text","")) for i in members):continue
            if is_upright_table_column(nodes,members):continue
            proposals.append({"kind":"vertical","members":members,"bbox":b,"text":"","confidence":0.,"priority":6.,
                              "proposal_source":"observed_edge_ink_connected_components"})
    # Geometric ink boxes extend fragmented OCR boxes to include missed end
    # characters. Prefer the complete observed component, not extra digits.
    kept=[]
    for p in sorted(proposals,key=lambda r:area(r["bbox"]),reverse=True):
        if any(overlap(p["bbox"],k["bbox"])/max(1,min(area(p["bbox"]),area(k["bbox"])))>.5 for k in kept):continue
        kept.append(p)
    for old in plan["requests"]:
        if old["kind"]=="vertical" and any(overlap(old["bbox"],p["bbox"])/max(1,area(old["bbox"]))>.5 for p in kept):continue
        if old["kind"]=="line" and any(old["members"][0] in p["members"] for p in kept):continue
        kept.append(old)
    # A small digit or a horizontal marginal identifier may have no Rapid
    # box at all. Connected ink components are cheap proposals; both readers
    # must recognize one complete number before anything is added.
    linked=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((1,max(3,round(width*.01))),np.uint8))
    candidates=[]
    _,_,stats,_=cv2.connectedComponentsWithStats(linked)
    for x,y,w,h,size in stats[1:]:
        if w<3 or h<8 or h>height*.08 or w>width*.4 or w/h>20:continue
        density=float(np.count_nonzero(ink[y:y+h,x:x+w]))/max(1,w*h)
        if not .07<density<.8:continue
        b=[int(x),int(y),int(x+w),int(y+h)]
        if any(overlap(n["bbox"],b)/max(1,area(b))>.2 for n in nodes):continue
        if any(overlap(p["bbox"],b)/max(1,area(b))>.2 for p in kept if p["kind"]=="vertical"):continue
        near=min((abs((n["bbox"][1]+n["bbox"][3])/2-(y+h/2))/max(h,n["bbox"][3]-n["bbox"][1]) for n in nodes),default=100)
        marginal=y>height*.85 or y+h<height*.15
        if near>1.5 and not marginal:continue
        candidates.append({"kind":"missing","members":[],"bbox":b,"text":"","confidence":0.,
                           "priority":2.+int(marginal)-min(near,2)*.1,
                           "proposal_source":"uncovered_original_ink_component"})
    kept.extend(sorted(candidates,key=lambda r:r["priority"],reverse=True)[:6])
    kept.sort(key=lambda r:r["priority"],reverse=True)
    return {"requests":kept[:plan["maximum"]],"deferred":plan["deferred"]+kept[plan["maximum"]:],"maximum":plan["maximum"]}


def retry_atomic_regions(first, reviews, maximum=4):
    """Re-read only changed tokens when whole-line scores dilute confidence."""
    retry=[]
    for r in reviews:
        req=r["request"]
        if req["kind"]!="line" or r["decision"]["accepted"]:continue
        rapid=r["rapid"];paddle=r["paddle"]
        if min(rapid.get("confidence",0),paddle.get("confidence",0))<.85:continue
        rp=sequence(rapid.get("text",""));pp=sequence(paddle.get("text",""));old=sequence(req["text"])
        if rp!=pp or len(old)!=len(pp) or old==pp:continue
        ni=req["members"][0];tokens=numeric_nodes([first[ni]])
        for token,expected in zip(tokens,pp):
            if token["quantity"]["value"]==expected or token["location_kind"]!="ctc_character_alignment_not_independent_detector":continue
            retry.append({"kind":"atomic","members":[ni],"bbox":token["bbox"],"text":token["text"],"confidence":req["confidence"],
                          "span":token["quantity"]["span"],"priority":1.})
    return retry[:maximum]


def sequence(text, locale=None):
    return [q["value"] for q in quantities(text,number_locale=locale)]


def crop_node(request):
    """Give narrow upright glyphs side context rather than rotating a '1'."""
    n=deepcopy(request);b=list(n["bbox"]);w=b[2]-b[0];h=b[3]-b[1]
    if n["kind"]!="vertical" and h>=1.5*w:
        pad=max(0,(h-w)/2)
        n["bbox"]=[b[0]-pad,b[1],b[2]+pad,b[3]]
        n.pop("polygon",None)
    return n


def decide_fast_review(request, rapid, paddle, *, crop_sha256, number_locale=None):
    base={"accepted":False,"reason":"reader_disagreement","text":request["text"]}
    if any(r.get("sha256")!=crop_sha256 or r.get("error") for r in (rapid,paddle)):
        return dict(base,reason="invalid_crop_identity_or_reader_error")
    rt=rapid.get("text","").strip();pt=paddle.get("text","").strip()
    rq=sequence(rt,number_locale);pq=sequence(pt,number_locale)
    if not pq or pq!=rq or min(rapid.get("confidence",0),paddle.get("confidence",0))<.92:return base
    if request["kind"]=="vertical":
        if re.fullmatch(r"\d{5,16}",rt) and rt==pt:
            return {"accepted":True,"text":pt,"reason":"pixel_confirmed_vertical_fragment_join"}
        return dict(base,reason="vertical_crop_not_one_agreed_identifier")
    if request["kind"]=="missing":
        qs=quantities(pt,number_locale=number_locale)
        if (rt==pt and len(qs)==1 and qs[0]["span"]==[0,len(pt)]
                and min(rapid.get("confidence",0),paddle.get("confidence",0))>=.97):
            return {"accepted":True,"text":pt,"reason":"two_readers_confirm_uncovered_ink"}
        return dict(base,reason="uncovered_ink_not_one_confident_number")
    old=sequence(request["text"],number_locale)
    if old==pq:return dict(base,reason="original_numeric_sequence_confirmed")
    original=quantities(request["text"],number_locale=number_locale)
    proposed=quantities(pt,number_locale=number_locale)
    if any(q["unit_raw"] for q in original):return dict(base,reason="preserve_explicit_unit")
    if old and len(old)!=len(pq):return dict(base,reason="numeric_token_count_changed")
    # Separator/sign changes can alter values by a thousand; require the same
    # punctuation pattern except where the original is visibly unparsed.
    if old:
        shape=lambda q:re.sub(r"\d","#",q["text"])
        shapes_equal=all(shape(a)==shape(b) for a,b in zip(original,proposed))
        if not shapes_equal:return dict(base,reason="number_format_or_digit_count_changed")
    if request["confidence"]>=.985:return dict(base,reason="preserve_high_confidence_original")
    return {"accepted":True,"text":pt,"reason":"two_readers_agree_on_source_line"}


def apply_fast_reviews(first, reviews):
    output=deepcopy(first);removed=set();added=[]
    for review in reviews:
        d=review["decision"];r=review["request"]
        if not d["accepted"]:continue
        if r["kind"] in {"vertical","missing"}:
            removed.update(r["members"])
            # The readers saw the padded/rectified crop, not just the ink
            # proposal. Bind the observation to that actual source extent.
            polygon=review.get("source_geometry",{}).get("source_polygon_px")
            bbox=([min(p[0] for p in polygon),min(p[1] for p in polygon),
                   max(p[0] for p in polygon),max(p[1] for p in polygon)] if polygon else r["bbox"])
            added.append({"text":d["text"],"bbox":bbox,"ink_proposal_bbox":r["bbox"],
                          "source_geometry":review.get("source_geometry"),"confidence":min(review["rapid"]["confidence"],review["paddle"]["confidence"]),
                          "evidence":review,"source_kind":"verified_original_ink_crop"})
        else:
            n=output[r["members"][0]]
            # Keep the observed CTC word extents for equal-format substitutions.
            # The corrected value is an observation on that existing region,
            # not newly estimated character geometry.
            updated=d["text"]
            if r["kind"]=="atomic":
                start,end=r["span"]
                updated=n["text"][:start]+d["text"]+n["text"][end:]
            old_q=numeric_nodes([n]);new_q=quantities(updated)
            if len(old_q)==len(new_q):
                words=[{"text":q["surface"],"bbox":old["bbox"],"confidence":min(review["rapid"]["confidence"],review["paddle"]["confidence"])} for old,q in zip(old_q,new_q)]
                n["words"]=words
            else:n.pop("words",None)
            n.update(text=updated,original_text=r["text"],evidence=review)
    return [n for i,n in enumerate(output) if i not in removed]+added
