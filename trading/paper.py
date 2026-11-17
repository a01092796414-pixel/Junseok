"""실시간 모의매매 (가짜 돈). 실제 주문 기능도, API 키를 읽는 코드도 없다.

하루 한 번, 한국시간 09:05 이후 실행:
  1) 업비트 공개 시세에서 직전에 완성된 일봉까지 받아 전략 신호(보유/현금)를 계산
  2) 지금 시세(현재가)로 가짜 주문을 체결했다고 기록 (수수료 0.05%, 슬리피지 0.1%)
     - 백테스트가 가정하는 체결가(오늘 봉 시가)와의 차이도 함께 기록
  3) 모든 주문은 RiskManager.check_order()를 통과해야 함 (하루 1회, 하루 손실 한도 등)
  4) 같은 날(한국시간) 두 번 실행해도 한 번만 판단
  5) 데이터가 이상하면(조회 실패, 마지막 봉 날짜 틀림, 날짜 누락, 가격 0 이하) 아무것도 하지 않음
  6) paper/STOP 파일이 있으면 모든 주문 거부 (비상 정지)
  7) 계좌 손실이 -40%에 닿으면 그 계좌를 멈추고 재검토 대기

    python -m trading.paper run       # 오늘 판단 실행
    python -m trading.paper status    # 계좌 현황
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from .risk import RiskManager, small_account_config
from .strategies import BuyAndHold, MovingAverageCross, Momentum, Strategy
from .upbit import fetch_daily

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "paper")
CONFIG = os.path.join(ROOT, "accounts.json")
STATE_DIR = os.path.join(ROOT, "state")
STOP_FILE = os.path.join(ROOT, "STOP")
LOG_FILE = os.path.join(ROOT, "log.jsonl")
FEE, SLIPPAGE, MIN_ORDER = 0.0005, 0.001, 5_000
HALT_DRAWDOWN = -0.40          # 시작 금액 대비 -40%면 정지
EARLIEST_RUN = time(9, 5)      # KST
KST = timezone(timedelta(hours=9))


class DataError(Exception):
    """시세가 이상해서 판단하면 안 되는 상황."""


def now_kst() -> datetime:
    return datetime.now(timezone.utc).astimezone(KST)


def build_strategy(spec: dict) -> Strategy:
    t = spec.get("type")
    if t == "ma":
        return MovingAverageCross(int(spec.get("short", 20)), int(spec.get("long", 60)))
    if t == "momentum":
        return Momentum(int(spec.get("lookback", 120)))
    if t == "hold":
        return BuyAndHold()
    raise ValueError(f"알 수 없는 전략: {t}")


def ticker(market: str) -> dict:
    url = f"https://api.upbit.com/v1/ticker?markets={market}"
    with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=20) as r:
        return json.loads(r.read().decode())[0]


def validate(bars, price: float, open_price: float, now: datetime) -> None:
    """판단 전에 데이터가 정상인지 확인. 이상하면 DataError."""
    if not bars:
        raise DataError("일봉을 받지 못함")
    expected_last = now.astimezone(timezone.utc).date() - timedelta(days=1)
    if bars[-1].date != expected_last:
        raise DataError(f"마지막 완성 봉 날짜가 {bars[-1].date}임 (기대 {expected_last})")
    for a, b in zip(bars, bars[1:]):
        if (b.date - a.date).days != 1:
            raise DataError(f"날짜 누락: {a.date} → {b.date}")
    if any(min(x.open, x.high, x.low, x.close) <= 0 for x in bars):
        raise DataError("일봉에 0 이하 가격이 있음")
    if price <= 0 or open_price <= 0:
        raise DataError("현재가 또는 시가가 0 이하")


@dataclass
class Decision:
    account: str
    date: str
    signal: int
    action: str          # buy / sell / hold-position / stay-cash / skipped / blocked / halted / error
    price: float
    equity: float
    note: str = ""


def load_accounts() -> list[dict]:
    with open(CONFIG, encoding="utf-8") as f:
        return json.load(f)["accounts"]


def new_state(acc: dict, today: str) -> dict:
    return {"id": acc["id"], "initial_cash": acc["cash"], "cash": acc["cash"], "qty": 0.0,
            "created": today, "last_decision_date": None, "halted": False,
            "trades": [], "equity": []}


def load_state(acc: dict) -> dict:
    path = os.path.join(STATE_DIR, f"{acc['id']}.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return new_state(acc, now_kst().date().isoformat())


def save_state(st: dict) -> None:
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(os.path.join(STATE_DIR, f"{st['id']}.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)


def log(entry: dict) -> None:
    os.makedirs(ROOT, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def decide(acc: dict, st: dict, bars, price: float, today: str, open_price: float | None = None,
           stop: bool = False) -> Decision:
    """한 계좌의 오늘 판단. st(상태)를 직접 바꾼다."""
    strat = build_strategy(acc["strategy"])
    signal = strat.signals(bars)[-1]  # 직전에 완성된 봉 종가 기준
    equity = st["cash"] + st["qty"] * price
    d = Decision(acc["id"], today, signal, "", price, equity)

    if st.get("halted"):
        d.action, d.note = "halted", "손실 한도(-40%)로 정지됨. 재검토 필요"
        return d
    if equity <= st["initial_cash"] * (1 + HALT_DRAWDOWN):
        st["halted"] = True
        d.action, d.note = "halted", f"계좌 손실 {equity / st['initial_cash'] - 1:.0%}로 정지"
        return d

    cfg = small_account_config(st["initial_cash"])
    cfg.kill_switch = stop
    rm = RiskManager(cfg)
    rm.new_day()
    if st["equity"]:
        rm.record_pnl(equity - st["equity"][-1]["value"])

    gap = None if not open_price else price / open_price - 1   # 백테스트 체결가(시가)와의 차이

    if signal == 1 and st["qty"] == 0:
        fill = price * (1 + SLIPPAGE)
        amount = min(st["cash"] / (1 + FEE), cfg.max_order_amount)   # 한도를 넘는 돈은 현금으로 둔다
        if amount < MIN_ORDER:
            d.action, d.note = "blocked", "최소 주문 금액(5,000원) 미만"
            return d
        chk = rm.check_order("buy", amount, amount, equity, live=False)
        if not chk.ok:
            d.action, d.note = "blocked", chk.reason
            return d
        qty = amount / fill
        st["cash"] -= amount * (1 + FEE)
        st["qty"] = qty
        st["trades"].append({"date": today, "side": "buy", "price": round(fill, 2), "qty": qty,
                             "amount": round(amount, 2), "fee": round(amount * FEE, 2),
                             "open": open_price, "gap_vs_open": None if gap is None else round(gap, 5)})
        d.action = "buy"
    elif signal == 0 and st["qty"] > 0:
        fill = price * (1 - SLIPPAGE)
        amount = st["qty"] * fill
        if amount < MIN_ORDER:
            d.action, d.note = "blocked", "매도 금액이 최소 주문 금액(5,000원) 미만"
            return d
        chk = rm.check_order("sell", amount, 0, equity, live=False)
        if not chk.ok:
            d.action, d.note = "blocked", chk.reason
            return d
        st["cash"] += amount * (1 - FEE)
        st["trades"].append({"date": today, "side": "sell", "price": round(fill, 2), "qty": st["qty"],
                             "amount": round(amount, 2), "fee": round(amount * FEE, 2),
                             "open": open_price, "gap_vs_open": None if gap is None else round(gap, 5)})
        st["qty"] = 0.0
        d.action = "sell"
    else:
        d.action = "hold-position" if st["qty"] > 0 else "stay-cash"
    d.equity = st["cash"] + st["qty"] * price
    return d


def run(force: bool = False, now: datetime | None = None) -> list[Decision]:
    now = (now or now_kst()).astimezone(KST)
    today = now.date().isoformat()
    if now.time() < EARLIEST_RUN and not force:
        raise SystemExit(f"한국시간 09:05 이후에 실행하세요 (지금 {now:%H:%M}). 그 전에는 어제 봉이 막 마감돼 시세가 불안정할 수 있습니다.")
    stop = os.path.exists(STOP_FILE)
    out = []
    cache: dict[str, tuple] = {}
    for acc in load_accounts():
        if not acc.get("enabled", True):
            continue
        st = load_state(acc)
        if st["last_decision_date"] == today and not force:
            out.append(Decision(acc["id"], today, -1, "skipped", st.get("last_price", 0),
                                st["equity"][-1]["value"] if st["equity"] else st["cash"], "오늘 이미 판단함"))
            continue
        try:
            if acc["market"] not in cache:
                bars = fetch_daily(acc["market"], days=200)
                t = ticker(acc["market"])
                price, open_price = float(t["trade_price"]), float(t["opening_price"])
                validate(bars, price, open_price, now)
                cache[acc["market"]] = (bars, price, open_price)
            bars, price, open_price = cache[acc["market"]]
            d = decide(acc, st, bars, price, today, open_price, stop)
        except Exception as e:  # 이상하면 아무것도 하지 않는다
            d = Decision(acc["id"], today, -1, "error", 0, 0, f"{type(e).__name__}: {e}")
            log({"at": now.isoformat(timespec="seconds"), **d.__dict__})
            out.append(d)
            continue
        st["last_decision_date"] = today
        st["last_signal"], st["last_action"], st["last_price"], st["last_note"] = d.signal, d.action, price, d.note
        st["equity"].append({"date": today, "value": round(d.equity, 2)})
        save_state(st)
        log({"at": now.isoformat(timespec="seconds"), "stop_file": stop, **d.__dict__})
        out.append(d)
    write_summary()
    return out


def write_summary() -> dict:
    """상황판에 올릴 계좌 요약 (paper/summary.json)."""
    accs = []
    for acc in load_accounts():
        st = load_state(acc)
        last = st["equity"][-1]["value"] if st["equity"] else st["cash"]
        gaps = [abs(t["gap_vs_open"]) for t in st["trades"] if t.get("gap_vs_open") is not None]
        accs.append({
            "id": acc["id"], "name": acc["name"], "market": acc["market"], "control": acc.get("control", False),
            "strategy": build_strategy(acc["strategy"]).describe(), "enabled": acc.get("enabled", True),
            "initial": st["initial_cash"], "equity": round(last, 0),
            "returnPct": round((last / st["initial_cash"] - 1) * 100, 2),
            "position": "정지" if st.get("halted") else ("보유" if st["qty"] > 0 else "현금"),
            "lastAction": st.get("last_action"), "lastDate": st.get("last_decision_date"),
            "lastNote": st.get("last_note", ""),
            "trades": len(st["trades"]), "since": st["created"],
            "avgGapPct": round(sum(gaps) / len(gaps) * 100, 3) if gaps else None,
            "history": st["equity"][-60:],
        })
    data = {"updated": now_kst().isoformat(timespec="minutes"), "stop": os.path.exists(STOP_FILE), "accounts": accs}
    with open(os.path.join(ROOT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


ACTION_KO = {"buy": "매수", "sell": "매도", "hold-position": "보유 유지", "stay-cash": "현금 유지",
             "skipped": "건너뜀", "blocked": "안전장치가 막음", "halted": "정지", "error": "오류로 중단"}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="실시간 모의매매 (가짜 돈)")
    p.add_argument("cmd", choices=["run", "status"])
    p.add_argument("--force", action="store_true", help="시간·중복 제한 무시 (테스트용)")
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
