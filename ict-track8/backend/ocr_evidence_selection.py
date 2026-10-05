"""Select region evidence across OCR attempts without inventing cell semantics."""
from copy import deepcopy
import math


class OcrEvidenceSelectionAgent:
    """Keep best coverage, refine only spatially equivalent complete regions.

    Every chosen region retains its actual executor frame and original mapping.
    Equal amounts plus spatial overlap are prerequisites, not factual validation.
    """
    @staticmethod
    def _rows(region):
        rows=region.get('rows',[]) if isinstance(region,dict) else []
        return rows if isinstance(rows,list) and 1<=len(rows)<=200 and all(
            isinstance(row,list) and len(row)==2 and all(isinstance(cell,dict) for cell in row) for row in rows) else []

    @staticmethod
    def _box(cell,source):
        geometry=cell.get('original_geometry',{})
        box=geometry.get('bbox_px')
        if (not geometry.get('original_bbox_eligible') or geometry.get('source_sha256')!=source
            or geometry.get('coordinate_scope')!='source_image_stored_pixel_edges'
            or not isinstance(box,list) or len(box)!=4
            or not all(type(v) in (int,float) and math.isfinite(v) for v in box)
            or not box[0]<box[2] or not box[1]<box[3]):return None
        return box

    def _equivalent(self,left,right,source):
        a=self._rows(left);b=self._rows(right)
        if not a or len(a)!=len(b) or left.get('scope')!=right.get('scope'):return False
        for row_a,row_b in zip(a,b):
            if str(row_a[1].get('text','')).strip()!=str(row_b[1].get('text','')).strip():return False
            for cell_a,cell_b in zip(row_a,row_b):
                x=self._box(cell_a,source);y=self._box(cell_b,source)
                if x is None or y is None:return False
                intersection=max(0,min(x[2],y[2])-max(x[0],y[0]))*max(0,min(x[3],y[3])-max(x[1],y[1]))
                union=(x[2]-x[0])*(x[3]-x[1])+(y[2]-y[0])*(y[3]-y[1])-intersection
                if union<=0 or intersection/union<.6:return False
        return True

    def _quality(self,region):
        rows=self._rows(region)
        if not rows:return -1.
        scores=[cell.get('confidence') for row in rows for cell in row[:1]]
        if not all(type(v) in (int,float) and math.isfinite(v) and 0<=v<=1 for v in scores):return -1.
        return sum(scores)/len(scores)

    def _disjoint_observations(self,result,attempts,source):
        """Retain separately observed pairs, without combining table structures.

        Only sparse pairs with their own source frame can supplement coverage.
        Any spatial overlap with selected evidence, or disagreement between
        attempts at the same location, prevents addition.
        """
        layout=result.get('table_layout',{})
        size=result.get('original_pixel_mapping',{}).get('source',{}).get('size_px')
        if (not isinstance(size,list) or len(size)!=2
                or not all(type(v) is int and v>0 for v in size)):return 0
        retained=[]
        regions=layout.get('regional_candidates') or [layout]
        sparse=layout.get('sparse_observations',[])
        if not isinstance(regions,list) or not isinstance(sparse,list) or len(sparse)>32:return 0
        for region in [*regions,*sparse]:
            for row in self._rows(region):
                for cell in row:
                    box=self._box(cell,source)
                    if box is None:return 0
                    retained.append(box)
        def overlaps(a,b):
            return min(a[2],b[2])+2>max(a[0],b[0]) and min(a[3],b[3])+2>max(a[1],b[1])
        pool=[]
        supported={'sparse_pairs_with_repeated_numeric_column','locally_reobserved_label_amount_pair',
                   'close_same_line_label_amount_pair'}
        for owner in attempts[:3]:
            mapping=owner.get('original_pixel_mapping',{})
            frame=owner.get('coordinate_frame',{})
            if (mapping.get('status')!='mapped' or mapping.get('source',{}).get('sha256')!=source
                    or mapping.get('source',{}).get('size_px')!=size
                    or not isinstance(frame.get('sha256'),str) or len(frame['sha256'])!=64
                    or any(ch not in '0123456789abcdef' for ch in frame['sha256'])):continue
            candidates=owner.get('table_layout',{}).get('sparse_observations',[])
            if not isinstance(candidates,list) or len(candidates)>32:continue
            for region in candidates:
                rows=self._rows(region)
                if (region.get('is_table') is not False or region.get('scope') not in supported
                        or len(rows)!=1 or self._quality(region)<0):continue
                cells=rows[0];boxes=[self._box(cell,source) for cell in cells]
                if any(box is None or box[0]<0 or box[1]<0 or box[2]>size[0] or box[3]>size[1] for box in boxes):continue
                if any(not isinstance(cell.get('text'),str) or not cell['text'].strip()
                       or type(cell.get('confidence')) not in (int,float) or not math.isfinite(cell['confidence'])
                       or not 0<=cell['confidence']<=1 for cell in cells):continue
                if overlaps(boxes[0],boxes[1]):continue
                pool.append((region,owner,boxes))
        added=0
        for region,owner,boxes in pool:
            if len(sparse)>=32:break
            if any(overlaps(box,existing) for box in boxes for existing in retained):continue
            texts=[cell['text'].strip() for cell in region['rows'][0]]
            conflicting=any(
                any(overlaps(a,b) for a in boxes for b in alternative_boxes)
                and [cell['text'].strip() for cell in alternative['rows'][0]]!=texts
                for alternative,_,alternative_boxes in pool)
            if conflicting:continue
            value=deepcopy(region)
            value['observation_provenance']={
                'source_attempt':owner.get('source_attempt'),
                'coordinate_frame':deepcopy(owner.get('coordinate_frame')),
                'image_geometry':deepcopy(owner.get('image_geometry')),
                'selected_transforms':list(owner.get('selected_transforms',[])),
                'selection_basis':'nonoverlapping_original_pair_without_cross_attempt_text_conflict'}
            value['cross_attempt_supplement']=True
            value['cell_values_verified']=False
            sparse.append(value);retained.extend(boxes);added+=1
        if added:layout['sparse_observations']=sparse
        return added

    def select(self,coverage_candidate,attempts):
        result=deepcopy(coverage_candidate)
        source=result.get('original_pixel_mapping',{}).get('source',{}).get('sha256')
        if not source or result.get('original_pixel_mapping',{}).get('status')!='mapped':return result
        layout=result.get('table_layout',{})
        replacements=0
        for key,limit in [('regional_candidates',16),('sparse_observations',32)]:
            regions=layout.get(key,[])
            if not isinstance(regions,list) or len(regions)>limit:continue
            for index,region in enumerate(regions):
                selected=region;owner=coverage_candidate
                for attempt in attempts[:3]:
                    if (attempt.get('original_pixel_mapping',{}).get('status')!='mapped'
                        or attempt.get('original_pixel_mapping',{}).get('source',{}).get('sha256')!=source):continue
                    alternative=attempt.get('table_layout',{}).get(key,[])
                    if not isinstance(alternative,list) or len(alternative)>limit:continue
                    matches=[item for item in alternative if self._equivalent(region,item,source)]
                    # Ambiguous geometric matches are never selected arbitrarily.
                    if len(matches)==1 and self._quality(matches[0])>self._quality(selected):
                        selected=matches[0];owner=attempt
                if owner is not coverage_candidate:replacements+=1
                value=deepcopy(selected)
                value['observation_provenance']={
                    'source_attempt':owner.get('source_attempt'),
                    'coordinate_frame':deepcopy(owner.get('coordinate_frame')),
                    'image_geometry':deepcopy(owner.get('image_geometry')),
                    'selected_transforms':list(owner.get('selected_transforms',[])),
                    'selection_basis':'equal_amount_sequence_and_original_region_overlap_then_label_confidence'}
                regions[index]=value
        # Root rows are only the compatibility view of the largest local region.
        matching=[region for region in layout.get('regional_candidates',[]) if self._equivalent(layout,region,source)]
        if len(matching)==1:
            layout['rows']=deepcopy(matching[0]['rows'])
            layout['confidence']=matching[0].get('confidence')
            if 'continuity_evidence' in matching[0]:layout['continuity_evidence']=deepcopy(matching[0]['continuity_evidence'])
            layout['observation_provenance']=deepcopy(matching[0]['observation_provenance'])
        supplemented=self._disjoint_observations(result,attempts,source)
        result['region_selection']={'agent':'OcrEvidenceSelectionAgent','replaced_regions':replacements,
            'supplemented_independent_pairs':supplemented,
            'coverage_source_attempt':result.get('source_attempt'),'values_independently_verified':False}
        result['coordinate_frame_scope']='region_observation_provenance_overrides_coverage_frame'
        return result
