"""Save two distinct historical entry-mode candidate journals."""
import hashlib
import json

from .datafeed import ROOT, load_csv
from .entry_modes import run
from .research import save_json


def main():
    spec = ROOT/'docs'/'ENTRY_MODES_SPEC.md'
    nested_spec = ROOT/'docs'/'NESTED_M15_M5_M1_SPEC.md'
    source = ROOT/'data'/'xauusd_m1.csv'
    nested = json.loads((ROOT/'results'/'nested_m15_m5_m1_run.json').read_text(encoding='utf-8'))
    if nested['spec_hash'] != hashlib.sha256(nested_spec.read_bytes()).hexdigest():
        raise ValueError('Журнал цепочек не соответствует текущей спецификации')
    result = run(load_csv(source), nested, hashlib.sha256(spec.read_bytes()).hexdigest(),
                 hashlib.sha256(source.read_bytes()).hexdigest())
    outdir = ROOT/'results'
    save_json(outdir/'entry_modes_run.json', result)
    with (outdir/'entry_modes_events.jsonl').open('w', encoding='utf-8') as f:
        for event in result['observations']:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
