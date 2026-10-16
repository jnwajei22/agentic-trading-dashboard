from datetime import datetime, timedelta, timezone
from uuid import uuid4
from app.models.orders import OrderRequest, OrderPreview
from app.models.enums import OrderPreviewStatus
from app.services.risk.engine import validate_order_request
from app.services.risk.position_sizing import calculate_lot_size

def create_order_preview(order: OrderRequest, account_balance: float = 1000.0) -> OrderPreview:
    decision = validate_order_request(order)
    lot_size = calculate_lot_size(
        account_balance=account_balance,
        risk_percent=order.risk_percent,
        entry=order.entry,
        stop_loss=order.stop_loss,
    )

    pip_risk = abs(order.entry - order.stop_loss) * 10000
    pip_reward = abs(order.take_profit - order.entry) * 10000
    reward_risk = round(pip_reward / pip_risk, 2) if pip_risk else 0

    now = datetime.now(timezone.utc)

    return OrderPreview(
        preview_id=f"fxprev_{uuid4().hex[:12]}",
        status=(
            OrderPreviewStatus.preview_only
            if decision.allowed
            else OrderPreviewStatus.rejected
        ),
        pair=order.pair,
        side=order.side,
        order_type=order.order_type,
        entry=order.entry,
        stop_loss=order.stop_loss,
        take_profit=order.take_profit,
        lot_size=lot_size,
        pip_risk=round(pip_risk, 1),
        risk_amount=round(account_balance * (order.risk_percent / 100), 2),
        reward_risk=reward_risk,
        violations=decision.violations,
        expires_at=now + timedelta(minutes=5),
        created_at=now,
    )
