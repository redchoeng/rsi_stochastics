"""
스토캐스틱(14,3,3) / RSI(14)+RSI 14MA 계산과 크로스·페일러 스윙 감지.

모든 함수는 OHLCV DataFrame(컬럼: Open/High/Low/Close/Volume, 시간 오름차순 정렬)을
입력으로 받는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

STOCH_K_PERIOD = 14
STOCH_K_SMOOTH = 3
STOCH_D_SMOOTH = 3
RSI_PERIOD = 14
RSI_MA_PERIOD = 14

OVERSOLD = 20.0
OVERBOUGHT = 80.0


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range (절대 가격 단위). universe.py의 ATR%%와 exit_strategy의 트레일링 스톱이 공유."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(period).mean()


def compute_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    """Slow Stochastic(14,3,3): fast %K(14) -> %K = SMA3(fast) -> %D = SMA3(%K)."""
    low_min = df["Low"].rolling(STOCH_K_PERIOD).min()
    high_max = df["High"].rolling(STOCH_K_PERIOD).max()
    fast_k = 100 * (df["Close"] - low_min) / (high_max - low_min)
    k = fast_k.rolling(STOCH_K_SMOOTH).mean()
    d = k.rolling(STOCH_D_SMOOTH).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d}, index=df.index)


def calculate_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """
    Wilder 원조 RSI. 첫 period개는 단순평균으로 시드(seed)하고, 이후는
    avg = (prev_avg*(period-1) + current) / period 로 재귀 평활한다.
    (주의: ewm(span=period)의 alpha=2/(period+1)은 Wilder의 alpha=1/period와
    달라서 TradingView/HTS 값과 어긋난다 — 실제로 겪은 버그라 재발 방지 코멘트)
    """
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()

    for i in range(period + 1, len(series)):
        avg_gain.iloc[i] = (avg_gain.iloc[i - 1] * (period - 1) + gain.iloc[i]) / period
        avg_loss.iloc[i] = (avg_loss.iloc[i - 1] * (period - 1) + loss.iloc[i]) / period

    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def compute_rsi_with_ma(df: pd.DataFrame) -> pd.DataFrame:
    rsi = calculate_rsi(df["Close"], RSI_PERIOD)
    rsi_ma = rsi.rolling(RSI_MA_PERIOD).mean()
    return pd.DataFrame({"rsi": rsi, "rsi_ma": rsi_ma}, index=df.index)


def session_gap_mask(index: pd.DatetimeIndex, gap_factor: float = 5.0) -> pd.Series:
    """
    이전 봉과의 시간 간격이 정상 간격의 gap_factor배를 넘으면 True (세션 경계).
    프리/애프터마켓까지 이어붙인 15분봉은 애프터마켓 마감~다음날 프리마켓 시작
    사이에 몇 시간짜리 갭이 생기는데, 그 경계를 '바로 다음 봉'처럼 비교하면
    갭 자체가 크로스로 잘못 잡힌다 (실제로 겪은 버그). 일봉은 주말 갭(~3배)
    정도라 5배 기준이면 걸리지 않는다.
    """
    diffs = index.to_series().diff().dt.total_seconds()
    median_gap = diffs.median()
    if not median_gap or pd.isna(median_gap) or median_gap <= 0:
        return pd.Series(False, index=index)
    return (diffs > median_gap * gap_factor).fillna(False)


def detect_cross(fast: pd.Series, slow: pd.Series, session_gap: pd.Series | None = None) -> pd.Series:
    """fast가 slow를 상향 돌파하면 'golden', 하향 돌파하면 'death', 그 외 None.

    session_gap이 True인 봉(세션 경계 바로 다음 봉)은 직전 봉과의 비교가
    무의미하므로 크로스로 치지 않는다.
    """
    prev_fast, prev_slow = fast.shift(1), slow.shift(1)
    golden = (prev_fast <= prev_slow) & (fast > slow)
    death = (prev_fast >= prev_slow) & (fast < slow)
    if session_gap is not None:
        golden &= ~session_gap
        death &= ~session_gap
    out = pd.Series(None, index=fast.index, dtype=object)
    out[golden] = "golden"
    out[death] = "death"
    return out


def _local_extrema(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """엄격한 로컬 고점/저점 위치를 표시 (양 옆 값보다 높거나/낮은 지점)."""
    n = len(values)
    is_peak = np.zeros(n, dtype=bool)
    is_trough = np.zeros(n, dtype=bool)
    for i in range(1, n - 1):
        left, cur, right = values[i - 1], values[i], values[i + 1]
        if cur >= left and cur >= right and cur > left:
            is_peak[i] = True
        if cur <= left and cur <= right and cur < left:
            is_trough[i] = True
    return is_peak, is_trough


def _scan_bullish_failure_swing(
    values: np.ndarray, level: float, session_gap: np.ndarray | None = None
) -> np.ndarray:
    """
    George Lane의 Bullish Failure Swing:
    과매도(level) 밑 저점 -> level 위로 반등한 고점(peak1) ->
    level 아래로 재진입하지 않는 더 높은 저점 -> peak1 돌파 시 확정.
    확정된 '바'에 True를 표시한다.

    session_gap이 True인 지점(세션 경계)에서는 진행 중이던 패턴을 버리고
    새로 찾기 시작한다 — 안 그러면 애프터마켓 마감과 다음날 프리마켓 시작
    사이의 갭이 저점/고점으로 섞여 들어가 패턴이 오염된다.
    """
    is_peak, is_trough = _local_extrema(values)
    n = len(values)
    confirmed = np.zeros(n, dtype=bool)

    state = "SEEK_TROUGH1"
    trough1 = peak1 = None

    for i in range(n):
        v = values[i]

        if session_gap is not None and session_gap[i]:
            state = "SEEK_TROUGH1"
            trough1 = peak1 = None

        if state == "SEEK_TROUGH1":
            if is_trough[i] and v < level:
                trough1 = v
                state = "SEEK_PEAK1"

        elif state == "SEEK_PEAK1":
            if is_trough[i] and v < level and v < trough1:
                trough1 = v  # 아직 더 낮은 저점 갱신, 계속 대기
            elif is_peak[i] and v >= level:
                peak1 = v
                state = "SEEK_HIGHER_LOW"

        elif state == "SEEK_HIGHER_LOW":
            if is_trough[i]:
                if v < level:
                    # 과매도 재진입 = 패턴 무효, 이 저점을 새 trough1으로 다시 시작
                    trough1, peak1 = v, None
                    state = "SEEK_PEAK1"
                elif v > trough1:
                    state = "SEEK_BREAKOUT"
                else:
                    # level 위지만 trough1보다 낮은 저점 = 무효, 여기서 재시작
                    trough1, peak1 = v, None
                    state = "SEEK_PEAK1"

        elif state == "SEEK_BREAKOUT":
            if v < level:
                trough1, peak1 = None, None
                state = "SEEK_TROUGH1"
            elif v > peak1:
                confirmed[i] = True
                trough1, peak1 = None, None
                state = "SEEK_TROUGH1"

    return confirmed


def detect_stochastic_failure_swings(stoch_k: pd.Series, session_gap: pd.Series | None = None) -> pd.DataFrame:
    """Bullish/Bearish Failure Swing 확정 지점. Bearish는 부호를 뒤집어 동일 알고리즘 재사용."""
    values = stoch_k.to_numpy(dtype=float)
    values = np.nan_to_num(values, nan=50.0)  # 워밍업 구간 NaN은 중립값으로 채워 상태기계 오염 방지
    gap_arr = session_gap.to_numpy() if session_gap is not None else None

    bullish = _scan_bullish_failure_swing(values, level=OVERSOLD, session_gap=gap_arr)
    bearish = _scan_bullish_failure_swing(-values, level=-OVERBOUGHT, session_gap=gap_arr)

    return pd.DataFrame(
        {"bull_failure_swing": bullish, "bear_failure_swing": bearish},
        index=stoch_k.index,
    )


def compute_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV -> 스토캐스틱/RSI + 크로스/페일러스윙 컬럼이 붙은 DataFrame."""
    stoch = compute_stochastic(df)
    rsi_df = compute_rsi_with_ma(df)
    gap = session_gap_mask(df.index)
    fs = detect_stochastic_failure_swings(stoch["stoch_k"], session_gap=gap)

    out = pd.concat([df, stoch, rsi_df, fs], axis=1)
    out["stoch_cross"] = detect_cross(stoch["stoch_k"], stoch["stoch_d"], session_gap=gap)
    out["rsi_cross"] = detect_cross(rsi_df["rsi"], rsi_df["rsi_ma"], session_gap=gap)
    return out


def bar_conditions(row: pd.Series, direction: str) -> list[str]:
    """한 봉에서 direction(bullish/bearish)에 해당하는, 충족된 조건 이름 목록.

    매수/매도 판단에 쓰이는 3가지 조건(스토캐 크로스/failure swing/RSI 크로스)이
    일봉 필터와 15분봉 트리거 양쪽에서 동일해야 하므로 하나로 공유한다.
    """
    if direction == "bullish":
        conditions = []
        if row["stoch_cross"] == "golden":
            conditions.append("stoch_golden_cross")
        if row["bull_failure_swing"]:
            conditions.append("stoch_bull_failure_swing")
        if row["rsi_cross"] == "golden":
            conditions.append("rsi_golden_cross")
        return conditions

    conditions = []
    if row["stoch_cross"] == "death":
        conditions.append("stoch_death_cross")
    if row["bear_failure_swing"]:
        conditions.append("stoch_bear_failure_swing")
    if row["rsi_cross"] == "death":
        conditions.append("rsi_death_cross")
    return conditions


def today_daily_signal(indicator_df: pd.DataFrame, today: object, min_conditions: int = 2) -> dict | None:
    """
    '오늘' 일봉(마지막 행) 그 자체가 min_conditions개 이상을 충족하는지만 본다
    (latest_daily_regime처럼 과거로 거슬러 올라가 가장 최근 신호를 찾지 않음).
    매수는 당일 신호가 나야 그날 15분봉을 보고, 매도는 당일 신호만으로 바로
    알림을 보내는 '당일 한정' 구조라서 필요.
    today와 마지막 봉의 날짜가 다르면(예: 프리마켓이라 오늘자 일봉이 아직
    없음) None을 반환한다.
    """
    last_idx = indicator_df.index[-1]
    if last_idx.date() != today:
        return None

    last = indicator_df.loc[last_idx]
    bull_conditions = bar_conditions(last, "bullish")
    bear_conditions = bar_conditions(last, "bearish")

    if len(bull_conditions) >= min_conditions:
        return {"direction": "bullish", "cross_date": str(last_idx.date()), "conditions": bull_conditions}
    if len(bear_conditions) >= min_conditions:
        return {"direction": "bearish", "cross_date": str(last_idx.date()), "conditions": bear_conditions}
    return None


def latest_intraday_triggers(indicator_df: pd.DataFrame, direction: str) -> list[dict]:
    """마지막 봉에서 direction(bullish/bearish)에 맞는 조건이 새로 충족됐는지 확인."""
    last = indicator_df.iloc[-1]
    bar_ts = indicator_df.index[-1]
    triggers = [{"condition": c} for c in bar_conditions(last, direction)]

    for t in triggers:
        t["bar_timestamp"] = str(bar_ts)
        t["price"] = float(last["Close"])
        t["stoch_k"] = float(last["stoch_k"])
        t["stoch_d"] = float(last["stoch_d"])
        t["rsi"] = float(last["rsi"])

    return triggers
