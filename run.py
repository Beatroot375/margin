#!/usr/bin/env python3
"""CLI entry point for margin calculation and stress testing."""

from __future__ import annotations

import argparse
from decimal import Decimal
from pathlib import Path

from margin_testing.engine import AssetClass
from margin_testing.engine import MarginCalculator
from margin_testing.engine import MarginConfig
from margin_testing.engine import MarginModel
from margin_testing.engine import PositionDirection
from margin_testing.engine import StressTester


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Calculate margins from notional and run stress scenarios."
    )
    parser.add_argument(
        "--notional",
        type=Decimal,
        required=True,
        help="Position notional in quote currency",
    )
    parser.add_argument(
        "--asset-class",
        choices=[asset.value for asset in AssetClass],
        default=AssetClass.MARGIN_APPROVED_STOCK.value,
        help="Asset class used to derive doc-backed margin rules",
    )
    parser.add_argument(
        "--price",
        type=Decimal,
        default=None,
        help="Security price, required for price-band asset classes",
    )
    parser.add_argument(
        "--face-value",
        type=Decimal,
        default=None,
        help="Face value for bond rules that reference par",
    )
    parser.add_argument(
        "--init-rate",
        type=Decimal,
        default=None,
        help="Override initial margin rate explicitly",
    )
    parser.add_argument(
        "--maint-rate",
        type=Decimal,
        default=None,
        help="Override maintenance margin rate explicitly",
    )
    parser.add_argument(
        "--model",
        choices=["standard", "leveraged"],
        default="standard",
        help="Margin model (default: standard)",
    )
    parser.add_argument(
        "--leverage",
        type=Decimal,
        default=Decimal("1"),
        help="Account leverage (default: 1)",
    )
    parser.add_argument(
        "--collateral",
        type=Decimal,
        default=None,
        help="Posted collateral; defaults to initial margin",
    )
    parser.add_argument(
        "--scenarios",
        type=Path,
        default=Path(__file__).parent / "scenarios.yaml",
        help="Path to scenarios YAML",
    )
    parser.add_argument(
        "--direction",
        choices=[direction.value for direction in PositionDirection],
        default=PositionDirection.LONG.value,
        help="Position direction (long or short, default: long)",
    )
    args = parser.parse_args()

    config = MarginConfig(
        notional=args.notional,
        margin_init_rate=args.init_rate,
        margin_maint_rate=args.maint_rate,
        asset_class=AssetClass(args.asset_class),
        price=args.price,
        face_value=args.face_value,
        model=MarginModel(args.model),
        leverage=args.leverage,
        collateral=args.collateral,
        direction=PositionDirection(args.direction),
    )

    result = MarginCalculator.calculate(config)
    print("=== Base Margin ===")
    print(f"Notional:            {result.notional:>14,.2f} {result.currency}")
    print(f"Initial Margin:      {result.initial_margin:>14,.2f} {result.currency}")
    print(f"Maintenance Margin:  {result.maintenance_margin:>14,.2f} {result.currency}")
    print(f"Model:               {result.model.value} (leverage {result.leverage}x)")
    print()

    tester = StressTester(config)
    scenarios = StressTester.load_scenarios(args.scenarios)
    results = tester.run_all(scenarios)
    print("=== Stress Test Results ===")
    print(StressTester.format_results(results))
    print()

    calls = [r for r in results if r.margin_call]
    print(f"Margin calls: {len(calls)} / {len(results)} scenarios")


if __name__ == "__main__":
    main()
