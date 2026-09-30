# Forward-проверка Radar v2

Срез по сохранённым журналам. Повторить по сохранённому срезу: `python -X utf8 -m app.v2_forward_report --check-snapshot results/v2_forward_snapshot_20260929T064259Z_a71fd6`. Счётчики разных моделей не складываются в общую доходность.

| Модель | Начало forward | События | Своевременные / поздние / устаревшая цена | Планы | Подтверждённые планы | Без подтверждения пока | Закрытые условные исходы |
|---|---|---:|---:|---:|---:|---:|---:|
| FVG | 28.09.2026 06:45 UTC | 14 | 11 / 3 / 0 | 1 | 0 | 1 | 0 |
| SBR | 28.09.2026 17:04 UTC | 44 | 31 / 13 / 0 | 7 | 4 | 3 | не измеряются |
| RBS | 28.09.2026 17:04 UTC | 28 | 19 / 9 / 0 | 0 | 0 | 0 | не измеряются |

FVG: 0 закрытых, 0 открытых, 0 с разрывом, 1 исключены без своевременного подтверждения; средний R: нет данных, закрытая просадка: нет данных.

- **FVG:** состояния {"zone_created": 3, "child_found": 2, "cancelled": 5, "near": 1, "confirmed": 1, "paper_entry": 1, "position_closed": 1}; доставка {"sent": 4, "failed": 1}; завершились без подтверждения 0.
- **SBR:** состояния {"expired": 7, "broken": 7, "confirmed": 4, "signal_ready": 4, "level_known": 16, "retest_unconfirmed": 3, "context_rejected": 3}; доставка {"sent": 13, "suppressed": 1}; завершились без подтверждения 3.
- **RBS:** состояния {"level_known": 14, "context_rejected": 9, "expired": 3, "incomplete": 2}; доставка {"local_only": 2}; завершились без подтверждения 0.

Числа «без подтверждения пока» включают ещё действующие планы. Состояния доставки считаются отдельно от исходов. SBR/RBS работают в режиме `live_signal_only`, поэтому результат сделок и просадка для них здесь не вычисляются. При нуле закрытых FVG-исходов средний R и просадка остаются неизвестными.

## SHA-256 входов

- `live.json`: `e1cef366ccc1f4503d94d58760619506d16477db6d174343799d66dd44025ea0`
- `forward_events.jsonl`: `f4c0eb67dda22d76dabf85fb5aeedd54b908ec98c195d285c0cac988682402ea`
- `snr_sbr_live.json`: `74177613857adcb33745c600b9e8e81657118198278db7d45f2d8417043b10f1`
- `snr_sbr_forward_events.jsonl`: `4afc7e2cac05d82de02645198a2ce55370fd1eac0994f3fdff9bf8b5b0dc08da`
- `snr_rbs_live.json`: `d8a29b05d2a15538eb9e5ad3e096fb2d415c630204e3ee18b78d329e18c061d3`
- `snr_rbs_forward_events.jsonl`: `bb4d49f014a49a7a2075e93e66c7007f3e90838c5e4da49198a211319ca43b7e`
- выборка статусов SQLite: `efa2c1785ce550a9738500f0e8febdfc8ad43c0f262cca1b13a777a97a64d499`

