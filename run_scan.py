"""
cron-job.org -> GitHub Actions workflow_dispatch가 04:00~20:00 ET 내내 수 분 간격으로
이 스크립트를 실행한다. '당일 한정' 구조: 매수/매도 모두 오늘 일봉 그 자체가
조건(스토캐/RSI 크로스, failure swing 중 2개 이상)을 충족해야만 알림이 나가고,
다음날로 안 넘어간다. 15분봉 확인 없이 일봉만으로 즉시 알림 — 진입/청산 타이밍은
사용자가 직접 판단.
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

from alerts import positions as position_store
from alerts import state as state_store
from alerts.telegram import TelegramNotifier
from engine.data_fetcher import fetch_daily
from engine.exit_strategy import evaluate_position_from_daily
from engine.indicators import compute_indicator_frame, today_daily_signal
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
        liquidity_floor_n=settings["universe"]["liquidity_floor_n"],
        lookback_days=settings["universe"]["lookback_days"],
        volatility_period=settings["universe"]["volatility_period"],
        force=args.force_universe_refresh,
    )
    tickers = [row["ticker"] for row in universe_rows]
    if args.limit:
        tickers = tickers[: args.limit]

    notifier = TelegramNotifier()
    state = state_store.load_state()
    buy_count = sell_count = exit_count = 0

    # /buy, /sell, /positions 명령 처리 — 알림이 떠도 실제로 산 종목만 여기서 추적된다
    positions = position_store.load_positions()
    positions = position_store.process_telegram_commands(notifier, str(today), positions, dry_run=args.dry_run)

    # ATR 트레일링 스톱 청산 체크 — 이번 주 유니버스에 없어도(팔지 않은 종목은) 계속 추적
    for ticker, pos in list(positions.items()):
        try:
            daily_df = fetch_daily(ticker, period=settings["daily_scan"]["history_period"])
            if daily_df.empty:
                continue
            result = evaluate_position_from_daily(
                daily_df,
                highest_price=pos["highest_price"],
                atr_period=settings["exit_strategy"]["atr_period"],
                multiplier=settings["exit_strategy"]["atr_multiplier"],
            )
            if result is None:
                continue  # ATR 워밍업 중
            pos["highest_price"] = result["highest_price"]
            if result["should_exit"]:
                current_price = float(daily_df["Close"].iloc[-1])
                logger.info("%s ATR 트레일링 스톱 도달: 진입 %.2f -> 현재 %.2f", ticker, pos["entry_price"], current_price)
                if not args.dry_run:
                    notifier.send_exit_alert(
                        ticker, pos["entry_price"], current_price, result["highest_price"], result["stop_level"]
                    )
                del positions[ticker]
                exit_count += 1
        except Exception:
            logger.exception("%s 포지션 청산 체크 중 오류, 스킵", ticker)
            continue

    position_store.save_positions(positions)

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

        # 오늘 일봉은 정규장 중 실시간으로 갱신되므로, 하루 안에서 방향이 여러 번
        # 뒤집힐 수 있다 (장중 데드크로스 -> 반등 후 골든크로스 등). 그날 먼저 확정된
        # 방향이 있으면 반대 방향 알림은 막아서, 같은 날 모순된 알림이 나가지 않게 한다.
        cross_date = signal["cross_date"]
        already_signaled_today = state_store.already_alerted(
            state, ticker, "daily_buy", cross_date
        ) or state_store.already_alerted(state, ticker, "daily_sell", cross_date)
        if already_signaled_today:
            continue

        daily_last = daily_ind.iloc[-1]
        if signal["direction"] == "bearish":
            logger.info("%s bearish 일봉 알림: %s", ticker, "+".join(signal["conditions"]))
            if not args.dry_run:
                notifier.send_daily_sell_alert(ticker, signal["conditions"], daily_last, cross_date)
            state_store.mark_alerted(state, ticker, "daily_sell", cross_date)
            sell_count += 1
        else:
            logger.info("%s bullish 일봉 알림: %s", ticker, "+".join(signal["conditions"]))
            if not args.dry_run:
                notifier.send_daily_buy_alert(ticker, signal["conditions"], daily_last, cross_date)
            state_store.mark_alerted(state, ticker, "daily_buy", cross_date)
            buy_count += 1

    state = state_store.prune_old_entries(state)
    state_store.save_state(state)
    logger.info(
        "스캔 완료: 매수 알림 %d건, 매도 알림 %d건, ATR 청산 %d건",
        buy_count, sell_count, exit_count,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
