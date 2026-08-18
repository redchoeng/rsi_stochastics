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

SESSION_LABELS = {"premarket": "⚠️ 프리마켓", "afterhours": "⚠️ 애프터마켓", "regular": "정규장"}


def _join_labels(conditions: list[str]) -> str:
    return " + ".join(CONDITION_LABELS.get(c, c) for c in conditions)


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

    def send_trigger_alert(self, ticker: str, conditions: list[str], bar: dict, session: str) -> bool:
        """
        오늘의 추천 매수 타이밍 — 하루 중 처음으로 유효한(거래량 필터 통과) 15분봉
        신호가 나온 시점 하나만 보낸다 (당일 재알림 없음).
        """
        label = _join_labels(conditions)
        session_label = SESSION_LABELS[session]
        text = (
            f"🟢 <b>{ticker}</b> — {label}\n"
            f"오늘의 매수 타이밍 추천\n"
            f"세션: {session_label}\n"
            f"가격: {bar['price']:.2f}\n"
            f"Stoch %K/%D: {bar['stoch_k']:.1f} / {bar['stoch_d']:.1f}\n"
            f"RSI(14): {bar['rsi']:.1f}\n"
            f"봉 시각: {bar['bar_timestamp']} (15m)"
        )
        return self.send_message(text)

    def send_daily_buy_candidate_alert(self, ticker: str, conditions: list[str], daily_row, cross_date: str) -> bool:
        """
        오늘 일봉이 매수 조건(2/3)을 만족했다는 1차 알림 — 아직 진입 신호는 아니고,
        이제부터 오늘 15분봉에서 같은 계열 조건이 나오는지 지켜보는 중이라는 안내.
        실제 진입 알림은 send_trigger_alert가 하루 한 번만 별도로 보낸다.
        """
        label = _join_labels(conditions)
        text = (
            f"🟡 <b>{ticker}</b> — {label} (일봉, 매수 후보)\n"
            f"기준일: {cross_date}\n"
            f"가격: {daily_row['Close']:.2f}\n"
            f"일봉 Stoch %K/%D: {daily_row['stoch_k']:.1f} / {daily_row['stoch_d']:.1f}\n"
            f"RSI(14): {daily_row['rsi']:.1f}\n"
            f"➡️ 오늘 15분봉에서 같은 계열 신호 나오면 진입 타이밍 별도 전송"
        )
        return self.send_message(text)

    def send_daily_sell_alert(self, ticker: str, conditions: list[str], daily_row, cross_date: str) -> bool:
        """
        매도는 15분봉 확인 없이 일봉 데드크로스(2/3 조건)만으로 즉시 알림.
        진입 타이밍은 사용자가 직접 판단하는 걸 전제로 한다.
        """
        label = _join_labels(conditions)
        text = (
            f"🔴 <b>{ticker}</b> — {label} (일봉)\n"
            f"기준일: {cross_date}\n"
            f"가격: {daily_row['Close']:.2f}\n"
            f"일봉 Stoch %K/%D: {daily_row['stoch_k']:.1f} / {daily_row['stoch_d']:.1f}\n"
            f"RSI(14): {daily_row['rsi']:.1f}\n"
            f"⚠️ 15분봉 확인 없이 일봉만으로 알림 — 매도 타이밍은 직접 판단"
        )
        return self.send_message(text)

    def send_exit_alert(
        self, ticker: str, entry_price: float, exit_price: float, highest_price: float, stop_level: float
    ) -> bool:
        """/buy로 등록한 포지션이 ATR 트레일링 스톱에 닿아 자동 청산 신호가 나왔을 때."""
        pnl_pct = (exit_price - entry_price) / entry_price * 100
        emoji = "🟢" if pnl_pct >= 0 else "🔴"
        text = (
            f"{emoji} <b>{ticker}</b> — ATR 트레일링 스톱 도달, 청산 권장\n"
            f"진입가: {entry_price:.2f} → 현재가: {exit_price:.2f} ({pnl_pct:+.1f}%)\n"
            f"보유 중 최고가: {highest_price:.2f}\n"
            f"스톱 라인: {stop_level:.2f}"
        )
        return self.send_message(text)

    def get_updates(self, offset: int | None = None, timeout: int = 0) -> list[dict]:
        """/buy, /sell, /positions 명령 폴링용. offset은 마지막으로 처리한 update_id + 1."""
        if not self.token:
            return []
        url = f"https://api.telegram.org/bot{self.token}/getUpdates"
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json().get("result", [])
        except requests.RequestException:
            logger.exception("getUpdates 실패")
            return []
