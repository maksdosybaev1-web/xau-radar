"""Offline audit reproductions. Temporary journals; no MT5 or network calls."""
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.fvg_forward import FVGForward
from app.snr_sbr_live import SBRForward
from app.snr_rbs_live import RBSForward


def event(zone='one'):
    return dict(id=1, time=160, type='near', zone_id=zone, low=100, high=101)


result = {}
with tempfile.TemporaryDirectory() as tmp:
    journal = Path(tmp)/'fvg.jsonl'
    store = Mock()
    forward = FVGForward('XAUUSD', 'test', 'rules', 100, journal, store)
    result['fvg_two_zone_key_collision'] = forward.key(event()) == forward.key(event('two'))
    with patch('app.fvg_forward.public_settings', return_value={'enabled': False}):
        with patch.object(forward.seen, 'add', side_effect=sqlite3.OperationalError('locked')):
            try:
                forward.persist([event()], 170, 169)
            except sqlite3.OperationalError:
                pass
        forward.persist([event()], 175, 174)
    result['fvg_rows_after_index_failure_same_process'] = len(journal.read_text(encoding='utf-8').splitlines())

for cls in (SBRForward, RBSForward):
    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp)/'events.jsonl'
        store = Mock()
        forward = cls('XAUUSD', 'source-A', 100, journal, store)
        forward.finish_bootstrap()
        new = dict(id='one', time=160, state='signal_ready', level_id='one',
                   source='test', stop=101, target=95)
        forward.model.levels['one'] = {'first_touch': {'close': 99}, 'level': 100}
        forward.model.step = lambda row: forward.model.events.append(new)
        with patch(cls.__module__+'.public_settings', return_value={'enabled': True}):
            forward.ingest({}, 170, 169)
            other_store = Mock()
            resumed = cls('XAUUSD', 'source-B', 100, journal, other_store)
            resumed.state(175, 174)
        result[cls.__name__+'_foreign_source_replayed'] = other_store.add.call_count

    with tempfile.TemporaryDirectory() as tmp:
        journal = Path(tmp)/'events.jsonl'
        forward = cls('XAUUSD', 'test', 100, journal, Mock())
        forward.finish_bootstrap()
        forward.model.step = lambda row: forward.model.events.append(dict(id='lost',time=160,state='armed'))
        with patch.object(Path,'open',side_effect=OSError('disk unavailable')):
            try:
                forward.ingest({},170,169)
            except OSError:
                pass
        forward.model.step = lambda row: None
        forward.ingest({},180,179)
        result[cls.__name__+'_rows_after_write_failure_next_step'] = len(journal.read_text(encoding='utf-8').splitlines())
        result[cls.__name__+'_buffer_after_next_step'] = len(forward.model.events)

target = Path(__file__).resolve().parents[1]/'results/full_checklist_reproductions.json'
target.write_text(json.dumps(result, indent=2), encoding='utf-8')
print(json.dumps(result))
