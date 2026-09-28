"""Save historical paper outcomes for two entry modes on frozen first returns."""
import hashlib
import json

from .datafeed import ROOT, load_csv
from .entry_modes_outcomes import run
from .research import save_json


def main():
    spec = ROOT/'docs'/'ENTRY_MODES_OUTCOME_SPEC.md'
    entry_spec = ROOT/'docs'/'ENTRY_MODES_SPEC.md'
    nested_spec = ROOT/'docs'/'NESTED_M15_M5_M1_SPEC.md'
    source = ROOT/'data'/'xauusd_m1.csv'
    entry = json.loads((ROOT/'results'/'entry_modes_run.json').read_text(encoding='utf-8'))
    nested = json.loads((ROOT/'results'/'nested_m15_m5_m1_run.json').read_text(encoding='utf-8'))
    if entry['spec_hash'] != hashlib.sha256(entry_spec.read_bytes()).hexdigest():
        raise ValueError('Журнал режимов не соответствует текущей спецификации')
    if nested['spec_hash'] != hashlib.sha256(nested_spec.read_bytes()).hexdigest():
        raise ValueError('Журнал цепочек не соответствует текущей спецификации')
    result = run(load_csv(source), entry, nested, hashlib.sha256(spec.read_bytes()).hexdigest(),
                 hashlib.sha256(source.read_bytes()).hexdigest())
    outdir = ROOT/'results'
    save_json(outdir/'entry_modes_outcomes_run.json', result)
    with (outdir/'entry_modes_outcomes_events.jsonl').open('w', encoding='utf-8') as f:
        for item in result['experiments']:
            f.write(json.dumps(item, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
