"""실시간 모의매매 (가짜 돈). 실제 주문 기능은 없다.

하루 한 번 실행:
  1) 업비트에서 어제까지 완성된 일봉을 받아 전략 신호(보유/현금)를 계산
  2) 지금 시세(현재가)로 가짜 주문을 체결했다고 기록 (수수료·슬리피지 반영)
  3) 모든 주문은 RiskManager.check_order()를 통과해야 함
  4) 같은 날(한국시간) 두 번 실행해도 한 번만 판단

    python -m trading.paper run       # 오늘 판단 실행
    python -m trading.paper status    # 계좌 현황
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .risk import RiskManager, small_account_config
from .strategies import BuyAndHold, MovingAverageCross, Momentum, Strategy
from .upbit import fetch_daily

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper")
CONFIG = os.path.join(ROOT, "accounts.json")
STATE_DIR = os.path.join(ROOT, "state")
FEE, SLIPPAGE, MIN_ORDER = 0.0005, 0.001, 5_000


def now_kst() -> datetime:
    return datetime.now(timezone.utc) + timedelta(hours=9)


def build_strategy(spec: dict) -> Strategy:
    t = spec.get("type")
    if t == "ma":
        return MovingAverageCross(int(spec.get("short", 20)), int(spec.get("long", 60)))
    if t == "momentum":
        return Momentum(int(spec.get("lookback", 120)))
    if t == "hold":
        return BuyAndHold()
    raise ValueError(f"알 수 없는 전략: {t}")


def current_price(market: str) -> float:
    url = f"https://api.upbit.com/v1/ticker?markets={market}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as r:
        return float(json.loads(r.read().decode())[0]["trade_price"])


@dataclass
class Decision:
    account: str
    date: str
    signal: int
    action: str          # buy / sell / hold-position / stay-cash / skipped / blocked
    price: float
    equity: float
    note: str = ""


def load_accounts() -> list[dict]:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)["accounts"]


def load_state(acc: dict) -> dict:
    path = os.path.join(STATE_DIR, f"{acc['id']}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return {"id": acc["id"], "initial_cash": acc["cash"], "cash": acc["cash"], "qty": 0.0,
            "created": now_kst().date().isoformat(), "last_decision_date": None,
            "trades": [], "equity": []}


def save_state(st: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, f"{st['id']}.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def decide(acc: dict, st: dict, bars, price: float, today: str) -> Decision:
    strat = build_strategy(acc["strategy"])
    signal = strat.signals(bars)[-1]  # 어제까지 완성된 봉 기준
    equity = st["cash"] + st["qty"] * price
    rm = RiskManager(small_account_config(st["initial_cash"]))
    if st["equity"]:
        rm.record_pnl(equity - st["equity"][-1]["value"])
    d = Decision(acc["id"], today, signal, "", price, equity)

    if signal == 1 and st["qty"] == 0:
        fill = price * (1 + SLIPPAGE)
        qty = st["cash"] / (fill * (1 + FEE))
        amount = qty * fill
        if amount < MIN_ORDER:
            d.action, d.note = "blocked", "최소 주문 금액 미만"
            return d
        chk = rm.check_order("buy", amount, amount, equity, live=False)
        if not chk.ok:
            d.action, d.note = "blocked", chk.reason
            return d
        st["cash"] -= amount * (1 + FEE)
        st["qty"] = qty
        st["trades"].append({"date": today, "side": "buy", "price": round(fill, 2), "qty": qty,
                             "amount": round(amount, 2), "fee": round(amount * FEE, 2)})
        d.action = "buy"
    elif signal == 0 and st["qty"] > 0:
        fill = price * (1 - SLIPPAGE)
        amount = st["qty"] * fill
        chk = rm.check_order("sell", amount, 0, equity, live=False)
        if not chk.ok:
            d.action, d.note = "blocked", chk.reason
            return d
        st["cash"] += amount * (1 - FEE)
        st["trades"].append({"date": today, "side": "sell", "price": round(fill, 2), "qty": st["qty"],
                             "amount": round(amount, 2), "fee": round(amount * FEE, 2)})
        st["qty"] = 0.0
        d.action = "sell"
    else:
        d.action = "hold-position" if st["qty"] > 0 else "stay-cash"
    d.equity = st["cash"] + st["qty"] * price
    return d


def run(force: bool = False) -> list[Decision]:
    today = now_kst().date().isoformat()
    out = []
    for acc in load_accounts():
        if not acc.get("enabled", True):
            continue
        st = load_state(acc)
        if st["last_decision_date"] == today and not force:
            price = current_price(acc["market"])
            out.append(Decision(acc["id"], today, -1, "skipped", price, st["cash"] + st["qty"] * price, "오늘 이미 판단함"))
            continue
        strat = build_strategy(acc["strategy"])
        need = (getattr(strat, "long", 0) or getattr(strat, "lookback", 0) or 1) + 10
        bars = fetch_daily(acc["market"], days=need)
        price = current_price(acc["market"])
        d = decide(acc, st, bars, price, today)
        st["last_decision_date"] = today
        st["last_signal"] = d.signal
        st["last_action"] = d.action
        st["last_price"] = price
        st["equity"].append({"date": today, "value": round(d.equity, 2)})
        save_state(st)
        out.append(d)
    write_summary()
    return out


def write_summary() -> dict:
    """상황판에 올릴 계좌 요약 (paper/summary.json)."""
    accs = []
    for acc in load_accounts():
        st = load_state(acc)
        last = st["equity"][-1]["value"] if st["equity"] else st["cash"]
        accs.append({
            "id": acc["id"], "name": acc["name"], "market": acc["market"],
            "strategy": build_strategy(acc["strategy"]).describe(), "enabled": acc.get("enabled", True),
            "initial": st["initial_cash"], "equity": round(last, 0),
            "returnPct": round((last / st["initial_cash"] - 1) * 100, 2),
            "position": "보유" if st["qty"] > 0 else "현금",
            "lastAction": st.get("last_action"), "lastDate": st.get("last_decision_date"),
            "trades": len(st["trades"]), "since": st["created"],
            "history": st["equity"][-60:],
        })
    data = {"updated": now_kst().isoformat(timespec="minutes"), "accounts": accs}
    with open(os.path.join(ROOT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


ACTION_KO = {"buy": "매수", "sell": "매도", "hold-position": "보유 유지", "stay-cash": "현금 유지",
             "skipped": "건너뜀", "blocked": "안전장치가 막음"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="실시간 모의매매 (가짜 돈)")
    p.add_argument("cmd", choices=["run", "status"])
    p.add_argument("--force", action="store_true", help="오늘 이미 판단했어도 다시 실행 (테스트용)")
    a = p.parse_args(argv)
    if a.cmd == "run":
        for d in run(a.force):
            sig = {1: "보유", 0: "현금", -1: "-"}[d.signal]
            print(f"[{d.account}] {d.date} 신호={sig} 행동={ACTION_KO.get(d.action, d.action)} "
                  f"현재가={d.price:,.0f}원 평가액={d.equity:,.0f}원 {d.note}")
    else:
        s = write_summary()
        for x in s["accounts"]:
            print(f"[{x['id']}] {x['name']} {x['strategy']} 평가액 {x['equity']:,.0f}원 "
                  f"({x['returnPct']:+.2f}%) {x['position']} 거래 {x['trades']}회")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
