import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config.settings import settings
from app.storage.execution import ExecutionRepository
from app.storage.position_management import PositionManagementRepository
from app.services.autonomous.execution import AutonomousDemoService
from app.services.autonomous.position_management import PositionManagementService,protection_stop

POLICY={'break_even':{'enabled':True,'activation_reward_multiple':1,'offset':0},'trailing_stop':{'enabled':False}}

def plan(**changes):
    values=dict(entry=1.1,initial_stop=1.09,current_stop=1.09,bid=1.12,ask=1.1202,side='long',policy=POLICY,tick_size=.00001,minimum_distance=.0001)
    return protection_stop(**{**values,**changes})

def test_protection_in_both_directions_and_invalid_data():
    assert plan()==1.1
    assert plan(bid=1.105,ask=1.1052) is None
    assert plan(current_stop=1.115) is None
    assert plan(current_stop=None) is None
    trail={'trailing_stop':{'enabled':True,'activation_mode':'reward_multiple','activation_value':1,'trail_value':.005}}
    assert plan(policy=trail)==1.115
    assert plan(side='short',initial_stop=1.11,current_stop=1.11,bid=1.0798,ask=1.08,policy=trail)==1.085
    assert plan(policy={'trailing_stop':{**trail['trailing_stop'],'trail_value':.00001}}) is None
    assert plan(bid=float('nan')) is None
    assert plan(bid=1.13,ask=1.12) is None

class Client:
    fields=['positionId','tradableInstrumentId','side','qty','stopLoss']
    def __init__(self):self.stop=1.09;self.writes=[];self.fail=False;self.reads=0;self.on_read=None
    async def get_config(self):return {'d':{'positionsConfig':{'columns':[{'id':key} for key in self.fields]}}}
    async def get_open_positions(self):
        self.reads+=1
        if self.on_read:self.on_read(self.reads)
        return {'d':[['123','1','buy',1000,self.stop]]}
    async def get_instrument_details(self,pair):
        return {'instrument_id':'1','listing':{'routes':[{'type':'TRADE','id':'route'}]},'details':{'tickSize':.00001,'contractSize':100000,'lotStep':.01,'minOrderQty':.01,'quoteCurrency':'USD','minStopLossDistance':.0001}}
    async def get_quote(self,pair):return {'bp':1.12,'ap':1.1202}
    async def update_position_stop(self,position,stop,**kwargs):
        self.writes.append((position,stop));await asyncio.sleep(0)
        if self.fail:raise TimeoutError('ambiguous broker response')
        self.stop=stop
    async def aclose(self):pass

@pytest.fixture
def management(tmp_path,monkeypatch):
    monkeypatch.setattr(settings,'live_trading_enabled',False)
    repo=ExecutionRepository(tmp_path/'management.db');states=PositionManagementRepository(tmp_path/'management.db')
    repo.update_autonomous_controls('owner',{'global_autonomous_kill_switch':False,'demo_autonomous_enabled':True},updated_by='test',source='test')
    profile={'public_id':'p','enabled':True,'execution_mode':'demo_autonomous','account_environment':'demo','is_demo':1,'profile_v2':{'exit_policy':POLICY}}
    brokers=SimpleNamespace(get_profile=lambda user,ref:profile if user=='owner' and ref=='p' else None,list_profiles=lambda user:[profile] if user=='owner' else [])
    context=SimpleNamespace(user_sub='owner',profile_ref='p',account_record_id='account-ref',connection_ref='connection-ref',connection_id='1',account_id='external',acc_num='10',account_alias='demo',environment='demo',server='test',base_url='https://demo.example',demo_classification='demo',connection=object())
    original={**vars(context),'broker_position_id':'123','pair':'EURUSD','side':'long','instrument_id':'1','entry':1.1,'stop_loss':1.09}
    monkeypatch.setattr(repo,'managed_positions',lambda user,ref:[original] if user=='owner' and ref=='p' else [])
    client=Client();demo=SimpleNamespace(context=AsyncMock(return_value=context),_client=lambda *args:client,_quote=AutonomousDemoService._quote)
    service=PositionManagementService(brokers=brokers,execution=repo,states=states,demo=demo)
    return service,client,repo,states,profile,original

@pytest.mark.asyncio
async def test_verified_update_persists_without_repeat(management):
    service,client,repo,states,_,_=management
    result=await service.manage_profile('owner','p')
    assert result['updated']==1 and client.writes==[('123',1.1)]
    assert states.state('owner','p','123')['state']=='idle'
    await service.manage_profile('owner','p');assert len(client.writes)==1
    action=repo.recent_executions('owner')[0]
    assert action['action_type']=='update_stop' and action['state']=='verified'
    assert states.state('other','p','123') is None

@pytest.mark.asyncio
async def test_unknown_write_never_replays_and_read_reconciles(management):
    service,client,_,states,_,_=management;client.fail=True
    assert (await service.manage_profile('owner','p'))['unconfirmed']==1
    assert states.state('owner','p','123')['state']=='unknown'
    await service.manage_profile('owner','p');assert len(client.writes)==1
    client.stop=1.1;client.fail=False
    await service.manage_profile('owner','p')
    assert states.state('owner','p','123')['state']=='idle' and len(client.writes)==1

@pytest.mark.asyncio
async def test_concurrent_managers_do_not_duplicate_writes(management):
    service,client,_,_,_,_=management
    await asyncio.gather(service.manage_profile('owner','p'),service.manage_profile('owner','p'))
    assert len(client.writes)==1

@pytest.mark.asyncio
async def test_revoked_controls_and_foreign_positions_never_dispatch(management):
    service,client,repo,states,profile,original=management
    def revoke(reads):
        if reads==2:repo.update_autonomous_controls('owner',{'global_autonomous_kill_switch':True},updated_by='test',source='test')
    client.on_read=revoke
    await service.manage_profile('owner','p');assert client.writes==[] and states.state('owner','p','123')['state']=='idle'
    repo.update_autonomous_controls('owner',{'global_autonomous_kill_switch':False},updated_by='test',source='test')
    original['account_record_id']='unrelated-account';client.on_read=None
    await service.manage_profile('owner','p');assert client.writes==[]
    profile['execution_mode']='read_only'
    assert (await service.manage_profile('owner','p'))['status']=='blocked'

@pytest.mark.asyncio
async def test_manual_improvement_during_claim_is_not_loosened(management):
    service,client,_,_,_,_=management
    def improve(reads):
        if reads==2:client.stop=1.115
    client.on_read=improve
    await service.manage_profile('owner','p');assert client.writes==[] and client.stop==1.115


@pytest.mark.asyncio
async def test_read_only_reconciliation_works_with_kill_switch_enabled(management):
    service,client,repo,states,_,_=management;client.fail=True
    await service.manage_profile('owner','p');assert len(client.writes)==1
    repo.update_autonomous_controls('owner',{'global_autonomous_kill_switch':True},updated_by='test',source='test')
    client.stop=1.1;client.fail=False
    await service.manage_profile('owner','p',read_only=True)
    assert states.state('owner','p','123')['state']=='idle' and len(client.writes)==1
