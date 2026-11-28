"""안전장치. 모든 실제·모의 주문은 RiskManager.check_order()를 통과해야 한다."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RiskConfig:
    max_order_amount: float = 500_000       # 1회 최대 주문 금액(원)
    max_daily_loss: float = 50_000          # 하루 최대 손실(원). 넘으면 그날 매매 중단
    max_position_pct: float = 0.5           # 한 종목 최대 비중(총자산 대비)
    max_orders_per_day: int = 10            # 하루 최대 주문 횟수 (주문 반복 버그 방지)
    kill_switch: bool = False               # True면 모든 주문 거부
    live_trading: bool = False              # False면 실전 주문 금지(모의투자만)


@dataclass
class OrderCheck:
    ok: bool
    reason: str = ""


class RiskManager:
    def __init__(self, config: RiskConfig | None = None):
        self.config = config or RiskConfig()
        self.daily_pnl = 0.0
        self.orders_today = 0

    def new_day(self) -> None:
        self.daily_pnl = 0.0
        self.orders_today = 0

    def record_pnl(self, pnl: float) -> None:
        self.daily_pnl += pnl

    def check_order(self, side: str, amount: float, position_value_after: float,
                    total_equity: float, live: bool = False) -> OrderCheck:
        c = self.config
        if side not in ("buy", "sell"):
            return OrderCheck(False, f"알 수 없는 주문 방향: {side}")
        if c.kill_switch:
            return OrderCheck(False, "비상 정지 스위치가 켜져 있음")
        if live and not c.live_trading:
            return OrderCheck(False, "실전 주문이 꺼져 있음 (live_trading=False)")
        if amount <= 0:
            return OrderCheck(False, "주문 금액이 0 이하")
        if self.orders_today >= c.max_orders_per_day:
            return OrderCheck(False, f"하루 최대 주문 횟수({c.max_orders_per_day}) 초과")
        if side == "buy":  # 손실 한도·크기 제한은 신규 매수에만 적용. 매도(위험 축소)는 허용
            if -self.daily_pnl >= c.max_daily_loss:
                return OrderCheck(False, f"하루 손실 한도({c.max_daily_loss:,.0f}원) 도달")
            if amount > c.max_order_amount:
                return OrderCheck(False, f"1회 최대 주문 금액({c.max_order_amount:,.0f}원) 초과")
            if total_equity > 0 and position_value_after / total_equity > c.max_position_pct:
                return OrderCheck(False, f"종목 비중 한도({c.max_position_pct:.0%}) 초과")
        self.orders_today += 1
        return OrderCheck(True)


def small_account_config(cash: float = 50_000, order_cap_ratio: float = 1.2) -> RiskConfig:
    """소액(예: 5만 원) 실험용 안전장치.

    1회 주문 한도는 시작 금액의 120%(5만 원이면 6만 원). 계좌가 불어나도 전략이 멈추지 않도록,
    실행기는 주문액을 min(현금, 한도)로 줄이고 넘는 돈은 현금으로 둔다.
    """
    return RiskConfig(
        max_order_amount=cash * order_cap_ratio,
        max_daily_loss=cash * 0.1,
        max_position_pct=1.0,
        max_orders_per_day=1,
    )
