"""
ATR 기반 트레일링 스톱(Chandelier Exit).

고정 % 손절 대신, 진입 이후 최고가에서 ATR의 배수만큼 아래를 손절선으로 잡고
가격이 오르면 손절선도 같이 따라 올라간다 (절대 내려가지 않음). 종목마다
변동성(ATR%)이 다른데 지금 유니버스가 변동성 상위로 재랭킹돼 있어서, 고정 %
손절보다 종목별 정상 변동폭에 맞춰지는 이 방식이 더 적합하다.
"""
from __future__ import annotations

import pandas as pd

from engine.indicators import compute_atr


def chandelier_stop(highest_price: float, atr: float, multiplier: float) -> float:
    return highest_price - multiplier * atr


def evaluate_position(
    highest_price: float,
    current_price: float,
    current_high: float,
    atr: float,
    multiplier: float,
) -> dict:
    """
    포지션의 최고가를 갱신하고, 그 기준 손절선 대비 현재가로 청산 여부를 판단한다.
    current_high는 당일 고가(트레일링 스톱을 최대한 유리하게 갱신하기 위함),
    current_price는 청산 판단에 쓸 현재가(주로 종가/실시간가).
    """
    new_highest = max(highest_price, current_high)
    stop_level = chandelier_stop(new_highest, atr, multiplier)
    return {
        "highest_price": new_highest,
        "stop_level": stop_level,
        "should_exit": current_price <= stop_level,
    }


def evaluate_position_from_daily(
    daily_df: pd.DataFrame, highest_price: float, atr_period: int = 14, multiplier: float = 3.0
) -> dict | None:
    """일봉 OHLCV에서 ATR을 직접 계산해 evaluate_position 호출. ATR이 아직 워밍업 중이면 None."""
    atr = compute_atr(daily_df, period=atr_period).iloc[-1]
    if pd.isna(atr):
        return None
    last = daily_df.iloc[-1]
    return evaluate_position(
        highest_price=highest_price,
        current_price=float(last["Close"]),
        current_high=float(last["High"]),
        atr=float(atr),
        multiplier=multiplier,
    )
