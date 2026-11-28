"""간단한 일봉 백테스트 엔진 (롱 온리, 전량 매수/전량 매도).

- i번째 날 종가 기준 신호 → i+1번째 날 시가에 체결 (미래 데이터 사용 방지)
- 수수료, 매도세, 슬리피지 반영
- 주식 수는 정수
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from .data import Bar
from .strategies import BuyAndHold, Strategy


@dataclass
class Costs:
    fee_rate: float = 0.00015    # 매수·매도 수수료 (증권사마다 다름)
    sell_tax: float = 0.0        # 매도세. 국내 주식형 ETF는 0, 개별 주식은 증권거래세 적용
    slippage: float = 0.001      # 체결 가격 불리함 (0.1%)


@dataclass
class Trade:
    entry_date: date
    entry_price: float
    shares: float
    exit_date: date | None = None
    exit_price: float | None = None
    pnl: float = 0.0


@dataclass
class Result:
    strategy: str
    start: date
    end: date
    initial_cash: float
    final_equity: float
    equity_curve: list[tuple[date, float]]
    trades: list[Trade] = field(default_factory=list)
    exposure: float = 0.0

    @property
    def total_return(self) -> float:
        return self.final_equity / self.initial_cash - 1

    @property
    def cagr(self) -> float:
        years = max((self.end - self.start).days, 1) / 365.25
        if self.final_equity <= 0:
            return -1.0
        return (self.final_equity / self.initial_cash) ** (1 / years) - 1

    @property
    def max_drawdown(self) -> float:
        peak, mdd = -math.inf, 0.0
        for _, eq in self.equity_curve:
            peak = max(peak, eq)
            if peak > 0:
                mdd = min(mdd, eq / peak - 1)
        return mdd

    @property
    def closed_trades(self) -> list[Trade]:
        return [t for t in self.trades if t.exit_date is not None]

    @property
    def win_rate(self) -> float | None:
        closed = self.closed_trades
        if not closed:
            return None
        return sum(1 for t in closed if t.pnl > 0) / len(closed)


def run_backtest(bars: list[Bar], strategy: Strategy, initial_cash: float = 10_000_000,
                 costs: Costs | None = None, position_pct: float = 1.0,
                 fractional: bool = False, min_order: float = 0.0) -> Result:
    """fractional=True면 소수 단위 매수(코인), min_order는 최소 주문 금액(예: 업비트 5,000원)."""
    if len(bars) < 2:
        raise ValueError("백테스트에는 최소 2일치 데이터가 필요합니다")
    if not 0 < position_pct <= 1:
        raise ValueError("position_pct는 0보다 크고 1 이하여야 합니다")
    costs = costs or Costs()
    sig = strategy.signals(bars)
    if len(sig) != len(bars):
        raise ValueError("전략 신호 개수가 데이터 개수와 다릅니다")

    cash, shares = float(initial_cash), 0.0
    trades: list[Trade] = []
    open_trade: Trade | None = None
    entry_cost = 0.0
    curve = [(bars[0].date, cash)]
    held_days = 0

    for i in range(1, len(bars)):
        bar, target = bars[i], sig[i - 1]
        if target == 1 and shares == 0:
            price = bar.open * (1 + costs.slippage)
            budget = cash * position_pct
            n = budget / (price * (1 + costs.fee_rate))
            if not fractional:
                n = float(int(n))
            if n > 0 and n * price >= min_order:
                cost = n * price * (1 + costs.fee_rate)
                cash -= cost
                shares = n
                entry_cost = cost
                open_trade = Trade(bar.date, price, n)
        elif target == 0 and shares > 0:
            price = bar.open * (1 - costs.slippage)
            proceeds = shares * price * (1 - costs.fee_rate - costs.sell_tax)
            cash += proceeds
            assert open_trade is not None
            open_trade.exit_date, open_trade.exit_price = bar.date, price
            open_trade.pnl = proceeds - entry_cost
            trades.append(open_trade)
            open_trade, shares = None, 0.0
        if shares > 0:
            held_days += 1
        curve.append((bar.date, cash + shares * bar.close))

    if open_trade is not None:  # 마지막까지 보유 중인 거래 (평가손익만 기록)
        last = bars[-1].close
        open_trade.pnl = shares * last * (1 - costs.fee_rate - costs.sell_tax) - entry_cost
        trades.append(open_trade)

    return Result(
        strategy=strategy.describe(), start=bars[0].date, end=bars[-1].date,
        initial_cash=initial_cash, final_equity=curve[-1][1], equity_curve=curve,
        trades=trades, exposure=held_days / (len(bars) - 1),
    )


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:+.2f}%"


def format_report(results: list[Result], title: str = "백테스트 결과", note: str = "") -> str:
    r0 = results[0]
    lines = [f"# {title}", "",
             f"- 기간: {r0.start} ~ {r0.end}",
             f"- 시작 자금: {r0.initial_cash:,.0f}원"]
    if note:
        lines.append(f"- 참고: {note}")
    lines += ["", "| 전략 | 최종 자산 | 총수익률 | 연환산(CAGR) | 최대낙폭(MDD) | 거래 수 | 승률 | 보유 비율 |",
              "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in results:
        wr = "-" if r.win_rate is None else f"{r.win_rate * 100:.0f}%"
        lines.append(f"| {r.strategy} | {r.final_equity:,.0f}원 | {_pct(r.total_return)} | {_pct(r.cagr)} | "
                     f"{_pct(r.max_drawdown)} | {len(r.closed_trades)} | {wr} | {r.exposure * 100:.0f}% |")
    return "\n".join(lines) + "\n"


def compare(bars: list[Bar], strategy: Strategy, **kwargs) -> list[Result]:
    """전략과 같은 조건의 '매수 후 보유'를 함께 돌린다."""
    return [run_backtest(bars, strategy, **kwargs), run_backtest(bars, BuyAndHold(), **kwargs)]
