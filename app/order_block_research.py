"""Run the isolated OB observation against the existing Dukascopy M1 archive."""

import hashlib
import json

from .datafeed import ROOT, load_csv
from .order_block_observation import run
from .research import save_json


def main():
    spec = ROOT / 'docs' / 'ORDER_BLOCK_OBSERVATION_SPEC.md'
    source = ROOT / 'data' / 'xauusd_m1.csv'
    result = run(load_csv(source), hashlib.sha256(spec.read_bytes()).hexdigest(),
                 hashlib.sha256(source.read_bytes()).hexdigest())
    outdir = ROOT / 'results'
    save_json(outdir / 'order_block_run.json', result)
    with (outdir / 'order_block_events.jsonl').open('w', encoding='utf-8') as file:
        for event in result['events']:
            file.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + '\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
