"""소액 모의투자 몬테카를로 시뮬레이션.

실제 시세 대신, 시장과 비슷한 성질(변동성, 가끔 오는 큰 급등락, 해마다 다른 추세)을 가진
가짜 가격 흐름을 여러 개 만들어 같은 전략을 반복 실행한다.
결과는 "미래 예측"이 아니라 "이 가정 아래에서 결과가 어느 범위로 흩어지는지"를 보여준다.
"""
from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass
from datetime import date, timedelta

from .backtest import Costs, run_backtest
from .data import Bar
from .strategies import BuyAndHold, MovingAverageCross, Momentum, Strategy


@dataclass(frozen=True)
class MarketProfile:
    key: str
    label: str
    start_price: float
    daily_vol: float          # 하루 변동성(표준편차)
    drift_mean: float         # 연간 기대 추세(경로마다 이 값 주변에서 무작위로 뽑음)
    drift_sd: float           # 경로별 연간 추세의 흩어짐 (상승장/하락장 섞기)
    days_per_year: int        # ETF 250 거래일, 코인 365일
    costs: Costs
    fractional: bool
    min_order: float
    tail_df: float = 4.0      # 꼬리 두께 (작을수록 급등락이 잦음)


PROFILES = {
    "etf": MarketProfile(
        key="etf", label="국내 주식형 ETF (1주 약 15,000원 가정)", start_price=15_000,
        daily_vol=0.011, drift_mean=0.06, drift_sd=0.15, days_per_year=250,
        costs=Costs(fee_rate=0.00015, sell_tax=0.0, slippage=0.0005),
        fractional=False, min_order=0),
    "coin": MarketProfile(
        key="coin", label="비트코인류 코인 (소수점 매수, 최소 주문 5,000원)", start_price=100_000_000,
        daily_vol=0.035, drift_mean=0.10, drift_sd=0.60, days_per_year=365,
        costs=Costs(fee_rate=0.0005, sell_tax=0.0, slippage=0.001),
        fractional=True, min_order=5_000),
}


def _student_t(rng: random.Random, df: float) -> float:
    """분산 1로 맞춘 t분포 난수 (정규분포보다 급등락이 잦음)."""
    z = rng.gauss(0, 1)
    chi2 = sum(rng.gauss(0, 1) ** 2 for _ in range(int(df)))
    t = z / math.sqrt(chi2 / df)
    return t / math.sqrt(df / (df - 2))


def simulate_path(profile: MarketProfile, days: int, rng: random.Random) -> list[Bar]:
    annual = rng.gauss(profile.drift_mean, profile.drift_sd)
    mu = annual / profile.days_per_year - 0.5 * profile.daily_vol ** 2
    price, d, bars = profile.start_price, date(2026, 1, 1), []
    gap_vol = profile.daily_vol * (0.3 if profile.days_per_year == 250 else 0.05)
    while len(bars) < days:
        if profile.days_per_year == 250 and d.weekday() >= 5:
            d += timedelta(days=1)
            continue
        o = price * math.exp(rng.gauss(0, gap_vol))
        c = price * math.exp(mu + profile.daily_vol * _student_t(rng, profile.tail_df))
        hi = max(o, c) * (1 + abs(rng.gauss(0, profile.daily_vol / 3)))
        lo = min(o, c) * (1 - abs(rng.gauss(0, profile.daily_vol / 3)))
        bars.append(Bar(d, o, hi, lo, c))
        price = c
        d += timedelta(days=1)
    return bars


class Warmup(Strategy):
    """앞부분 n일은 지표 계산용으로만 쓰고 매매하지 않는다 (모든 전략이 같은 날 시작)."""

    def __init__(self, inner: Strategy, n: int):
        self.inner, self.n = inner, n
        self.name = inner.name

    def signals(self, bars):
        sig = self.inner.signals(bars)
        return [0 if i < self.n else s for i, s in enumerate(sig)]

    def describe(self):
        return self.inner.describe()


@dataclass
class Summary:
    strategy: str
    runs: int
    median_final: float
    p05_final: float
    p95_final: float
    loss_prob: float
    median_mdd: float
    p05_mdd: float
    avg_trades: float
    avg_cost: float
    beat_hold: float | None = None


def _pct(values: list[float], q: float) -> float:
    v = sorted(values)
    k = (len(v) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return v[lo] + (v[hi] - v[lo]) * (k - lo)


def run_simulation(profile_key: str = "etf", runs: int = 500, years: float = 1.0,
                   cash: float = 50_000, seed: int = 7,
                   strategies: list[Strategy] | None = None) -> tuple[MarketProfile, list[Summary]]:
    profile = PROFILES[profile_key]
    strategies = strategies or [MovingAverageCross(20, 60), Momentum(120), BuyAndHold()]
    warm = max(getattr(s, "long", 0) or getattr(s, "lookback", 0) or 0 for s in strategies)
    days = warm + int(profile.days_per_year * years) + 1
    rng = random.Random(seed)
    finals = {s.describe(): [] for s in strategies}
    mdds = {s.describe(): [] for s in strategies}
    trades = {s.describe(): [] for s in strategies}
    costs_paid = {s.describe(): [] for s in strategies}
    hold_key = BuyAndHold().describe()
    for _ in range(runs):
        bars = simulate_path(profile, days, rng)
        for s in strategies:
            r = run_backtest(bars, Warmup(s, warm), initial_cash=cash, costs=profile.costs,
                             fractional=profile.fractional, min_order=profile.min_order)
            k = s.describe()
            finals[k].append(r.final_equity)
            mdds[k].append(r.max_drawdown)
            trades[k].append(len(r.trades))
            c = profile.costs
            cost = sum(t.shares * t.entry_price * c.fee_rate +
                       (t.shares * t.exit_price * (c.fee_rate + c.sell_tax) if t.exit_price else 0)
                       for t in r.trades)
            costs_paid[k].append(cost)
    out = []
    for s in strategies:
        k = s.describe()
        f = finals[k]
        sm = Summary(
            strategy=k, runs=runs,
            median_final=statistics.median(f), p05_final=_pct(f, 0.05), p95_final=_pct(f, 0.95),
            loss_prob=sum(1 for x in f if x < cash) / runs,
            median_mdd=statistics.median(mdds[k]), p05_mdd=_pct(mdds[k], 0.05),
            avg_trades=statistics.mean(trades[k]), avg_cost=statistics.mean(costs_paid[k]))
        if k != hold_key and hold_key in finals:
            sm.beat_hold = sum(1 for a, b in zip(f, finals[hold_key]) if a > b) / runs
        out.append(sm)
    return profile, out


def format_simulation(profile: MarketProfile, summaries: list[Summary], cash: float, years: float) -> str:
    def won(x):
        return f"{x:,.0f}원"
    lines = [
        f"## {profile.label}",
        "",
        f"- 시작 금액 {won(cash)}, 기간 {years:g}년, 시뮬레이션 {summaries[0].runs}회",
        f"- 가정: 하루 변동성 {profile.daily_vol:.1%}, 연간 추세 평균 {profile.drift_mean:+.0%} (경로마다 ±{profile.drift_sd:.0%} 흩어짐), "
        f"수수료 {profile.costs.fee_rate:.3%}, 슬리피지 {profile.costs.slippage:.2%}",
        "",
        "| 전략 | 중간값 | 나쁜 경우 (하위 5%) | 좋은 경우 (상위 5%) | 손실 날 확률 | 최대낙폭 중간값 | 최대낙폭 나쁜 경우 | 평균 거래 수 | 평균 수수료 | 보유보다 나은 비율 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in summaries:
        beat = "-" if s.beat_hold is None else f"{s.beat_hold:.0%}"
        lines.append(
            f"| {s.strategy} | {won(s.median_final)} | {won(s.p05_final)} | {won(s.p95_final)} | {s.loss_prob:.0%} | "
            f"{s.median_mdd:.1%} | {s.p05_mdd:.1%} | {s.avg_trades:.1f} | {won(s.avg_cost)} | {beat} |")
    return "\n".join(lines) + "\n"
