"""Offline BPR study; only reads saved market data."""
import hashlib
import json
from .datafeed import ROOT, load_csv
from .research import save_json
from .bpr_observation import run


def main():
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    source = ROOT/'data/xauusd_m1.csv'
    result = run(load_csv(source), digest(ROOT/'docs/BPR_OBSERVATION_SPEC.md'),
                 digest(source), digest(ROOT/'app/engine.py'))
    save_json(ROOT/'results/bpr_run.json', result)
    with (ROOT/'results/bpr_events.jsonl').open('w', encoding='utf-8') as stream:
        for event in result['events']:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    summary = result['summary']
    text = ('# Историческое наблюдение BPR\n\n'
            'Метод: [спецификация](../docs/BPR_OBSERVATION_SPEC.md). '
            'Источник: сохранённый Dukascopy XAUUSD bid/ask M1; анализ по закрытым bid M15.\n\n'
            f'FVG: {summary["fvg_count"]}. Пересечений BPR: {summary["bpr_count"]}. Условных сделок: 0.\n\n'
            '```json\n'+json.dumps(summary, ensure_ascii=False, indent=2)+'\n```\n\n'
            'Пересечение и касание не являются сделкой или доказательством преимущества. '
            'Разные пары могут давать совпадающие зоны; это не независимые торговые возможности. '
            'Протестированные ранее FVG не исключаются: исследуется геометрия, а не свежесть зоны. '
            'Пороги FVG и окно 8 часов — допущения проекта, не точные правила PDF. '
            'Живой радар и Telegram не подключены к этому наблюдателю.\n')
    (ROOT/'results/bpr_validation.md').write_text(text, encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
