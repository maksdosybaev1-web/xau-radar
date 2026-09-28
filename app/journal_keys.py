"""Disk-backed deduplication index; the JSONL journal remains authoritative."""
from contextlib import closing
import json
import sqlite3


class JournalKeys:
    def __init__(self, journal, key_field='dedup_key'):
        self.path = journal.with_suffix(journal.suffix + '.keys.sqlite3')
        self.key_field = key_field
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS keys (key TEXT PRIMARY KEY) WITHOUT ROWID')
            db.execute('DELETE FROM keys')
            if journal.exists():
                with journal.open(encoding='utf-8') as source:
                    db.executemany('INSERT OR IGNORE INTO keys VALUES (?)',
                                   ((json.loads(line)[key_field],) for line in source if line.strip()))

    def __contains__(self, key):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT 1 FROM keys WHERE key = ?', (key,)).fetchone() is not None

    def __len__(self):
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute('SELECT COUNT(*) FROM keys').fetchone()[0]

    def add(self, key):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR IGNORE INTO keys VALUES (?)', (key,))
