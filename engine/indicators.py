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


def compute_stochastic(df: pd.DataFrame) -> pd.DataFrame:
    """Slow Stochastic(14,3,3): fast %K(14) -> %K = SMA3(fast) -> %D = SMA3(%K)."""
    low_min = df["Low"].rolling(STOCH_K_PERIOD).min()
    high_max = df["High"].rolling(STOCH_K_PERIOD).max()
    fast_k = 100 * (df["Close"] - low_min) / (high_max - low_min)
    k = fast_k.rolling(STOCH_K_SMOOTH).mean()
    d = k.rolling(STOCH_D_SMOOTH).mean()
    return pd.DataFrame({"stoch_k": k, "stoch_d": d}, index=df.index)


def calculate_rsi(series: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    """Wilder RSI (EWM 방식)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0).ewm(span=period, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0.0)).ewm(span=period, adjust=False).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))


def compute_rsi_with_ma(df: pd.DataFrame) -> pd.DataFrame:
    rsi = calculate_rsi(df["Close"], RSI_PERIOD)
    rsi_ma = rsi.rolling(RSI_MA_PERIOD).mean()
    return pd.DataFrame({"rsi": rsi, "rsi_ma": rsi_ma}, index=df.index)


def detect_cross(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """fast가 slow를 상향 돌파하면 'golden', 하향 돌파하면 'death', 그 외 None."""
    prev_fast, prev_slow = fast.shift(1), slow.shift(1)
    golden = (prev_fast <= prev_slow) & (fast > slow)
    death = (prev_fast >= prev_slow) & (fast < slow)
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


def _scan_bullish_failure_swing(values: np.ndarray, level: float) -> np.ndarray:
    """
    George Lane의 Bullish Failure Swing:
    과매도(level) 밑 저점 -> level 위로 반등한 고점(peak1) ->
    level 아래로 재진입하지 않는 더 높은 저점 -> peak1 돌파 시 확정.
    확정된 '바'에 True를 표시한다.
    """
    is_peak, is_trough = _local_extrema(values)
    n = len(values)
    confirmed = np.zeros(n, dtype=bool)

    state = "SEEK_TROUGH1"
    trough1 = peak1 = None

    for i in range(n):
        v = values[i]

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


def detect_stochastic_failure_swings(stoch_k: pd.Series) -> pd.DataFrame:
    """Bullish/Bearish Failure Swing 확정 지점. Bearish는 부호를 뒤집어 동일 알고리즘 재사용."""
    values = stoch_k.to_numpy(dtype=float)
    values = np.nan_to_num(values, nan=50.0)  # 워밍업 구간 NaN은 중립값으로 채워 상태기계 오염 방지

    bullish = _scan_bullish_failure_swing(values, level=OVERSOLD)
    bearish = _scan_bullish_failure_swing(-values, level=-OVERBOUGHT)

    return pd.DataFrame(
        {"bull_failure_swing": bullish, "bear_failure_swing": bearish},
        index=stoch_k.index,
    )


def compute_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    """OHLCV -> 스토캐스틱/RSI + 크로스/페일러스윙 컬럼이 붙은 DataFrame."""
    stoch = compute_stochastic(df)
    rsi_df = compute_rsi_with_ma(df)
    fs = detect_stochastic_failure_swings(stoch["stoch_k"])

    out = pd.concat([df, stoch, rsi_df, fs], axis=1)
    out["stoch_cross"] = detect_cross(stoch["stoch_k"], stoch["stoch_d"])
    out["rsi_cross"] = detect_cross(rsi_df["rsi"], rsi_df["rsi_ma"])
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


def latest_daily_regime(indicator_df: pd.DataFrame) -> dict | None:
    """
    가장 최근에 매수 조건(스토캐/RSI 골든크로스 또는 failure swing) 또는
    매도 조건(데드크로스 또는 failure swing) 중 하나라도 충족된 날의 방향을
    일봉 필터로 산출. 같은 날 양쪽이 동시에 충족되는 드문 경우는 bullish 우선.
    """
    bull_mask = (
        (indicator_df["stoch_cross"] == "golden")
        | indicator_df["bull_failure_swing"]
        | (indicator_df["rsi_cross"] == "golden")
    )
    bear_mask = (
        (indicator_df["stoch_cross"] == "death")
        | indicator_df["bear_failure_swing"]
        | (indicator_df["rsi_cross"] == "death")
    )
    any_mask = bull_mask | bear_mask
    if not any_mask.any():
        return None

    last_idx = indicator_df.index[any_mask][-1]
    direction = "bullish" if bull_mask.loc[last_idx] else "bearish"
    conditions = bar_conditions(indicator_df.loc[last_idx], direction)
    return {"direction": direction, "cross_date": str(last_idx.date()), "conditions": conditions}


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
