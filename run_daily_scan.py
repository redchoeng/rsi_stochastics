"""
일 1회(미장 마감 직후) 실행:
1. 월요일이면 top100 유니버스(config/universe.json) 재계산
2. 유니버스 각 종목의 일봉 스토캐스틱/RSI로 방향 필터(bullish/bearish) 산출 -> config/daily_signals.json
3. state.json의 오래된 dedup 엔트리 정리
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo
import datetime as dt

import yaml

from alerts import state as state_store
from alerts.daily_signals import save_daily_signals
from engine import universe as universe_module
from engine.data_fetcher import fetch_daily
from engine.indicators import compute_indicator_frame, latest_daily_regime

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
UNIVERSE_FILE = ROOT / "config" / "universe.json"
SETTINGS_FILE = ROOT / "config" / "settings.yaml"


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8"))


def is_refresh_day(refresh_weekday: int) -> bool:
    now_et = dt.datetime.now(ZoneInfo("America/New_York"))
    return now_et.weekday() == refresh_weekday


def load_universe() -> list[dict]:
    if UNIVERSE_FILE.exists():
        return json.loads(UNIVERSE_FILE.read_text(encoding="utf-8"))
    return []


def save_universe(rows: list[dict]) -> None:
    UNIVERSE_FILE.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-universe-refresh", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="테스트용: 유니버스 상위 N개만 처리")
    args = parser.parse_args()

    settings = load_settings()

    universe_rows = load_universe()
    if args.force_universe_refresh or not universe_rows or is_refresh_day(settings["universe"]["refresh_weekday"]):
        logger.info("유니버스(top%d) 재계산 시작", settings["universe"]["top_n"])
        universe_rows = universe_module.build_universe(top_n=settings["universe"]["top_n"])
        save_universe(universe_rows)
        logger.info("유니버스 %d개 종목 저장 완료", len(universe_rows))
    else:
        logger.info("기존 유니버스(%d개 종목) 재사용 (재계산은 월요일에만)", len(universe_rows))

    tickers = [row["ticker"] for row in universe_rows]
    if args.limit:
        tickers = tickers[: args.limit]

    daily_signals: dict = {}
    for ticker in tickers:
        try:
            df = fetch_daily(ticker, period=settings["daily_scan"]["history_period"])
            if df.empty:
                logger.warning("%s: 일봉 데이터 없음, 스킵", ticker)
                continue
            indicator_df = compute_indicator_frame(df)
            regime = latest_daily_regime(indicator_df, min_conditions=settings["daily_scan"]["min_conditions"])
            if regime:
                daily_signals[ticker] = regime
        except Exception:
            logger.exception("%s 처리 중 오류, 스킵", ticker)

    save_daily_signals(daily_signals)
    logger.info(
        "일봉 필터 완료: %d개 종목 중 %d개 방향 확정 (bullish=%d, bearish=%d)",
        len(tickers),
        len(daily_signals),
        sum(1 for v in daily_signals.values() if v["direction"] == "bullish"),
        sum(1 for v in daily_signals.values() if v["direction"] == "bearish"),
    )

    pruned = state_store.prune_old_entries(state_store.load_state())
    state_store.save_state(pruned)

    return 0


if __name__ == "__main__":
    sys.exit(main())
