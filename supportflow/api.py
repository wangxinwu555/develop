"""第 6 层：HTTP 接口。同步路由由 FastAPI 在线程池中运行。

X-Demo-Token 是公开的演示身份，不是生产鉴权。不要暴露到公网处理真实数据。
"""

import logging
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, StrictBool, field_validator

from supportflow.business import BusinessError
from supportflow.config import ROOT, Settings
from supportflow.service import AgentService, RunFailed

DEMO_USERS = {"demo-alice": "alice", "demo-bob": "bob"}


def identity(x_demo_token: Annotated[str | None, Header()] = None) -> str:
    if x_demo_token not in DEMO_USERS:
        raise HTTPException(401, "请提供 X-Demo-Token: demo-alice 或 demo-bob。")
    return DEMO_USERS[x_demo_token]


class ChatInput(BaseModel):
    message: str = Field(min_length=1, max_length=2000)

    @field_validator("message")
    @classmethod
    def nonblank(cls, value: str):
        if not value.strip():
            raise ValueError("消息不能为空白。")
        return value.strip()


class ApprovalInput(BaseModel):
    approval_id: str = Field(min_length=1, max_length=200)
    approved: StrictBool


def create_app(settings: Settings | None = None, model=None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        app.state.service = AgentService(settings, model=model)
        try:
            yield
        finally:
            app.state.service.close()

    app = FastAPI(title="SupportFlow", version="0.1.0", lifespan=lifespan)
    User = Annotated[str, Depends(identity)]

    @app.exception_handler(BusinessError)
    async def business_error_handler(request, error):
        status = 404 if error.code in ("order_unavailable", "session_unavailable") else 409
        return JSONResponse(
            status_code=status, content={"error": error.code, "message": error.message}
        )

    @app.exception_handler(RunFailed)
    async def run_error_handler(request, error):
        return JSONResponse(status_code=503, content={"error": "run_failed", "message": str(error)})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(ROOT / "web/index.html")

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "agent_mode": settings.agent_mode,
            "retrieval_mode": settings.retrieval_mode,
            "embedding_provider": settings.embedding_provider,
            "auth": "demo_only",
        }

    @app.post("/api/sessions", status_code=201)
    def create_session(user_id: User):
        return {"session_id": app.state.service.store.create_session(user_id)}

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str, user_id: User):
        with app.state.service.lock(session_id):
            return app.state.service.response(user_id, session_id)

    @app.post("/api/sessions/{session_id}/messages")
    def chat(session_id: str, body: ChatInput, user_id: User):
        return app.state.service.chat(user_id, session_id, body.message)

    @app.post("/api/sessions/{session_id}/approval")
    def approve(session_id: str, body: ApprovalInput, user_id: User):
        return app.state.service.approve(user_id, session_id, body.approval_id, body.approved)

    @app.post("/api/sessions/{session_id}/retry")
    def retry(session_id: str, user_id: User):
        return app.state.service.retry(user_id, session_id)

    @app.get("/api/orders/{order_id}")
    def get_order(order_id: str, user_id: User):
        return app.state.service.store.get_order(user_id, order_id.upper())

    @app.get("/api/tickets")
    def tickets(user_id: User):
        return app.state.service.store.list_tickets(user_id)

    return app


app = create_app()
