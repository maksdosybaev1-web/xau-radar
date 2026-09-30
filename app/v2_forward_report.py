"""Reproducible, read-only cohorts from saved Radar v2 forward evidence."""
import hashlib
import json
import argparse
import shutil
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .datafeed import ROOT

SOURCES = {
    'FVG': ('live.json', 'forward_events.jsonl', 'fvg_near', 'confirmed', 'zone_id'),
    'SBR': ('snr_sbr_live.json', 'snr_sbr_forward_events.jsonl', 'sbr_sell_plan', 'signal_ready', 'level_id'),
    'RBS': ('snr_rbs_live.json', 'snr_rbs_forward_events.jsonl', 'rbs_buy_plan', 'signal_ready', 'level_id'),
}


def _read(path, hashes):
    data = path.read_bytes()
    hashes[path.name] = hashlib.sha256(data).hexdigest()
    return data


def _utc(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')


def _delivery(root, ids):
    frozen = root / 'runtime' / 'alert_statuses.json'
    if frozen.exists():
        statuses = json.loads(frozen.read_text(encoding='utf-8'))
        rows = sorted((identity, status) for identity, status in statuses.items() if identity in ids)
        digest = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
        return dict(rows), digest
    path = root / 'runtime' / 'alerts.sqlite3'
    if not path.exists():
        return {}, hashlib.sha256(b'[]').hexdigest()
    with closing(sqlite3.connect('file:' + path.resolve().as_posix() + '?mode=ro', uri=True)) as db:
        rows = sorted((identity, status) for identity, status in db.execute(
            'SELECT id,status FROM alerts') if identity in ids)
    digest = hashlib.sha256(json.dumps(rows, separators=(',', ':')).encode()).hexdigest()
    return dict(rows), digest


def freeze(root=ROOT):
    root = Path(root)
    target = root / 'results' / ('v2_forward_snapshot_' +
              datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid4().hex[:6])
    (target / 'results').mkdir(parents=True)
    (target / 'runtime').mkdir()
    for snapshot_name, journal_name, *_ in SOURCES.values():
        for name in (snapshot_name, journal_name):
            shutil.copyfile(root / 'results' / name, target / 'results' / name)
    path = root / 'runtime' / 'alerts.sqlite3'
    if path.exists():
        with closing(sqlite3.connect('file:' + path.resolve().as_posix() + '?mode=ro', uri=True)) as db:
            statuses = dict(db.execute('SELECT id,status FROM alerts'))
        (target / 'runtime' / 'alert_statuses.json').write_text(
            json.dumps(statuses, ensure_ascii=False, sort_keys=True), encoding='utf-8')
    return target


def _paper_fvg(live, rows, since):
    confirmed = {(row.get('zone_id'), row['time']) for row in rows
                 if row.get('type') == 'confirmed' and row.get('observation_quality') == 'timely'}
    trades = [trade for trade in live.get('trades', [])
              if (trade.get('zone_id'), trade.get('signal_time')) in confirmed]
    excluded = sum(trade.get('signal_time', 0) >= since and
                   (trade.get('zone_id'), trade.get('signal_time')) not in confirmed
                   for trade in live.get('trades', []))
    closed = sorted((trade for trade in trades if trade.get('exit_time') is not None
                     and not trade.get('data_gap')), key=lambda trade: (trade['exit_time'], trade['id']))
    initial = live.get('config', {}).get('initial_equity')
    equity = peak = initial if isinstance(initial, (int, float)) and initial > 0 else None
    drawdown = None
    for trade in closed:
        if equity is not None:
            equity += trade['pnl']
            peak = max(peak, equity)
            drawdown = max(drawdown or 0, (peak - equity) / peak) if peak > 0 else None
    return {'closed': len(closed),
            'not_timely_confirmed': excluded,
            'open': sum(trade.get('exit_time') is None for trade in trades),
            'data_gap': sum(bool(trade.get('data_gap')) for trade in trades),
            'net_pnl_usd': sum(trade['pnl'] for trade in closed) if closed else None,
            'average_r': sum(trade['r'] for trade in closed) / len(closed) if closed else None,
            'closed_only_drawdown_fraction': drawdown if closed else None}


def collect(root=ROOT):
    root = Path(root)
    hashes, sources, alert_ids = {}, {}, set()
    for model, (snapshot_name, journal_name, plan_type, confirmed_type, key_name) in SOURCES.items():
        live = json.loads(_read(root / 'results' / snapshot_name, hashes))
        rows = [json.loads(line) for line in _read(root / 'results' / journal_name, hashes).splitlines()
                if line.strip()]
        since = int(live['forward_since'])
        rows = [row for row in rows if row['time'] >= since]
        version_key = 'config_hash' if model == 'FVG' else 'version'
        expected = live[version_key]
        if any(row.get(version_key) != expected for row in rows):
            raise ValueError(f'{model}: журнал содержит другую версию правил')
        if len({row.get('source_hash') for row in rows}) > 1:
            raise ValueError(f'{model}: в журнале смешаны источники')
        if model != 'FVG' and len({row.get('spec_hash') for row in rows}) > 1:
            raise ValueError(f'{model}: в журнале смешаны спецификации')
        plans, confirmations, terminal = set(), set(), set()
        quality, states, delivery_ids = Counter(), Counter(), []
        for row in rows:
            quality[row.get('observation_quality', 'unknown')] += 1
            states[row.get('type') or row.get('state') or 'unknown'] += 1
            key = (row.get('source_hash'), row.get(key_name))
            alert = row.get('delivery_alert') or {}
            if alert.get('id'):
                alert_ids.add(alert['id'])
                delivery_ids.append(alert['id'])
            if alert.get('type') == plan_type and isinstance(alert.get('analysis_plan'), dict):
                if key in plans:
                    raise ValueError(f'{model}: повторный план для одной зоны или уровня')
                plans.add(key)
            if (row.get('type') or row.get('state')) == confirmed_type and row.get('observation_quality') == 'timely':
                confirmations.add(key)
            if row.get('type') == 'cancelled' or row.get('state') in (
                    'expired', 'context_rejected', 'invalidated', 'retest_unconfirmed',
                    'entry_rejected', 'incomplete'):
                terminal.add(key)
        sources[model] = {'forward_since': since, 'last_m1': live.get('source', {}).get('last')
                          if model == 'FVG' else live.get('last_m1'),
                          'quote_time': live.get('quote_time'), 'version': expected,
                          'source_hash': next(iter({r.get('source_hash') for r in rows}), None),
                          'journal_rows': len(rows), 'quality': dict(quality), 'states': dict(states),
                          'plans': len(plans), 'timely_confirmed_plans': len(plans & confirmations),
                          'plans_without_confirmation_so_far': len(plans - confirmations),
                          'ended_without_confirmation': len((plans - confirmations) & terminal),
                          'delivery_ids': delivery_ids,
                          'paper': _paper_fvg(live, rows, since) if model == 'FVG' else None}
    statuses, delivery_hash = _delivery(root, alert_ids)
    for result in sources.values():
        result['delivery'] = dict(Counter(statuses.get(identity, 'not_in_queue')
                                          for identity in result.pop('delivery_ids')))
    return {'input_sha256': hashes, 'alert_rows_sha256': delivery_hash, 'models': sources,
            'interpretation': 'SBR/RBS ведут сигналы без условных сделок; FVG-исходы учитываются только после своевременного подтверждения. Статус sent означает ответ Telegram API, а не прибыль и не брокерское исполнение.'}


def markdown(report):
    rows = []
    for model, data in report['models'].items():
        paper = data['paper']
        rows.append(f"| {model} | {_utc(data['forward_since'])} | {data['journal_rows']} | "
                    f"{data['quality'].get('timely', 0)} / {data['quality'].get('late_bar', 0)} / "
                    f"{data['quality'].get('stale_quote', 0)} | {data['plans']} | "
                    f"{data['timely_confirmed_plans']} | {data['plans_without_confirmation_so_far']} | "
                    f"{paper['closed'] if paper else 'не измеряются'} |")
    fvg = report['models']['FVG']['paper']
    outcome = (f"{fvg['closed']} закрытых, {fvg['open']} открытых, {fvg['data_gap']} с разрывом, "
               f"{fvg['not_timely_confirmed']} исключены без своевременного подтверждения; "
               f"средний R: {fvg['average_r'] if fvg['average_r'] is not None else 'нет данных'}, "
               f"закрытая просадка: {fvg['closed_only_drawdown_fraction'] if fvg['closed_only_drawdown_fraction'] is not None else 'нет данных'}.")
    details = '\n'.join(f"- **{model}:** состояния {json.dumps(data['states'], ensure_ascii=False)}; "
                        f"доставка {json.dumps(data['delivery'], ensure_ascii=False)}; "
                        f"завершились без подтверждения {data['ended_without_confirmation']}."
                        for model, data in report['models'].items())
    hashes = '\n'.join(f'- `{name}`: `{digest}`' for name, digest in report['input_sha256'].items())
    snapshot = report.get('snapshot')
    repeat = ('Повторить по сохранённому срезу: `python -X utf8 -m app.v2_forward_report '
              f'--check-snapshot {snapshot}`. ' if snapshot else '')
    return ('# Forward-проверка Radar v2\n\n'
            'Срез по сохранённым журналам. ' + repeat +
            'Счётчики разных моделей не складываются в общую доходность.\n\n'
            '| Модель | Начало forward | События | Своевременные / поздние / устаревшая цена | '
            'Планы | Подтверждённые планы | Без подтверждения пока | Закрытые условные исходы |\n'
            '|---|---|---:|---:|---:|---:|---:|---:|\n' + '\n'.join(rows) + '\n\n'
            'FVG: ' + outcome + '\n\n' + details + '\n\n'
            'Числа «без подтверждения пока» включают ещё действующие планы. Состояния доставки '
            'считаются отдельно от исходов. SBR/RBS работают в режиме `live_signal_only`, поэтому '
            'результат сделок и просадка для них здесь не вычисляются. При нуле закрытых FVG-исходов '
            'средний R и просадка остаются неизвестными.\n\n'
            '## SHA-256 входов\n\n' + hashes + '\n'
            f"- выборка статусов SQLite: `{report['alert_rows_sha256']}`\n")


def main():
    parser = argparse.ArgumentParser(description='Forward-срез Radar v2')
    parser.add_argument('--check-snapshot', type=Path, help='Повторить отчёт по сохранённым входам')
    args = parser.parse_args()
    if args.check_snapshot:
        report = collect(args.check_snapshot)
        report['snapshot'] = args.check_snapshot.as_posix()
        print(markdown(report))
        return
    frozen = freeze()
    report = collect(frozen)
    report['snapshot'] = frozen.relative_to(ROOT).as_posix()
    folder = ROOT / 'results'
    for name, content in (('v2_forward_evidence.json', json.dumps(report, ensure_ascii=False, indent=2)),
                          ('v2_forward_evidence.md', markdown(report))):
        target = folder / name
        temp = target.with_suffix(target.suffix + '.tmp')
        temp.write_text(content + '\n', encoding='utf-8')
        temp.replace(target)
    print(markdown(report))


if __name__ == '__main__':
    main()
