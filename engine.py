"""Margin calculation engine with scenario stress testing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

import yaml


class MarginModel(str, Enum):
    """Margin calculation model."""

    STANDARD = "standard"  # broker-style: fixed % of notional, leverage ignored
    LEVERAGED = "leveraged"  # exchange-style: notional / leverage × rate


class PositionDirection(str, Enum):
    """Position direction (long or short)."""

    LONG = "long"
    SHORT = "short"


class AssetClass(str, Enum):
    """Asset classes with margin rules derived from the manual."""

    MARGIN_APPROVED_STOCK = "margin_approved_stock"
    SHORT_STOCK = "short_stock"
    LEVERAGED_ETF_LONG = "leveraged_etf_long"
    LEVERAGED_ETF_SHORT = "leveraged_etf_short"
    CONVERTIBLE_BOND = "convertible_bond"
    CORPORATE_MUNICIPAL_BOND = "corporate_municipal_bond"
    US_GOVERNMENT_BOND = "us_government_bond"
    SHORT_BOX = "short_box"


def _as_decimal(value: Decimal | float | str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _require(value: Decimal | None, message: str) -> Decimal:
    if value is None:
        raise ValueError(message)
    return value


def _rate_from_per_share(
    per_share: Decimal,
    *,
    price: Decimal,
) -> Decimal:
    """Convert a per-share margin requirement into an effective rate."""
    if price <= 0:
        raise ValueError("price must be positive when calculating per-share margin")
    return per_share / price


def _fixed_per_share_margin(
    per_share: Decimal,
    notional: Decimal,
    *,
    price: Decimal,
) -> Decimal:
    """Calculate fixed per-share margin as a rate of notional.
    
    For short stocks, maintenance margin is a fixed $ per share, not a percentage.
    This calculates: (shares * per_share) / notional = per_share / price
    """
    if price <= 0:
        raise ValueError("price must be positive when calculating per-share margin")
    return per_share / price


def _resolve_docx_rates(config: MarginConfig) -> tuple[Decimal, Decimal, Decimal | None, Decimal | None]:
    """Resolve initial and maintenance margin rates from the manual.
    
    Returns: (init_rate, maint_rate, fixed_maint_per_share, shares)
    fixed_maint_per_share is set when maintenance is a fixed $ per share amount.
    """
    asset_class = config.asset_class
    price = config.price
    leverage = config.leverage
    notional = config.notional
    face_value = config.face_value

    if asset_class == AssetClass.MARGIN_APPROVED_STOCK:
        if price is None:
            raise ValueError("price is required for margin-approved stock rules")
        if price <= Decimal("3"):
            return Decimal("1"), Decimal("1"), None, None
        return Decimal("0.50"), Decimal("0.25"), None, None

    if asset_class == AssetClass.SHORT_STOCK:
        if price is None:
            raise ValueError("price is required for short-stock rules")
        shares = notional / price
        if price >= Decimal("16.70"):
            return Decimal("0.50"), Decimal("0.30"), None, None
        if price >= Decimal("5"):
            # 100% initial, $5/share maintenance
            return Decimal("1"), _fixed_per_share_margin(Decimal("5"), notional, price=price), Decimal("5"), shares
        if price >= Decimal("2.51"):
            return Decimal("1"), Decimal("1"), None, None
        # 100% initial, $2.50/share maintenance
        return Decimal("1"), _fixed_per_share_margin(Decimal("2.50"), notional, price=price), Decimal("2.50"), shares

    if asset_class == AssetClass.SHORT_BOX:
        # No initial margin, 5% maintenance margin
        return Decimal("1"), Decimal("0.05"), None, None

    if asset_class == AssetClass.LEVERAGED_ETF_LONG:
        if leverage <= 0:
            raise ValueError("leverage must be positive for leveraged ETF rules")
        rate = Decimal("0.25") * leverage
        return rate, rate, None, None

    if asset_class == AssetClass.LEVERAGED_ETF_SHORT:
        if leverage <= 0:
            raise ValueError("leverage must be positive for leveraged ETF rules")
        # Leveraged ETF Short: 60% for 2x, 90% for 3x (30% * leverage)
        rate = Decimal("0.30") * leverage
        return rate, rate, None, None

    if asset_class == AssetClass.CONVERTIBLE_BOND:
        return Decimal("0.50"), Decimal("0.35"), None, None

    if asset_class == AssetClass.CORPORATE_MUNICIPAL_BOND:
        if face_value is None:
            raise ValueError("face_value is required for corporate/municipal bonds")
        initial = max(Decimal("0.25") * notional, Decimal("0.20") * face_value)
        rate = initial / notional
        return rate, rate, None, None

    if asset_class == AssetClass.US_GOVERNMENT_BOND:
        if face_value is None:
            raise ValueError("face_value is required for US government bonds")
        initial = max(Decimal("0.10") * notional, Decimal("0.03") * face_value)
        rate = initial / notional
        return rate, rate, None, None

    raise ValueError(f"Unsupported asset class: {asset_class.value}")


@dataclass(frozen=True)
class MarginConfig:
    """Instrument and account parameters for margin calculation."""

    notional: Decimal
    margin_init_rate: Decimal | None = None
    margin_maint_rate: Decimal | None = None
    asset_class: AssetClass = AssetClass.MARGIN_APPROVED_STOCK
    price: Decimal | None = None
    face_value: Decimal | None = None
    model: MarginModel = MarginModel.STANDARD
    leverage: Decimal = Decimal("1")
    currency: str = "USD"
    collateral: Decimal | None = None  # equity posted; defaults to initial margin
    direction: PositionDirection = PositionDirection.LONG

    def __post_init__(self) -> None:
        if self.notional <= 0:
            raise ValueError("notional must be positive")
        if self.price is not None and self.price <= 0:
            raise ValueError("price must be positive")
        if self.face_value is not None and self.face_value <= 0:
            raise ValueError("face_value must be positive")
        if self.leverage <= 0:
            raise ValueError("leverage must be positive")
        if (self.margin_init_rate is None) != (self.margin_maint_rate is None):
            raise ValueError("margin_init_rate and margin_maint_rate must be set together")
        if self.margin_init_rate is not None and self.margin_init_rate < 0:
            raise ValueError("margin_init_rate must be non-negative")
        if self.margin_maint_rate is not None and self.margin_maint_rate <= 0:
            raise ValueError("margin_maint_rate must be positive")


@dataclass(frozen=True)
class MarginResult:
    """Computed margin requirements."""

    notional: Decimal
    initial_margin: Decimal
    maintenance_margin: Decimal
    asset_class: AssetClass
    model: MarginModel
    leverage: Decimal
    margin_init_rate: Decimal
    margin_maint_rate: Decimal
    currency: str
    fixed_maint_per_share: Decimal | None = None  # For fixed per-share maintenance
    shares: Decimal | None = None  # Number of shares for fixed per-share calculations

    @property
    def maint_to_init_ratio(self) -> Decimal:
        """Maintenance as a fraction of initial margin."""
        if self.initial_margin == 0:
            return Decimal("0")
        return self.maintenance_margin / self.initial_margin


@dataclass(frozen=True)
class StressScenario:
    """A single stress-test scenario definition."""

    name: str
    description: str
    notional_multiplier: Decimal = Decimal("1")
    price_shock_pct: Decimal = Decimal("0")  # e.g. -0.10 = -10% adverse move
    margin_init_rate_multiplier: Decimal = Decimal("1")
    margin_maint_rate_multiplier: Decimal = Decimal("1")
    leverage: Decimal | None = None
    collateral: Decimal | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> StressScenario:
        return cls(
            name=data["name"],
            description=data.get("description", ""),
            notional_multiplier=Decimal(str(data.get("notional_multiplier", 1))),
            price_shock_pct=Decimal(str(data.get("price_shock_pct", 0))),
            margin_init_rate_multiplier=Decimal(
                str(data.get("margin_init_rate_multiplier", 1))
            ),
            margin_maint_rate_multiplier=Decimal(
                str(data.get("margin_maint_rate_multiplier", 1))
            ),
            leverage=Decimal(str(data["leverage"])) if "leverage" in data else None,
            collateral=Decimal(str(data["collateral"])) if "collateral" in data else None,
        )


@dataclass(frozen=True)
class StressTestResult:
    """Outcome of applying a stress scenario to a base position."""

    scenario: StressScenario
    base: MarginResult
    stressed: MarginResult
    effective_notional: Decimal
    pnl: Decimal
    equity: Decimal
    margin_call: bool
    margin_deficit: Decimal  # positive when under maintenance
    margin_utilization: Decimal  # maintenance / equity
    equity_ratio: Decimal  # equity / effective_notional
    credit_balance: Decimal | None = None  # for shorts: proceeds + initial margin

    @property
    def survives(self) -> bool:
        return not self.margin_call


class MarginCalculator:
    """Compute initial and maintenance margin from notional."""

    @staticmethod
    def calculate(config: MarginConfig) -> MarginResult:
        if config.margin_init_rate is not None and config.margin_maint_rate is not None:
            margin_init_rate = config.margin_init_rate
            margin_maint_rate = config.margin_maint_rate
            fixed_maint_per_share = None
            shares = None
        else:
            if (
                config.asset_class == AssetClass.MARGIN_APPROVED_STOCK
                and config.price is None
                and config.face_value is None
            ):
                margin_init_rate = Decimal("0.10")
                margin_maint_rate = Decimal("0.05")
                fixed_maint_per_share = None
                shares = None
            else:
                margin_init_rate, margin_maint_rate, fixed_maint_per_share, shares = _resolve_docx_rates(config)

        base = config.notional
        if config.model == MarginModel.LEVERAGED:
            base = config.notional / config.leverage

        initial = base * margin_init_rate
        maintenance = base * margin_maint_rate

        return MarginResult(
            notional=config.notional,
            initial_margin=initial,
            maintenance_margin=maintenance,
            asset_class=config.asset_class,
            model=config.model,
            leverage=config.leverage,
            margin_init_rate=margin_init_rate,
            margin_maint_rate=margin_maint_rate,
            currency=config.currency,
            fixed_maint_per_share=fixed_maint_per_share,
            shares=shares,
        )

    @staticmethod
    def from_notional(
        notional: Decimal | float | str,
        margin_init_rate: Decimal | float | str | None = None,
        margin_maint_rate: Decimal | float | str | None = None,
        *,
        asset_class: AssetClass = AssetClass.MARGIN_APPROVED_STOCK,
        price: Decimal | float | str | None = None,
        face_value: Decimal | float | str | None = None,
        model: MarginModel = MarginModel.STANDARD,
        leverage: Decimal | float | str = "1",
        currency: str = "USD",
    ) -> MarginResult:
        """Convenience wrapper: pass notional, get margins."""
        config = MarginConfig(
            notional=Decimal(str(notional)),
            margin_init_rate=_as_decimal(margin_init_rate),
            margin_maint_rate=_as_decimal(margin_maint_rate),
            asset_class=asset_class,
            price=_as_decimal(price),
            face_value=_as_decimal(face_value),
            model=model,
            leverage=Decimal(str(leverage)),
            currency=currency,
            direction=PositionDirection.LONG,
        )
        return MarginCalculator.calculate(config)


class StressTester:
    """Apply stress scenarios to a base margin configuration."""

    def __init__(self, base_config: MarginConfig) -> None:
        self.base_config = base_config
        self.base_result = MarginCalculator.calculate(base_config)

    def apply(self, scenario: StressScenario) -> StressTestResult:
        shocked_notional = (
            self.base_config.notional
            * scenario.notional_multiplier
            * (Decimal("1") + scenario.price_shock_pct)
        )

        base_result = self.base_result
        
        # For fixed per-share maintenance, recalculate based on original share count
        if base_result.fixed_maint_per_share is not None and base_result.shares is not None:
            # Maintenance = shares * fixed_per_share (constant regardless of price)
            stressed_maintenance = base_result.shares * base_result.fixed_maint_per_share
            stressed_maint_rate = stressed_maintenance / shocked_notional
        else:
            stressed_maint_rate = base_result.margin_maint_rate * scenario.margin_maint_rate_multiplier
            stressed_maintenance = None
        
        stressed_init_rate = base_result.margin_init_rate * scenario.margin_init_rate_multiplier
        
        stressed_config = MarginConfig(
            notional=shocked_notional,
            margin_init_rate=stressed_init_rate,
            margin_maint_rate=stressed_maint_rate,
            asset_class=self.base_config.asset_class,
            price=self.base_config.price,
            face_value=self.base_config.face_value,
            model=self.base_config.model,
            leverage=scenario.leverage or self.base_config.leverage,
            currency=self.base_config.currency,
            collateral=scenario.collateral,
            direction=self.base_config.direction,
        )
        stressed_result = MarginCalculator.calculate(stressed_config)
        
        # Override maintenance margin if it's fixed per-share
        if stressed_maintenance is not None:
            stressed_result = MarginResult(
                notional=stressed_result.notional,
                initial_margin=stressed_result.initial_margin,
                maintenance_margin=stressed_maintenance,
                asset_class=stressed_result.asset_class,
                model=stressed_result.model,
                leverage=stressed_result.leverage,
                margin_init_rate=stressed_result.margin_init_rate,
                margin_maint_rate=stressed_maint_rate / shocked_notional,
                currency=stressed_result.currency,
                fixed_maint_per_share=base_result.fixed_maint_per_share,
                shares=base_result.shares,
            )

        collateral = (
            scenario.collateral
            if scenario.collateral is not None
            else self.base_config.collateral
            if self.base_config.collateral is not None
            else self.base_result.initial_margin
        )

        # PnL: long positions profit from price increases, short positions profit from price decreases
        pnl_multiplier = -1 if self.base_config.direction == PositionDirection.SHORT else 1
        pnl = self.base_config.notional * scenario.price_shock_pct * pnl_multiplier
        equity = collateral + pnl
        equity_ratio = equity / shocked_notional
        # Margin call: equity falls below maintenance margin in dollar terms
        # This is equivalent to equity ratio < maintenance margin rate, but dollar comparison is clearer
        margin_call = equity < stressed_result.maintenance_margin
        margin_deficit = max(
            Decimal("0"), stressed_result.maintenance_margin - equity
        )
        margin_utilization = (
            stressed_result.maintenance_margin / equity
            if equity > 0
            else Decimal("999")
        )

        # Credit balance for short positions: proceeds + initial margin
        credit_balance = None
        if self.base_config.direction == PositionDirection.SHORT:
            credit_balance = self.base_config.notional + self.base_result.initial_margin

        return StressTestResult(
            scenario=scenario,
            base=base_result,
            stressed=stressed_result,
            effective_notional=shocked_notional,
            pnl=pnl,
            equity=equity,
            margin_call=margin_call,
            margin_deficit=margin_deficit,
            margin_utilization=margin_utilization,
            equity_ratio=equity_ratio,
            credit_balance=credit_balance,
        )

    def run_all(self, scenarios: list[StressScenario]) -> list[StressTestResult]:
        return [self.apply(s) for s in scenarios]

    @staticmethod
    def load_scenarios(path: str | Path) -> list[StressScenario]:
        with open(path) as f:
            data = yaml.safe_load(f)
        return [StressScenario.from_dict(s) for s in data["scenarios"]]

    @staticmethod
    def format_results(results: list[StressTestResult]) -> str:
        # Check if any result has credit_balance (short position)
        has_credit = any(r.credit_balance is not None for r in results)
        
        if has_credit:
            header = (
                f"{'Scenario':<28} {'Notional':>14} "
                f"{'Maint Margin':>14} {'Equity':>14} {'Equity Ratio':>14} "
                f"{'Credit Bal':>14} {'PnL':>12} {'Call?':>6}"
            )
            separator = "-" * 112
        else:
            header = (
                f"{'Scenario':<28} {'Notional':>14} "
                f"{'Maint Margin':>14} {'Equity':>14} {'Equity Ratio':>14} "
                f"{'PnL':>12} {'Call?':>6}"
            )
            separator = "-" * 100
        
        lines = [header, separator]
        
        for r in results:
            if has_credit:
                credit_str = f"{r.credit_balance:>14,.2f}" if r.credit_balance is not None else f"{'N/A':>14}"
                lines.append(
                    f"{r.scenario.name:<28} "
                    f"{r.effective_notional:>14,.2f} "
                    f"{r.stressed.maintenance_margin:>14,.2f} "
                    f"{r.equity:>14,.2f} "
                    f"{r.equity_ratio:>14,.2%} "
                    f"{credit_str} "
                    f"{r.pnl:>12,.2f} "
                    f"{'YES' if r.margin_call else 'no':>6}"
                )
            else:
                lines.append(
                    f"{r.scenario.name:<28} "
                    f"{r.effective_notional:>14,.2f} "
                    f"{r.stressed.maintenance_margin:>14,.2f} "
                    f"{r.equity:>14,.2f} "
                    f"{r.equity_ratio:>14,.2%} "
                    f"{r.pnl:>12,.2f} "
                    f"{'YES' if r.margin_call else 'no':>6}"
                )
        return "\n".join(lines)
