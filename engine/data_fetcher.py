"""yfinance 데이터 수집 (일봉/15분봉), 실패에 대한 얇은 재시도 래퍼."""
from __future__ import annotations

import logging
import time

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

_MAX_RETRIES = 3
_RETRY_DELAY_SEC = 2


def _flatten_single_ticker_columns(df: pd.DataFrame) -> pd.DataFrame:
    """단일 티커 다운로드도 (Price, Ticker) MultiIndex 컬럼으로 오는 yfinance 버전 대응."""
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = df.columns.get_level_values(0)
    return df


def _download_with_retry(**kwargs) -> pd.DataFrame:
    last_exc = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            df = yf.download(**kwargs)
            if df is not None and not df.empty:
                return _flatten_single_ticker_columns(df)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            logger.warning("yfinance 다운로드 실패 (attempt %d/%d): %s", attempt, _MAX_RETRIES, exc)
        time.sleep(_RETRY_DELAY_SEC)
    if last_exc:
        raise last_exc
    return pd.DataFrame()


def fetch_daily(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """일봉 OHLCV. 지표 워밍업(스토캐스틱/RSI 14 + failure swing lookback)에 충분한 기간."""
    df = _download_with_retry(
        tickers=ticker, period=period, interval="1d", auto_adjust=False, progress=False
    )
    return df.dropna(subset=["Close", "High", "Low"]) if not df.empty else df


def fetch_last_price(ticker: str) -> float | None:
    """/buy 명령에서 가격 생략 시 쓰는 현재가 (최근 일봉 종가, 정규장 중이면 실시간 반영)."""
    df = fetch_daily(ticker, period="5d")
    if df.empty:
        return None
    return float(df["Close"].iloc[-1])


def fetch_intraday_15m(ticker: str, period: str = "5d") -> pd.DataFrame:
    """
    15분봉 OHLCV. yfinance는 15m 데이터를 최근 60일까지만 제공.
    prepost=True로 프리마켓(04:00 ET~)/애프터마켓(~20:00 ET) 봉도 포함한다 —
    정규장 전후에도 진입 타이밍을 감지하기 위함. 거래량이 얕아 신호가
    더 노이즈성일 수 있음을 감안해야 한다.
    """
    df = _download_with_retry(
        tickers=ticker,
        period=period,
        interval="15m",
        auto_adjust=False,
        prepost=True,
        progress=False,
    )
    return df.dropna(subset=["Close", "High", "Low"]) if not df.empty else df
