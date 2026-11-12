"""명령줄 실행.

예시:
    python -m trading.cli sample --out data/sample_synthetic.csv
    python -m trading.cli backtest --csv data/sample_synthetic.csv --strategy ma --short 20 --long 60 --split
"""
from __future__ import annotations

import argparse
import sys

from .backtest import Costs, compare, format_report
from .data import load_csv, save_csv, synthetic_bars
from .strategies import BuyAndHold, MovingAverageCross, Momentum


def build_strategy(args):
    if args.strategy == "ma":
        return MovingAverageCross(args.short, args.long)
    if args.strategy == "momentum":
        return Momentum(args.lookback)
    return BuyAndHold()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="trading", description="자동 투자 팀 백테스트 도구")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("sample", help="연습용 가짜(합성) 주가 CSV 만들기")
    s.add_argument("--out", default="data/sample_synthetic.csv")
    s.add_argument("--days", type=int, default=750)
    s.add_argument("--seed", type=int, default=42)

    b = sub.add_parser("backtest", help="CSV 데이터로 전략 백테스트")
    b.add_argument("--csv", required=True)
    b.add_argument("--strategy", choices=["ma", "momentum", "hold"], default="ma")
    b.add_argument("--short", type=int, default=20)
    b.add_argument("--long", type=int, default=60)
    b.add_argument("--lookback", type=int, default=120)
    b.add_argument("--cash", type=float, default=10_000_000)
    b.add_argument("--fee", type=float, default=0.00015, help="수수료율 (기본 0.015%%)")
    b.add_argument("--tax", type=float, default=0.0, help="매도세율 (국내 주식형 ETF 0, 개별 주식은 증권거래세)")
    b.add_argument("--slippage", type=float, default=0.001)
    b.add_argument("--split", action="store_true", help="기간을 앞/뒤 절반으로 나눠서도 결과 보기")
    b.add_argument("--out", help="리포트를 저장할 마크다운 파일 경로")
    b.add_argument("--synthetic", action="store_true", help="합성 데이터임을 리포트에 표시")

    m = sub.add_parser("simulate", help="소액 모의투자 몬테카를로 시뮬레이션 (합성 가격)")
    m.add_argument("--market", choices=["etf", "coin", "all"], default="all")
    m.add_argument("--cash", type=float, default=50_000)
    m.add_argument("--runs", type=int, default=500)
    m.add_argument("--years", type=float, default=1.0)
    m.add_argument("--seed", type=int, default=7)
    m.add_argument("--out", help="리포트를 저장할 마크다운 파일 경로")

    args = p.parse_args(argv)

    if args.cmd == "simulate":
        from .simulate import format_simulation, run_simulation
        parts = [f"# 모의투자 시뮬레이션: {args.cash:,.0f}원\n",
                 "> ⚠️ 실제 시세가 아닌 합성 가격 흐름으로 돌린 결과입니다. 미래 수익 예측이 아니라, "
                 "가정 아래에서 결과가 어느 범위로 흩어지는지 보여줍니다.\n"]
        for key in (["etf", "coin"] if args.market == "all" else [args.market]):
            prof, sums = run_simulation(key, args.runs, args.years, args.cash, args.seed)
            parts.append(format_simulation(prof, sums, args.cash, args.years))
        report = "\n".join(parts)
        print(report)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(report)
        return 0

    if args.cmd == "sample":
        save_csv(synthetic_bars(args.days, seed=args.seed), args.out)
        print(f"합성 데이터 저장: {args.out} (실제 시장 데이터 아님)")
        return 0

    bars = load_csv(args.csv)
    strat = build_strategy(args)
    kw = dict(initial_cash=args.cash, costs=Costs(args.fee, args.tax, args.slippage))
    note = "⚠️ 합성(가짜) 데이터 — 실제 성과 아님" if args.synthetic else ""
    parts = [format_report(compare(bars, strat, **kw), title=f"백테스트: {strat.describe()}", note=note)]
    if args.split:
        mid = len(bars) // 2
        for label, chunk in (("앞 절반", bars[:mid]), ("뒤 절반", bars[mid:])):
            parts.append(format_report(compare(chunk, build_strategy(args), **kw), title=f"{label}", note=note)
                         .replace("# ", "## ", 1))
    report = "\n".join(parts)
    print(report)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"리포트 저장: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
