# -*- coding: utf-8 -*-
"""
공포지수 앱 — 프로덕션 크롤러 (미국 글로벌 + 크립토)

출력 (data/out/):
  latest.json      최신 지수 2종 + 성분 + 라벨 + 과거비교(전일/1주/1달/1년)
  history_global.json   글로벌 일별 시계열 (최근 365일, 차트용)
  history_crypto.json   크립토 일별 시계열
  context_global.json   ⭐무기: "이 공포가 역사적으로 뭘 의미했나"
                        (과거 유사 레벨 이후 S&P 30/90일 수익률)

지수: 0=극단공포 ~ 100=극단탐욕 (CNN 구간규약).
방식: 각 성분을 rolling 2년 백분위 정규화 → 탐욕방향 정렬 → 가중평균 ×100.
전 과거일을 backfill 계산 → 1일차부터 1년 차트 완성.

⚠️ FRED = curl 기본 UA만 허용(-A 넣으면 차단). cosd로 기간제한(전체=타임아웃).
코스피는 별도 모듈(사용자 data.go.kr 신청 후) 추가 예정.
"""
import io
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = BASE  # 데이터=저장소 루트 (앱이 raw .../main/latest.json 접근)
os.makedirs(OUT, exist_ok=True)

WINDOW = 504          # 백분위 창 = 최근 2년 거래일
HISTORY_DAYS = 365    # 차트 보존일
FRED_START = "2018-01-01"   # backfill 위해 넉넉히


def http_get(url):
    r = subprocess.run(["curl", "-s", "--max-time", "60", url],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0 or not r.stdout:
        raise RuntimeError(f"curl 실패({r.returncode}): {url}")
    return r.stdout


def fred_series(series_id, start=FRED_START):
    csv = http_get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}")
    out = []
    for line in csv.strip().splitlines()[1:]:
        d, _, v = line.partition(",")
        v = v.strip()
        if v and v != ".":
            try:
                out.append((d, float(v)))
            except ValueError:
                pass
    return out


def align_to_dates(series, dates):
    """series [(date,val)] → dates 리스트에 맞춰 forward-fill한 값 배열."""
    m = dict(series)
    out, last = [], None
    for d in dates:
        if d in m:
            last = m[d]
        out.append(last)
    return out


def pctile(window_vals, x):
    if not window_vals:
        return 0.5
    return sum(1 for v in window_vals if v is not None and v <= x) / \
        max(1, sum(1 for v in window_vals if v is not None))


def classify(score):
    if score < 25:
        return "extreme_fear"
    if score < 45:
        return "fear"
    if score < 56:
        return "neutral"
    if score < 76:
        return "greed"
    return "extreme_greed"


# ---- 파생 시계열 헬퍼 (앞쪽 정렬, None 보존) ----
def series_ratio_ma(vals, n):
    out = [None] * len(vals)
    for i in range(n, len(vals)):
        w = [v for v in vals[i - n:i] if v is not None]
        if len(w) == n and vals[i] is not None:
            out[i] = vals[i] / (sum(w) / n)
    return out


def series_chg(vals, n):
    out = [None] * len(vals)
    for i in range(n, len(vals)):
        a, b = vals[i], vals[i - n]
        if a is not None and b:
            out[i] = (a - b) / b
    return out


def rolling_pctile_signal(vals, invert):
    """각 시점 값을 직전 WINDOW 백분위로 → 탐욕방향 0~1 시계열."""
    out = [None] * len(vals)
    for i in range(len(vals)):
        if vals[i] is None:
            continue
        lo = max(0, i - WINDOW)
        w = [v for v in vals[lo:i + 1] if v is not None]
        if len(w) < 30:
            continue
        p = pctile(w, vals[i])
        out[i] = (1 - p) if invert else p
    return out


def build_global():
    print("=== 글로벌 (FRED 자체산출, backfill) ===")
    vix = fred_series("VIXCLS")
    curve = fred_series("T10Y2Y")
    dxy = fred_series("DTWEXBGS")
    hy = fred_series("BAMLH0A0HYM2")
    sp = fred_series("SP500")

    # 공통 날짜축 = VIX 거래일
    dates = [d for d, _ in vix]
    vix_v = [v for _, v in vix]
    curve_v = align_to_dates(curve, dates)
    dxy_v = align_to_dates(dxy, dates)
    hy_v = align_to_dates(hy, dates)
    sp_v = align_to_dates(sp, dates)

    sig = {
        "vix": rolling_pctile_signal(vix_v, invert=True),
        "momentum": rolling_pctile_signal(series_ratio_ma(sp_v, 125), invert=False),
        "hy": rolling_pctile_signal(hy_v, invert=True),
        "dollar": rolling_pctile_signal(series_chg(dxy_v, 20), invert=True),
        "curve": rolling_pctile_signal(curve_v, invert=False),
    }
    W = {"vix": 0.30, "momentum": 0.25, "hy": 0.20, "dollar": 0.15, "curve": 0.10}

    hist = []
    for i, d in enumerate(dates):
        parts = {k: sig[k][i] for k in W}
        if any(parts[k] is None for k in W):
            continue
        score = round(sum(parts[k] * W[k] for k in W) * 100)
        hist.append({"date": d, "score": score, "sp": sp_v[i], "parts": parts})
    return hist


def build_crypto():
    print("=== 크립토 (alternative.me) ===")
    data = json.loads(http_get("https://api.alternative.me/fng/?limit=400&format=json"))["data"]
    hist = []
    for row in reversed(data):  # 과거→현재
        ts = int(row["timestamp"])
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        hist.append({"date": d, "score": int(row["value"])})
    return hist


def nearest_on_or_before(hist, target_date):
    best = None
    for h in hist:
        if h["date"] <= target_date:
            best = h
        else:
            break
    return best


def compare_block(hist):
    """전일/1주/1달/1년 전 대비."""
    cur = hist[-1]["date"]
    cd = datetime.strptime(cur, "%Y-%m-%d")
    out = {}
    for key, days in [("prev", 1), ("week", 7), ("month", 30), ("year", 365)]:
        t = (cd - timedelta(days=days)).strftime("%Y-%m-%d")
        b = nearest_on_or_before(hist, t)
        out[key] = b["score"] if b else None
    return out


def build_context(global_hist):
    """⭐무기: 현재 공포 레벨과 유사했던 과거 시점들 → 이후 S&P 30/90일 수익률."""
    cur = global_hist[-1]
    cur_score = cur["score"]
    band = 5  # ±5 이내를 '유사'로
    sp_by_date = {h["date"]: h["sp"] for h in global_hist if h.get("sp")}
    dates_sorted = [h["date"] for h in global_hist if h.get("sp")]
    idx = {d: i for i, d in enumerate(dates_sorted)}

    samples = []
    for h in global_hist[:-90]:  # 이후 90일 관측 가능한 것만
        if abs(h["score"] - cur_score) <= band and h.get("sp"):
            d = h["date"]
            if d not in idx:
                continue
            i = idx[d]
            base = sp_by_date[d]
            r30 = r90 = None
            if i + 21 < len(dates_sorted):
                r30 = (sp_by_date[dates_sorted[i + 21]] - base) / base
            if i + 63 < len(dates_sorted):
                r90 = (sp_by_date[dates_sorted[i + 63]] - base) / base
            samples.append({"date": d, "score": h["score"], "r30": r30, "r90": r90})

    def avg(key):
        vs = [s[key] for s in samples if s[key] is not None]
        return round(sum(vs) / len(vs) * 100, 1) if vs else None

    return {
        "current_score": cur_score,
        "current_label": classify(cur_score),
        "band": band,
        "sample_count": len(samples),
        "avg_return_30d": avg("r30"),
        "avg_return_90d": avg("r90"),
        # 대표 과거사례 3개(가장 최근순)
        "examples": [
            {"date": s["date"], "score": s["score"],
             "return_90d": round(s["r90"] * 100, 1) if s["r90"] is not None else None}
            for s in sorted(samples, key=lambda x: x["date"], reverse=True)[:3]
        ],
    }


def trim(hist, days=HISTORY_DAYS):
    return hist[-days:] if len(hist) > days else hist


def main():
    g = build_global()
    c = build_crypto()
    print(f"글로벌 {len(g)}일, 크립토 {len(c)}일")

    g_latest, c_latest = g[-1], c[-1]
    print(f"글로벌 = {g_latest['score']} ({classify(g_latest['score'])})  "
          f"크립토 = {c_latest['score']} ({classify(c_latest['score'])})")

    latest = {
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "global": {
            "score": g_latest["score"], "label": classify(g_latest["score"]),
            "parts": {k: round(v, 3) for k, v in g_latest["parts"].items()},
            "compare": compare_block(g),
        },
        "crypto": {
            "score": c_latest["score"], "label": classify(c_latest["score"]),
            "compare": compare_block(c),
        },
    }
    ctx = build_context(g)
    print(f"⭐역사맥락: 공포 {ctx['current_score']}±{ctx['band']} 유사시점 {ctx['sample_count']}개 → "
          f"이후 90일 평균 S&P {ctx['avg_return_90d']}%")

    def dump(name, obj):
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))

    dump("latest.json", latest)
    dump("history_global.json", [{"d": h["date"], "s": h["score"]} for h in trim(g)])
    dump("history_crypto.json", [{"d": h["date"], "s": h["score"]} for h in trim(c)])
    dump("context_global.json", ctx)
    print(f"저장 완료: {OUT}")


if __name__ == "__main__":
    main()
