"""
장중 수분 간격으로 cron-job.org -> GitHub Actions workflow_dispatch가 이 스크립트를 실행한다.

1. 미국 정규장 시간이 아니면 즉시 종료 (cron-job.org의 KST 크론 문자열로 DST를 관리할 필요 없게 함)
2. config/daily_signals.json에서 방향(bullish/bearish)이 있는 종목만 15분봉으로 재검사
3. 조건 충족 + 같은 봉에서 아직 안 보낸 알림이면 텔레그램 전송, state.json에 기록
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
from alerts.daily_signals import load_daily_signals
from alerts.telegram import TelegramNotifier
from engine.data_fetcher import fetch_intraday_15m
from engine.indicators import compute_indicator_frame, latest_intraday_triggers

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

ROOT = Path(__file__).parent
SETTINGS_FILE = ROOT / "config" / "settings.yaml"

load_dotenv(ROOT / ".env")


def load_settings() -> dict:
    return yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8"))


def is_market_open(market_hours: dict) -> bool:
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
    args = parser.parse_args()

    settings = load_settings()

    if not args.ignore_market_hours and not is_market_open(settings["market_hours"]):
        logger.info("미국 정규장 시간이 아니므로 종료")
        return 0

    daily_signals = load_daily_signals()
    if not daily_signals:
        logger.warning("daily_signals.json이 비어 있음 — run_daily_scan.py를 먼저 실행해야 함")
        return 0

    notifier = TelegramNotifier()
    state = state_store.load_state()
    sent_count = 0

    for ticker, signal in daily_signals.items():
        direction = signal["direction"]
        try:
            df = fetch_intraday_15m(ticker, period=settings["intraday_check"]["history_period"])
            if df.empty:
                logger.warning("%s: 15분봉 데이터 없음, 스킵", ticker)
                continue
            indicator_df = compute_indicator_frame(df)
            triggers = latest_intraday_triggers(indicator_df, direction)
        except Exception:
            logger.exception("%s 처리 중 오류, 스킵", ticker)
            continue

        for trigger in triggers:
            if state_store.already_alerted(state, ticker, trigger["condition"], trigger["bar_timestamp"]):
                continue
            logger.info("%s %s 트리거 발생: %s", ticker, direction, trigger["condition"])
            if not args.dry_run:
                notifier.send_trigger_alert(ticker, direction, trigger)
            state_store.mark_alerted(state, ticker, trigger["condition"], trigger["bar_timestamp"])
            sent_count += 1

    state_store.save_state(state)
    logger.info("장중 체크 완료: %d개 신규 알림", sent_count)
    return 0


if __name__ == "__main__":
    sys.exit(main())
