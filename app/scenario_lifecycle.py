"""Producer-owned presentation lifecycle for live scenario alerts.

The research model decides whether a setup confirms or ends. These deadlines
say how long its alert remains actionable in the human-facing workspace.
"""

VERSION = 1
M5_SECONDS = 5 * 60
SNR_RETEST_BARS = 12
SNR_RETEST_SECONDS = SNR_RETEST_BARS * M5_SECONDS
FVG_PLAN_SECONDS = 15 * 60
CONFIRMED_SECONDS = 15 * 60


def plan(model, when):
    if model == 'FVG':
        return {'version': VERSION, 'stage': 'near',
                'valid_until': when + FVG_PLAN_SECONDS,
                'expiry_reason': 'Истёк срок актуальности плана (15 минут); нужен новый анализ'}
    if model in ('SBR', 'RBS'):
        return {'version': VERSION, 'stage': 'waiting',
                'valid_until': when + SNR_RETEST_SECONDS,
                'expiry_reason': 'Истёк срок ожидания первого ретеста (12 M5); нужен новый план'}
    raise ValueError('Unknown scenario model')


def confirmed(when):
    return {'version': VERSION, 'stage': 'confirmed',
            'valid_until': when + CONFIRMED_SECONDS,
            'expiry_reason': 'Истёк срок актуальности подтверждения (15 минут); нужен новый анализ'}


def ended(when):
    return {'version': VERSION, 'stage': 'ended', 'valid_until': when}
