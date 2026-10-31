"""Apply strategy exit settings before deterministic sizing and broker validation."""
import math
from decimal import Decimal,ROUND_FLOOR,ROUND_CEILING,ROUND_HALF_UP
from app.services.autonomous.decision import DecisionAction,StructuredDecision


def apply_exit_policy(decision,profile,context,snapshot):
    policy=(profile.get('profile_v2') or {}).get('exit_policy') or {}
    if decision.action!=DecisionAction.TRADE or not policy:return decision
    sl=policy.get('stop_loss') or {};tp=policy.get('take_profit') or {}
    if sl.get('enabled') is False or tp.get('enabled') is False or tp.get('mode') in {'none','trailing_only'}:
        raise ValueError('protective_stop_and_target_required')
    entry=float(decision.entry);direction=1 if decision.side=='long' else -1
    market=(context.get('market') or {}).get(decision.symbol) or {}
    source=((snapshot.get('market') or {}).get('pairs') or {}).get(decision.symbol) or {}
    tick=(source.get('instrument_metadata') or {}).get('tick_size')
    def rounded(value,rounding=ROUND_HALF_UP):
        if not math.isfinite(value) or value<=0:raise ValueError('invalid_policy_exit_price')
        return float((Decimal(str(value))/Decimal(str(tick))).to_integral_value(rounding=rounding)*Decimal(str(tick))) if tick else value
    mode=sl.get('mode');stop=float(decision.stop_loss)
    if mode=='fixed_distance':stop=entry-direction*float(sl['fixed_distance'])
    elif mode=='fixed_percentage':stop=entry-direction*entry*float(sl['fixed_percentage'])/100
    elif mode=='volatility':
        atr=((market.get('timeframes') or {}).get('1h') or {}).get('atr14')
        if atr is None or not math.isfinite(float(atr)) or float(atr)<=0:raise ValueError('hourly_volatility_unavailable')
        stop=entry-direction*2*float(atr)
    stop=rounded(stop);distance=direction*(entry-stop)
    if distance<=0:raise ValueError('invalid_policy_stop_side')
    maximum=sl.get('maximum_risk_distance')
    if maximum is not None and distance>float(maximum)+1e-10:raise ValueError('maximum_stop_distance_exceeded')
    target=float(decision.take_profit)
    if tp.get('mode')=='reward_to_risk':
        spread=max(0,float(market.get('ask') or entry)-float(market.get('bid') or entry))
        reward=float(tp['target_reward_to_risk'])*(distance+spread)+spread
        target=rounded(entry+direction*reward,ROUND_CEILING if direction>0 else ROUND_FLOOR)
    else:target=rounded(target)
    return StructuredDecision.model_validate({**decision.model_dump(),'stop_loss':stop,'take_profit':target})
