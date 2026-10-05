"""Remove only a wide top-edge artefact outside all interior ink columns."""
import numpy as np
from PIL import ImageOps


def suppress_disconnected_top_edge(image):
    """Preserve every interior ink column and all bottom punctuation pixels.

    This is a crop observation, not an OCR correction. It cannot trim text or
    erase a line through characters. Only a one-pixel boundary distributed
    across >=80% of a wide cell is eligible, with narrow interior ink.
    """
    gray=np.array(ImageOps.grayscale(image))
    height,width=gray.shape
    evidence={'status':'unchanged','removed_pixels':0,'top_rows_examined':1}
    if width<80 or height<12:return image,evidence
    ink=gray<230
    body_columns=np.flatnonzero(ink[1:].any(axis=0))
    top_columns=np.flatnonzero(ink[0])
    if len(body_columns)==0 or len(top_columns)<8:return image,evidence
    left,right=int(body_columns[0]),int(body_columns[-1])+1
    if (right-left>width*.6 or int(top_columns[-1])-int(top_columns[0])+1<width*.8):
        return image,evidence
    keep_left,keep_right=max(0,left-3),min(width,right+3)
    remove=ink[0].copy();remove[keep_left:keep_right]=False
    if int(remove.sum())<8:return image,evidence
    result=np.array(image.convert('RGB'));result[0,remove]=255
    evidence.update(status='disconnected_top_edge_suppressed',removed_pixels=int(remove.sum()),
                    preserved_columns_px=[keep_left,keep_right],removed_x_pixels=np.flatnonzero(remove).tolist())
    from PIL import Image
    return Image.fromarray(result),evidence
