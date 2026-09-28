"""Save an isolated M15->M5->M1 nested-zone observation journal."""
import hashlib
import json

from .datafeed import ROOT, load_csv
from .research import save_json
from .nested_m15_m5_m1 import run


def main():
    spec = ROOT/'docs'/'NESTED_M15_M5_M1_SPEC.md'
    source = ROOT/'data'/'xauusd_m1.csv'
    result = run(load_csv(source), hashlib.sha256(spec.read_bytes()).hexdigest(),
                 hashlib.sha256(source.read_bytes()).hexdigest())
    outdir = ROOT/'results'
    save_json(outdir/'nested_m15_m5_m1_run.json', result)
    with (outdir/'nested_m15_m5_m1_events.jsonl').open('w', encoding='utf-8') as f:
        for event in result['events']:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
