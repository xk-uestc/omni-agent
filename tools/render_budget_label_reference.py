"""Render original-page label at larger scale for human transcription."""
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
output=ROOT/'runtime/budget-parking-label-reference-20261005.png'
if output.exists():raise SystemExit('Preserve existing reference image.')
with Image.open(ROOT/'runtime/public-budget-original-20261005.png') as image:
    image.crop((55,475,330,496)).resize((1100,84)).save(output)
