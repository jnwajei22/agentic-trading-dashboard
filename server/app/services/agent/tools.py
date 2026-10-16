import inspect
import json
from dataclasses import dataclass
from typing import get_type_hints
from pydantic import ConfigDict, create_model
from app.auth.identity import set_current_claims, reset_current_claims
from app.services import desk_tools as desk
from app.services.agent.contracts import Mode
from app.storage.brokers import BrokerRepository
from app.storage.execution import ExecutionRepository
from app.storage.schedules import ScheduleRepository
from app.services.market_workspace import MarketWorkspaceService

class AuthorizationError(ValueError):
    pass

async def search_markets(query: str):
    service=MarketWorkspaceService()
    try:return await service.search(query,25)
    finally:await service.aclose()

# Categories are server-owned; model names/arguments cannot change them.
READ = ['get_forex_watchlist','get_market_candles','render_market_chart','get_watchlist_market_data',
        'get_economic_calendar','get_market_news','search_macro_series','get_macro_series',
        'get_macro_release_calendar','get_forex_research_bundle','get_provider_capabilities',
        'get_account_status','get_open_positions','get_pending_orders',
        'list_my_tradelocker_connections','list_my_tradelocker_accounts','list_execution_profiles',
        'get_tradelocker_symbols','get_tradelocker_quote','get_autonomous_trading_status',
        'get_autonomous_run_result','list_autonomous_schedules','get_autonomous_schedule_status',
        'list_recent_autonomous_runs','get_autonomous_daily_summary','get_demo_execution_status']
PREPARE = ['review_demo_order','review_cancel_demo_order','review_close_demo_position']
EXECUTE = ['submit_demo_order','submit_cancel_demo_order','submit_close_demo_position']
AUTONOMOUS = ['run_autonomous_demo_profile','stop_autonomous_trading','emergency_stop_autonomous_trading']

@dataclass
class Tool:
    name: str
    fn: object
    category: str
    model: object

    def schema(self):
        schema=self.model.model_json_schema()
        def strict(node):
            if isinstance(node,dict):
                node.pop('default',None)
                if node.get('type')=='object':
                    node['additionalProperties']=False
                    node['required']=list(node.get('properties',{}))
                for v in node.values():strict(v)
            elif isinstance(node,list):
                for v in node:strict(v)
        strict(schema)
        return {'type':'function','name':self.name,'description':inspect.getdoc(self.fn) or self.name,
                'parameters':schema,'strict':True}


def make_tool(name,fn,category):
    hints=get_type_hints(fn)
    fields={p.name:(hints.get(p.name,str), ... if p.default is inspect.Parameter.empty else p.default)
            for p in inspect.signature(fn).parameters.values()}
    return Tool(name,fn,category,create_model(name+'Arguments',__config__=ConfigDict(extra='forbid',strict=True),**fields))

TOOLS={name:make_tool(name,getattr(desk,name),category) for category,names in [('read',READ),('prepare',PREPARE),('execute',EXECUTE),('autonomous',AUTONOMOUS)] for name in names}
TOOLS['search_markets']=make_tool('search_markets',search_markets,'read')


def required_scope(category):
    return 'forex:read' if category=='read' else 'forex:preview' if category=='prepare' else 'forex:execute'


def authorize(mode,category,scopes):
    if mode not in set(Mode):raise AuthorizationError('invalid_mode')
    if required_scope(category) not in scopes:raise AuthorizationError('insufficient_scope')
    if mode==Mode.RESEARCH and category!='read':raise AuthorizationError('research_read_only')
    if mode==Mode.COOP and category=='autonomous':raise AuthorizationError('autonomous_mode_required')


def validate_context(user,context,mode):
    repo=BrokerRepository()
    if context.account_ref and not repo.get_account_record(user,account_ref=context.account_ref):
        raise AuthorizationError('account_not_found')
    profile=repo.get_profile(user,context.profile_ref) if context.profile_ref else None
    if context.profile_ref and not profile:raise AuthorizationError('profile_not_found')
    if mode==Mode.AUTONOMOUS and (not profile or not profile.get('enabled')):
        raise AuthorizationError('enabled_profile_required')
    if profile and context.account_ref and profile['account_id']!=context.account_ref:
        raise AuthorizationError('profile_account_mismatch')
    if context.schedule_id:
        row=ScheduleRepository().get_schedule(user,context.schedule_id)
        if not row:raise AuthorizationError('schedule_not_found')
    if context.run_id and not ExecutionRepository().get_decision_run(user,context.run_id):
        raise AuthorizationError('run_not_found')
    if context.preview_id:
        row=ExecutionRepository().get_preview(context.preview_id)
        if not row or row['user_sub']!=user:raise AuthorizationError('preview_not_found')


def check_profile_binding(user,context,category,args,name):
    if category=='read':return
    if not context.profile_ref:raise AuthorizationError('profile_context_required')
    profile=BrokerRepository().get_profile(user,context.profile_ref)
    if not profile:raise AuthorizationError('profile_not_found')
    if args.get('account_alias') and args['account_alias']!=profile.get('account_alias'):
        raise AuthorizationError('profile_account_mismatch')
    if args.get('environment') and args['environment']!=profile.get('account_environment'):
        raise AuthorizationError('profile_environment_mismatch')
    if args.get('schedule_id'):
        schedule=ScheduleRepository().get_schedule(user,args['schedule_id'])
        if not schedule or schedule.get('profile_ref')!=context.profile_ref:
            raise AuthorizationError('schedule_context_mismatch')
    requested=args.get('profile_id') or args.get('profile_ref')
    if requested and requested!=context.profile_ref:raise AuthorizationError('profile_context_mismatch')
    if 'preview_id' in args:
        repo=ExecutionRepository(); row=repo.get_preview(args['preview_id'])
        if not row:
            with repo._connect() as db:
                found=db.execute('SELECT * FROM demo_action_previews WHERE id=?',(args['preview_id'],)).fetchone()
                row=dict(found) if found else None
        expected={'submit_cancel_demo_order':'cancel_order','submit_close_demo_position':'close_position'}.get(name)
        if expected and (not row or row.get('action_type')!=expected):raise AuthorizationError('action_type_mismatch')
        if name=='submit_demo_order' and row and row.get('action_type'):raise AuthorizationError('action_type_mismatch')
        if not row or row['user_sub']!=user or row.get('profile_ref')!=context.profile_ref:
            raise AuthorizationError('preview_context_mismatch')

async def invoke(user,name,args,*,guard=None):
    # Only called after registry authorization; fail closed even if identity context is absent elsewhere.
    if not user:raise AuthorizationError('authentication_required')
    token=set_current_claims({'sub':user})
    try:
        if name in EXECUTE:
            from app.services.autonomous.execution import AutonomousDemoService, AutonomousExecutionError
            engine=AutonomousDemoService()
            try:
                fn=engine.submit if name=='submit_demo_order' else engine.submit_action
                return await fn(user,args['preview_id'],args['idempotency_key'],before_dispatch=guard)
            except AutonomousExecutionError as exc:return exc.as_dict()
        result=TOOLS[name].fn(**args)
        return await result if inspect.isawaitable(result) else result
    finally:reset_current_claims(token)


def enforce_execution_policy(user,context,mode,category):
    if category not in {'prepare','execute','autonomous'}:return
    profile=BrokerRepository().get_profile(user,context.profile_ref)
    if not profile or not profile.get('enabled'):raise AuthorizationError('enabled_profile_required')
    controls=ExecutionRepository().get_autonomous_controls(user)
    if category=='execute':
        if controls['global_autonomous_kill_switch']:raise AuthorizationError('kill_switch_enabled')
        if profile.get('account_environment')!='demo':raise AuthorizationError('live_execution_unsupported')
        if mode==Mode.AUTONOMOUS and not controls['demo_autonomous_enabled']:
            raise AuthorizationError('autonomy_not_enabled')


def preview_summary(user,preview_id):
    repo=ExecutionRepository();row=repo.get_preview(preview_id)
    if not row:
        with repo._connect() as db:
            found=db.execute('SELECT * FROM demo_action_previews WHERE id=?',(preview_id,)).fetchone()
            row=dict(found) if found else None
    if not row or row['user_sub']!=user:raise AuthorizationError('preview_not_found')
    fields=('id','profile_ref','account_alias','environment','action_type','target_id','pair','side','order_type',
            'entry','stop_loss','take_profit','quantity','lot_size','estimated_risk','estimated_margin','expires_at','status')
    return {key:row[key] for key in fields if key in row}


def get_order_preview(preview_id: str):
    """Read the authenticated user's exact server-validated preview, without broker routing or credentials."""
    from app.auth.identity import get_current_user_sub
    return preview_summary(get_current_user_sub(),preview_id)

TOOLS['get_order_preview']=make_tool('get_order_preview',get_order_preview,'read')


def enforce_session_policy(user,context,name):
    if name not in {'review_demo_order','submit_demo_order','run_autonomous_demo_profile'}:return
    from datetime import datetime, timezone
    from app.services.autonomous.runner import AutonomousDecisionRunner
    profile=BrokerRepository().get_profile(user,context.profile_ref)
    if not profile:raise AuthorizationError('profile_not_found')
    extensions=profile.get('profile_v2') or {}
    configured=(extensions.get('forex') or {}).get('sessions') or profile.get('allowed_sessions') or []
    if configured and not set(configured).intersection(AutonomousDecisionRunner._session(datetime.now(timezone.utc))):
        raise AuthorizationError('outside_allowed_sessions')


async def review_autonomous_order(user,args):
    """Adapt the native order form to the existing autonomous preview boundary."""
    from app.models.autonomous import AutonomousOrderProposal
    from app.services.autonomous.execution import AutonomousDemoService, normalize_pair
    service=AutonomousDemoService()
    snapshot=await service.snapshot(user,args['profile_id'],args['symbol'],autonomous=True)
    entry=args.get('entry')
    if entry is None:
        if args['order_type']!='market':raise ValueError('entry_required')
        bid,ask=service._quote(snapshot['market']['pairs'][normalize_pair(args['symbol'])]['quote'])
        entry=ask if args['side']=='long' else bid
    proposal=AutonomousOrderProposal(snapshot_id=snapshot['snapshot_id'],pair=args['symbol'],side=args['side'],
        order_type=args['order_type'],entry=entry,stop_loss=args['stop_loss'],take_profit=args['take_profit'],reason_codes=['assistant_proposal'])
    return await service.review(user,args['profile_id'],proposal,autonomous=True)


async def get_market_summary(canonical_id: str):
    """Read canonical public-market summary; public prices are not execution prices."""
    service=MarketWorkspaceService()
    try:return await service.summary(canonical_id)
    finally:await service.aclose()


def get_execution_profile(profile_ref: str):
    """Read one owned execution profile and its validated risk/trading policies."""
    from app.auth.identity import get_current_user_sub
    row=BrokerRepository().get_profile(get_current_user_sub(),profile_ref)
    if not row:raise AuthorizationError('profile_not_found')
    return {key:row.get(key) for key in ['public_id','name','enabled','account_id','account_alias','account_environment',
           'profile_v2','allowed_sessions','decision_provider','model_identifier','minimum_confidence']}

TOOLS['get_market_summary']=make_tool('get_market_summary',get_market_summary,'read')
TOOLS['get_execution_profile']=make_tool('get_execution_profile',get_execution_profile,'read')


def pause_schedule(schedule_id: str):
    """Pause an owned schedule without cancelling broker orders or closing positions."""
    from app.auth.identity import get_current_user_sub
    from app.jobs.autonomous_scheduler import AutonomousScheduleService
    return AutonomousScheduleService().set_enabled(get_current_user_sub(),schedule_id,False)

TOOLS['pause_schedule']=make_tool('pause_schedule',pause_schedule,'autonomous')
