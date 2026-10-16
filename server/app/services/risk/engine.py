from app.config.settings import settings
from app.models.orders import OrderRequest

class RiskDecision:
    def __init__(self, allowed: bool, violations: list[str]):
        self.allowed = allowed
        self.violations = violations

def validate_order_request(order: OrderRequest) -> RiskDecision:
    violations: list[str] = []

    if order.stop_loss is None:
        violations.append("Stop loss is required.")

    if order.side == "long" and order.stop_loss >= order.entry:
        violations.append("Long stop loss must be below entry.")

    if order.side == "short" and order.stop_loss <= order.entry:
        violations.append("Short stop loss must be above entry.")

    if order.risk_percent > settings.default_max_risk_percent:
        violations.append("Risk percent exceeds configured max risk per trade.")

    return RiskDecision(allowed=not violations, violations=violations)
