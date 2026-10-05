"""Route expert detection by uncovered original-image ink, not reference answers."""
from copy import deepcopy
import re
from .ocr_table_fusion import area, overlap, quantities


def decide_detected_number(request, rapid, paddle, *, crop_sha256, number_locale=None):
    """Calibrated expert-candidate gate; detector supports location, not text."""
    reject=dict(accepted=False,reason="expert_numeric_evidence_insufficient",text="")
    if any(r.get("sha256")!=crop_sha256 or r.get("error") for r in (rapid,paddle)):return reject
    text=rapid.get("text","").strip()
    if text!=paddle.get("text","").strip():return reject
    q=quantities(text,number_locale=number_locale)
    if len(q)!=1 or q[0]["span"]!=[0,len(text)] or q[0]["unit_raw"]:return reject
    if min(rapid.get("confidence",0),paddle.get("confidence",0))<.92:return reject
    if request.get("detector",{}).get("confidence",0)<.6:return reject
    return dict(accepted=True,reason="localized_expert_box_and_two_exact_numeric_readings",text=text)


def field_gap_route(nodes):
    """Trigger expensive detection for missing values beside amount labels."""
    anchors=[]
    for i,node in enumerate(nodes):
        text=node.get("text","")
        if not re.search(r"(?i)\b(?:total|subtotal|tax|price|amount|balance|payment|cash|tunai)\b|金额|合计|税额|单价",text):continue
        if quantities(text) and "%" not in text:continue
        b=node["bbox"];height=b[3]-b[1]
        covered=False
        for j,other in enumerate(nodes):
            if i==j or not quantities(other.get("text","")):continue
            a=other["bbox"]
            if a[0]<b[2]-.1*height:continue
            y_overlap=max(0,min(b[3],a[3])-max(b[1],a[1]))
            if y_overlap>.4*min(height,a[3]-a[1]):covered=True;break
        if not covered:anchors.append(dict(node_index=i,bbox=b,text=text))
    return dict(triggered=len(anchors)>=2,uncovered_fields=anchors,
                reason="multiple_amount_labels_without_observed_row_values" if len(anchors)>=2 else "no_repeated_amount_field_gap")


def plan_detection_gaps(image, nodes, *, maximum=2, area_budget=.4, automatic=True):
    import cv2
    import numpy as np
    gray=np.asarray(image.convert("L"));h,w=gray.shape
    if maximum<0 or not 0<=area_budget<=1:raise ValueError("invalid_detection_budget")
    if maximum==0 or area_budget==0:return dict(regions=[],residual_candidates=[],area_fraction=0,area_budget=area_budget,maximum=maximum)
    route=field_gap_route(nodes)
    if automatic and not route["triggered"]:
        return dict(regions=[],residual_candidates=[],area_fraction=0,area_budget=area_budget,maximum=maximum,route=route)
    heights=[n["bbox"][3]-n["bbox"][1] for n in nodes if n["bbox"][3]>n["bbox"][1]]
    font=float(np.median(heights)) if heights else max(10,h*.018)
    # Local background subtraction keeps pale dot-matrix ink which a global
    # Otsu cutoff can lose beside a dark logo. It proposes regions only.
    background=cv2.GaussianBlur(gray,(0,0),max(3,font*.6))
    ink=((background.astype(float)-gray.astype(float))>8).astype(np.uint8)*255
    linked=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((3,max(5,round(font*.65))),np.uint8))
    _,_,stats,_=cv2.connectedComponentsWithStats(linked)
    candidates=[]
    for x,y,cw,ch,_ in stats[1:]:
        if cw<4 or ch<font*.35 or ch>font*2.3 or cw/ch>25:continue
        box=[int(x),int(y),int(x+cw),int(y+ch)]
        if any(overlap(box,n["bbox"])/max(1,area(box))>.3 for n in nodes):continue
        density=float(np.count_nonzero(ink[y:y+ch,x:x+cw]))/max(1,cw*ch)
        if not .025<density<.8:continue
        near=min((abs((n["bbox"][1]+n["bbox"][3])/2-(y+ch/2))/font for n in nodes),default=0)
        if near>2:continue
        candidates.append(dict(bbox=box,density=density,near_known_row=near))
    # Cluster residual text into vertical strips, rather than running a
    # neural detector over the complete page. No dataset name enters routing.
    groups=[]
    for c in sorted(candidates,key=lambda c:c["bbox"][0]):
        center=(c["bbox"][0]+c["bbox"][2])/2
        group=next((g for g in groups if abs(center-g["center"])<w*.12),None)
        if group is None:groups.append(dict(center=center,items=[c]))
        else:
            group["items"].append(c)
            group["center"]=sum((q["bbox"][0]+q["bbox"][2])/2 for q in group["items"])/len(group["items"])
    regions=[]
    for g in groups:
        boxes=[c["bbox"] for c in g["items"]]
        box=[max(0,min(b[0] for b in boxes)-round(font)),max(0,min(b[1] for b in boxes)-round(font)),
             min(w,max(b[2] for b in boxes)+round(font)),min(h,max(b[3] for b in boxes)+round(font))]
        if area(box)>w*h*area_budget:continue
        priority=sum(area(c["bbox"])*c["density"] for c in g["items"])/max(1,area(box)**.5)
        regions.append(dict(bbox=box,residual_components=len(boxes),priority=priority,
                            reason="locally_visible_ink_not_covered_by_fast_detector"))
    kept=[];used=0
    for r in sorted(regions,key=lambda r:r["priority"],reverse=True):
        # Geometric area sum is a conservative budget, including overlaps.
        if used+area(r["bbox"])>w*h*area_budget:continue
        kept.append(r);used+=area(r["bbox"])
        if len(kept)>=maximum:break
    return dict(regions=kept,residual_candidates=candidates,font_height=font,
                area_fraction=used/(w*h),area_budget=area_budget,maximum=maximum,route=route)


def expert_gap_requests(detections, existing, *, maximum=18):
    requests=[]
    for n in detections:
        if n.get("confidence",0)<.5:continue
        b=n["bbox"]
        if any(overlap(b,old["bbox"])/max(1,min(area(b),area(old["bbox"])))>.35 for old in existing):continue
        if any(overlap(b,r["bbox"])/max(1,min(area(b),area(r["bbox"])))>.5 for r in requests):continue
        requests.append(dict(kind="missing",members=[],bbox=b,polygon=n.get("polygon"),text="",confidence=0.,
                             priority=n["confidence"],proposal_source="budgeted_paddle_local_detection",detector=n))
    return sorted(requests,key=lambda r:r["priority"],reverse=True)[:maximum]


def merge_confirmed_gap_nodes(first, additions):
    """Never delete a trusted existing field to insert an expert proposal."""
    output=deepcopy(first)
    for node in additions:
        if not quantities(node.get("text","")):continue
        if any(overlap(node["bbox"],old["bbox"])/max(1,min(area(node["bbox"]),area(old["bbox"])))>.35 for old in output):continue
        output.append(deepcopy(node))
    return output
