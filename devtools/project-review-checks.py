"""Isolated audit reproductions; no network and no production data mutations."""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from app.notifications import AlertStore
from app.snr_sbr_live import SBRForward
from app.snr_classic import ClassicObservation

def main():
    results={}
    with tempfile.TemporaryDirectory() as tmp:
        folder=Path(tmp);alerts=AlertStore(folder/'alerts.db')
        forward=SBRForward('XAUUSD','audit',100,folder/'journal.jsonl',alerts)
        forward.finish_bootstrap()
        event=dict(id='audit',time=160,state='signal_ready',level_id='one',source='audit',stop=101,target=95)
        forward.model.levels['one']={'first_touch':{'close':99},'level':100}
        forward.model.step=lambda row:forward.model.events.append(event)
        with patch('app.snr_sbr_live.public_settings',return_value={'enabled':True}):
            with patch.object(alerts,'add',side_effect=sqlite3.OperationalError('audit lock')):
                try:forward.ingest({},170,165)
                except sqlite3.OperationalError:pass
            forward.ingest({},171,166)
        results['outbox_failure']={'journal_records':len((folder/'journal.jsonl').read_text(encoding='utf-8').splitlines()),
                                   'queued_after_retry':len(alerts.recent()),'seen':len(forward.seen)}
    m=ClassicObservation('audit','audit')
    known=892500;deadline=known+24*300;t=deadline+60
    level=dict(id='audit',model='A',direction='sell',state='armed',known_at=known,armed_at=known,low_band=99.9,high_band=100.1)
    m.levels['audit']=level;m.active['audit']=level;m.last=t-60
    m.bars[60].extend({'end':deadline} for _ in range(240))
    row=dict(time=t,volume=1)
    for side,spread in [('bid',0),('ask',.1)]:
        row.update({side+'_open':100+spread,side+'_close':100+spread,side+'_low':99.95+spread,side+'_high':100.05+spread})
    with patch('app.snr_classic.trend',return_value='sell'):m.step(row)
    results['classic_expiry']={'deadline':deadline,'touch_known_at':t+60,'actual_state':level['state'],
                                'seconds_after_deadline':t+60-deadline}
    out=ROOT/'results/project-review-reproductions.json'
    out.write_text(json.dumps(results,indent=2),encoding='utf-8')
    print(json.dumps(results))

if __name__=='__main__':main()
