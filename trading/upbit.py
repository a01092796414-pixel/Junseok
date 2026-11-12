"""업비트 공개 시세(API 키 불필요)로 일봉 CSV 만들기.

예시:
    python -m trading.upbit --market KRW-BTC --days 1825 --out data/upbit_KRW-BTC_daily.csv
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

from .data import Bar, save_csv

API = "https://api.upbit.com/v1/candles/days"


def _get(params: dict) -> list[dict]:
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode())
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    return []


def fetch_daily(market: str = "KRW-BTC", days: int = 1825, include_today: bool = False) -> list[Bar]:
    """최근 days일 일봉. 기본으로 아직 끝나지 않은 오늘 봉(한국시간 09:00 기준)은 뺀다."""
    out: dict[date, Bar] = {}
    to = None
    while len(out) < days + 1:
        params = {"market": market, "count": 200}
        if to:
            params["to"] = to
        rows = _get(params)
        if not rows:
            break
        for r in rows:
            d = datetime.fromisoformat(r["candle_date_time_kst"]).date()
            out[d] = Bar(d, float(r["opening_price"]), float(r["high_price"]), float(r["low_price"]),
                         float(r["trade_price"]), float(r["candle_acc_trade_volume"]))
        oldest = min(datetime.fromisoformat(r["candle_date_time_utc"]) for r in rows)
        to = (oldest).replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if len(rows) < 200:
            break
        time.sleep(0.2)  # 공개 API 요청 제한 지키기
    bars = sorted(out.values(), key=lambda b: b.date)
    if not include_today:
        today_kst = (datetime.now(timezone.utc) + timedelta(hours=9)).date()
        bars = [b for b in bars if b.date < today_kst]
    return bars[-days:]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="업비트 일봉 내려받기")
    p.add_argument("--market", default="KRW-BTC")
    p.add_argument("--days", type=int, default=1825)
    p.add_argument("--out")
    a = p.parse_args(argv)
    bars = fetch_daily(a.market, a.days)
    out = a.out or f"data/upbit_{a.market}_daily.csv"
    save_csv(bars, out)
    print(f"{a.market}: {len(bars)}일 ({bars[0].date} ~ {bars[-1].date}) → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
