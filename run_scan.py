"""
cron-job.org -> GitHub Actions workflow_dispatch가 04:00~20:00 ET 내내 수 분 간격으로
이 스크립트를 실행한다. '당일 한정' 구조:

- 매수: 오늘 일봉이 그 자체로 매수 조건(스토캐/RSI 골든크로스, failure swing 중 2개 이상)을
  충족해야만, 오늘 15분봉에서 같은 계열 조건이 새로 나올 때 알림. 다음날로 안 넘어간다.
- 매도: 오늘 일봉이 매도 조건(2개 이상)을 충족하면 15분봉 확인 없이 바로 알림 — 진입
  타이밍은 사용자가 직접 판단.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from dotenv import load_dotenv

from alerts import state as state_store
from alerts.telegram import TelegramNotifier
from engine.data_fetcher import fetch_daily, fetch_intraday_15m
from engine.indicators import compute_indicator_frame, latest_intraday_triggers, today_daily_signal
from engine.universe import get_or_refresh_universe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
SETTINGS_FILE = ROOT / "config" / "settings.yaml"

load_dotenv(ROOT / ".env")


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8"))


def is_market_open(market_hours: dict) -> bool:
    """프리마켓(open)~애프터마켓(close) 전체 세션 기준 (settings.yaml 참고)."""
    tz = ZoneInfo(market_hours["timezone"])
    now = dt.datetime.now(tz)
    if now.weekday() >= 5:  # 토/일
        return False
    open_t = dt.datetime.strptime(market_hours["open"], "%H:%M").time()
    close_t = dt.datetime.strptime(market_hours["close"], "%H:%M").time()
    return open_t <= now.time() <= close_t


def classify_session(bar_timestamp: str, market_hours: dict) -> str:
    """봉 시각이 프리마켓/정규장/애프터마켓 중 어디인지 (알림에서 노이즈 가능성 판단용)."""
    ts = dt.datetime.fromisoformat(bar_timestamp)
    regular_open = dt.datetime.strptime(market_hours["regular_open"], "%H:%M").time()
    regular_close = dt.datetime.strptime(market_hours["regular_close"], "%H:%M").time()
    t = ts.timetz().replace(tzinfo=None)
    if t < regular_open:
        return "premarket"
    if t >= regular_close:
        return "afterhours"
    return "regular"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ignore-market-hours", action="store_true", help="로컬 테스트용")
    parser.add_argument("--dry-run", action="store_true", help="텔레그램 전송 없이 콘솔에만 출력")
    parser.add_argument("--force-universe-refresh", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="테스트용: 유니버스 상위 N개만 처리")
    parser.add_argument(
        "--as-of-date",
        type=str,
        default=None,
        help="테스트용: 오늘 날짜(YYYY-MM-DD) 대신 이 날짜를 '오늘'로 간주 (예: 주말에 직전 거래일 기준으로 확인)",
    )
    args = parser.parse_args()

    settings = load_settings()
    market_hours = settings["market_hours"]

    if not args.ignore_market_hours and not is_market_open(market_hours):
        logger.info("미국 정규장(프리~애프터마켓) 시간이 아니므로 종료")
        return 0

    today = (
        dt.date.fromisoformat(args.as_of_date)
        if args.as_of_date
        else dt.datetime.now(ZoneInfo(market_hours["timezone"])).date()
    )

    universe_rows = get_or_refresh_universe(
        top_n=settings["universe"]["top_n"],
        refresh_weekday=settings["universe"]["refresh_weekday"],
        timezone=market_hours["timezone"],
        force=args.force_universe_refresh,
    )
    tickers = [row["ticker"] for row in universe_rows]
    if args.limit:
        tickers = tickers[: args.limit]

    notifier = TelegramNotifier()
    state = state_store.load_state()
    buy_count = sell_count = 0

    for ticker in tickers:
        try:
            daily_df = fetch_daily(ticker, period=settings["daily_scan"]["history_period"])
            if daily_df.empty:
                continue
            daily_ind = compute_indicator_frame(daily_df)
            signal = today_daily_signal(daily_ind, today, min_conditions=settings["daily_scan"]["min_conditions"])
        except Exception:
            logger.exception("%s 일봉 처리 중 오류, 스킵", ticker)
            continue

        if signal is None:
            continue

        if signal["direction"] == "bearish":
            daily_last = daily_ind.iloc[-1]
            for condition in signal["conditions"]:
                if state_store.already_alerted(state, ticker, condition, signal["cross_date"]):
                    continue
                logger.info("%s bearish 일봉 알림: %s", ticker, condition)
                if not args.dry_run:
                    notifier.send_daily_sell_alert(ticker, condition, daily_last, signal["cross_date"])
                state_store.mark_alerted(state, ticker, condition, signal["cross_date"])
                sell_count += 1
            continue

        # bullish: 오늘 일봉이 조건을 만족했으니 오늘 15분봉에서 진입 트리거 확인
        try:
            intraday_df = fetch_intraday_15m(ticker, period=settings["intraday_check"]["history_period"])
            if intraday_df.empty:
                continue
            intraday_ind = compute_indicator_frame(intraday_df)
            if intraday_ind.index[-1].date() != today:
                continue  # 오늘자 15분봉이 아직 없음
            triggers = latest_intraday_triggers(intraday_ind, "bullish")
        except Exception:
            logger.exception("%s 15분봉 처리 중 오류, 스킵", ticker)
            continue

        for trigger in triggers:
            if state_store.already_alerted(state, ticker, trigger["condition"], trigger["bar_timestamp"]):
                continue
            trigger["session"] = classify_session(trigger["bar_timestamp"], market_hours)
            logger.info("%s bullish 15분봉 트리거: %s (%s)", ticker, trigger["condition"], trigger["session"])
            if not args.dry_run:
                notifier.send_trigger_alert(ticker, "bullish", trigger)
            state_store.mark_alerted(state, ticker, trigger["condition"], trigger["bar_timestamp"])
            buy_count += 1

    state = state_store.prune_old_entries(state)
    state_store.save_state(state)
    logger.info("스캔 완료: 매수 알림 %d건, 매도 알림 %d건", buy_count, sell_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
