"""매매 전략. 각 전략은 날짜별 목표 포지션(1=보유, 0=현금)을 돌려준다.

규칙: i번째 신호는 bars[0..i]의 정보(그날 종가까지)만 사용해야 한다.
백테스트 엔진은 i번째 신호를 i+1번째 날 시가에 체결한다.
"""
from __future__ import annotations

from .data import Bar


class Strategy:
    name = "base"

    def signals(self, bars: list[Bar]) -> list[int]:
        raise NotImplementedError

    def describe(self) -> str:
        return self.name


def sma(values: list[float], n: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= n:
            total -= values[i - n]
        if i >= n - 1:
            out[i] = total / n
    return out


class BuyAndHold(Strategy):
    name = "hold"

    def signals(self, bars):
        return [1] * len(bars)

    def describe(self):
        return "매수 후 보유"


class MovingAverageCross(Strategy):
    """단기 이동평균이 장기 이동평균 위면 보유, 아래면 현금."""

    name = "ma"

    def __init__(self, short: int = 20, long: int = 60):
        if not 0 < short < long:
            raise ValueError("short는 0보다 크고 long보다 작아야 합니다")
        self.short, self.long = short, long

    def signals(self, bars):
        closes = [b.close for b in bars]
        s, l = sma(closes, self.short), sma(closes, self.long)
        return [1 if (a is not None and b is not None and a > b) else 0 for a, b in zip(s, l)]

    def describe(self):
        return f"이동평균 교차 ({self.short}일 / {self.long}일)"


class Momentum(Strategy):
    """최근 N일 수익률이 0보다 크면 보유."""

    name = "momentum"

    def __init__(self, lookback: int = 120):
        if lookback < 1:
            raise ValueError("lookback은 1 이상이어야 합니다")
        self.lookback = lookback

    def signals(self, bars):
        out = []
        for i, b in enumerate(bars):
            if i < self.lookback:
                out.append(0)
            else:
                out.append(1 if b.close > bars[i - self.lookback].close else 0)
        return out

    def describe(self):
        return f"모멘텀 ({self.lookback}일)"


STRATEGIES = {cls.name: cls for cls in (BuyAndHold, MovingAverageCross, Momentum)}
