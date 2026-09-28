"""Recover idempotent alert insertion from durable signal journal records."""
import json


class ReplayPending(dict):
    """Replay the startup journal one record at a time, then handle new records."""
    def __init__(self, journal):
        super().__init__()
        self.journal = journal
        self.position = 0
        self.end = journal.stat().st_size if journal.exists() else 0

    def __bool__(self):
        return self.position < self.end or len(self) > 0

    def replay(self, store, now, enabled):
        if self.position >= self.end:
            return
        with self.journal.open('rb') as source:
            source.seek(self.position)
            while self.position < self.end:
                line = source.readline()
                if not line:
                    raise ValueError('Forward journal was truncated during recovery')
                if line.strip():
                    record = json.loads(line)
                    if record.get('delivery_alert'):
                        deliver(record, store, now, enabled)
                # Advance only after successful insertion; retries preserve this row.
                self.position = source.tell()


def deliver(record, store, now, enabled):
    if record.get('delivery_local_only'):
        store.add_local(record['delivery_alert'], record['received_at'])
        return
    store.add(record['delivery_alert'], now, record['quote_time'],
              enabled=bool(enabled and record.get('delivery_enabled', False)))

def remember(pending, record):
    if record.get('delivery_alert'):
        pending[record['dedup_key']]=record

def reconcile(pending, store, now, enabled):
    if isinstance(pending, ReplayPending):
        pending.replay(store, now, enabled)
    while len(pending):
        key = next(iter(pending))
        record = pending[key]
        # add rejects stale records and preserves existing IDs, including sent ones.
        deliver(record, store, now, enabled)
        del pending[key]
