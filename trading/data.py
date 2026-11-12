"""주가 데이터 불러오기.

CSV 형식(헤더 필수, 대소문자·한글 헤더 일부 허용):
    date,open,high,low,close,volume
    2024-01-02,35000,35500,34800,35200,1234567
"""
from __future__ import annotations

import csv
import math
import random
from dataclasses import dataclass
from datetime import date, datetime, timedelta

_ALIASES = {
    "date": "date", "날짜": "date", "일자": "date",
    "open": "open", "시가": "open",
    "high": "high", "고가": "high",
    "low": "low", "저가": "low",
    "close": "close", "종가": "close",
    "volume": "volume", "거래량": "volume",
}


@dataclass(frozen=True)
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


def _num(text: str) -> float:
    return float(text.replace(",", "").strip())


def _parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"날짜 형식을 알 수 없습니다: {text!r}")


def load_csv(path: str) -> list[Bar]:
    """CSV를 읽어 날짜순으로 정렬된 Bar 목록을 돌려준다."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames is None:
            raise ValueError("CSV에 헤더가 없습니다")
        cols = {}
        for name in reader.fieldnames:
            key = _ALIASES.get(name.strip().lower()) or _ALIASES.get(name.strip())
            if key:
                cols[key] = name
        missing = {"date", "open", "high", "low", "close"} - cols.keys()
        if missing:
            raise ValueError(f"CSV에 필요한 열이 없습니다: {sorted(missing)}")
        bars = []
        for row in reader:
            if not row.get(cols["date"]):
                continue
            bars.append(Bar(
                date=_parse_date(row[cols["date"]]),
                open=_num(row[cols["open"]]),
                high=_num(row[cols["high"]]),
                low=_num(row[cols["low"]]),
                close=_num(row[cols["close"]]),
                volume=_num(row[cols["volume"]]) if "volume" in cols and row[cols["volume"]] else 0.0,
            ))
    bars.sort(key=lambda b: b.date)
    return bars


def synthetic_bars(days: int = 750, start_price: float = 30000.0, seed: int = 42,
                   drift: float = 0.0003, vol: float = 0.012) -> list[Bar]:
    """테스트·연습용 가짜 주가. 실제 시장 데이터가 아님!"""
    rng = random.Random(seed)
    bars, price, d = [], start_price, date(2023, 1, 2)
    while len(bars) < days:
        if d.weekday() < 5:
            ret = drift + vol * rng.gauss(0, 1)
            o = price * (1 + vol / 3 * rng.gauss(0, 1))
            c = price * math.exp(ret)
            hi = max(o, c) * (1 + abs(rng.gauss(0, vol / 2)))
            lo = min(o, c) * (1 - abs(rng.gauss(0, vol / 2)))
            bars.append(Bar(d, round(o), round(hi), round(lo), round(c), rng.randint(10**5, 10**6)))
            price = c
        d += timedelta(days=1)
    return bars


def save_csv(bars: list[Bar], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume"])
        for b in bars:
            w.writerow([b.date.isoformat(), b.open, b.high, b.low, b.close, int(b.volume)])
