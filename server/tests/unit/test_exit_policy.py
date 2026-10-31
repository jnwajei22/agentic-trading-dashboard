import pytest
from app.services.autonomous.decision import StructuredDecision,DecisionAction
from app.services.autonomous.exit_policy import apply_exit_policy


def decision(side='long'):
    return StructuredDecision(action=DecisionAction.TRADE,symbol='EURUSD',side=side,order_type='market',entry=1.1,stop_loss=1.09 if side=='long' else 1.11,take_profit=1.13 if side=='long' else 1.07,confidence=.9,reason_codes=['test'],rationale='test')

def apply(policy,side='long'):
    return apply_exit_policy(decision(side),{'profile_v2':{'exit_policy':policy}},
        {'market':{'EURUSD':{'bid':1.1,'ask':1.1002,'timeframes':{'1h':{'atr14':.001}}}}},
        {'market':{'pairs':{'EURUSD':{'instrument_metadata':{'tick_size':.00001}}}}})

@pytest.mark.parametrize('side',['long','short'])
def test_fixed_stop_and_net_reward_target_are_enforced(side):
    result=apply({'stop_loss':{'mode':'fixed_distance','fixed_distance':.005},'take_profit':{'mode':'reward_to_risk','target_reward_to_risk':2}},side)
    assert abs(result.entry-result.stop_loss)==pytest.approx(.005)
    assert (abs(result.take_profit-result.entry)-.0002)/(.005+.0002)>=2-1e-9
    assert result.side==side and result.symbol=='EURUSD'

def test_percentage_volatility_and_maximum_stop_distance():
    assert apply({'stop_loss':{'mode':'fixed_percentage','fixed_percentage':1}}).stop_loss==pytest.approx(1.089)
    assert apply({'stop_loss':{'mode':'volatility'}}).stop_loss==pytest.approx(1.098)
    with pytest.raises(ValueError,match='maximum_stop_distance_exceeded'):
        apply({'stop_loss':{'mode':'fixed_distance','fixed_distance':.02,'maximum_risk_distance':.01}})
    with pytest.raises(ValueError,match='protective_stop_and_target_required'):
        apply({'take_profit':{'mode':'none'}})


def test_gross_exposure_limit_checks_new_and_existing_notional_or_fails_closed():
    from app.services.autonomous.execution import AutonomousDemoService,AutonomousExecutionError
    check=AutonomousDemoService._check_gross_exposure
    allocation={'maximum_gross_exposure_multiple':2}
    check(allocation,1000,[],1900,'USD')
    with pytest.raises(AutonomousExecutionError,match='limit'):
        check(allocation,1000,[{'qty':1,'notionalAccountCurrency':500}],1800,'USD')
    with pytest.raises(AutonomousExecutionError,match='comparable'):
        check(allocation,1000,[{'qty':1}],100,'USD')


def test_adaptive_confidence_sizing_is_bounded_and_fixed_manual_risk_is_unchanged():
    from types import SimpleNamespace
    from app.services.autonomous.execution import AutonomousDemoService,AutonomousExecutionError
    context=SimpleNamespace(risk={'risk_per_trade_percent':.25,'minimum_confidence':.7,'risk_policy':{'mode':'adaptive','base_risk_pct':.25,'minimum_risk_pct':.1,'maximum_risk_pct':.3}})
    assert AutonomousDemoService._risk_percent(context,1,True)==.3
    assert AutonomousDemoService._risk_percent(context,.7,True)==.25
    assert AutonomousDemoService._risk_percent(context,1,False)==.25
    with pytest.raises(AutonomousExecutionError):AutonomousDemoService._risk_percent(context,None,True)
    with pytest.raises(AutonomousExecutionError):AutonomousDemoService._risk_percent(context,float('nan'),True)


def test_hourly_preset_requires_alignment_and_confirmation_independent_of_model_confidence():
    from app.services.autonomous.strategy_policy import preset_blocker
    profile={'profile_v2':{'trading_policy':{'mode':'preset','preset_id':'hourly_forex'}}}
    frames={tf:{'complete':True,'sma20':1.1,'sma50':1.09,'last_close':1.11,'momentum_20':.01} for tf in ('1d','4h','1h','15m')}
    context={'market':{'EURUSD':{'timeframes':frames}}}
    assert preset_blocker(decision(),profile,context) is None
    frames['4h']['sma20']=1.08
    assert preset_blocker(decision(),profile,context)=='hourly_forex_trend_alignment_required'
    frames['4h']['sma20']=1.1;frames['15m']['momentum_20']=-.01
    assert preset_blocker(decision(),profile,context)=='hourly_forex_confirmation_required'
    profile['profile_v2']['trading_policy']['mode']='adaptive'
    assert preset_blocker(decision(),profile,context) is None
