"""Join SBR/RBS break journals to closed M5 pairs for OCL research."""
import hashlib
import json

from .datafeed import ROOT, load_csv
from .research import save_json
from .snr_ocl import PARENTS, run


def main():
    spec = ROOT/'docs'/'SNR_OCL_SPEC.md'
    source = ROOT/'data'/'xauusd_m1.csv'
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    parents = {}
    for slug in PARENTS:
        path = ROOT/'results'/f'snr_{slug}_run.json'
        parents[slug] = json.loads(path.read_text(encoding='utf-8'))
        parent_spec = ROOT/'docs'/f'SNR_{slug.upper()}_SPEC.md'
        if parents[slug]['spec_hash'] != hashlib.sha256(parent_spec.read_bytes()).hexdigest():
            raise ValueError(f'Устарела спецификация родительского исследования {slug}')
    result = run(load_csv(source), parents, hashlib.sha256(spec.read_bytes()).hexdigest(), source_hash)
    outdir = ROOT/'results'
    save_json(outdir/'snr_ocl_run.json', result)
    with (outdir/'snr_ocl_events.jsonl').open('w', encoding='utf-8') as f:
        for event in result['events']:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
