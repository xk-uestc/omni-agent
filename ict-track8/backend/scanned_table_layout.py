"""Bounded ruled-table geometry from pixels with OCR observation bindings.

No business headers or missing cells are invented. A complete rectangular grid
is a layout observation, not a guarantee that OCR values are semantically true.
Rectangular merged cells require locally absent separators and observed outer
boundaries. Borderless, perspective and broken ambiguous grids stay unresolved.
"""
from io import BytesIO
import math
import hashlib
import time
import re
from PIL import Image


def _join_local_crop_observations(observations, box, scale):
    """Strict same-line assembly, with duplicate/overlapping detections rejected."""
    if not isinstance(observations,list) or not 1<=len(observations)<=8:
        raise ValueError('no_bounded_crop_observations')
    parts=[]
    for observation in observations:
        polygon=[[float(p[0])/scale+box[0],float(p[1])/scale+box[1]] for p in observation['bbox']]
        value=str(observation['text']).strip();confidence=float(observation['confidence'])
        if not value or len(value)>4000 or not .6<=confidence<=1 or len(polygon)!=4:
            raise ValueError('invalid_crop_text_or_confidence')
        if not all(math.isfinite(v) for p in polygon for v in p):raise ValueError('nonfinite_crop_box')
        if not all(box[0]<=p[0]<=box[2] and box[1]<=p[1]<=box[3] for p in polygon):
            raise ValueError('crop_box_outside')
        bounds=[min(p[0] for p in polygon),min(p[1] for p in polygon),max(p[0] for p in polygon),max(p[1] for p in polygon)]
        if bounds[2]<=bounds[0] or bounds[3]<=bounds[1]:raise ValueError('empty_crop_box')
        parts.append({'text':value,'confidence':confidence,'bbox':bounds})
    parts.sort(key=lambda part:part['bbox'][0])
    if any(a['bbox'][2]>b['bbox'][0] or abs(sum(a['bbox'][1::2])-sum(b['bbox'][1::2]))/2>
        min(a['bbox'][3]-a['bbox'][1],b['bbox'][3]-b['bbox'][1])*.5 for a,b in zip(parts,parts[1:])):
        raise ValueError('overlapping_or_multiline_crop')
    bounds=[min(p['bbox'][0] for p in parts),min(p['bbox'][1] for p in parts),max(p['bbox'][2] for p in parts),max(p['bbox'][3] for p in parts)]
    return {'text':' '.join(part['text'] for part in parts),'confidence':min(part['confidence'] for part in parts),'bbox':bounds}


def _recognize_pixel_bounded_line(data, box, scale, recognize_line):
    """Recognition-only fallback; location is observed ink, not invented boxes."""
    import numpy as np
    with Image.open(BytesIO(data)) as image:
        ink=np.asarray(image.convert('L'))<160
    ink[ink.mean(axis=1)>.8,:]=False
    ys,xs=np.nonzero(ink)
    if not len(xs):raise ValueError('no_observed_crop_ink')
    rows=np.flatnonzero(ink.any(axis=1))
    if any(b-a>max(2,(int(ys.max())-int(ys.min())+1)*.25) for a,b in zip(rows,rows[1:])):
        raise ValueError('multiple_crop_ink_bands')
    observations=recognize_line(data)
    if not isinstance(observations,list) or len(observations)!=1:raise ValueError('not_one_crop_line')
    text=str(observations[0]['text']).strip();confidence=float(observations[0]['confidence'])
    if not text or len(text)>4000 or not .9<=confidence<=1:raise ValueError('low_crop_line_confidence')
    return {'text':text,'confidence':confidence,
        'bbox':[int(xs.min())/scale+box[0],int(ys.min())/scale+box[1],
                (int(xs.max())+1)/scale+box[0],(int(ys.max())+1)/scale+box[1]],
        'recognition_mode':'single_line_from_observed_pixel_envelope',
        'bbox_basis':'crop_foreground_ink_envelope'}


def recover_merged_amount_pairs(image_bytes,regions,recognize,*,recognize_line=None,max_calls=20,max_seconds=15):
    """Split only at observed pixel whitespace, then independently OCR both crops.

    Original prose observations remain unchanged. Matching OCR passes are not
    semantic verification; each returned pair is explicitly a non-table item.
    """
    import numpy as np
    suffix=re.compile(r'((?:[$€£¥₹]|(?<=\s)S)\s*[-+]?\d[\d,]*(?:\.\d+)?\s*(?:m|bn|%)?)\s*$',re.I)
    numeric=re.compile(r'^[$€£¥₹]\s*[-+]?\d[\d,]*(?:\.\d+)?\s*(?:m|bn|%)?$',re.I)
    normalize=lambda text:re.sub(r'\s+','',text).lower()
    calls=0;started=time.monotonic();pairs=[];attempts=[]
    if not isinstance(regions,list) or len(regions)>2000:return pairs,{'calls':0,'recovered_pairs':0}
    with Image.open(BytesIO(image_bytes)) as source:
        if source.width*source.height>6_000_000:return pairs,{'calls':0,'recovered_pairs':0}
        # Preserve recovery budget for actually observed currency symbols
        # before trying ambiguous S cues. New candidates must not crowd out
        # previously recoverable entries.
        ordered = sorted(regions,key=lambda item: bool(
            suffix.search(str(item.get('text',''))) and suffix.search(str(item.get('text',''))).group(1).startswith('S')))
        for item in ordered:
            if calls+2>max_calls or time.monotonic()-started>=max_seconds:break
            text=str(item.get('text',''));match=suffix.search(text)
            if not match or not text[:match.start()].strip():continue
            try:
                points=item['bbox']
                if len(points)!=4:continue
                if isinstance(points[0],(list,tuple)):
                    xs=[float(p[0]) for p in points];ys=[float(p[1]) for p in points]
                    bounds=[math.floor(min(xs)),math.floor(min(ys)),math.ceil(max(xs)),math.ceil(max(ys))]
                else:bounds=[math.floor(float(points[0])),math.floor(float(points[1])),math.ceil(float(points[2])),math.ceil(float(points[3]))]
            except (KeyError,ValueError,TypeError,OverflowError,IndexError):continue
            x0,y0,x1,y1=bounds
            if not 0<=x0<x1<=source.width or not 0<=y0<y1<=source.height:continue
            pixels=np.asarray(source.crop(bounds).convert('L'))<160
            # Horizontal rules cannot be used as a text boundary.
            pixels[pixels.mean(axis=1)>.8,:]=False
            occupied=pixels.any(axis=0)
            ink=np.flatnonzero(occupied)
            if not len(ink):continue
            gaps=[];begin=None
            for x in range(int(ink[0])+1,int(ink[-1])):
                if not occupied[x] and begin is None:begin=x
                if occupied[x] and begin is not None:
                    if x-begin>=1:gaps.append((begin,x))
                    begin=None
            for left,right in sorted(gaps,key=lambda gap:gap[1]-gap[0],reverse=True)[:3]:
                if calls+2>max_calls or time.monotonic()-started>=max_seconds:break
                split=x0+(left+right)//2;found=[];crop_reads=[]
                for box in ([x0,max(0,y0-3),split,min(source.height,y1+3)],
                            [split,max(0,y0-3),x1,min(source.height,y1+3)]):
                    recovered=None
                    for scale in (3,2):
                        if calls>=max_calls or time.monotonic()-started>=max_seconds:break
                        output=BytesIO();source.crop(box).resize(((box[2]-box[0])*scale,(box[3]-box[1])*scale)).save(output,format='PNG')
                        data=output.getvalue();calls+=1
                        try:observations=recognize(data)
                        except (ValueError,RuntimeError,TypeError):observations=[]
                        read={'scale':scale,'crop_bbox_px':box,'observations':[
                            {'text':str(observation.get('text',''))[:4000],'confidence':observation.get('confidence')}
                            for observation in observations[:8] if isinstance(observation,dict)] if isinstance(observations,list) else []}
                        crop_reads.append(read)
                        try:
                            recovered=_join_local_crop_observations(observations,box,scale)
                            recovered.update({'observation_origin':'merged_amount_crop_ocr',
                                'crop_sha256':hashlib.sha256(data).hexdigest(),'crop_bbox_px':box,
                                'crop_scale':scale,'original_merged_text':text})
                            break
                        except (ValueError,KeyError,TypeError,IndexError) as exc:
                            read['rejected']=str(exc)[:100]
                            if recognize_line is not None and calls<max_calls and time.monotonic()-started<max_seconds:
                                calls+=1
                                try:
                                    recovered=_recognize_pixel_bounded_line(data,box,scale,recognize_line)
                                    recovered.update({'observation_origin':'merged_amount_crop_ocr',
                                        'crop_sha256':hashlib.sha256(data).hexdigest(),'crop_bbox_px':box,
                                        'crop_scale':scale,'original_merged_text':text})
                                    read['line_fallback']={'text':recovered['text'],'confidence':recovered['confidence']}
                                    break
                                except (ValueError,KeyError,TypeError,IndexError,RuntimeError) as line_error:
                                    read['line_fallback_rejected']=str(line_error)[:100]
                    found.append(recovered)
                currency_reobserved = (match.group(1).startswith('S') and all(found)
                    and numeric.fullmatch(found[1]['text'])
                    and normalize(found[1]['text'][1:]) == normalize(match.group(1)[1:]))
                attempts.append({'original_merged_text':text,'split_x':split,
                    'label_observed':found[0]['text'] if found[0] else None,
                    'amount_observed':found[1]['text'] if found[1] else None,
                    'label_bbox_px':found[0]['bbox'] if found[0] else None,
                    'amount_bbox_px':found[1]['bbox'] if found[1] else None,
                    'crop_reads':crop_reads,
                    'currency_reobserved_from_crop':bool(currency_reobserved)})
                if (all(found) and not numeric.fullmatch(found[0]['text']) and numeric.fullmatch(found[1]['text'])
                    and normalize(found[0]['text'])==normalize(text[:match.start()])
                    and (normalize(found[1]['text'])==normalize(match.group(1)) or currency_reobserved)
                    and found[0]['bbox'][2]<=found[1]['bbox'][0]):
                    pairs.append({'status':'candidate','is_table':False,'scope':'locally_reobserved_label_amount_pair',
                        'cell_values_verified':False,'row_count':1,'column_count':2,'rows':[found],
                        'split_basis':'observed_pixel_whitespace_then_two_crop_ocr','original_merged_text':text,
                        'currency_reobserved_from_crop':bool(currency_reobserved)})
                    break
    return pairs,{'calls':calls,'recovered_pairs':len(pairs),'values_independently_verified':False,
        'crop_attempts':attempts}


def recover_borderless_rows(image_bytes,layout,regions,recognize,*,max_cells=8,max_seconds=10):
    """Recognize only missing positions bounded by observed row/column geometry.

    Returns additional OCR boxes; never fills a value merely from alignment.
    The caller must rerun the layout check. Whole-page prose stays unchanged.
    """
    if layout.get('status')!='candidate' or layout.get('alignment_method')!='repeated_left_or_right_edges_with_global_gutters':
        return list(regions),{'calls':0,'recovered_regions':0}
    rows=layout['rows'];count=layout['column_count']
    full=[row for row in rows if len(row)==count]
    bands=[(min(row[col]['bbox'][0] for row in full),max(row[col]['bbox'][2] for row in full)) for col in range(count)]
    added=[];calls=0;started=time.monotonic()
    with Image.open(BytesIO(image_bytes)) as source:
        for row in rows:
            if len(row)==count:continue
            top=min(cell['bbox'][1] for cell in row);bottom=max(cell['bbox'][3] for cell in row)
            height=bottom-top
            present={col for col,(left,right) in enumerate(bands) if any(left-3<=cell['bbox'][0] and cell['bbox'][2]<=right+3 for cell in row)}
            for col,(left,right) in enumerate(bands):
                if col in present or calls>=max_cells or time.monotonic()-started>=max_seconds:continue
                box=[max(0,int(left-8)),max(0,int(top-height*.6)),min(source.width,int(right+8)),min(source.height,int(bottom+height*.6))]
                if box[2]<=box[0] or box[3]<=box[1] or (box[2]-box[0])*(box[3]-box[1])*4>6_000_000:continue
                crop=source.crop(box).resize(((box[2]-box[0])*2,(box[3]-box[1])*2))
                output=BytesIO();crop.save(output,format='PNG');data=output.getvalue();calls+=1
                try:observations=recognize(data)
                except (ValueError,RuntimeError,TypeError):continue
                for item in observations:
                    try:
                        points=[[float(p[0])/2+box[0],float(p[1])/2+box[1]] for p in item['bbox']]
                        if len(points)!=4 or not all(math.isfinite(v) for p in points for v in p):continue
                        text=str(item['text']).strip();confidence=float(item['confidence'])
                        if not text or len(text)>4000 or not math.isfinite(confidence) or not 0<=confidence<=1:continue
                        if not all(box[0]<=p[0]<=box[2] and box[1]<=p[1]<=box[3] for p in points):continue
                        added.append({'text':text,'confidence':confidence,'bbox':points,
                            'observation_origin':'borderless_cell_crop_ocr','crop_sha256':hashlib.sha256(data).hexdigest(),
                            'crop_bbox_px':box})
                    except (KeyError,ValueError,TypeError,IndexError):continue
    return [*regions,*added],{'calls':calls,'recovered_regions':len(added),'values_independently_verified':False}


def extract_scanned_grids(image_bytes, regions):
    import cv2
    import numpy as np
    if not isinstance(regions, list) or len(regions) > 2000:
        return {'status': 'region_budget_exceeded', 'tables': []}
    with Image.open(BytesIO(image_bytes)) as image:
        if image.width * image.height > 6_000_000:
            return {'status': 'pixel_budget_exceeded', 'tables': []}
        gray = np.asarray(image.convert('L'))
    if min(gray.shape) < 60:
        return {'status': 'too_small', 'tables': []}
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                  cv2.THRESH_BINARY_INV, 31, 15)
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (max(25, gray.shape[1] // 30), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(25, gray.shape[0] // 30))))
    mask = cv2.bitwise_or(horizontal, vertical)
    components, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    candidates = [(x, y, width, height) for x, y, width, height, area in stats[1:]
                  if width >= 80 and height >= 50 and area >= 100]
    if len(candidates) > 16:
        return {'status': 'table_budget_exceeded', 'tables': []}

    def clusters(values):
        groups = []
        for value in values:
            if groups and value - groups[-1][-1] <= 3:
                groups[-1].append(value)
            else:
                groups.append([value])
        return [int(round(sum(group) / len(group))) for group in groups]

    tables = []
    for x, y, width, height in candidates:
        h = horizontal[y:y+height, x:x+width]
        v = vertical[y:y+height, x:x+width]
        ys = clusters(np.flatnonzero(np.mean(h > 0, axis=1) >= .2))
        xs = clusters(np.flatnonzero(np.mean(v > 0, axis=0) >= .2))
        if len(xs) < 3 or len(ys) < 3:
            continue
        if (len(xs)-1) * (len(ys)-1) > 1000:
            continue
        xs, ys = [int(a+x) for a in xs], [int(a+y) for a in ys]
        # Short tables can align repeated glyph stems above the global 20%
        # projection threshold. A ruled separator must join two observed
        # horizontal boundaries, even if merged cells limit it to one row.
        def joins_row_boundaries(at):
            band=vertical[:,max(0,at-2):at+3]>0
            for top,bottom in zip(ys,ys[1:]):
                if bottom-top<8:continue
                upper=band[max(0,top-2):top+3]
                lower=band[max(0,bottom-2):bottom+3]
                inside=band[top+2:bottom-1]
                if (upper.size and lower.size and inside.size and upper.any() and lower.any()
                        and float(np.mean(np.any(inside,axis=1)))>=.8):return True
            return False
        xs=[at for at in xs if joins_row_boundaries(at)]
        if len(xs)<3:continue
        nrows, ncols = len(ys)-1, len(xs)-1
        parents = list(range(nrows * ncols))
        def root(index):
            while parents[index] != index:
                parents[index] = parents[parents[index]]
                index = parents[index]
            return index
        def join(first, second):
            parents[root(second)] = root(first)
        def vertical_support(at, start, end):
            section = vertical[max(0,start+4):end-3, max(0,at-2):at+3]
            return float(np.mean(np.any(section > 0, axis=1))) if section.size else 0
        def horizontal_support(at, start, end):
            section = horizontal[max(0,at-2):at+3, max(0,start+4):end-3]
            return float(np.mean(np.any(section > 0, axis=0))) if section.size else 0
        # A separator is either observed or absent. Intermediate coverage is
        # broken-line evidence, never permission to invent a merged cell.
        ambiguous = False
        for row in range(nrows):
            if any(vertical_support(at, ys[row], ys[row+1]) < .8 for at in (xs[0], xs[-1])):
                ambiguous = True
            for col in range(1, ncols):
                support = vertical_support(xs[col], ys[row], ys[row+1])
                if support <= .1:
                    join(row*ncols+col-1, row*ncols+col)
                elif support < .8:
                    ambiguous = True
        for col in range(ncols):
            if any(horizontal_support(at, xs[col], xs[col+1]) < .8 for at in (ys[0], ys[-1])):
                ambiguous = True
            for row in range(1, nrows):
                support = horizontal_support(ys[row], xs[col], xs[col+1])
                if support <= .1:
                    join((row-1)*ncols+col, row*ncols+col)
                elif support < .8:
                    ambiguous = True
        groups = {}
        for index in range(nrows*ncols):
            groups.setdefault(root(index), []).append(divmod(index, ncols))
        spans = []
        for group in groups.values():
            r0, r1 = min(r for r,c in group), max(r for r,c in group)
            c0, c1 = min(c for r,c in group), max(c for r,c in group)
            if len(group) != (r1-r0+1)*(c1-c0+1):
                ambiguous = True
            # A union reached by walking around one surviving internal line
            # is not a single merged rectangle. All its internal edges must
            # be absent, preventing a damaged grid from swallowing real cells.
            if any(vertical_support(xs[c], ys[r], ys[r+1]) > .1
                   for r in range(r0,r1+1) for c in range(c0+1,c1+1)):
                ambiguous = True
            if any(horizontal_support(ys[r], xs[c], xs[c+1]) > .1
                   for r in range(r0+1,r1+1) for c in range(c0,c1+1)):
                ambiguous = True
            spans.append((r0,r1,c0,c1))
        if ambiguous:
            continue
        cells = []
        unresolved = []
        for row in range(len(ys)-1):
            cells.append([{'row': row, 'column': col,
                'bbox_px': [xs[col], ys[row], xs[col+1], ys[row+1]],
                'rowspan': 1, 'colspan': 1,
                'observations': [], 'text': '', 'status': 'empty_or_unrecognized'}
                for col in range(len(xs)-1)])
        for r0,r1,c0,c1 in spans:
            anchor = cells[r0][c0]
            anchor.update({'rowspan': r1-r0+1, 'colspan': c1-c0+1,
                           'bbox_px': [xs[c0], ys[r0], xs[c1+1], ys[r1+1]]})
            for row in range(r0,r1+1):
                for col in range(c0,c1+1):
                    if (row,col) != (r0,c0):
                        cells[row][col].update({'status': 'covered_by_merged_cell', 'anchor': [r0,c0]})
        for region_index, region in enumerate(regions or []):
            try:
                points = region['bbox']
                if len(points) != 4:
                    continue
                left, right = min(float(p[0]) for p in points), max(float(p[0]) for p in points)
                top, bottom = min(float(p[1]) for p in points), max(float(p[1]) for p in points)
                if not all(math.isfinite(value) for value in (left, right, top, bottom)):
                    continue
                if not (xs[0] <= (left+right)/2 <= xs[-1] and ys[0] <= (top+bottom)/2 <= ys[-1]):
                    continue
                matches = [cell for row in cells for cell in row
                    if cell['status'] != 'covered_by_merged_cell'
                    and cell['bbox_px'][0]-2 <= left < right <= cell['bbox_px'][2]+2
                    and cell['bbox_px'][1]-2 <= top < bottom <= cell['bbox_px'][3]+2]
                if len(matches) != 1:
                    unresolved.append(region_index)
                    continue
                matches[0]['observations'].append({'region_index': region_index,
                    'text': region['text'], 'confidence': region['confidence'],
                    'bbox_px': [left, top, right, bottom]})
            except (KeyError, ValueError, TypeError, IndexError):
                unresolved.append(region_index)
        for row in cells:
            for cell in row:
                cell['observations'].sort(key=lambda item: (item['bbox_px'][1], item['bbox_px'][0]))
                cell['text'] = ' '.join(item['text'] for item in cell['observations'])
                if cell['observations']:
                    cell['status'] = 'ocr_observed'
        tables.append({'status': 'layout_observed', 'bbox_px': [int(x), int(y), int(x+width), int(y+height)],
            'row_count': len(cells), 'column_count': len(cells[0]), 'cells': cells,
            'merged_cell_count': sum(cell['rowspan'] > 1 or cell['colspan'] > 1 for row in cells for cell in row),
            'unbound_region_indices': unresolved, 'header_semantics': 'not_inferred',
            'cell_values_verified': False})
    return {'status': 'observed' if tables else 'no_complete_rectangular_grid',
        'method': 'pixel_ruled_grid_with_contained_ocr_boxes',
        'coordinate_scope': 'ocr_executor_input_pixels', 'tables': tables,
        'limitations': ['only_rectangular_merged_cells_with_observed_boundaries', 'borderless_tables_not_resolved',
                       'perspective_not_rectified', 'ocr_values_not_independently_verified']}


def recover_grid_cells(image_bytes, layout, recognize, *, max_cells=8, max_seconds=10):
    """Bounded cell-crop recognition for empty/unbound OCR observations.

    Crop results stay observations and carry their own hash and confidence;
    the whole-page text and successful observations are never overwritten.
    Time is a scheduling budget checked before calls, not a hard inference kill.
    """
    if layout.get('status') != 'observed':
        return layout
    started = time.monotonic()
    calls, failures = 0, 0
    with Image.open(BytesIO(image_bytes)) as source:
        image = source.convert('RGB')
        for table in layout['tables']:
            for row in table['cells']:
                for cell in row:
                    if cell['observations'] or cell['status'] == 'covered_by_merged_cell':
                        continue
                    if calls >= max_cells or time.monotonic()-started >= max_seconds:
                        continue
                    left, top, right, bottom = cell['bbox_px']
                    crop = image.crop((left+3, top+3, right-3, bottom-3))
                    if min(crop.size) < 12:
                        continue
                    output = BytesIO()
                    crop.save(output, format='PNG')
                    data = output.getvalue()
                    calls += 1
                    try:
                        observations = recognize(data)
                        cleaned = []
                        for item in observations:
                            text, confidence = str(item['text']).strip(), float(item['confidence'])
                            if text and len(text) <= 4000 and math.isfinite(confidence) and 0 <= confidence <= 1:
                                cleaned.append({'text': text, 'confidence': confidence,
                                    'method': 'cell_crop_ocr', 'crop_sha256': hashlib.sha256(data).hexdigest(),
                                    'crop_bbox_px': [left+3, top+3, right-3, bottom-3]})
                        if cleaned:
                            cell['crop_observations'] = cleaned
                            cell['text'] = ' '.join(item['text'] for item in cleaned)
                            cell['status'] = 'cell_crop_ocr_observed'
                    except (ValueError, TypeError, RuntimeError):
                        failures += 1
    layout['cell_recovery'] = {'calls': calls, 'failures': failures, 'max_cells': max_cells,
        'scheduling_budget_seconds': max_seconds,
        'elapsed_seconds': round(time.monotonic()-started, 3),
        'values_independently_verified': False}
    return layout
