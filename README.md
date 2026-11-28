# Junseok

## 자동 투자 AI 팀 (실험용)

역할을 나눈 AI 팀원이 **전략 작성 → 백테스트 → 리스크 검토 → (모의)매매** 순서로 일하는 구조입니다.
팀 구성과 로드맵은 [TEAM.md](TEAM.md)를 보세요.

> ⚠️ 학습·실험용입니다. 수익을 보장하지 않습니다.

### 빠르게 해보기

```bash
python -m unittest                                   # 테스트
python -m trading.cli sample                         # 연습용 가짜 데이터 만들기
python -m trading.cli backtest --csv data/sample_synthetic.csv --strategy ma --split --synthetic
```

전략: `ma`(이동평균 교차, `--short 20 --long 60`), `momentum`(`--lookback 120`), `hold`(매수 후 보유)

### 폴더 구조

```
.claude/agents/   AI 팀원 정의 (리서처, 전략가, 백테스터, 리스크 매니저, 트레이더)
trading/          백테스트 엔진, 전략, 안전장치(risk.py)
tests/            테스트
data/             주가 CSV
reports/          브리핑·백테스트 리포트
```
