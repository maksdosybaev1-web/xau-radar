"""Preliminary price cards from levels already known at a live event."""

import math
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


def _on_tick(value, step, rounding):
    step = Decimal(str(step))
    return (Decimal(str(value)) / step).to_integral_value(rounding=rounding) * step


def _levels(low, high, stop, buy, price_step):
    """Widen the entry and stop outward; round objectives conservatively."""
    if not math.isfinite(price_step) or price_step <= 0:
        return None
    low = _on_tick(low, price_step, ROUND_FLOOR)
    high = _on_tick(high, price_step, ROUND_CEILING)
    stop = _on_tick(stop, price_step, ROUND_FLOOR if buy else ROUND_CEILING)
    midpoint = (low + high) / 2
    risk = midpoint - stop if buy else stop - midpoint
    rounding = ROUND_FLOOR if buy else ROUND_CEILING
    targets = [_on_tick(midpoint + (1 if buy else -1) * risk * n,
                        price_step, rounding) for n in (1, 2, 3)]
    if not (low < high and (stop < low and high < targets[0] < targets[1] < targets[2]
                           if buy else targets[2] < targets[1] < targets[0] < low and high < stop)):
        return None
    return [float(low), float(high)], float(stop), [float(x) for x in targets]


def _price(value):
    return format(Decimal(str(value)).normalize(), 'f')


def fvg_near_plan(event, bid, ask, price_step=0.01):
    if event['type'] != 'near' or bid is None or ask is None:
        return None
    buy = event.get('direction') == 'buy'
    if not buy and event.get('direction') != 'sell':
        return None
    low, high = event.get('child_low'), event.get('child_high')
    parent_low, parent_high = event.get('low'), event.get('high')
    if any(value is None or not math.isfinite(value) for value in
           (low, high, parent_low, parent_high, bid, ask)):
        return None
    if not 0 < parent_low <= low < high <= parent_high or bid > ask:
        return None
    width = high - low
    stop = parent_low - .1 * width if buy else parent_high + .1 * width
    levels = _levels(low, high, stop, buy, price_step)
    if levels is None:
        return None
    entry, stop, targets = levels
    if bid >= targets[0] if buy else ask <= targets[0]:
        return None
    edge_text = _price(entry[0] if buy else entry[1])
    return {'side': 'long' if buy else 'short', 'entry': entry,
            'stop': stop, 'targets': targets,
            'cancel_rule': f'закрытие M5 {"ниже" if buy else "выше"} {edge_text}',
            'price_step': price_step,
            'target_method': 'midpoint_r_1_2_3',
            'stop_method': 'h1_edge_plus_child_width_buffer',
            'known_at': event['time']}


def snr_break_plan(level, direction, when, price_step=0.01):
    if direction not in ('buy', 'sell'):
        return None
    low, high, volatility = level.get('low_band'), level.get('high_band'), level.get('atr')
    if any(value is None or not math.isfinite(value) for value in (low, high, volatility)):
        return None
    if not 0 < low < high or volatility <= 0:
        return None
    buy = direction == 'buy'
    stop = low - .1 * volatility if buy else high + .1 * volatility
    levels = _levels(low, high, stop, buy, price_step)
    if levels is None:
        return None
    entry, stop, targets = levels
    edge_text = _price(entry[0] if buy else entry[1])
    return {'side': 'long' if buy else 'short', 'entry': entry,
            'stop': stop, 'targets': targets, 'retest': True,
            'cancel_rule': f'закрытие M5 {"ниже" if buy else "выше"} {edge_text}',
            'price_step': price_step,
            'target_method': 'midpoint_r_1_2_3',
            'stop_method': 'band_edge_plus_atr_buffer',
            'known_at': when}


def snr_terminal_reason(event):
    reasons = {
        'retest_deadline': 'Истёк срок ожидания первого ретеста.',
        'h1_context_changed': 'Контекст H1 изменился после пробоя.',
        'close_above_band': 'Свеча M5 закрылась выше полосы пробитой поддержки.',
        'close_below_band': 'Свеча M5 закрылась ниже полосы пробитого сопротивления.',
        'first_retest_failed': 'Первый ретест не подтвердил сценарий.',
        'no_known_target': 'Ретест был, но заранее подтверждённой структурной цели M15 нет. Предварительные ТП были расчётными ориентирами.',
        'm1_gap': 'Разрыв в M1-данных прервал наблюдение.',
    }
    if event.get('state') not in ('expired', 'context_rejected', 'invalidated',
                                  'retest_unconfirmed', 'entry_rejected', 'incomplete'):
        return None
    return reasons.get(event.get('reason'))
