"""Dukascopy bid/ask M1 archive. No credentials, no orders."""
import concurrent.futures, csv, datetime as dt, hashlib, json, lzma, math
import pathlib, struct, time, urllib.request, urllib.error

ROOT = pathlib.Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
FIELDS = ['time', 'bid_open', 'bid_high', 'bid_low', 'bid_close',
          'ask_open', 'ask_high', 'ask_low', 'ask_close', 'volume']

def parse_day(blob, day, factor=1000):
    raw = lzma.decompress(blob) if blob else b''
    if len(raw) % 24:
        raise ValueError('Некорректная длина архива свечей')
    base = int(dt.datetime.combine(day, dt.time(), UTC).timestamp())
    rows = {}
    for offset, o, c, lo, hi, volume in struct.iter_unpack('>5If', raw):
        if offset % 60 or not 0 <= offset < 86400:
            raise ValueError('Некорректная временная метка')
        vals = [o/factor, hi/factor, lo/factor, c/factor, volume]
        if not all(math.isfinite(v) for v in vals) or not 0 < lo <= min(o,c) <= max(o,c) <= hi:
            raise ValueError('Некорректная OHLC-свеча')
        if base + offset in rows:
            raise ValueError('Повтор свечи в архиве')
        rows[base + offset] = vals
    return rows

def fetch_day(day, side, cache, cache_only=False):
    # Dukascopy uses zero-based month in the archive path.
    relative = f'XAUUSD/{day.year}/{day.month-1:02}/{day.day:02}/{side}_candles_min_1.bi5'
    url = 'https://www.dukascopy.com/datafeed/' + relative
    path = cache / f'{day}_{side}.bi5'
    if path.exists():
        blob = path.read_bytes()
    else:
        if cache_only:
            return {}, {'date':str(day),'side':side,'url':url,'status':'not_downloaded','reason':'HTTP 429 при загрузке; использована доступная часть архива'}
        for attempt in range(5):
            try:
                with urllib.request.urlopen(url, timeout=25) as response:
                    blob = response.read()
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return {}, {'date':str(day),'side':side,'url':url,'status':'missing'}
                if attempt == 4: raise
                delay=min(60,max(5,int(exc.headers.get('Retry-After','15')))) if exc.code==429 else 2+attempt
                time.sleep(delay)
            except (OSError, TimeoutError):
                if attempt == 4: raise
                time.sleep(1 + attempt)
        # Validate before caching; HTML errors are never accepted as market data.
        parse_day(blob, day)
        path.write_bytes(blob)
    rows = parse_day(blob, day)
    return rows, {'date':str(day),'side':side,'url':url,'status':'ok',
                  'sha256':hashlib.sha256(blob).hexdigest(),'rows':len(rows)}

def download(start='2025-07-01', end='2025-10-01', cache_only=False):
    start, end = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    folder = ROOT/'data'; folder.mkdir(exist_ok=True)
    cache = folder/'raw'; cache.mkdir(exist_ok=True)
    days = [start+dt.timedelta(days=n) for n in range((end-start).days)]
    days = [d for d in days if d.weekday() < 5]
    results = {}; manifest = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        futures = {pool.submit(fetch_day,d,s,cache,cache_only):(d,s) for d in days for s in ('BID','ASK')}
        for n, future in enumerate(concurrent.futures.as_completed(futures),1):
            key=futures[future]; rows,meta=future.result(); results[key]=rows;manifest.append(meta)
            if n % 12 == 0: print(f'Архивы: {n}/{len(futures)}',flush=True)
    bars=[]; mismatch=0; zero_volume=0
    for day in days:
        bids, asks = results[(day,'BID')], results[(day,'ASK')]
        mismatch += len(set(bids)^set(asks))
        for t in sorted(set(bids)&set(asks)):
            b,a=bids[t],asks[t]
            if a[0] < b[0] or a[3] < b[3]:
                raise ValueError(f'Отрицательный bid/ask spread: {t}')
            if b[4] <= 0 or a[4] <= 0:
                zero_volume += 1
                continue
            bars.append(dict(zip(FIELDS,[t,*b[:4],*a[:4],b[4]])))
    if not bars: raise ValueError('Нет котировок')
    path=folder/'xauusd_m1.csv'
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(bars)
    missing=[r for r in manifest if r['status']!='ok']
    gaps=[{'after':a['time'],'minutes':(b['time']-a['time'])//60-1} for a,b in zip(bars,bars[1:]) if b['time']-a['time']>60]
    meta={'source':'Dukascopy','symbol':'XAUUSD','timeframe':'M1','timezone':'UTC',
          'requested_start':str(start),'requested_end_exclusive':str(end),
          'first':bars[0]['time'],'last':bars[-1]['time'],'rows':len(bars),
          'price_divisor':1000,'price_divisor_reference':'https://github.com/Leo4815162342/dukascopy-node/blob/v1.40.0/src/utils/instrument-meta-data/generated/instrument-meta-data.json',
          'downloaded_at':dt.datetime.now(UTC).isoformat(),'csv_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
          'bid_ask_unmatched_minutes':mismatch,'zero_volume_minutes_excluded':zero_volume,
          'missing_archives':missing,'gaps':gaps,'files':sorted(manifest,key=lambda x:(x['date'],x['side']))}
    (folder/'provenance.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2),encoding='utf-8')
    print(f'Сохранено {len(bars)} свечей; отсутствующих архивов: {len(missing)}',flush=True)
    return meta

def load_csv(path=None):
    path=path or ROOT/'data'/'xauusd_m1.csv'
    with pathlib.Path(path).open(encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        if not set(FIELDS)<=set(reader.fieldnames or []): raise ValueError('Ожидается CSV bid/ask M1 по документированному формату')
        rows=[{k:(int(r[k]) if k=='time' else float(r[k])) for k in FIELDS} for r in reader]
    for n,r in enumerate(rows):
        if not all(math.isfinite(v) for v in r.values()): raise ValueError('NaN/Infinity в котировках')
        if n and r['time']<=rows[n-1]['time']: raise ValueError('Нарушен порядок или есть дубликаты времени')
        if r['time']%60: raise ValueError('Свечи не выровнены по минутам')
        for s in ('bid','ask'):
            if not 0<r[s+'_low']<=min(r[s+'_open'],r[s+'_close'])<=max(r[s+'_open'],r[s+'_close'])<=r[s+'_high']:
                raise ValueError('Ошибка OHLC')
        if r['ask_open']<r['bid_open'] or r['ask_close']<r['bid_close']: raise ValueError('Отрицательный spread')
    return rows

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--start',default='2025-07-01');p.add_argument('--end',default='2025-10-01');p.add_argument('--cache-only',action='store_true')
    a=p.parse_args();download(a.start,a.end,a.cache_only)
