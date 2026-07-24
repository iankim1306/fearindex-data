# fearindex-data

**공포지수 (Fear & Greed Index)** 앱의 데이터 저장소.

매일 1회 GitHub Actions가 `crawl.py`를 실행해 시장 공포·탐욕 지수를 자체 산출하고,
아래 JSON을 갱신합니다. 앱은 raw URL로 이 파일들을 직접 fetch 합니다.

| 파일 | 내용 |
|---|---|
| `latest.json` | 최신 글로벌·크립토 지수 + 성분 + 과거비교 |
| `history_global.json` / `history_crypto.json` | 최근 365일 시계열 |
| `context_global.json` | 역사 맥락 (유사 공포시점 이후 S&P 수익률) |

## 데이터 출처 (전부 무료·상업 이용 가능)
- 글로벌: [FRED](https://fred.stlouisfed.org) (VIX·금리차·하이일드·달러·S&P500) → 자체 산출
- 크립토: [alternative.me](https://alternative.me/crypto/fear-and-greed-index/) API

> 본 지수는 정보 제공용이며 투자 자문이 아닙니다.
