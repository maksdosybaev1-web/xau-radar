"""Extract reproducible evidence for the first video-to-code comparison."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    {'id':'A', 'video':3, 'start':233, 'end':550, 'frames':[330,390,450,510],
     'title':'Сужение зоны и пример покупки'},
    {'id':'B', 'video':3, 'start':1785, 'end':2070, 'frames':[1845,1905,1965,2025],
     'title':'Пример после перехода к часовой зоне, продажа'},
    {'id':'C', 'video':1, 'start':1075, 'end':1360, 'frames':[1095,1155,1275,1335],
     'title':'Старший контекст и локальный пример продажи'},
]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--ffmpeg',required=True)
    args=parser.parse_args()
    videos=sorted((ROOT/'materials').glob('*.mp4'))
    assert len(videos)==3, 'Expected the three original videos'
    folder=ROOT/'docs'/'evidence'/'step1';folder.mkdir(parents=True,exist_ok=True)
    manifest={'purpose':'Сопоставление примеров с кодом; не проверка исполнения сделок',
              'implementation_sha256':{name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                                       for name in ('app/engine.py','config.json','docs/SPEC.md')},'cases':[]}
    for case in CASES:
        video=videos[case['video']-1]
        transcript=ROOT/'docs'/'transcripts'/f"transcript_{case['video']}_refined.jsonl"
        segments=[json.loads(line) for line in transcript.read_text(encoding='utf-8').splitlines()]
        selected=[s for s in segments if s['start']<case['end'] and s['end']>case['start']]
        excerpt=folder/f"case_{case['id']}_transcript.json"
        excerpt.write_text(json.dumps(selected,ensure_ascii=False,indent=2),encoding='utf-8')
        with video.open('rb') as source:
            source_hash=hashlib.file_digest(source,'sha256').hexdigest()
        result=dict(case,video_file=video.name,video_sha256=source_hash,
                    transcript_file=transcript.relative_to(ROOT).as_posix(),excerpt=excerpt.name,images=[])
        for stamp in case['frames']:
            target=folder/f"case_{case['id']}_{stamp:04d}.jpg"
            subprocess.run([args.ffmpeg,'-hide_banner','-loglevel','error','-y','-ss',str(stamp),
                            '-i',str(video),'-frames:v','1','-q:v','2',str(target)],check=True)
            result['images'].append({'seconds':stamp,'file':target.name,
                                     'sha256':hashlib.sha256(target.read_bytes()).hexdigest()})
        manifest['cases'].append(result)
    (folder/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'cases':len(CASES),'frames':sum(len(c['frames']) for c in CASES)}))


if __name__=='__main__':main()
