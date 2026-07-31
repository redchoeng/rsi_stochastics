"""텔레그램 알림 전송 (etf_guide alerts/telegram.py의 TelegramNotifier 패턴을 이식)."""
from __future__ import annotations

import logging
import os

import requests

logger = logging.getLogger(__name__)

CONDITION_LABELS = {
    "stoch_golden_cross": "스토캐스틱 골든크로스",
    "stoch_death_cross": "스토캐스틱 데드크로스",
    "stoch_bull_failure_swing": "스토캐스틱 Bullish Failure Swing",
    "stoch_bear_failure_swing": "스토캐스틱 Bearish Failure Swing",
    "rsi_golden_cross": "RSI 골든크로스",
    "rsi_death_cross": "RSI 데드크로스",
}


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")

    def send_message(self, text: str) -> bool:
        if not self.token or not self.chat_id:
            logger.warning("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID가 설정되지 않아 전송을 건너뜁니다")
            return False
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML"}
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            return True
        except requests.RequestException:
            logger.exception("텔레그램 전송 실패")
            return False

    def send_trigger_alert(self, ticker: str, direction: str, trigger: dict) -> bool:
        label = CONDITION_LABELS.get(trigger["condition"], trigger["condition"])
        emoji = "🟢" if direction == "bullish" else "🔴"
        session = trigger.get("session", "regular")
        session_label = {"premarket": "⚠️ 프리마켓", "afterhours": "⚠️ 애프터마켓", "regular": "정규장"}[session]
        text = (
            f"{emoji} <b>{ticker}</b> — {label}\n"
            f"세션: {session_label}\n"
            f"가격: {trigger['price']:.2f}\n"
            f"Stoch %K/%D: {trigger['stoch_k']:.1f} / {trigger['stoch_d']:.1f}\n"
            f"RSI(14): {trigger['rsi']:.1f}\n"
            f"봉 시각: {trigger['bar_timestamp']} (15m)"
        )
        return self.send_message(text)

    def send_daily_sell_alert(self, ticker: str, condition: str, daily_row, cross_date: str) -> bool:
        """
        매도는 15분봉 확인 없이 일봉 데드크로스(2/3 조건)만으로 즉시 알림.
        진입 타이밍은 사용자가 직접 판단하는 걸 전제로 한다.
        """
        label = CONDITION_LABELS.get(condition, condition)
        text = (
            f"🔴 <b>{ticker}</b> — {label} (일봉)\n"
            f"기준일: {cross_date}\n"
            f"가격: {daily_row['Close']:.2f}\n"
            f"일봉 Stoch %K/%D: {daily_row['stoch_k']:.1f} / {daily_row['stoch_d']:.1f}\n"
            f"RSI(14): {daily_row['rsi']:.1f}\n"
            f"⚠️ 15분봉 확인 없이 일봉만으로 알림 — 매도 타이밍은 직접 판단"
        )
        return self.send_message(text)
