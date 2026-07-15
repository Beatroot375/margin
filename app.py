import streamlit as st
from decimal import Decimal
from pathlib import Path
import sys
import os

# Ensure the parent directory is in sys.path for package imports
script_path = Path(__file__).resolve()
parent_dir = script_path.parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Also try adding the margin_testing directory itself
margin_testing_dir = script_path.parent
if str(margin_testing_dir) not in sys.path:
    sys.path.insert(0, str(margin_testing_dir))

try:
    from margin_testing.engine import (
        AssetClass,
        MarginCalculator,
        MarginConfig,
        MarginModel,
        PositionDirection,
        StressTester,
    )
except ImportError:
    # Fallback to direct import if package import fails
    from engine import (
        AssetClass,
        MarginCalculator,
        MarginConfig,
        MarginModel,
        PositionDirection,
        StressTester,
    )


st.set_page_config(
    page_title="Margin Calculator",
    page_icon="💰",
    layout="wide",
)

st.title("💰 Margin Calculator")
st.markdown("Calculate margin requirements and run stress tests for various asset classes.")


def to_decimal(value) -> Decimal | None:
    """Convert input to Decimal or return None if empty."""
    if value is None or value == "":
        return None
    return Decimal(str(value))


# Sidebar for inputs
with st.sidebar:
    st.header("Input Parameters")
    
    notional = st.number_input(
        "Notional Amount",
        min_value=0.0,
        value=10000.0,
        step=1000.0,
        format="%.2f",
        help="Total position notional value",
    )
    
    asset_class = st.selectbox(
        "Asset Class",
        options=[ac.value for ac in AssetClass],
        index=0,
        help="Type of asset for margin calculation",
    )
    
    price = st.number_input(
        "Price per Share/Unit",
        min_value=0.0,
        value=50.0,
        step=1.0,
        format="%.2f",
        help="Current price per share or unit (required for stocks)",
    )
    
    face_value = st.number_input(
        "Face Value",
        min_value=0.0,
        value=None,
        step=100.0,
        format="%.2f",
        help="Face value for bonds (optional)",
    )
    
    init_rate = st.number_input(
        "Initial Margin Rate (optional)",
        min_value=0.0,
        max_value=1.0,
        value=None,
        step=0.01,
        format="%.4f",
        help="Override initial margin rate (e.g., 0.5 for 50%)",
    )
    
    maint_rate = st.number_input(
        "Maintenance Margin Rate (optional)",
        min_value=0.0,
        max_value=1.0,
        value=None,
        step=0.01,
        format="%.4f",
        help="Override maintenance margin rate (e.g., 0.25 for 25%)",
    )
    
    model = st.selectbox(
        "Margin Model",
        options=[m.value for m in MarginModel],
        index=0,
        help="Standard or leveraged margin model",
    )
    
    leverage = st.number_input(
        "Leverage",
        min_value=1.0,
        value=1.0,
        step=0.5,
        format="%.1f",
        help="Leverage multiplier (used for leveraged ETFs)",
    )
    
    collateral = st.number_input(
        "Collateral (optional)",
        min_value=0.0,
        value=None,
        step=1000.0,
        format="%.2f",
        help="Equity posted as collateral (defaults to initial margin)",
    )
    
    direction = st.selectbox(
        "Position Direction",
        options=[d.value for d in PositionDirection],
        index=0,
        help="Long or short position",
    )
    
    scenarios_path = st.text_input(
        "Scenarios File Path",
        value=str(Path(__file__).parent / "scenarios.yaml"),
        help="Path to YAML file containing stress test scenarios",
    )
    
    calculate_btn = st.button("Calculate Margin", type="primary")


# Main content area
if calculate_btn or "results" not in st.session_state:
    try:
        config = MarginConfig(
            notional=Decimal(str(notional)),
            margin_init_rate=to_decimal(init_rate) if init_rate else None,
            margin_maint_rate=to_decimal(maint_rate) if maint_rate else None,
            asset_class=AssetClass(asset_class),
            price=to_decimal(price) if price else None,
            face_value=to_decimal(face_value) if face_value else None,
            model=MarginModel(model),
            leverage=Decimal(str(leverage)),
            collateral=to_decimal(collateral) if collateral else None,
            direction=PositionDirection(direction),
        )
        
        result = MarginCalculator.calculate(config)
        
        # Store in session state
        st.session_state.result = result
        st.session_state.config = config
        st.session_state.scenarios_path = scenarios_path
        
    except Exception as e:
        st.error(f"Error calculating margin: {e}")
        st.stop()


if "result" in st.session_state:
    result = st.session_state.result
    config = st.session_state.config
    
    # Display Base Margin Results
    st.header("Base Margin Requirements")
    
    col1, col2, col3, col4 = st.columns(4)
    
    with col1:
        st.metric("Notional", f"{result.notional:,.2f} {result.currency}")
    
    with col2:
        st.metric("Initial Margin", f"{result.initial_margin:,.2f} {result.currency}")
    
    with col3:
        st.metric("Maintenance Margin", f"{result.maintenance_margin:,.2f} {result.currency}")
    
    with col4:
        st.metric("Model", f"{result.model.value} ({result.leverage}x)")
    
    # Placeholder metrics for day trade buying power and cash balance
    st.subheader("Account Statistics")
    col5, col6 = st.columns(2)
    
    with col5:
        st.metric("Day Trade Buying Power", "--")
    
    with col6:
        st.metric("Cash Balance", "--")
    
    # Additional details
    with st.expander("Margin Details"):
        st.json({
            "Notional": str(result.notional),
            "Initial Margin": str(result.initial_margin),
            "Maintenance Margin": str(result.maintenance_margin),
            "Asset Class": result.asset_class.value,
            "Model": result.model.value,
            "Leverage": str(result.leverage),
            "Initial Margin Rate": f"{result.margin_init_rate:.4f}",
            "Maintenance Margin Rate": f"{result.margin_maint_rate:.4f}",
            "Currency": result.currency,
            "Fixed Per-Share Maintenance": str(result.fixed_maint_per_share) if result.fixed_maint_per_share else None,
            "Shares": str(result.shares) if result.shares else None,
        })
    
    # Stress Testing
    st.header("Stress Test Results")
    
    if st.button("Run Stress Tests"):
        try:
            scenarios_path = st.session_state.scenarios_path
            tester = StressTester(config)
            scenarios = StressTester.load_scenarios(scenarios_path)
            results = tester.run_all(scenarios)
            
            st.session_state.stress_results = results
            
        except Exception as e:
            st.error(f"Error running stress tests: {e}")
            st.stop()
    
    if "stress_results" in st.session_state:
        results = st.session_state.stress_results
        
        # Summary
        margin_calls = sum(1 for r in results if r.margin_call)
        st.info(f"Margin calls: {margin_calls} / {len(results)} scenarios")
        
        # Results table
        results_df = []
        for r in results:
            row = {
                "Scenario": r.scenario.name,
                "Notional": float(r.effective_notional),
                "Maint Margin": float(r.stressed.maintenance_margin),
                "Equity": float(r.equity),
                "Equity Ratio": float(r.equity_ratio),
                "PnL": float(r.pnl),
                "Margin Call": "YES" if r.margin_call else "no",
            }
            if r.credit_balance is not None:
                row["Credit Balance"] = float(r.credit_balance)
            results_df.append(row)
        
        st.dataframe(
            results_df,
            use_container_width=True,
            hide_index=True,
        )
        
        # Format as text for copy-paste
        st.subheader("Formatted Results")
        st.text(StressTester.format_results(results))
