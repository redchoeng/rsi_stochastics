"""
감시 대상(top100) 산출.

전체 미국 시장을 무료 인프라로 실시간 스크리닝하는 것은 불가능하므로,
S&P500 + Nasdaq100 구성종목(유동성 높은 대형주 위주 후보군, ~550~600개)에서 2단계로 뽑는다:
1. 거래대금(Close*Volume) 상위 liquidity_floor_n개 — 유동성 최소 기준 (너무 얇은 종목 제외)
2. 그 안에서 ATR%(변동성) 상위 top_n개 — PG/KO 같은 거래대금은 크지만 하루 변동폭이
   작아 크로스가 나와도 수익이 잘 안 나는 종목 대신, 실제로 움직이는 종목 위주로 재랭킹
"""
from __future__ import annotations

import datetime as dt
import io
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import yfinance as yf

from engine.indicators import compute_atr

logger = logging.getLogger(__name__)

UNIVERSE_FILE = Path(__file__).parent.parent / "config" / "universe.json"

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


def compute_atr_percent(df: pd.DataFrame, period: int = 14) -> float:
    """최근 period일 ATR을 현재가 대비 %로 환산 (변동성 랭킹용)."""
    atr = compute_atr(df, period=period).iloc[-1]
    last_close = df["Close"].iloc[-1]
    if pd.isna(atr) or not last_close:
        return 0.0
    return float(atr / last_close * 100)


def rank_by_liquidity_then_volatility(
    tickers: list[str],
    top_n: int = 100,
    liquidity_floor_n: int = 300,
    lookback_days: int = 5,
    volatility_period: int = 14,
) -> list[dict]:
    """
    1단계: 최근 lookback_days 거래일 Close*Volume 합계로 liquidity_floor_n개까지 유동성 필터.
    2단계: 그 안에서 ATR%(volatility_period일) 상위 top_n개로 재랭킹.
    """
    data = yf.download(
        tickers,
        period="2mo",
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
            df = df.dropna(subset=["Close", "High", "Low", "Volume"])
            if len(df) < volatility_period + 1:
                continue
            recent = df.tail(lookback_days)
            dollar_volume = float((recent["Close"] * recent["Volume"]).sum())
            if dollar_volume <= 0:
                continue
            rows.append({
                "ticker": ticker,
                "dollar_volume": dollar_volume,
                "atr_percent": compute_atr_percent(df, period=volatility_period),
                "last_close": float(recent["Close"].iloc[-1]),
            })
        except (KeyError, IndexError):
            continue

    rows.sort(key=lambda r: r["dollar_volume"], reverse=True)
    liquidity_pool = rows[:liquidity_floor_n]

    liquidity_pool.sort(key=lambda r: r["atr_percent"], reverse=True)
    return liquidity_pool[:top_n]


def build_universe(
    top_n: int = 100,
    liquidity_floor_n: int = 300,
    lookback_days: int = 5,
    volatility_period: int = 14,
) -> list[dict]:
    tickers = fetch_candidate_pool()
    logger.info("후보군 %d개 티커 확보, 유동성->변동성 2단계 랭킹 계산 중", len(tickers))
    return rank_by_liquidity_then_volatility(
        tickers,
        top_n=top_n,
        liquidity_floor_n=liquidity_floor_n,
        lookback_days=lookback_days,
        volatility_period=volatility_period,
    )


def load_universe() -> list[dict]:
    if UNIVERSE_FILE.exists():
        return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
    return []


def save_universe(rows: list[dict]) -> None:
    UNIVERSE_FILE.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def is_refresh_day(refresh_weekday: int, timezone: str = "America/New_York") -> bool:
    return dt.datetime.now(ZoneInfo(timezone)).weekday() == refresh_weekday


def get_or_refresh_universe(
    top_n: int,
    refresh_weekday: int,
    timezone: str,
    liquidity_floor_n: int = 300,
    lookback_days: int = 5,
    volatility_period: int = 14,
    force: bool = False,
) -> list[dict]:
    """월요일(refresh_weekday)에만, 또는 파일이 없거나 force일 때만 재계산 — 매 폴링마다 다시 랭킹하지 않기 위함."""
    rows = load_universe()
    if force or not rows or is_refresh_day(refresh_weekday, timezone):
        logger.info("유니버스(top%d) 재계산 시작", top_n)
        rows = build_universe(
            top_n=top_n,
            liquidity_floor_n=liquidity_floor_n,
            lookback_days=lookback_days,
            volatility_period=volatility_period,
        )
        save_universe(rows)
        logger.info("유니버스 %d개 종목 저장 완료", len(rows))
    else:
        logger.info("기존 유니버스(%d개 종목) 재사용 (재계산은 월요일에만)", len(rows))
    return rows
