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
        text = (
            f"{emoji} <b>{ticker}</b> — {label}\n"
            f"가격: {trigger['price']:.2f}\n"
            f"Stoch %K/%D: {trigger['stoch_k']:.1f} / {trigger['stoch_d']:.1f}\n"
            f"RSI(14): {trigger['rsi']:.1f}\n"
            f"봉 시각: {trigger['bar_timestamp']} (15m)"
        )
        return self.send_message(text)
