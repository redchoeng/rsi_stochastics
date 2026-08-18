"""
수동 매수 포지션 추적.

매수 알림이 떠도 사용자가 실제로 사지 않을 수 있어서, 봇이 알림만으로
자동으로 포지션을 잡지 않는다 — 텔레그램 /buy로 직접 등록한 것만 추적하고
ATR 트레일링 스톱(engine/exit_strategy.py)으로 청산 여부를 감시한다.

명령:
  /buy TICKER [가격]   - 가격 생략 시 현재가로 등록 (기존 포지션 있으면 덮어씀)
  /sell TICKER [가격]  - 수동 청산 (봇이 스톱을 못 잡았거나 직접 판단한 경우)
  /positions           - 현재 추적 중인 포지션 목록
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from engine.data_fetcher import fetch_last_price

logger = logging.getLogger(__name__)

POSITIONS_FILE = Path(__file__).parent / "positions.json"
OFFSET_FILE = Path(__file__).parent / "telegram_offset.json"


def load_positions() -> dict:
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_positions(positions: dict) -> None:
    POSITIONS_FILE.write_text(json.dumps(positions, indent=2, ensure_ascii=False), encoding="utf-8")


def _load_offset() -> int | None:
    if OFFSET_FILE.exists():
        try:
            return json.loads(OFFSET_FILE.read_text(encoding="utf-8")).get("offset")
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _save_offset(offset: int) -> None:
    OFFSET_FILE.write_text(json.dumps({"offset": offset}), encoding="utf-8")


def _parse_command(text: str) -> tuple[str, list[str]] | None:
    parts = text.strip().split()
    if not parts or not parts[0].startswith("/"):
        return None
    return parts[0].lower(), parts[1:]


def process_telegram_commands(notifier, today: str, positions: dict, dry_run: bool = False) -> dict:
    """새 텔레그램 메시지를 확인해 /buy, /sell, /positions 명령을 처리하고 positions를 갱신해 반환."""
    send = (lambda _text: None) if dry_run else notifier.send_message
    offset = _load_offset()
    updates = notifier.get_updates(offset=offset)
    if not updates:
        return positions

    new_offset = offset
    for update in updates:
        new_offset = update["update_id"] + 1
        message = update.get("message")
        if not message or "text" not in message:
            continue
        if str(message.get("chat", {}).get("id")) != str(notifier.chat_id):
            continue  # 다른 채팅방에서 온 메시지는 무시

        parsed = _parse_command(message["text"])
        if not parsed:
            continue
        command, args = parsed

        if command == "/buy" and args:
            ticker = args[0].upper()
            if len(args) >= 2:
                try:
                    price = float(args[1])
                except ValueError:
                    send(f"❌ 가격을 숫자로 인식 못했습니다: {args[1]}")
                    continue
            else:
                price = fetch_last_price(ticker)
                if price is None:
                    send(f"❌ {ticker} 현재가를 못 가져왔습니다. 가격을 직접 입력해주세요: /buy {ticker} 123.45")
                    continue
            replaced = ticker in positions
            positions[ticker] = {"entry_price": price, "entry_date": today, "highest_price": price}
            prefix = "🔁 (기존 포지션 덮어씀) " if replaced else "✅ "
            send(f"{prefix}<b>{ticker}</b> 진입 등록: {price:.2f} ({today})")

        elif command == "/sell" and args:
            ticker = args[0].upper()
            if ticker not in positions:
                send(f"❌ {ticker} 추적 중인 포지션이 없습니다.")
                continue
            entry_price = positions[ticker]["entry_price"]
            if len(args) >= 2:
                try:
                    exit_price = float(args[1])
                except ValueError:
                    exit_price = fetch_last_price(ticker)
            else:
                exit_price = fetch_last_price(ticker)
            del positions[ticker]
            if exit_price is not None:
                pnl_pct = (exit_price - entry_price) / entry_price * 100
                send(
                    f"✅ <b>{ticker}</b> 수동 청산: {exit_price:.2f} (진입 {entry_price:.2f}, {pnl_pct:+.1f}%)"
                )
            else:
                send(f"✅ <b>{ticker}</b> 포지션 추적을 종료했습니다 (청산가 미확인).")

        elif command == "/positions":
            if not positions:
                send("현재 추적 중인 포지션이 없습니다.")
            else:
                lines = [
                    f"• <b>{t}</b> 진입 {p['entry_price']:.2f} ({p['entry_date']}), 최고가 {p['highest_price']:.2f}"
                    for t, p in positions.items()
                ]
                send("📋 <b>추적 중인 포지션</b>\n" + "\n".join(lines))

    _save_offset(new_offset)
    return positions
