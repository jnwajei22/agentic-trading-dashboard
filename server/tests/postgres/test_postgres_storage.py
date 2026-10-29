"""Contract tests against an isolated local PostgreSQL-compatible server.

Never accept a hosted target here. Set DESK_TEST_POSTGRES_URL explicitly.
"""
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app.config.settings import settings
from app.storage.database import check_database, connect, DatabaseUnavailable, PostgresConnection, IntegrityError
from app.storage.brokers import BrokerRepository, BrokerStorageError
from app.storage.execution import ExecutionRepository
from app.storage.agent import AgentRepository, AgentStateError
from app.storage.user_experience import UserExperienceRepository
from app.storage.runtime_settings import RuntimeSettingsRepository
from app.storage.signal_intents import SignalIntentRepository
from app.storage.schedules import ScheduleRepository
from app.services.tradelocker.rate_limiter import TradeLockerRequestLimiter
from app.main import app

URL = os.environ.get('DESK_TEST_POSTGRES_URL')
pytestmark = pytest.mark.skipif(not URL, reason='Isolated local Postgres is opt-in')

@pytest.fixture(scope='session')
def wasm_connection():
    if not URL or os.environ.get('DESK_TEST_NATIVE_POSTGRES') == '1':
        yield None
        return
    import psycopg2
    raw = psycopg2.connect(URL)
    yield raw
    raw.close()


@pytest.fixture(autouse=True)
def postgres(monkeypatch, wasm_connection):
    if not URL: return
    assert urlparse(URL).hostname in {'127.0.0.1','localhost'}, 'Remote databases are forbidden in tests'
    monkeypatch.setattr(settings,'storage_backend','postgres')
    monkeypatch.setattr(settings,'supabase_db_url',URL)
    monkeypatch.setattr(settings,'broker_secret_key','synthetic-test-key')
    monkeypatch.setattr(settings,'workspace_owner_sub','pg-'+uuid4().hex)
    app.dependency_overrides.clear()
    if wasm_connection is not None:
        # PGlite has one underlying connection. Native CI uses real independent
        # connections and runs the concurrent-claim test without these hooks.
        def init(self):
            self.raw = wasm_connection
            self._configure()
        def exit(self, kind, value, traceback):
            self.raw.rollback() if kind else self.raw.commit()
        monkeypatch.setattr(PostgresConnection, '__init__', init)
        monkeypatch.setattr(PostgresConnection, '__exit__', exit)


def test_schema_and_runtime_role():
    assert check_database() == {'backend':'postgres','ready':True}
    with connect() as db:
        assert db.execute('SELECT current_user').fetchone()[0] == 'desk_runtime'
        assert db.execute("SELECT count(*) FROM information_schema.tables WHERE table_schema='desk'").fetchone()[0] == 31
        with pytest.raises(DatabaseUnavailable): db.execute('CREATE TABLE forbidden(id int)')


def test_encrypted_connection_account_profile_and_ownership():
    repo=BrokerRepository();owner=settings.workspace_owner_sub
    connection=repo.save_connection(owner,base_url='https://demo.example/backend-api',username='test',password='synthetic-password',server='Test',environment='demo')
    assert connection.password == 'synthetic-password'
    assert repo.list_connections(owner)[0]['connection_id']==connection.connection_ref
    repo.sync_accounts(owner,connection.connection_ref,{'accounts':[{'accountId':'external-1','accNum':'123','currency':'USD'}]})
    account=repo.list_accounts(owner)[0]
    assert repo.get_account_record(owner,alias=account['account_alias'].upper()) is not None
    assert repo.list_accounts('stranger') == []
    profile=repo.create_profile(owner,name='Test Profile',account_ref=account['public_id'])
    assert repo.get_profile(owner,profile['public_id']) is not None
    assert repo.get_profile('stranger',profile['public_id']) is None


def test_preferences_watchlists_and_duplicate_rollback():
    repo=UserExperienceRepository();owner=settings.workspace_owner_sub
    assert repo.update_preferences(owner,{'appearance':'dark'})['appearance']=='dark'
    record=repo.create_watchlist(owner,'Research',['EURUSD','GBPUSD'])
    assert any(row['id']==record['id'] for row in repo.list_watchlists(owner))
    with pytest.raises(IntegrityError):repo.create_watchlist(owner,'research',[])
    assert repo.delete_watchlist('stranger',record['id']) is False
    assert repo.delete_watchlist(owner,record['id']) is True


def test_atomic_turn_and_approval_replay():
    repo=AgentRepository();owner=settings.workspace_owner_sub;key=repo.create(owner,'co_op',None)
    repo.claim_turn(owner,key)
    with pytest.raises(AgentStateError): AgentRepository().claim_turn(owner,key)
    repo.release(key)
    approval=repo.add(key,'approval',{'preview_id':'preview-test'},pending=True)
    assert AgentRepository().claim_approval(owner,key,approval)['preview_id']=='preview-test'
    with pytest.raises(AgentStateError):repo.claim_approval(owner,key,approval)
    assert len(repo.messages(owner,key))==1


def test_execution_controls_and_settings():
    repo=ExecutionRepository();owner=settings.workspace_owner_sub
    assert repo.get_autonomous_controls(owner)['global_autonomous_kill_switch'] is True
    result=repo.update_autonomous_controls(owner,{'demo_autonomous_enabled':False},updated_by=owner,audit_unchanged=True)
    assert result['demo_autonomous_enabled'] is False
    assert repo.autonomous_control_audit(owner)
    first=repo.get_or_create_settings(owner,'conn-test','account-test','123')
    second=repo.get_or_create_settings(owner,'conn-test','account-test','123')
    assert first['id']==second['id']


def test_runtime_settings_and_idempotent_signal():
    assert RuntimeSettingsRepository().update({'maintenance_mode':False})['maintenance_mode'] is False
    key=uuid4().hex;repo=SignalIntentRepository()
    assert repo.create(key,'forex:EUR/USD',{'source':'test'}) is True
    assert repo.create(key,'forex:EUR/USD',{'source':'test'}) is False


def test_schedule_leases_are_not_reclaimed_while_valid():
    owner=settings.workspace_owner_sub;repo=ScheduleRepository();now=datetime.now(timezone.utc)
    schedule=repo.upsert_schedule(owner,'profile-'+uuid4().hex,timezone_name='UTC',local_times=['00:00'],enabled=True,next_run_at=(now-timedelta(seconds=1)).isoformat())
    args=dict(now=now,lease_seconds=60,limit=1,next_run=lambda *_:now+timedelta(days=1))
    first=repo.claim_due(worker_id='worker-a',**args)
    assert first[0]['schedule_id']==schedule['id']
    assert repo.claim_due(worker_id='worker-b',**args)==[]


@pytest.mark.asyncio
async def test_request_cooldown_persists():
    limiter=TradeLockerRequestLimiter();key=uuid4().hex
    lease=await limiter.acquire(key);limiter.release(lease)
    until=limiter.set_cooldown(key,3)
    assert TradeLockerRequestLimiter().state(key)['cooldown_epoch']==until


def test_no_login_api_uses_postgres():
    with TestClient(app) as client:
        assert client.get('/ready').json()=={'status':'ready','database':'postgres'}
        assert client.get('/api/me').json()['sub']==settings.workspace_owner_sub
        assert client.get('/api/assistant/conversations').json()=={'conversations':[]}
        response=client.post('/api/watchlists',json={'name':'Desk','symbols':['EURUSD']})
        assert response.status_code==201
        assert any(row['name']=='Desk' for row in client.get('/api/watchlists').json()['watchlists'])


def test_candle_cache_reuse_and_eviction():
    from app.services.market_data.candle_cache import DurableCandleCache, CandleCacheKey
    from app.models.market import Candle
    now=datetime.now(timezone.utc);stamp=(int(now.timestamp())//60-1)*60000
    cache=DurableCandleCache(max_entries=1)
    key=CandleCacheKey(settings.workspace_owner_sub,'c','a','1','77','EURUSD','1m')
    candle=Candle(timestamp=stamp,open=1,high=2,low=.5,close=1.5)
    cache.put(key,[candle],source='test',metadata={})
    assert DurableCandleCache().get(key).candles[0].timestamp==stamp
    another=CandleCacheKey(settings.workspace_owner_sub,'c','a','1','78','GBPUSD','1m')
    cache.put(another,[candle],source='test',metadata={})
    assert cache.get(another) is not None
    assert cache.get(key) is None


@pytest.mark.skipif(os.environ.get('DESK_TEST_NATIVE_POSTGRES') != '1', reason='Native Postgres required for concurrent connections')
def test_two_connections_cannot_claim_one_approval_twice():
    from concurrent.futures import ThreadPoolExecutor
    repo=AgentRepository();owner=settings.workspace_owner_sub;key=repo.create(owner,'co_op',None)
    approval=repo.add(key,'approval',{'preview_id':'one-action'},pending=True)
    def claim(_):
        try:AgentRepository().claim_approval(owner,key,approval);return 'claimed'
        except AgentStateError:return 'rejected'
    with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(claim,range(2)))
    assert sorted(outcomes)==['claimed','rejected']


def test_named_execution_records_round_trip_through_runtime_role():
    import json
    repo=ExecutionRepository();owner=settings.workspace_owner_sub;token=uuid4().hex
    now=datetime.now(timezone.utc);stamp=now.isoformat();expiry=(now+timedelta(minutes=5)).isoformat()
    snapshot_id='snap-'+token;preview_id='preview-'+token;run_id='run-'+token
    routing={'user_sub':owner,'connection_id':'connection','account_id':'broker-account','acc_num':'1'}
    repo.insert_snapshot({'id':snapshot_id,**routing,'environment':'demo','strategy_name':'strategy','strategy_version':'1',
        'normalized_snapshot_json':json.dumps({'label':"O'Reilly :literal ? 50%"}),'retrieved_at':stamp,'expires_at':expiry,'created_at':stamp})
    assert repo.get_snapshot(snapshot_id)['normalized_snapshot']['label']=="O'Reilly :literal ? 50%"
    preview={'id':preview_id,'snapshot_id':snapshot_id,**routing,'profile_ref':'profile','environment':'demo',
        'pair':'EURUSD','instrument_id':'42','route_id':'1','side':'long','order_type':'market',
        'entry':1.1,'stop_loss':1.09,'take_profit':1.12,'quantity':1000,'lot_size':.01,'estimated_risk':10,
        'risk_percent':.1,'estimated_reward':20,'reward_risk':2,'broker_metadata_json':'{}',
        'status':'approved','violations_json':'[]','expires_at':expiry,'created_at':stamp}
    repo.insert_preview(preview)
    assert repo.get_preview(preview_id)['lot_size']==.01
    submission_id='submission-'+token
    assert repo.claim_submission(submission_id,preview_id,'key-'+token,'fingerprint')[0]
    repo.update_submission(submission_id,submission_state='verified',reconciliation_json={'verified':True},execution_id=run_id)
    assert repo.get_submission(preview_id=preview_id)['reconciliation']['verified'] is True
    repo.insert_run({'id':run_id,**routing,'snapshot_id':snapshot_id,'preview_id':preview_id,'strategy_name':'strategy',
        'strategy_version':'1','decision':'submit','result_status':'unknown','result_json':{'verified':False},
        'started_at':stamp,'completed_at':stamp,'created_at':stamp})
    repo.update_run(run_id,result_status='verified',result_json={'verified':True})
    assert repo.get_run(owner,run_id)['result']['verified'] is True
    decision_id='decision-'+token
    assert repo.claim_decision_run({'id':decision_id,'run_key':'decision-key-'+token,'user_sub':owner,'profile_ref':'profile-'+token,
        'strategy_ref':'strategy','strategy_version':'1','decision_provider':'no_trade','trigger_reason':'test',
        'state':'claimed','shadow_mode':True,'started_at':stamp,'created_at':stamp,'updated_at':stamp})[0]
    repo.update_decision_run(decision_id,state='no_trade',validation_json={'would_submit':False},reason_codes_json=['test_only'])
    assert repo.get_decision_run(owner,decision_id)['reason_codes']==['test_only']


def test_named_demo_action_preview_and_durable_lookup():
    repo=ExecutionRepository();owner=settings.workspace_owner_sub;token=uuid4().hex;now=datetime.now(timezone.utc)
    preview_id='close-'+token
    repo.insert_action_preview({'id':preview_id,'user_sub':owner,'action_type':'close_position','profile_ref':'profile',
        'account_record_id':'account-ref','connection_ref':'connection-ref','connection_id':'connection',
        'account_id':'broker-account','acc_num':'1','account_alias':'demo','environment':'demo','server':'Test',
        'base_url':'https://demo.example/backend-api','demo_classification':'demo','target_id':'position-1',
        'target_json':{'id':'position-1','qty':.01},'status':'approved',
        'expires_at':(now+timedelta(minutes=5)).isoformat(),'created_at':now.isoformat()})
    assert repo.get_action_preview(preview_id)['target']['qty']==.01
    execution_id='action-'+token;key='key-'+token
    assert repo.claim_action(execution_id,preview_id,owner,key,'close_position','position-1')[0]
    repo.update_action_execution(execution_id,state='verified',reconciliation={'verified':True,'target_absent':True})
    assert repo.get_action_submission(owner,preview_id,key)['reconciliation']['target_absent'] is True
    assert repo.get_action_submission('stranger',preview_id,key) is None


def test_named_bindings_preserve_casts_and_repeated_values():
    with connect() as db:
        row=db.execute("SELECT :amount::numeric + :amount::numeric AS total, ':literal ?' AS label, :flag AS enabled",
                       {'flag':True,'amount':1.25}).fetchone()
    assert row['total']==2.5 and row['label']==':literal ?' and row['enabled']==1
