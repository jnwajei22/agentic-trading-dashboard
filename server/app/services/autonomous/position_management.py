"""Deterministic demo position protection; no model can change routing or remove a stop."""
import asyncio
import math
from collections import Counter
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR, ROUND_CEILING
from uuid import uuid4

from app.config.settings import settings
from app.brokers.tradelocker.mapping import map_configured_rows
from app.services.autonomous.execution import AutonomousDemoService, _find_value, parse_instrument_metadata
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository, utcnow
from app.storage.position_management import PositionManagementRepository


def number(value):
    try:
        result=float(value)
        return result if math.isfinite(result) else None
    except (TypeError,ValueError):return None


def at_least(current,target,side):
    value=number(current)
    return value is not None and value>0 and (value>=target-1e-10 if side=='long' else value<=target+1e-10)


def protection_stop(*,entry,initial_stop,current_stop,bid,ask,side,policy,tick_size,minimum_distance):
    """Plan price-only reductions in risk. R always refers to the immutable original stop."""
    values=[number(v) for v in (entry,initial_stop,bid,ask,tick_size,minimum_distance)]
    if number(current_stop) is None or any(v is None for v in values) or side not in {'long','short'}:return None
    entry,initial_stop,bid,ask,tick,minimum=values
    if min(entry,initial_stop,bid,ask,tick)<=0 or minimum<0 or bid>ask:return None
    if (side=='long' and initial_stop>=entry) or (side=='short' and initial_stop<=entry):return None
    direction=1 if side=='long' else -1;price=bid if side=='long' else ask
    risk=abs(entry-initial_stop);gain=direction*(price-entry);candidates=[]
    be=policy.get('break_even') or {};trail=policy.get('trailing_stop') or {}
    if be.get('enabled'):
        activation=number(be.get('activation_reward_multiple'));offset=number(be.get('offset',0))
        if activation and activation>0 and offset is not None and offset>=0 and gain>=activation*risk:
            candidates.append(entry+direction*offset)
    if trail.get('enabled'):
        activation=number(trail.get('activation_value'));distance=number(trail.get('trail_value'))
        threshold=None
        if activation and activation>0:
            mode=trail.get('activation_mode')
            threshold=activation*risk if mode=='reward_multiple' else activation if mode=='fixed_distance' else entry*activation/100 if mode=='percentage' else None
        if threshold is not None and distance and distance>0 and gain>=threshold:
            candidates.append(price-direction*distance)
    if not candidates:return None
    proposed=max(candidates) if side=='long' else min(candidates)
    rounding=ROUND_FLOOR if side=='long' else ROUND_CEILING
    proposed=float((Decimal(str(proposed))/Decimal(str(tick))).to_integral_value(rounding=rounding)*Decimal(str(tick)))
    if proposed<=0 or direction*(price-proposed)<max(minimum,tick)-1e-10:return None
    previous=number(current_stop)
    if previous and previous>0 and direction*(proposed-previous)<tick-1e-10:return None
    if direction*(proposed-initial_stop)<0:return None
    return proposed


class PositionManagementService:
    def __init__(self,*,brokers=None,execution=None,states=None,demo=None):
        self.brokers=brokers or BrokerRepository();self.execution=execution or ExecutionRepository()
        self.states=states or PositionManagementRepository()
        self.demo=demo or AutonomousDemoService(broker_repository=self.brokers,execution_repository=self.execution)

    def allowed(self,user,profile):
        controls=self.execution.get_autonomous_controls(user)
        return bool(profile and profile.get('enabled') and profile.get('execution_mode')=='demo_autonomous'
            and profile.get('account_environment')=='demo' and profile.get('is_demo')==1
            and not controls['global_autonomous_kill_switch'] and controls['demo_autonomous_enabled']
            and not settings.live_trading_enabled)

    async def run_once(self):
        user=settings.workspace_owner_sub;results=[]
        for profile in self.brokers.list_profiles(user):
            pending=self.states.summary(user,profile['public_id'])
            has_pending=bool(pending['pending_updates'] or pending['unconfirmed_updates'])
            if not self.allowed(user,profile) and not has_pending:continue
            policy=(profile.get('profile_v2') or {}).get('exit_policy') or {}
            if not has_pending and not any((policy.get(key) or {}).get('enabled') for key in ('trailing_stop','break_even')):continue
            try:results.append(await self.manage_profile(user,profile['public_id']))
            except asyncio.CancelledError:raise
            except Exception:results.append({'profile_ref':profile['public_id'],'status':'unavailable'})
        return results

    @staticmethod
    def rows(config,payload):
        return map_configured_rows(config_response=config,data_response=payload,config_key='positionsConfig',data_key='positions')

    async def manage_profile(self,user,profile_ref,*,read_only=False):
        profile=self.brokers.get_profile(user,profile_ref)
        if not profile or profile.get('account_environment')!='demo' or profile.get('is_demo')!=1:return {'status':'blocked','updated':0}
        can_write=not read_only and self.allowed(user,profile)
        pending_summary=self.states.summary(user,profile_ref)
        if not can_write and not (pending_summary['pending_updates'] or pending_summary['unconfirmed_updates']):return {'status':'blocked','updated':0}
        policy=(profile.get('profile_v2') or {}).get('exit_policy') or {}
        owned=self.execution.managed_positions(user,profile_ref)
        if not owned:return {'status':'no_owned_positions','updated':0}
        context=await self.demo.context(user,profile_ref,require_mode=can_write,allow_autonomous=True)
        client=self.demo._client(context.connection,context);updated=0;unconfirmed=0
        try:
            config=await client.get_config();rows=self.rows(config,await client.get_open_positions())
            positions={str(_find_value(row,('positionId','id'))):row for row in rows}
            counts=Counter(str(item['broker_position_id']) for item in owned)
            for original in owned:
                position=str(original['broker_position_id']);side=original['side']
                # Netting of multiple entries has no unique initial risk baseline; fail closed.
                if counts[position]!=1:continue
                if any(str(original.get(field))!=str(getattr(context,field)) for field in
                       ('profile_ref','account_record_id','connection_ref','connection_id','account_id','acc_num','environment','server','base_url','demo_classification')):continue
                current=positions.get(position);pending=self.states.state(user,profile_ref,position)
                if pending and pending['state'] in {'dispatching','unknown'}:
                    if current is None:self.states.finish(pending,outcome='closed',reconciliation={'position_absent':True})
                    elif at_least(_find_value(current,('stopLoss',)),pending['desired_stop'],side):
                        self.states.finish(pending,outcome='verified',reconciliation={'stop_loss':_find_value(current,('stopLoss',)),'verified':True})
                    else:unconfirmed+=1
                    continue
                if not can_write or current is None or (pending and pending['state']=='closed'):continue
                quantity=number(_find_value(current,('qty','quantity','size')))
                broker_side=str(_find_value(current,('side',)) or '').lower()
                if not quantity or quantity<=0 or broker_side not in ({'buy','long'} if side=='long' else {'sell','short'}):continue
                if str(_find_value(current,('tradableInstrumentId','instrumentId')))!=str(original['instrument_id']):continue
                metadata=parse_instrument_metadata(await client.get_instrument_details(original['pair']),original['pair'])
                if metadata.instrument_id!=str(original['instrument_id']):continue
                bid,ask=self.demo._quote(await client.get_quote(original['pair']))
                stop=protection_stop(entry=original['entry'],initial_stop=original['stop_loss'],
                    current_stop=_find_value(current,('stopLoss',)),bid=bid,ask=ask,side=side,policy=policy,
                    tick_size=metadata.tick_size,minimum_distance=metadata.minimum_stop_distance)
                if stop is None:continue
                now=utcnow();preview={'id':f'manage_{uuid4().hex}','user_sub':user,'action_type':'update_stop',
                    **{field:getattr(context,field) for field in ('profile_ref','account_record_id','connection_ref','connection_id','account_id','acc_num','account_alias','environment','server','base_url','demo_classification')},
                    'target_id':position,'target_json':{'position':current,'requested_stop':stop},'status':'approved',
                    'created_at':now.isoformat(),'expires_at':(now+timedelta(seconds=90)).isoformat()}
                claim=self.states.claim(preview,stop)
                if not claim:continue
                intent=False
                try:
                    # Recheck the broker stop and all controls after acquiring the durable write claim.
                    fresh=self.rows(config,await client.get_open_positions())
                    target=next((row for row in fresh if str(_find_value(row,('positionId','id')))==position),None)
                    if target is None or number(_find_value(target,('qty','quantity','size')))==0:
                        self.states.finish(claim,outcome='closed',reconciliation={'position_absent':True});continue
                    if number(_find_value(target,('stopLoss',))) is None or str(_find_value(target,('tradableInstrumentId','instrumentId')))!=str(original['instrument_id']) or str(_find_value(target,('side',)) or '').lower() not in ({'buy','long'} if side=='long' else {'sell','short'}):
                        self.states.finish(claim,outcome='rejected',error='position_state_unverifiable');continue
                    if at_least(_find_value(target,('stopLoss',)),stop,side):
                        self.states.finish(claim,outcome='verified',reconciliation={'already_protected':True,'verified':True});continue
                    if not self.allowed(user,self.brokers.get_profile(user,profile_ref)):
                        self.states.finish(claim,outcome='rejected',error='management_permission_revoked');continue
                    intent=True
                    await client.update_position_stop(position,stop,strategy_id=f'afd-{claim["token"][:20]}')
                    after=self.rows(config,await client.get_open_positions())
                    target=next((row for row in after if str(_find_value(row,('positionId','id')))==position),None)
                    verified=target is not None and at_least(_find_value(target,('stopLoss',)),stop,side)
                    if target is None:self.states.finish(claim,outcome='closed',reconciliation={'position_absent':True})
                    else:self.states.finish(claim,outcome='verified' if verified else 'unknown',reconciliation={'verified':verified,'requested_stop':stop},error=None if verified else 'protection_update_unconfirmed')
                    updated+=int(verified);unconfirmed+=int(not verified and target is not None)
                except BaseException as error:
                    self.states.finish(claim,outcome='unknown' if intent else 'rejected',error='protection_update_unconfirmed' if intent else 'protection_read_failed')
                    if isinstance(error,(asyncio.CancelledError,KeyboardInterrupt,SystemExit)):raise
                    unconfirmed+=int(intent)
            return {'profile_ref':profile_ref,'status':'attention_required' if unconfirmed else 'checked','updated':updated,'unconfirmed':unconfirmed}
        finally:await client.aclose()
