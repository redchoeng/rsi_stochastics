"""
장중 트리거 중복 알림 방지.

etf_guide의 dedup은 'KST 날짜 기준 하루 1번'이라 하루 수십 번 폴링하는 이 봉에는
맞지 않는다 (같은 15분봉 안에서 여러 번 폴링되므로 매번 새 알림이 나가버림).
그래서 키를 (ticker, condition, bar_timestamp)로 바꿔 '같은 봉 + 같은 조건'은
한 번만 알림이 나가게 한다.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path(__file__).parent / "state.json"
KST = timezone(timedelta(hours=9))


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def make_key(ticker: str, condition: str, bar_timestamp: str) -> str:
    return f"{ticker}|{condition}|{bar_timestamp}"


def already_alerted(state: dict, ticker: str, condition: str, bar_timestamp: str) -> bool:
    return state.get(make_key(ticker, condition, bar_timestamp), False)


def mark_alerted(state: dict, ticker: str, condition: str, bar_timestamp: str) -> None:
    state[make_key(ticker, condition, bar_timestamp)] = True


def prune_old_entries(state: dict, keep_days: int = 7) -> dict:
    """bar_timestamp가 keep_days보다 오래된 dedup 키를 정리 (state.json 무한 증가 방지)."""
    cutoff = datetime.now(KST) - timedelta(days=keep_days)
    pruned = {}
    for key, value in state.items():
        parts = key.split("|", 2)
        if len(parts) != 3:
            pruned[key] = value  # 형식이 다른 키(향후 확장용)는 보존
            continue
        _, _, bar_ts = parts
        try:
            ts = datetime.fromisoformat(bar_ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=KST)
            if ts >= cutoff:
                pruned[key] = value
        except ValueError:
            pruned[key] = value
    return pruned
