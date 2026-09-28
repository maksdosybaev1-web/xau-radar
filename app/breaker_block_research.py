"""Offline BB observation on the saved archive; no broker or notification calls."""
import hashlib
import json
from .datafeed import ROOT, load_csv
from .research import save_json
from .breaker_block_observation import run


def main():
    source = ROOT/'data/xauusd_m1.csv'
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    result = run(load_csv(source), digest(ROOT/'docs/BREAKER_BLOCK_OBSERVATION_SPEC.md'),
                 digest(source), digest(ROOT/'docs/ORDER_BLOCK_OBSERVATION_SPEC.md'))
    save_json(ROOT/'results/breaker_block_run.json', result)
    with (ROOT/'results/breaker_block_events.jsonl').open('w', encoding='utf-8') as stream:
        for event in result['events']:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n')
    summary = result['summary']
    text = ('# Историческое наблюдение Breaker Block\n\n'
            'Метод: [спецификация](../docs/BREAKER_BLOCK_OBSERVATION_SPEC.md). '
            'Источник — сохранённый XAUUSD bid/ask M1 Dukascopy.\n\n'
            f'Родительских OB: {summary["parent_ob_count"]}. Кандидатов BB: {summary["breakers"]}. '
            f'Ожидающих пробоя родителей: {summary["pending_parents"]}. Условных сделок: 0.\n\n'
            'Состояния BB:\n\n```json\n'+json.dumps(summary['states'], ensure_ascii=False, indent=2)+
            '\n```\n\nСобытия:\n\n```json\n'+json.dumps(summary['event_types'], ensure_ascii=False, indent=2)+
            '\n```\n\nЭто счёт наблюдаемых конструкций, не доля успешных сделок. '
            'Разрывы данных делают дальнейшее поведение неизвестным. '
            'Порог 1,5 ATR и H1-разметка — допущения проекта; они не доказывают совпадение с PDF. '
            'Результаты не меняют живой радар и не отправляются в Telegram.\n')
    (ROOT/'results/breaker_block_validation.md').write_text(text, encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
