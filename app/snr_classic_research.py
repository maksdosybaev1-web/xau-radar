"""Save the isolated Classic A/V observation journal from the M1 archive."""
import hashlib
import json

from .datafeed import ROOT, load_csv
from .research import save_json
from .snr_classic import run


def main():
    spec = ROOT/'docs'/'SNR_CLASSIC_SPEC.md'
    source = ROOT/'data'/'xauusd_m1.csv'
    result = run(load_csv(source), hashlib.sha256(spec.read_bytes()).hexdigest(),
                 hashlib.sha256(source.read_bytes()).hexdigest())
    outdir = ROOT/'results'
    outdir.mkdir(exist_ok=True)
    save_json(outdir/'snr_classic_run.json', result)
    with (outdir/'snr_classic_events.jsonl').open('w', encoding='utf-8') as f:
        for event in result['events']:
            f.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    print(json.dumps(result['summary'], ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
