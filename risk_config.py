"""Κεντρικές ρυθμίσεις ρίσκου για το demo paper-trading wallet.

Τα SHORT είναι συνθετική προσομοίωση 1x, χωρίς πραγματικό δανεισμό,
leverage, exchange orders, fees ή funding.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Tuple


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class RiskSettings:
    # LONG / spot simulation
    stop_loss_pct: float = 35.0
    take_profit_1_pct: float = 50.0
    take_profit_1_sell_pct: float = 25.0
    take_profit_2_pct: float = 100.0
    take_profit_2_sell_pct: float = 25.0
    take_profit_3_pct: float = 200.0
    take_profit_3_sell_pct: float = 25.0
    trailing_stop_activation_pct: float = 100.0
    trailing_stop_distance_pct: float = 20.0
    max_position_age_hours: int = 168

    # Synthetic SHORT / 1x margin simulation
    short_stop_loss_pct: float = 20.0
    short_take_profit_1_pct: float = 15.0
    short_take_profit_1_sell_pct: float = 25.0
    short_take_profit_2_pct: float = 30.0
    short_take_profit_2_sell_pct: float = 25.0
    short_take_profit_3_pct: float = 50.0
    short_take_profit_3_sell_pct: float = 25.0
    short_trailing_stop_activation_pct: float = 20.0
    short_trailing_stop_distance_pct: float = 10.0
    short_max_position_age_hours: int = 48
    short_liquidation_pct: float = 75.0

    stale_price_minutes: int = 5
    max_open_positions: int = 10
    max_short_positions: int = 2

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def take_profit_levels(self, side: str = "LONG") -> Tuple[Tuple[str, float, float], ...]:
        if str(side or "LONG").upper() == "SHORT":
            return (
                ("SHORT_TAKE_PROFIT_1", self.short_take_profit_1_pct, self.short_take_profit_1_sell_pct),
                ("SHORT_TAKE_PROFIT_2", self.short_take_profit_2_pct, self.short_take_profit_2_sell_pct),
                ("SHORT_TAKE_PROFIT_3", self.short_take_profit_3_pct, self.short_take_profit_3_sell_pct),
            )
        return (
            ("TAKE_PROFIT_1", self.take_profit_1_pct, self.take_profit_1_sell_pct),
            ("TAKE_PROFIT_2", self.take_profit_2_pct, self.take_profit_2_sell_pct),
            ("TAKE_PROFIT_3", self.take_profit_3_pct, self.take_profit_3_sell_pct),
        )

    def stop_loss_for(self, side: str) -> float:
        return self.short_stop_loss_pct if str(side).upper() == "SHORT" else self.stop_loss_pct

    def trailing_activation_for(self, side: str) -> float:
        return (
            self.short_trailing_stop_activation_pct
            if str(side).upper() == "SHORT"
            else self.trailing_stop_activation_pct
        )

    def trailing_distance_for(self, side: str) -> float:
        return (
            self.short_trailing_stop_distance_pct
            if str(side).upper() == "SHORT"
            else self.trailing_stop_distance_pct
        )

    def max_age_for(self, side: str) -> int:
        return self.short_max_position_age_hours if str(side).upper() == "SHORT" else self.max_position_age_hours


RISK_SETTINGS = RiskSettings(
    stop_loss_pct=_env_float("PAPER_STOP_LOSS_PCT", 35.0),
    take_profit_1_pct=_env_float("PAPER_TAKE_PROFIT_1_PCT", 50.0),
    take_profit_1_sell_pct=_env_float("PAPER_TAKE_PROFIT_1_SELL_PCT", 25.0),
    take_profit_2_pct=_env_float("PAPER_TAKE_PROFIT_2_PCT", 100.0),
    take_profit_2_sell_pct=_env_float("PAPER_TAKE_PROFIT_2_SELL_PCT", 25.0),
    take_profit_3_pct=_env_float("PAPER_TAKE_PROFIT_3_PCT", 200.0),
    take_profit_3_sell_pct=_env_float("PAPER_TAKE_PROFIT_3_SELL_PCT", 25.0),
    trailing_stop_activation_pct=_env_float("PAPER_TRAILING_ACTIVATION_PCT", 100.0),
    trailing_stop_distance_pct=_env_float("PAPER_TRAILING_DISTANCE_PCT", 20.0),
    max_position_age_hours=_env_int("PAPER_MAX_POSITION_AGE_HOURS", 168),
    short_stop_loss_pct=_env_float("PAPER_SHORT_STOP_LOSS_PCT", 20.0),
    short_take_profit_1_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_1_PCT", 15.0),
    short_take_profit_1_sell_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_1_SELL_PCT", 25.0),
    short_take_profit_2_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_2_PCT", 30.0),
    short_take_profit_2_sell_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_2_SELL_PCT", 25.0),
    short_take_profit_3_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_3_PCT", 50.0),
    short_take_profit_3_sell_pct=_env_float("PAPER_SHORT_TAKE_PROFIT_3_SELL_PCT", 25.0),
    short_trailing_stop_activation_pct=_env_float("PAPER_SHORT_TRAILING_ACTIVATION_PCT", 20.0),
    short_trailing_stop_distance_pct=_env_float("PAPER_SHORT_TRAILING_DISTANCE_PCT", 10.0),
    short_max_position_age_hours=_env_int("PAPER_SHORT_MAX_POSITION_AGE_HOURS", 48),
    short_liquidation_pct=_env_float("PAPER_SHORT_LIQUIDATION_PCT", 75.0),
    stale_price_minutes=_env_int("PAPER_STALE_PRICE_MINUTES", 5),
    max_open_positions=_env_int("PAPER_MAX_OPEN_POSITIONS", 10),
    max_short_positions=_env_int("PAPER_MAX_SHORT_POSITIONS", 2),
)
