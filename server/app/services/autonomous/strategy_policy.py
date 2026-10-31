"""The hourly preset is a rule gate, independently of the AI's confidence or rationale."""
import math
from app.services.autonomous.decision import DecisionAction


def preset_blocker(decision,profile,context):
    policy=(profile.get('profile_v2') or {}).get('trading_policy') or {}
    if decision.action!=DecisionAction.TRADE or policy.get('mode')!='preset':return None
    if policy.get('preset_id')!='hourly_forex':return 'unsupported_strategy_preset'
    frames=((context.get('market') or {}).get(decision.symbol) or {}).get('timeframes') or {}
    direction=1 if decision.side=='long' else -1
    def value(frame,key):
        try:
            result=float(frame[key]);return result if math.isfinite(result) else None
        except (KeyError,TypeError,ValueError):return None
    for timeframe in ('1d','4h','1h'):
        frame=frames.get(timeframe) or {};fast=value(frame,'sma20');slow=value(frame,'sma50');close=value(frame,'last_close')
        if not frame.get('complete') or any(item is None for item in (fast,slow,close)):
            return 'hourly_forex_data_incomplete'
        if direction*(fast-slow)<=0 or direction*(close-fast)<=0:return 'hourly_forex_trend_alignment_required'
    confirmation=frames.get('15m') or {};momentum=value(confirmation,'momentum_20');close=value(confirmation,'last_close');fast=value(confirmation,'sma20')
    if not confirmation.get('complete') or any(item is None for item in (momentum,close,fast)):
        return 'hourly_forex_data_incomplete'
    if direction*momentum<=0 or direction*(close-fast)<=0:return 'hourly_forex_confirmation_required'
    return None
