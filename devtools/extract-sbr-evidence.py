"""Extract source evidence for the isolated SBR specification."""
import argparse, hashlib, json, subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
CASES=[('definition',2,279,369,[315,350,365]),
       ('example',3,1155,1485,[1200,1260,1380]),
       ('exception',3,1755,1785,[1770])]

def main():
    p=argparse.ArgumentParser();p.add_argument('--ffmpeg',required=True);args=p.parse_args()
    folder=ROOT/'docs/evidence/step2';folder.mkdir(parents=True,exist_ok=True)
    videos=sorted((ROOT/'materials').glob('*.mp4'));assert len(videos)==3
    manifest={'scope':'Формализация SBR; без внедрения в радар','cases':[],
              'unchanged_files':{str(x.relative_to(ROOT)).replace('\\','/'):hashlib.sha256(x.read_bytes()).hexdigest()
                                 for x in [*sorted((ROOT/'app').glob('*.py')),ROOT/'config.json',ROOT/'radar_config.json']}}
    for name,n,start,end,stamps in CASES:
        video=videos[n-1]
        rows=[json.loads(line) for line in (ROOT/f'docs/transcripts/transcript_{n}_refined.jsonl').read_text(encoding='utf-8').splitlines()]
        selected=[r for r in rows if r['start']<end and r['end']>start]
        (folder/f'{name}_transcript.json').write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
        with video.open('rb') as f:digest=hashlib.file_digest(f,'sha256').hexdigest()
        case=dict(id=name,video=n,source=video.name,sha256=digest,start=start,end=end,images=[])
        for stamp in stamps:
            out=folder/f'{name}_{stamp:04d}.jpg'
            subprocess.run([args.ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(stamp),'-i',str(video),'-frames:v','1','-q:v','2',str(out)],check=True)
            case['images'].append(dict(seconds=stamp,file=out.name,sha256=hashlib.sha256(out.read_bytes()).hexdigest()))
        manifest['cases'].append(case)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('3 source fragments, 7 frames')

if __name__=='__main__':main()
