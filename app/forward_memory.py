"""Discard inactive live SNR levels after their events have been handled."""

TERMINAL_STATES = frozenset({
    'expired', 'context_rejected', 'invalidated', 'retest_unconfirmed',
    'entry_rejected', 'signal_ready', 'incomplete', 'closed',
})


def prune_finished_levels(model):
    if not model.signals_only:
        return
    observing = {trade['level_id'] for trade in model.trades
                 if trade['state'] == 'observing'}
    for key, level in list(model.levels.items()):
        if level.get('state') in TERMINAL_STATES and key not in observing:
            del model.levels[key]
