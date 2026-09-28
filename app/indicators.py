"""Streaming indicators with SMA seeds; Wilder RSI/ATR. No look-ahead."""
from collections import deque
import statistics


class Average:
    def __init__(self, period, alpha=None):
        self.period=period;self.alpha=alpha if alpha is not None else 2/(period+1)
        self.seed=[];self.value=None

    def add(self, value):
        if value is None:return None
        if self.value is None:
            self.seed.append(value)
            if len(self.seed)==self.period:self.value=statistics.mean(self.seed);self.seed.clear()
        else:self.value+=self.alpha*(value-self.value)
        return self.value


class Indicators:
    def __init__(self):
        self.ema={n:Average(n) for n in (12,20,26,50,200)}
        self.signal=Average(9);self.gain=Average(14,1/14);self.loss=Average(14,1/14)
        self.atr=Average(14,1/14);self.previous=None
        self.volumes=deque(maxlen=20);self.atrs=deque(maxlen=100)

    def add(self, bar):
        close=bar['close'];previous=self.previous
        emas={n:a.add(close) for n,a in self.ema.items()}
        rsi=None
        if previous is not None:
            change=close-previous;g=self.gain.add(max(change,0));l=self.loss.add(max(-change,0))
            if g is not None:rsi=50 if g==l==0 else 100 if l==0 else 100-100/(1+g/l)
        tr=bar['high']-bar['low'] if previous is None else max(bar['high']-bar['low'],abs(bar['high']-previous),abs(bar['low']-previous))
        atr=self.atr.add(tr)
        macd=emas[12]-emas[26] if emas[26] is not None else None
        signal=self.signal.add(macd)
        base=statistics.mean(self.volumes) if len(self.volumes)==20 else None
        volume_ratio=bar['volume']/base if base and base>0 else None
        atr_base=statistics.median(self.atrs) if len(self.atrs)==100 else None
        atr_ratio=atr/atr_base if atr_base and atr_base>0 else None
        self.volumes.append(bar['volume'])
        if atr is not None:self.atrs.append(atr)
        self.previous=close
        return {'ema20':emas[20],'ema50':emas[50],'ema200':emas[200],
                'rsi':rsi,'macd':macd,'macd_signal':signal,'macd_hist':macd-signal if signal is not None else None,
                'atr':atr,'atr_ratio':atr_ratio,'volume_ratio':volume_ratio}
