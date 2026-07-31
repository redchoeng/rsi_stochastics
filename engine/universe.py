"""
감시 대상(top100) 산출.

전체 미국 시장을 무료 인프라로 실시간 스크리닝하는 것은 불가능하므로,
S&P500 + Nasdaq100 구성종목(유동성 높은 대형주 위주 후보군, ~550~600개)에서
최근 5거래일 거래대금(Close*Volume 합계) 기준 top100을 근사치로 산출한다.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests
import yfinance as yf

logger = logging.getLogger(__name__)

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
# Wikipedia의 Nasdaq-100 문서는 구성종목 표를 더 이상 문서에 직접 담지 않고
# nasdaq.com으로 외부 링크만 걸어둔다 (2026-07 확인) -> slickcharts로 대체
NASDAQ100_URL = "https://www.slickcharts.com/nasdaq100"

# Wikipedia/slickcharts 모두 User-Agent 없는 요청을 403으로 거부한다
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) stock_indicator_bot"}


def _read_html_tables(url: str) -> list[pd.DataFrame]:
    resp = requests.get(url, headers=_HEADERS, timeout=15)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


# yfinance는 티커의 '.'을 '-'로 표기한다 (예: BRK.B -> BRK-B)
def _normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper().replace(".", "-")


def fetch_candidate_pool() -> list[str]:
    """S&P500 + Nasdaq100 구성종목 티커 목록 (중복 제거)."""
    tickers: set[str] = set()

    try:
        sp500_tables = _read_html_tables(SP500_WIKI_URL)
        sp500 = sp500_tables[0]
        col = "Symbol" if "Symbol" in sp500.columns else sp500.columns[0]
        tickers.update(_normalize_ticker(t) for t in sp500[col].dropna())
    except Exception:
        logger.exception("S&P500 목록 로드 실패")

    try:
        nasdaq_tables = _read_html_tables(NASDAQ100_URL)
        nasdaq100 = next(t for t in nasdaq_tables if "Symbol" in t.columns)
        tickers.update(_normalize_ticker(t) for t in nasdaq100["Symbol"].dropna())
    except Exception:
        logger.exception("Nasdaq100 목록 로드 실패")

    if not tickers:
        raise RuntimeError("후보군 티커를 하나도 가져오지 못했습니다 (Wikipedia 파싱 실패)")

    return sorted(tickers)


def rank_top_n_by_dollar_volume(tickers: list[str], top_n: int = 100, lookback_days: int = 5) -> list[dict]:
    """최근 lookback_days 거래일 Close*Volume 합계 기준 상위 top_n 티커."""
    data = yf.download(
        tickers,
        period="1mo",
        interval="1d",
        group_by="ticker",
        auto_adjust=False,
        threads=True,
        progress=False,
    )

    rows = []
    for ticker in tickers:
        try:
            df = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
            df = df.dropna(subset=["Close", "Volume"])
            if df.empty:
                continue
            recent = df.tail(lookback_days)
            dollar_volume = float((recent["Close"] * recent["Volume"]).sum())
            if dollar_volume <= 0:
                continue
            rows.append({
                "ticker": ticker,
                "dollar_volume": dollar_volume,
                "last_close": float(recent["Close"].iloc[-1]),
            })
        except (KeyError, IndexError):
            continue

    rows.sort(key=lambda r: r["dollar_volume"], reverse=True)
    return rows[:top_n]


def build_universe(top_n: int = 100) -> list[dict]:
    tickers = fetch_candidate_pool()
    logger.info("후보군 %d개 티커 확보, 거래대금 랭킹 계산 중", len(tickers))
    return rank_top_n_by_dollar_volume(tickers, top_n=top_n)
