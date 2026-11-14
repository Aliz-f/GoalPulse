"""Authenticated internal Goal Management read API (D1-GM-02)."""
from __future__ import annotations
import base64, hashlib, hmac, json, os, time, uuid
from typing import Any, Protocol
from fastapi import FastAPI, Header, HTTPException, Query

class Reads(Protocol):
    def get_version(self, goal_id: uuid.UUID, version: int) -> dict[str, Any] | None: ...
    def get_runtime(self, goal_id: uuid.UUID) -> dict[str, Any] | None: ...
    def list_active(self, cursor: uuid.UUID | None, limit: int) -> tuple[list[dict[str, Any]], uuid.UUID | None]: ...

def _user(value: str | None) -> uuid.UUID:
    if not value or not value.startswith('Bearer '): raise HTTPException(401,'unauthorized')
    try:
        h,p,s=value[7:].split('.'); claim=json.loads(base64.urlsafe_b64decode(p+'='*(-len(p)%4)))
        sig=base64.urlsafe_b64decode(s+'='*(-len(s)%4)); expected=hmac.new(os.environ['GATEWAY_DEV_JWT_SECRET'].encode(),f'{h}.{p}'.encode(),hashlib.sha256).digest()
        if not hmac.compare_digest(sig,expected) or claim.get('iss')!=os.environ['GATEWAY_JWT_ISSUER'] or claim.get('aud')!=os.environ['GATEWAY_JWT_AUDIENCE'] or claim.get('token_kind')!='user' or claim.get('exp',0)<=time.time(): raise ValueError
        return uuid.UUID(claim['sub'])
    except Exception as exc: raise HTTPException(401,'unauthorized') from exc

def _service_token(value: str | None) -> None:
    if not value or not value.startswith("Bearer "):
        raise HTTPException(403, "service identity required")
    try:
        h,p,s=value[7:].split('.'); payload=json.loads(base64.urlsafe_b64decode(p+'='*(-len(p)%4)))
        expected=hmac.new(os.environ['SERVICE_JWT_SECRET'].encode(),f'{h}.{p}'.encode(),hashlib.sha256).digest()
        signature=base64.urlsafe_b64decode(s+'='*(-len(s)%4))
        if not hmac.compare_digest(expected,signature) or payload.get('iss')!=os.environ['SERVICE_JWT_ISSUER'] or payload.get('aud')!=os.environ['SERVICE_JWT_AUDIENCE'] or payload.get('token_kind')!='service' or payload.get('exp',0)<=time.time() or 'gm.read' not in payload.get('scope','').split(): raise ValueError
    except Exception as exc: raise HTTPException(403,'service identity required') from exc

def create_app(reads: Reads) -> FastAPI:
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    @app.get('/internal/v1/goals/{goal_id}/versions/{version}')
    async def version(goal_id: uuid.UUID, version: int, authorization: str|None=Header(default=None)):
        _service_token(authorization); result=reads.get_version(goal_id,version)
        if result is None: raise HTTPException(404,'version not found')
        return result
    @app.get('/internal/v1/goals/{goal_id}/runtime')
    async def runtime(goal_id: uuid.UUID, authorization: str|None=Header(default=None)):
        _service_token(authorization); result=reads.get_runtime(goal_id)
        if result is None: raise HTTPException(404,'goal not found')
        return result
    @app.get('/internal/v1/goals/active')
    async def active(cursor: uuid.UUID|None=None,limit:int=Query(50,ge=1,le=100),authorization: str|None=Header(default=None)):
        _service_token(authorization); items,next_cursor=reads.list_active(cursor,limit)
        return {'items':items,'next_cursor':None if next_cursor is None else str(next_cursor)}
    @app.get('/v1/goals')
    async def goals(status:str|None=None,authorization:str|None=Header(default=None)):
        owner=_user(authorization); return reads.list_for_owner(owner,status)
    @app.get('/v1/goals/{goal_id}')
    async def goal(goal_id:uuid.UUID,authorization:str|None=Header(default=None)):
        result=reads.get_for_owner(_user(authorization),goal_id)
        if result is None: raise HTTPException(404,'not found')
        return result
    @app.patch('/v1/goals/{goal_id}/lifecycle',status_code=204)
    async def lifecycle(goal_id:uuid.UUID,body:dict[str,str],authorization:str|None=Header(default=None)):
        outcome=reads.transition(_user(authorization),goal_id,body.get('action'))
        if outcome=='missing': raise HTTPException(404,'not found')
        if outcome=='invalid': raise HTTPException(409,'invalid lifecycle transition')
    return app
