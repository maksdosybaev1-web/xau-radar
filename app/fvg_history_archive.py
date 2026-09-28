"""Offline, lossless checkpoint archive. Does not change the live engine state."""
import argparse
from contextlib import closing
import copy
import hashlib
import json
from pathlib import Path
import sqlite3

from .fvg_checkpoint import encode


HISTORY = ('events', 'trades', 'curve', 'zones', 'children', 'chart')


def checked(envelope):
    payload = envelope['payload']
    if hashlib.sha256(encode(payload)).hexdigest() != envelope['sha256']:
        raise ValueError('Checkpoint checksum mismatch')
    if payload['identity']['schema'] != 1:
        raise ValueError('Unsupported checkpoint schema')
    if any(not isinstance(payload['state'][key], list) for key in HISTORY):
        raise ValueError('Invalid checkpoint history')
    return payload


def _restore(db, digest):
    row = db.execute('SELECT metadata FROM snapshots WHERE digest=?', (digest,)).fetchone()
    if row is None:
        raise ValueError('Archived snapshot not found')
    payload = json.loads(row[0])
    for kind in HISTORY:
        rows = db.execute('SELECT ordinal,payload FROM history WHERE digest=? AND kind=? ORDER BY ordinal',
                          (digest, kind))
        items = []
        for expected, (ordinal, item) in enumerate(rows):
            if ordinal != expected:
                raise ValueError('Archive sequence gap')
            items.append(json.loads(item))
        payload['state'][kind] = items
    envelope = {'sha256': digest, 'payload': payload}
    checked(envelope)
    return envelope


def restore(archive, digest):
    uri = Path(archive).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return _restore(db, digest)


def _curve_companion(checkpoint, payload):
    required = payload.get('curve_archived_count', 0)
    if not required:
        return None
    source = checkpoint.with_name('fvg-curve.sqlite3')
    if not source.exists():
        raise ValueError('Required FVG curve archive is missing')
    identity = encode({'checkpoint':payload['identity'],
                       'forward_since':payload['forward_since']}).decode('utf-8')
    uri = source.resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as origin:
        row = origin.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        if not row or row[0] != identity:
            raise ValueError('FVG curve archive belongs to another source or session')
        points = origin.execute('SELECT time,payload FROM points ORDER BY time LIMIT ?', (required,)).fetchall()
    if len(points) != required:
        raise ValueError('FVG curve archive is incomplete')
    if payload['state']['curve'] and points[-1][0] >= payload['state']['curve'][0]['time']:
        raise ValueError('FVG curve archive overlaps checkpoint tail')
    peak = payload['state']['cfg']['initial_equity']
    drawdown = 0.0
    with closing(sqlite3.connect(':memory:')) as db:
        with db:
            db.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
            db.execute('CREATE TABLE points (time INTEGER PRIMARY KEY, payload TEXT NOT NULL)')
            for time, item in points:
                point = json.loads(item)
                if point['time'] != time:
                    raise ValueError('FVG curve archive has an invalid point')
                db.execute('INSERT INTO points(time,payload) VALUES (?,?)', (time,item))
                peak = max(peak, point['equity'])
                if peak > 0:
                    drawdown = max(drawdown, (peak-point['equity'])/peak)
            stats = {'peak':peak,'drawdown':drawdown,'count':required,'last_time':points[-1][0]}
            db.execute("INSERT INTO meta(key,value) VALUES ('identity',?)", (identity,))
            db.execute("INSERT INTO meta(key,value) VALUES ('stats',?)", (encode(stats).decode('utf-8'),))
        return db.serialize()


def restore_bundle(archive, digest, directory):
    """Restore a checkpoint with the curve archive needed for live resumption."""
    directory = Path(directory)
    checkpoint = directory/'fvg-checkpoint.json'
    curve = directory/'fvg-curve.sqlite3'
    if checkpoint.exists() or curve.exists():
        raise ValueError('Restore destination already contains FVG state')
    uri = Path(archive).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        envelope = _restore(db, digest)
        required = envelope['payload'].get('curve_archived_count', 0)
        row = db.execute('SELECT payload,sha256 FROM companions WHERE digest=? AND kind=?',
                         (digest,'curve')).fetchone() if required else None
        if required and not row:
            raise ValueError('Archived checkpoint has no required curve companion')
        if row and hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise ValueError('Archived curve companion checksum mismatch')
        if row:
            with closing(sqlite3.connect(':memory:')) as curve_db:
                curve_db.deserialize(row[0])
                identity = encode({'checkpoint':envelope['payload']['identity'],
                                   'forward_since':envelope['payload']['forward_since']}).decode('utf-8')
                saved = curve_db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
                count = curve_db.execute('SELECT COUNT(*) FROM points').fetchone()[0]
                if not saved or saved[0] != identity or count != required:
                    raise ValueError('Archived curve companion does not match checkpoint')
    directory.mkdir(parents=True, exist_ok=True)
    if row:
        curve.write_bytes(row[0])
    checkpoint.write_bytes(encode(envelope))
    return {'checkpoint':str(checkpoint),'curve':str(curve) if row else None,'sha256':digest}


def export(checkpoint, archive):
    checkpoint, archive = Path(checkpoint), Path(archive)
    if checkpoint.resolve() == archive.resolve():
        raise ValueError('Archive must be separate from checkpoint')
    envelope = json.loads(checkpoint.read_text(encoding='utf-8'))
    payload = checked(envelope)
    companion = _curve_companion(checkpoint, payload)
    digest = envelope['sha256']
    metadata = copy.deepcopy(payload)
    for kind in HISTORY:
        del metadata['state'][kind]
    archive.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(archive)) as db, db:
        db.execute('CREATE TABLE IF NOT EXISTS snapshots (digest TEXT PRIMARY KEY, metadata TEXT NOT NULL)')
        db.execute('CREATE TABLE IF NOT EXISTS history (digest TEXT NOT NULL, kind TEXT NOT NULL, '
                   'ordinal INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(digest,kind,ordinal))')
        db.execute('CREATE TABLE IF NOT EXISTS companions (digest TEXT NOT NULL, kind TEXT NOT NULL, '
                   'payload BLOB NOT NULL, sha256 TEXT NOT NULL, PRIMARY KEY(digest,kind))')
        exists = db.execute('SELECT 1 FROM snapshots WHERE digest=?', (digest,)).fetchone()
        if not exists:
            db.execute('INSERT INTO snapshots VALUES (?,?)', (digest, encode(metadata).decode('utf-8')))
            for kind in HISTORY:
                db.executemany('INSERT INTO history VALUES (?,?,?,?)',
                               ((digest, kind, i, encode(item).decode('utf-8'))
                                for i, item in enumerate(payload['state'][kind])))
            if companion:
                db.execute('INSERT INTO companions VALUES (?,?,?,?)',
                           (digest,'curve',companion,hashlib.sha256(companion).hexdigest()))
        if _restore(db, digest) != envelope:
            raise ValueError('Archive round-trip mismatch')
        if companion:
            saved=db.execute('SELECT payload,sha256 FROM companions WHERE digest=? AND kind=?',
                             (digest,'curve')).fetchone()
            if not saved or hashlib.sha256(saved[0]).hexdigest()!=saved[1] or saved[0]!=companion:
                raise ValueError('Archive curve companion mismatch')
    return {'sha256': digest, 'verified': True,
            'counts': {kind: len(payload['state'][kind]) for kind in HISTORY},
            'curve_archived_count':payload.get('curve_archived_count',0)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Export and verify an offline FVG checkpoint archive')
    parser.add_argument('--checkpoint')
    parser.add_argument('--archive', required=True)
    parser.add_argument('--restore-digest')
    parser.add_argument('--directory')
    args = parser.parse_args()
    if args.restore_digest:
        if not args.directory or args.checkpoint:
            parser.error('Restoration requires --restore-digest and --directory, without --checkpoint')
        result=restore_bundle(args.archive,args.restore_digest,args.directory)
    else:
        if not args.checkpoint or args.directory:
            parser.error('Export requires --checkpoint, without --directory')
        result=export(args.checkpoint,args.archive)
    print(json.dumps(result,ensure_ascii=False))
