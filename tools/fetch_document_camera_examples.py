"""Pin author-released distorted examples; no benchmark accuracy is implied."""
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen
from io import BytesIO
import hashlib,json
from PIL import Image

ROOT=Path(r'D:\ICT8-OfficialDatasets\document-camera-examples')
COMMIT='2b12e48d5ba24580eaa7e17e12db4057c8ac6058'
FILES={'25.jpg':'74e408ce9a2fb5609cecae12c72394017c6d1eb4',
       '48_2 copy.png':'643f22e7dd13244ab75fb83189af0403e58f01ca',
       '51_1 copy.png':'9e65c92e03af1a814ffafa7d9a2f5bda10fb6c9c'}


def main():
    ROOT.mkdir(parents=True,exist_ok=True)
    records=[]
    for filename,blob in FILES.items():
        url=f'https://raw.githubusercontent.com/fh2019ustc/DocTr-Plus/{COMMIT}/distorted/{quote(filename)}'
        target=ROOT/filename
        if target.exists():data=target.read_bytes()
        else:
            with urlopen(url,timeout=30) as response:
                data=response.read(8*1024*1024+1)
        if len(data)>8*1024*1024:raise ValueError('camera example exceeds image payload budget')
        git_blob=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
        if git_blob!=blob:raise ValueError('author commit blob mismatch')
        with Image.open(BytesIO(data)) as image:size=list(image.size);image.verify()
        if not target.exists():target.write_bytes(data)
        record={'filename':filename,'url':url,'repository':'https://github.com/fh2019ustc/DocTr-Plus',
            'commit':COMMIT,'git_blob_sha1':blob,'sha256':hashlib.sha256(data).hexdigest(),
            'bytes':len(data),'size_px':size,'provenance':'author_released_distorted_input_example_not_full_benchmark'}
        records.append(record)
        print(json.dumps(record),flush=True)
    manifest=ROOT/'SOURCE_MANIFEST.json'
    if manifest.exists():
        if json.loads(manifest.read_text(encoding='utf-8'))['files']!=records:raise ValueError('preserve existing source manifest')
    else:manifest.write_text(json.dumps({'not_official_contest_dataset':True,'files':records},indent=2),encoding='utf-8')


if __name__=='__main__':main()
