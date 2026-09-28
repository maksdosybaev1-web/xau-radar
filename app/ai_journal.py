"""One-off, read-only Ollama review of a frozen research journal."""
import argparse
import collections
import hashlib
import json
import math
import os
import urllib.request

from .datafeed import ROOT
from .research import save_json


SYSTEM = ('Ты редактируешь краткий исследовательский разбор на русском. Верни JSON '
          '{"dominant_reasons":{"touch_limit":"...","after_close":"..."},'
          '"observations":["...","..."]}. Скопируй ключи главных причин точно из поля '
          'dominant_reasons входных данных. В двух или трёх предложениях верно объясни '
          'их по отдельности и укажи недостаток данных для сравнения. '
          'Не объявляй редкую причину главной. Не придумывай причину движений цен. '
          'Не пиши числа, торговые рекомендации, прогнозы и заявления о преимуществе режима.')


def snapshot(run, forward):
    experiments = run['experiments']
    modes = {}
    closed_ids={}
    if any(x['mode'] not in ('touch_limit','after_close') for x in experiments):
        raise ValueError('Неизвестный режим опыта')
    for mode in ('touch_limit', 'after_close'):
        rows = [x for x in experiments if x['mode']==mode]
        states = dict(collections.Counter(x['state'] for x in rows))
        reasons = dict(collections.Counter(x.get('reason', x.get('exit_reason', ''))
                                           for x in rows if x['state']!='closed'))
        summary = run['summary']['by_mode'][mode]
        closed=[x for x in rows if x['state']=='closed']
        if any(not isinstance(x.get('r'),(int,float)) or not math.isfinite(x['r']) for x in closed):
            raise ValueError('Некорректный результат R')
        closed_ids[mode]={x['candidate_id'] for x in closed}
        if len(closed_ids[mode])!=len(closed):raise ValueError('Повтор закрытого кандидата')
        wins=sum(x['r']>0 for x in closed)
        average=sum(x['r'] for x in closed)/len(closed) if closed else None
        saved_average=summary['average_r']
        average_matches=(saved_average is None if average is None else
                         isinstance(saved_average,(int,float)) and math.isfinite(saved_average) and
                         math.isclose(average,saved_average,rel_tol=1e-10,abs_tol=1e-10))
        if len(rows)!=summary['total'] or states.get('closed',0)!=summary['closed']:
            raise ValueError('Итоговый JSON не совпадает с журналом опытов')
        if wins!=summary['wins'] or not average_matches:
            raise ValueError('Итоговый JSON не совпадает с журналом опытов: wins/average_r')
        modes[mode] = {'total':len(rows), 'states':states, 'reasons':reasons,
                       'wins':wins, 'average_r':average}
    paired=len(closed_ids['touch_limit'] & closed_ids['after_close'])
    if paired!=run['summary']['paired_closed']:
        raise ValueError('Итоговый JSON не совпадает с журналом опытов: paired_closed')
    return {'research_version':run['version'], 'source_hash':run['source_hash'],
            'paired_closed':paired, 'modes':modes,
            'forward':{'forward_since':forward['forward_since'],
                       'quote_time':forward['quote_time'], 'stale':forward['stale'],
                       'events':forward['forward_events_total'],
                       'paper_outcomes':forward['paper_outcomes_total'],
                       'tick_checks':forward['tick_checks_total']},
            'boundary':'Исторические независимые условные опыты; без кривой портфеля, '
                       'брокерского исполнения и новых результатов.'}


def ask_ollama(model, facts):
    dominant={mode:max(data['reasons'],key=data['reasons'].get) if data['reasons'] else None
              for mode,data in facts['modes'].items()}
    model_facts={'dominant_reasons':dominant,
                 'rejection_counts':{mode:data['reasons'] for mode,data in facts['modes'].items()},
                 'paired_closed':facts['paired_closed'], 'forward':facts['forward'],
                 'boundary':facts['boundary']}
    payload={'model':model, 'stream':False, 'think':False, 'format':'json',
             'options':{'temperature':0, 'num_predict':450},
             'messages':[{'role':'system','content':SYSTEM},
                         {'role':'user','content':json.dumps(model_facts,ensure_ascii=False,sort_keys=True)}]}
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',
        data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(request,timeout=120) as response:
        answer=json.load(response)
    if not isinstance(answer,dict) or not isinstance(answer.get('message'),dict):
        raise ValueError('Некорректный ответ Ollama')
    parsed=json.loads(answer['message']['content'])
    return validate_review(parsed, dominant)


def validate_review(parsed, dominant):
    if not isinstance(parsed,dict):raise ValueError('Ответ модели должен быть JSON-объектом')
    if parsed.get('dominant_reasons') != dominant:
        raise ValueError('ИИ неверно определил главные причины отсевов; разбор не сохранён')
    observations=parsed.get('observations')
    if not isinstance(observations,list) or not 2<=len(observations)<=3 or not all(
            isinstance(item,str) and 10<=len(item)<=350 for item in observations):
        raise ValueError('Ollama не вернула краткий разбор журнала в ожидаемом формате')
    return observations


def verified_observations(facts):
    labels={'spread_guard':'фильтр спреда','limit_crossed_at_open':'открытие за уровнем',
            'first_return_not_confirmed':'нет подтверждения первого возврата','missing_next_m1':'нет следующей M1'}
    out=[]
    for mode,title in [('touch_limit','Касание/лимит'),('after_close','После закрытия')]:
        reasons=facts['modes'][mode]['reasons']
        if not reasons:out.append(title+': незавершённых или отсеянных опытов нет.');continue
        maximum=max(reasons.values())
        leaders=sorted(k for k,v in reasons.items() if v==maximum)
        out.append(title+': наиболее частые причины незакрытых опытов — '+
                   ', '.join(labels.get(k,k or 'причина не указана') for k in leaders)+f' (по {maximum}).')
    out.append(f'Попарно закрытых опытов: {facts["paired_closed"]}; новых расчётных исходов: '
               f'{facts["forward"]["paper_outcomes"]}. Эти счётчики сами по себе не доказывают пользу ИИ-фильтра.')
    return out


def render(facts, observations, model, digest, status):
    modes=facts['modes']
    lines=['# ИИ-разбор исследовательского журнала', '',
           'Локальная модель: `'+model+'`. SHA256 исходного результата: `'+digest+'`.',
           'Статус итогового прогона: '+('черновик модели, требуется ручная сверка' if status=='schema_passed' else
                                        'текст модели не использован')+'.',
           'Это ретроспективный разбор уже известных условных исходов. Модель не отбирала '
           'сигналы до их появления и не меняла радар.', '',
           '| Факт из журнала | Касание/лимит | После закрытия |',
           '|---|---:|---:|']
    for title,key in [('Рассмотрено', 'total'), ('Закрыто', 'closed'), ('Отклонено', 'rejected'), ('Не допущено к входу','not_eligible')]:
        lines.append('| '+title+' | '+str(modes['touch_limit']['states'].get(key, modes['touch_limit'].get(key,0)))+
                     ' | '+str(modes['after_close']['states'].get(key, modes['after_close'].get(key,0)))+' |')
    lines.extend(['',f'Попарно закрытых опытов: **{facts["paired_closed"]}**. '
                  f'Forward-событий: **{facts["forward"]["events"]}**, '
                  f'расчётных исходов: **{facts["forward"]["paper_outcomes"]}**, '
                  f'тиковых проверок: **{facts["forward"]["tick_checks"]}**. '
                  f'Котировка устарела: **{"да" if facts["forward"]["stale"] else "нет"}**.',
                  '', '## '+('Черновик модели' if status=='schema_passed' else
                             'Проверенные наблюдения без текста модели'), ''])
    lines.extend('- '+item for item in observations)
    lines.extend(['', '## Предел вывода', '',
                  ('Поля главных причин прошли проверку; свободный текст модели требует отдельной сверки и не управляет сигналами. '
                   if status=='schema_passed' else
                   'Ответ модели не прошёл проверку главных причин или не запускался; выше приведена числовая сводка из журнала. ')+
                  'Для сравнения отбора нужны заранее зафиксированные решения по новым сигналам '
                  'и независимая оценка исходов с учётом ограничений портфеля. '
                  f'Попарно закрытых опытов в этой выборке: {facts["paired_closed"]}; '
                  f'новых расчётных исходов: {facts["forward"]["paper_outcomes"]}.', ''])
    return '\n'.join(lines)


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--model',default=os.environ.get('RADAR_OLLAMA_MODEL','qwen2.5:7b'))
    p.add_argument('--no-model',action='store_true',help='Сформировать проверенную сводку без нового вызова Ollama')
    args=p.parse_args()
    source=ROOT/'results'/'entry_modes_outcomes_run.json'
    raw=source.read_bytes()
    run=json.loads(raw)
    forward=json.loads((ROOT/'results'/'nested_forward_live.json').read_text(encoding='utf-8'))
    facts=snapshot(run,forward)
    status='not_run' if args.no_model else 'schema_passed'
    if args.no_model:
        observations=verified_observations(facts)
    else:
        try:
            observations=ask_ollama(args.model,facts)
        except (ValueError,OSError,KeyError,TypeError) as exc:
            status='rejected'
            observations=verified_observations(facts)
            print('Ответ ИИ отклонён: '+str(exc))
    digest=hashlib.sha256(raw).hexdigest()
    folder=ROOT/'results'
    save_json(folder/'ai_journal_review.json',
              {'model':args.model,'source_sha256':digest,'facts':facts,
               'observations':observations,'model_status':status,
               'role':'retrospective_explanation_only'})
    (folder/'ai_journal_review.md').write_text(
        render(facts,observations,args.model,digest,status),encoding='utf-8')
    print(folder/'ai_journal_review.md')


if __name__=='__main__':
    main()
