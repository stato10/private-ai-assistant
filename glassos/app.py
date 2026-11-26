import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .provider import DEFAULT_MODEL, MODELS, ChatProvider, Generation, ModelError, ModelTimeout, Ollama

WEB = Path(__file__).parent / "web"
MAX_BODY = 24_576
SYSTEM = (
    "You are עוזר מקומי, a private local AI assistant. Reply in the user's language. "
    "Answer only what was asked. Base answers about supplied text on that text. "
    "Correct false premises briefly. If you are uncertain, say what you are unsure about. "
    "Do not invent facts, citations, or details."
)


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1500)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str | None = None
    messages: list[Message] = Field(min_length=1, max_length=9)

    @model_validator(mode="after")
    def check_history(self):
        if self.model is not None and self.model not in MODELS:
            raise ValueError("Unknown local model")
        roles = [message.role for message in self.messages]
        if len(roles) % 2 == 0 or roles != (["user", "assistant"] * (len(roles) // 2) + ["user"]):
            raise ValueError("History must alternate and end with a user message")
        if sum(len(message.content) for message in self.messages) > 2400:
            raise ValueError("History is too long")
        return self


class ChatResponse(BaseModel):
    model: str
    answer: str
    elapsed_ms: float
    output_tokens: int
    generation_ms: float
    load_ms: float


def create_app(token: str | None = None, provider: ChatProvider | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        configured_token = token if token is not None else os.getenv("GLASSOS_TOKEN", "")
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", configured_token):
            raise RuntimeError("Set GLASSOS_TOKEN to a random URL-safe token of at least 32 characters.")
        app.state.token = configured_token.encode("ascii")
        app.state.generation_lock = asyncio.Lock()
        ollama_port = int(os.getenv("GLASSOS_OLLAMA_PORT", "11434"))
        if not 1 <= ollama_port <= 65535:
            raise RuntimeError("GLASSOS_OLLAMA_PORT must be a valid local port.")
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{ollama_port}", trust_env=False, timeout=httpx.Timeout(180, connect=2)
        ) as client:
            app.state.provider = provider if provider is not None else Ollama(client)
            app.state.ollama_client = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    bearer = HTTPBearer(auto_error=False)

    def authenticate(request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
        supplied = credentials.credentials.encode("utf-8") if credentials else b""
        if not secrets.compare_digest(supplied, request.app.state.token):
            raise HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request: Request, exc: RequestValidationError):
        return JSONResponse({"detail": "invalid_chat_request"}, status_code=422)

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/session", dependencies=[Depends(authenticate)], status_code=204)
    async def session():
        return Response(status_code=204)

    @app.get("/api/v1/models", dependencies=[Depends(authenticate)])
    async def models(request: Request):
        try:
            result = await request.app.state.ollama_client.get("/api/tags", timeout=3)
            result.raise_for_status()
            installed = {item["name"] for item in result.json()["models"]}
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(503, "model_unavailable") from exc
        return {
            "default": DEFAULT_MODEL,
            "models": [{"id": model, "label": label} for model, label in MODELS.items() if model in installed],
        }

    @app.post("/api/v1/chat", response_model=ChatResponse, dependencies=[Depends(authenticate)])
    async def chat(request: Request):
        if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
            raise HTTPException(415, "json_required")
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > MAX_BODY:
                raise HTTPException(413, "request_too_large")
            body.extend(chunk)
        try:
            data = ChatRequest.model_validate_json(body)
        except ValueError as exc:
            raise HTTPException(422, "invalid_chat_request") from exc

        lock = request.app.state.generation_lock
        if lock.locked():
            raise HTTPException(429, "model_busy", headers={"Retry-After": "2"})
        model: ChatProvider = request.app.state.provider
        if data.model is not None and isinstance(model, Ollama):
            model = Ollama(model.client, data.model)
        start = perf_counter()
        try:
            async with lock, asyncio.timeout(185):
                result: Generation = await model.generate([
                    {"role": "system", "content": SYSTEM},
                    *(message.model_dump() for message in data.messages),
                ])
        except (ModelTimeout, TimeoutError) as exc:
            raise HTTPException(504, "model_timeout") from exc
        except ModelError as exc:
            raise HTTPException(503, "model_unavailable") from exc
        return ChatResponse(
            model=model.model, answer=result.text, elapsed_ms=round((perf_counter() - start) * 1000, 1),
            output_tokens=result.output_tokens, generation_ms=result.generation_ms, load_ms=result.load_ms,
        )

    @app.get("/", include_in_schema=False)
    async def home():
        return FileResponse(WEB / "index.html")

    app.mount("/assets", StaticFiles(directory=WEB), name="assets")
    return app


app = create_app()
