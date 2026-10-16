def calculate_lot_size(
    account_balance: float,
    risk_percent: float,
    entry: float,
    stop_loss: float,
    pip_value_per_standard_lot: float = 10.0,
) -> float:
    # Simplified placeholder for major pairs. Replace with pair-aware pip-value logic.
    risk_amount = account_balance * (risk_percent / 100)
    pip_risk = abs(entry - stop_loss) * 10000
    if pip_risk <= 0:
        raise ValueError("pip risk must be greater than zero")
    standard_lots = risk_amount / (pip_risk * pip_value_per_standard_lot)
    return round(max(0.01, standard_lots), 2)
