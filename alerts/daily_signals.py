"""일봉 필터 결과(config/daily_signals.json) 로드/저장. 15분봉 트리거가 이 결과를 읽어 방향을 판단."""
from __future__ import annotations

import json
from pathlib import Path

DAILY_SIGNALS_FILE = Path(__file__).parent.parent / "config" / "daily_signals.json"


def load_daily_signals() -> dict:
    if DAILY_SIGNALS_FILE.exists():
        try:
            return json.loads(DAILY_SIGNALS_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_daily_signals(signals: dict) -> None:
    DAILY_SIGNALS_FILE.write_text(
        json.dumps(signals, indent=2, ensure_ascii=False), encoding="utf-8"
    )
