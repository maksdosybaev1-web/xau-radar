"""Optional local Ollama commentary. Never changes strategy decisions."""
import datetime as dt, hashlib, json, os, urllib.request, urllib.error
from .datafeed import ROOT

PROMPT='''Перефразируй запись на русском в 3–4 коротких предложениях. Обязательно упомяни направление H1, что M15-зона находится внутри H1, что именно закрытая свеча M5 коснулась M15-зоны и вернулась в сторону сигнала, а также стоп. Сохрани числа. Не добавляй иных фактов, прогноза или оценки вероятности. Не описывай собственный процесс рассуждения.'''

def snapshot(result,event_id):
    event=next((x for x in result['events'] if x['id']==event_id),None)
    if not event:raise ValueError('Событие не найдено')
    t=event['time']
    fields=('type','direction','low','high','child_low','child_high','stop','explanation','reason')
    return {'symbol':'XAUUSD','as_of':t,'as_of_utc':dt.datetime.fromtimestamp(t,dt.timezone.utc).isoformat(),
            'event':{k:event[k] for k in fields if k in event},
            'closed_m5':[{k:b[k] for k in ('end','open','high','low','close') if k in b}
                         for b in [b for b in result['bars'] if b['end']<=t][-3:]],
            'notice':'Будущие свечи, результат сделки и будущие события исключены.'}

def status():
    model=os.environ.get('RADAR_OLLAMA_MODEL','')
    return {'configured':bool(model),'model':model or None,'mode':'advisory_only',
            'message':'Модель подключена для объяснений' if model else 'ИИ-модель не настроена. Объяснения условий сейчас формирует алгоритм.'}

def verified_explanation(event):
    direction='покупка' if event.get('direction')=='buy' else 'продажа' if event.get('direction')=='sell' else 'не указано'
    statement=(f"Направление сигнала: {direction}. {event.get('explanation',event.get('reason',''))} Стоп: {event['stop']:.2f}. "
               if event.get('stop') is not None else event.get('reason',''))
    return statement+' Это только сигнал учебной симуляции, не исполненная сделка.'

def explain(result,event_id):
    model=os.environ.get('RADAR_OLLAMA_MODEL','')
    if not model:raise ValueError('Задайте RADAR_OLLAMA_MODEL для установленной локальной модели Ollama. Текущие объяснения — алгоритмические, не ИИ.')
    evidence=snapshot(result,event_id);serialized=json.dumps(evidence,ensure_ascii=False,sort_keys=True)
    event=evidence['event']
    if event.get('type')!='confirmed':raise ValueError('ИИ-пояснение доступно только для подтверждённых сигналов')
    model_input=verified_explanation(event)
    key=hashlib.sha256((PROMPT+model+serialized+model_input+'think=false').encode()).hexdigest()
    folder=ROOT/'results'/'ai';folder.mkdir(parents=True,exist_ok=True);path=folder/(key+'.json')
    if path.exists():
        cached=json.loads(path.read_text(encoding='utf-8'))
        return dict(cached, verified_facts=model_input)
    payload={'model':model,'stream':False,'think':False,'options':{'temperature':0,'num_predict':500},
             'messages':[{'role':'system','content':PROMPT},{'role':'user','content':model_input}]}
    request=urllib.request.Request('http://127.0.0.1:11434/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=90) as r:answer=json.load(r)
    except (OSError,ValueError) as exc:raise ValueError('Не удалось получить ответ локальной Ollama. Проверьте её запуск и имя модели.') from exc
    content=answer.get('message',{}).get('content','').strip()
    if not content:raise ValueError('Локальная модель не вернула объяснение. Повторите запрос или выберите другую модель.')
    out={'text':content,'model':model,'snapshot_sha256':hashlib.sha256(serialized.encode()).hexdigest(),
         'event_id':event_id,'as_of':evidence['as_of'],
         'verified_facts':model_input,
         'role':'Проверенные условия показаны отдельно; модель не меняет сигнал.'}
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');return out
