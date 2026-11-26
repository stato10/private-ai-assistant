import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from glassos.app import create_app
from glassos.calculator import calculate
from glassos.provider import DEFAULT_MODEL, Generation, ModelError, ModelTimeout, Ollama

TOKEN = "test_" * 8
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/api/v1/chat"
BODY = {"messages": [{"role": "user", "content": "שלום"}]}


class Provider:
    model = "test"
    failure = None

    async def generate(self, messages):
        assert messages[0]["role"] == "system"
        assert messages[-1] == BODY["messages"][0]
        if self.failure:
            raise self.failure
        return Generation(text="שלום!", output_tokens=2, generation_ms=20, load_ms=2)


@pytest.fixture
def client():
    with TestClient(create_app(TOKEN, Provider())) as session:
        yield session


def test_chat_and_page(client):
    assert client.get("/").status_code == 200
    assert "default-src 'self'" in client.get("/assets/chat.js").headers["Content-Security-Policy"]
    assert client.get("/api/v1/session", headers=AUTH).status_code == 204
    response = client.post(URL, headers=AUTH, json=BODY)
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["answer"] == "שלום!"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer nope"}])
def test_auth(client, headers):
    assert client.get("/api/v1/session", headers=headers).status_code == 401
    assert client.post(URL, headers=headers, json=BODY).status_code == 401


def test_rejects_external_host(client):
    assert client.get("/", headers={"Host": "example.invalid"}).status_code == 400


@pytest.mark.parametrize("body", [
    {"messages": []},
    {"messages": [{"role": "system", "content": "private text"}]},
    {"messages": [{"role": "assistant", "content": "private text"}]},
    {"messages": [{"role": "user", "content": " "}]},
    {"messages": [{"role": "user", "content": "א" * 1501}]},
    {**BODY, "model": "other"},
])
def test_invalid_input(client, body):
    response = client.post(URL, headers=AUTH, json=body)
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid_chat_request"}


def test_body_limit(client):
    response = client.post(URL, headers={**AUTH, "Content-Type": "application/json"}, content=b" " * 24_577)
    assert response.status_code == 413
    assert client.post(URL, headers=AUTH, content=b"{}").status_code == 415


@pytest.mark.parametrize("failure,code", [(ModelError(), 503), (ModelTimeout(), 504)])
def test_provider_failure(failure, code):
    provider = Provider()
    provider.failure = failure
    with TestClient(create_app(TOKEN, provider)) as client:
        assert client.post(URL, headers=AUTH, json=BODY).status_code == code
        provider.failure = None
        assert client.post(URL, headers=AUTH, json=BODY).status_code == 200


def test_one_generation_at_a_time():
    started, release = Event(), Event()

    class BlockingProvider(Provider):
        async def generate(self, messages):
            started.set()
            assert await asyncio.to_thread(release.wait, 5)
            return await super().generate(messages)

    with TestClient(create_app(TOKEN, BlockingProvider())) as client, ThreadPoolExecutor() as pool:
        first = pool.submit(client.post, URL, headers=AUTH, json=BODY)
        try:
            assert started.wait(5)
            assert client.post(URL, headers=AUTH, json=BODY).status_code == 429
        finally:
            release.set()
        assert first.result().status_code == 200


def test_startup_requires_secret():
    with pytest.raises(RuntimeError, match="GLASSOS_TOKEN"):
        with TestClient(create_app("short")):
            pass


def test_ollama_http_contract():
    def transport(request):
        assert request.url.host == "127.0.0.1"
        assert request.url.path == "/api/chat"
        assert b"qwen3.5:4b" in request.content
        return httpx.Response(200, json={
            "message": {"content": " שלום "}, "eval_count": 2, "eval_duration": 2_000_000,
        })

    async def run():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(transport)
        ) as client:
            result = await Ollama(client).generate(BODY["messages"])
            assert result.text == "שלום"
            assert result.generation_ms == 2

    asyncio.run(run())


@pytest.mark.parametrize("expression,expected", [
    ("18*7", "126"),
    ("10/4", "2.5"),
    ("0.1+0.2", "0.3"),
    ("(2+3)×4", "20"),
    ("-(3-5)", "2"),
    ("1/3", "≈ 0.3333333333"),
    ("-1/8", "-0.125"),
])
def test_calculator(expression, expected):
    assert calculate(expression) == expected


@pytest.mark.parametrize("expression,error", [
    ("2**10", ValueError),
    ("1/0", ZeroDivisionError),
    ("__import__('os')", ValueError),
    ("1" * 121, ValueError),
    ("1 2", SyntaxError),
])
def test_calculator_rejects(expression, error):
    with pytest.raises(error):
        calculate(expression)


def run_ollama(model, reply):
    sent = {}

    def transport(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, json=reply)

    async def run():
        async with httpx.AsyncClient(
            base_url="http://127.0.0.1:11434", transport=httpx.MockTransport(transport)
        ) as client:
            return await Ollama(client, model).generate(
                [{"role": "system", "content": "system"}, *BODY["messages"]]
            )

    return asyncio.run(run()), sent


def test_calculator_tool_call():
    reply = {"message": {"content": "", "tool_calls": [
        {"function": {"name": "calculate", "arguments": {"expression": "10 / 4"}}},
    ]}, "eval_count": 5}
    result, sent = run_ollama("lfm2.5:8b", reply)
    assert sent["tools"][0]["function"]["name"] == "calculate"
    assert sent["messages"][0]["role"] == "system" and "calculate" in sent["messages"][0]["content"]
    assert len([m for m in sent["messages"] if m["role"] == "system"]) == 1
    assert result.text == "10 / 4 = 2.5"


def test_calculator_tool_rejected_for_other_models():
    reply = {"message": {"content": "", "tool_calls": [
        {"function": {"name": "calculate", "arguments": {"expression": "1+1"}}},
    ]}}
    with pytest.raises(ModelError):
        run_ollama(DEFAULT_MODEL, reply)


def test_reasoning_is_stripped_and_truncation_rejected():
    result, _ = run_ollama("lfm2.5:8b", {"message": {"content": "<think>plan</think>\nשלום"}})
    assert result.text == "שלום"
    with pytest.raises(ModelError):
        run_ollama("lfm2.5:8b", {"message": {"content": "<think>still thinking"}, "done_reason": "length"})
