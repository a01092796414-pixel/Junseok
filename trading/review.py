"""'손실이 계속 이어지는가' 검토 도구.

모의매매 시스템을 과거 특정 시점에 켰다고 가정하고(재생), 매일 평가액을 따라가며
- 시작 금액 아래(마이너스)에 머문 날의 비율
- 마이너스가 가장 오래 이어진 기간
- 월별 수익이 마이너스였던 달의 수, 가장 긴 연속 마이너스 달
- 연속으로 손해 본 거래 수
를 계산한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from .backtest import Costs, run_backtest
from .data import Bar
from .simulate import Warmup
from .strategies import Strategy

COIN_COSTS = Costs(0.0005, 0.0, 0.001)


@dataclass
class Replay:
    strategy: str
    start: str
    end: str
    final: float
    ret: float
    below_days_pct: float      # 시작 금액보다 낮았던 날 비율
    longest_below: int         # 시작 금액 아래 연속 최장 일수
    mdd: float
    months: int
    neg_months: int
    longest_neg_months: int
    trades: int
    longest_losing_trades: int
    below_now: bool


def replay(bars: list[Bar], strategy: Strategy, days: int, cash: float = 50_000, warm: int = 120) -> Replay:
    chunk = bars[-(days + warm + 1):]
    r = run_backtest(chunk, Warmup(strategy, warm), initial_cash=cash, costs=COIN_COSTS,
                     fractional=True, min_order=5_000)
    curve = r.equity_curve[warm:]           # 시스템을 켠 날부터
    vals = [v for _, v in curve]
    below = [v < cash for v in vals]
    longest = run = 0
    for b in below:
        run = run + 1 if b else 0
        longest = max(longest, run)
    # 월별 수익
    month_end: dict[str, float] = {}
    month_start: dict[str, float] = {}
    for d, v in curve:
        k = f"{d.year}-{d.month:02d}"
        month_start.setdefault(k, v)
        month_end[k] = v
    keys = sorted(month_end)
    prev = None
    rets = []
    for k in keys:
        base = prev if prev is not None else month_start[k]
        rets.append(month_end[k] / base - 1)
        prev = month_end[k]
    neg = [x < -1e-9 for x in rets]
    lneg = run = 0
    for n in neg:
        run = run + 1 if n else 0
        lneg = max(lneg, run)
    closed = [t for t in r.trades if t.exit_date is not None]
    llose = run = 0
    for t in closed:
        run = run + 1 if t.pnl <= 0 else 0
        llose = max(llose, run)
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    return Replay(strategy.describe(), str(curve[0][0]), str(curve[-1][0]), vals[-1], vals[-1] / cash - 1,
                  sum(below) / len(below), longest, mdd, len(rets), sum(neg), lneg, len(closed), llose, below[-1])
