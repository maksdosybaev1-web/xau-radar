"""Versioned JSON snapshots of the complete simulated FVG engine state."""
import hashlib
import json
import os
import sqlite3
import time
import uuid
from collections import deque
from contextlib import closing
from pathlib import Path
from .engine import Radar, Aggregator, summarize


def encode(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode('utf-8')


def summarize_curve(trades,initial,curve,prefix=None):
    if not prefix or not prefix['count']:
        return summarize(trades,initial,curve)
    peak=prefix['peak'];drawdown=prefix['drawdown']
    synthetic=[{'equity':peak},{'equity':peak*(1-drawdown)}]
    return summarize(trades,initial,synthetic+curve)


class FVGCheckpoint:
    def __init__(self,path,source_identity,cfg):
        self.path=Path(path)
        self.initial_equity=cfg['initial_equity']
        self.required_curve_count=0
        self.identity={'schema':1,'source_sha256':hashlib.sha256(source_identity.encode()).hexdigest(),
                       'config_sha256':Radar(cfg).config_hash,
                       'engine_sha256':hashlib.sha256(Path(__file__).with_name('engine.py').read_bytes()).hexdigest()}

    def archive_chart(self,radar,keep=240):
        """Keep the live chart bounded after durably archiving older closed M5 bars."""
        if len(radar.chart)<=keep:return 0
        older=radar.chart[:-keep]
        archive=self.path.with_name('fvg-chart.sqlite3')
        archive.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(archive)) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS bars (end INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
                identity=encode(self.identity).decode('utf-8')
                saved=db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
                if saved and saved[0]!=identity:raise ValueError('Архив графика FVG относится к другому источнику или версии правил')
                if not saved:db.execute("INSERT INTO meta(key,value) VALUES ('identity',?)",(identity,))
                for bar in older:
                    payload=encode(bar).decode('utf-8')
                    previous=db.execute('SELECT payload FROM bars WHERE end=?',(bar['end'],)).fetchone()
                    if previous and previous[0]!=payload:raise ValueError('Свеча в архиве графика FVG не совпадает со снимком')
                    if not previous:db.execute('INSERT INTO bars(end,payload) VALUES (?,?)',(bar['end'],payload))
        radar.chart=radar.chart[-keep:]
        return len(older)

    def archive_curve(self,radar,forward_since,keep=2400):
        """Archive old M1 equity points and retain exact drawdown state."""
        if len(radar.curve)<=keep:return 0
        older=radar.curve[:-keep]
        archive=self.path.with_name('fvg-curve.sqlite3')
        archive.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(archive)) as db:
            with db:
                db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                db.execute('CREATE TABLE IF NOT EXISTS points (time INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
                identity=encode({'checkpoint':self.identity,'forward_since':forward_since}).decode('utf-8')
                saved=db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
                if saved and saved[0]!=identity:raise ValueError('Архив кривой FVG относится к другому источнику или версии правил')
                if not saved:db.execute("INSERT INTO meta(key,value) VALUES ('identity',?)",(identity,))
                stats=db.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
                stats=json.loads(stats[0]) if stats else {'peak':self.initial_equity,'drawdown':0.0,'count':0,'last_time':None}
                for point in older:
                    payload=encode(point).decode('utf-8')
                    previous=db.execute('SELECT payload FROM points WHERE time=?',(point['time'],)).fetchone()
                    if previous:
                        if previous[0]!=payload:raise ValueError('Точка кривой FVG не совпадает с архивом')
                        continue
                    if stats['last_time'] is not None and point['time']<=stats['last_time']:
                        raise ValueError('Нарушен порядок архивной кривой FVG')
                    db.execute('INSERT INTO points(time,payload) VALUES (?,?)',(point['time'],payload))
                    stats['peak']=max(stats['peak'],point['equity'])
                    if stats['peak']>0:stats['drawdown']=max(stats['drawdown'],(stats['peak']-point['equity'])/stats['peak'])
                    stats['count']+=1;stats['last_time']=point['time']
                db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('stats',?)",(encode(stats).decode('utf-8'),))
        radar.curve=radar.curve[-keep:]
        self.required_curve_count=max(self.required_curve_count,stats['count'])
        return len(older)

    def curve_prefix(self,forward_since):
        archive=self.path.with_name('fvg-curve.sqlite3')
        if not archive.exists():
            if self.required_curve_count:raise ValueError('Отсутствует обязательный архив кривой FVG')
            return None,0
        with closing(sqlite3.connect(archive)) as db:
            saved=db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
            if not saved or saved[0]!=encode({'checkpoint':self.identity,'forward_since':forward_since}).decode('utf-8'):
                raise ValueError('Архив кривой FVG относится к другому источнику или версии правил')
            row=db.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
            stats=json.loads(row[0]) if row else None
            if not stats or stats['count']<self.required_curve_count:raise ValueError('Архив кривой FVG неполон')
            count=db.execute('SELECT COUNT(*) FROM points WHERE time>=?',(forward_since,)).fetchone()[0]
            return stats,count

    def _align_curve_archive(self,forward_since,required,first_retained_time):
        """Discard archive writes that were not committed by the JSON snapshot."""
        archive=self.path.with_name('fvg-curve.sqlite3')
        if not archive.exists():
            if required:raise ValueError('curve archive missing')
            return
        identity=encode({'checkpoint':self.identity,'forward_since':forward_since}).decode('utf-8')
        with closing(sqlite3.connect(archive)) as db:
            with db:
                saved=db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
                if not saved or saved[0]!=identity:raise ValueError('curve archive identity')
                count=db.execute('SELECT COUNT(*) FROM points').fetchone()[0]
                if count<required:raise ValueError('curve archive incomplete')
                if count>required:
                    rows=db.execute('SELECT time,payload FROM points ORDER BY time LIMIT ?', (required,)).fetchall()
                    peak=self.initial_equity;drawdown=0.0
                    for time_value,payload in rows:
                        point=json.loads(payload)
                        if point['time']!=time_value:raise ValueError('curve archive point')
                        peak=max(peak,point['equity'])
                        if peak>0:drawdown=max(drawdown,(peak-point['equity'])/peak)
                    last_time=rows[-1][0] if rows else None
                    if last_time is None:db.execute('DELETE FROM points')
                    else:db.execute('DELETE FROM points WHERE time>?',(last_time,))
                    stats={'peak':peak,'drawdown':drawdown,'count':required,'last_time':last_time}
                    db.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('stats',?)",
                               (encode(stats).decode('utf-8'),))
                else:
                    row=db.execute("SELECT value FROM meta WHERE key='stats'").fetchone()
                    stats=json.loads(row[0]) if row else None
                    last=db.execute('SELECT MAX(time) FROM points').fetchone()[0]
                    if not stats or stats['count']!=required or stats['last_time']!=last:
                        raise ValueError('curve archive stats')
                    last_time=last
                if first_retained_time is not None and last_time is not None and last_time>=first_retained_time:
                    raise ValueError('curve archive overlaps checkpoint')

    def save(self,radar,forward_since,processed_rows):
        state={k:v for k,v in vars(radar).items() if k not in ('agg','bars','manual_added','positions')}
        state['manual_added']=sorted(radar.manual_added)
        state['position_ids']=[p['id'] for p in radar.positions]
        state['bars']={str(n):list(b) for n,b in radar.bars.items()}
        state['agg']={str(n):{'bucket':a.bucket,'rows':a.rows} for n,a in radar.agg.items()}
        prefix,_=self.curve_prefix(forward_since)
        payload={'identity':self.identity,'forward_since':forward_since,'processed_rows':processed_rows,
                 'curve_archived_count':prefix['count'] if prefix else 0,'state':state}
        envelope={'sha256':hashlib.sha256(encode(payload)).hexdigest(),'payload':payload}
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temp=self.path.with_name(self.path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temp.open('wb') as f:f.write(encode(envelope));f.flush();os.fsync(f.fileno())
            for attempt in range(4):
                try:temp.replace(self.path);break
                except PermissionError:
                    if attempt==3:raise
                    time.sleep(.05*(attempt+1))
        finally:
            temp.unlink(missing_ok=True)

    def load(self,cfg):
        if not self.path.exists():return None
        try:
            envelope=json.loads(self.path.read_text(encoding='utf-8'));payload=envelope['payload']
            if hashlib.sha256(encode(payload)).hexdigest()!=envelope['sha256']:raise ValueError('checksum')
            if payload['identity']!=self.identity:raise ValueError('identity')
            required_archive=payload.get('curve_archived_count',0)
            state=payload['state'];radar=Radar(cfg)
            normal=set(vars(radar))-{'agg','bars','manual_added','positions'}
            if set(state)!=normal|{'agg','bars','manual_added','position_ids'}:raise ValueError('fields')
            for k in normal:setattr(radar,k,state[k])
            radar.manual_added=set(state['manual_added'])
            radar.bars={int(n):deque(b,maxlen=240) for n,b in state['bars'].items()}
            radar.agg={}
            for n,a in state['agg'].items():
                agg=Aggregator(int(n));agg.bucket=a['bucket'];agg.rows=a['rows'];radar.agg[int(n)]=agg
            trades={t['id']:t for t in radar.trades}
            if len(trades)!=len(radar.trades):raise ValueError('trade IDs')
            radar.positions=[trades[key] for key in state['position_ids']]
            if any(p.get('exit_time') is not None for p in radar.positions):raise ValueError('closed position')
            self._align_curve_archive(payload['forward_since'],required_archive,
                                      radar.curve[0]['time'] if radar.curve else None)
            self.required_curve_count=required_archive
            return radar,payload['forward_since'],payload['processed_rows']
        except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:
            raise ValueError('Снимок FVG повреждён или не соответствует источнику/правилам. Автоматический сброс запрещён: '+str(self.path)) from exc
