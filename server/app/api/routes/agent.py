import json
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from app.api.routes.platform import current_claims
from app.services.agent.contracts import TurnRequest
from app.services.agent.service import AgentService
from app.services.agent.tools import AuthorizationError
from app.storage.agent import AgentStateError

router=APIRouter(prefix='/api/assistant',tags=['assistant'])

def service():return AgentService()

def scopes(claims):
    value=claims.get('scope',claims.get('scp',''))
    return set(value.split()) if isinstance(value,str) else set(value)

@router.get('/conversations')
async def list_conversations(claims=Depends(current_claims),agent=Depends(service)):
    if 'forex:read' not in scopes(claims):raise HTTPException(403,'Read permission required.')
    return {'conversations':agent.repo.list(claims['sub'])}

@router.get('/conversations/{key}')
async def conversation(key:str,claims=Depends(current_claims),agent=Depends(service)):
    if 'forex:read' not in scopes(claims):raise HTTPException(403,'Read permission required.')
    try:return {'conversation':agent.repo.owned(claims['sub'],key),'messages':agent.repo.messages(claims['sub'],key)}
    except AgentStateError:raise HTTPException(404,'Conversation not found.') from None

@router.post('/stream')
async def stream_turn(payload:TurnRequest,claims=Depends(current_claims),agent=Depends(service)):
    if 'forex:read' not in scopes(claims):raise HTTPException(403,'Read permission required.')
    try:key=agent.prepare(claims['sub'],payload)
    except (AgentStateError,AuthorizationError) as exc:raise HTTPException(409,str(exc)) from None
    async def events():
        async for event in agent.stream(claims['sub'],key,payload,scopes(claims)):
            yield 'data: '+json.dumps(event,default=str)+'\n\n'
    return StreamingResponse(events(),media_type='text/event-stream',headers={
        'Cache-Control':'no-store','X-Accel-Buffering':'no','X-Content-Type-Options':'nosniff'})

@router.post('/conversations/{key}/approvals/{approval}')
async def approve(key:str,approval:str,claims=Depends(current_claims),agent=Depends(service)):
    try:return await agent.approve(claims['sub'],key,approval,scopes(claims))
    except (AgentStateError,AuthorizationError) as exc:raise HTTPException(409,str(exc)) from None
