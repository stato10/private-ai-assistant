import re
from typing import Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

from .calculator import TOOL, calculate

DEFAULT_MODEL = "qwen3.5:4b"
MODELS = {
    DEFAULT_MODEL: "Qwen 3.5 · 4B",
    "qwen3.5:9b-q4_K_M": "Qwen 3.5 · 9B",
    "gemma4:e4b-it-q4_K_M": "Gemma 4 · E4B",
    "nemotron-3-nano:4b": "NVIDIA Nemotron 3 Nano · 4B",
    "nemotron-3.5-bolt:e4b-q4_K_M": "NVIDIA Nemotron 3.5 Bolt · E4B",
    "ministral-3:8b": "Ministral 3 · 8B",
    "lfm2.5:8b": "Liquid AI LFM2.5 · 8B",
}
CALCULATOR_MODELS = {"lfm2.5:8b", "qwen3.5:9b-q4_K_M"}


class ModelError(Exception):
    pass


class ModelTimeout(ModelError):
    pass


class Generation(BaseModel):
    text: str = Field(min_length=1)
    output_tokens: int = Field(ge=0)
    generation_ms: float = Field(ge=0)
    load_ms: float = Field(ge=0)


class ChatProvider(Protocol):
    model: str

    async def generate(self, messages: list[dict[str, str]]) -> Generation: ...


class Ollama:
    def __init__(self, client: httpx.AsyncClient, model: str = DEFAULT_MODEL):
        self.client = client
        self.model = model

    async def generate(self, messages: list[dict[str, str]]) -> Generation:
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {"num_ctx": 4096, "num_predict": 512, "temperature": 0.2},
        }
        if self.model in CALCULATOR_MODELS:
            payload["tools"] = [TOOL]
            instruction = "For arithmetic questions, call calculate with the expression before answering."
            if messages and messages[0]["role"] == "system":
                instruction = f"{messages[0]['content']} {instruction}"
                messages = messages[1:]
            payload["messages"] = [{"role": "system", "content": instruction}, *messages]
        if self.model == "lfm2.5:8b":
            payload["options"]["num_predict"] = 1024
        try:
            response = await self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
            message = result["message"]
            calls = message.get("tool_calls") or []
            if calls:
                if self.model not in CALCULATOR_MODELS or len(calls) != 1:
                    raise ModelError("Unexpected calculation request")
                function = calls[0]["function"]
                expression = function["arguments"]["expression"]
                if function["name"] != "calculate" or not isinstance(expression, str):
                    raise ModelError("Unexpected calculation request")
                try:
                    answer = calculate(expression)
                except (SyntaxError, ValueError, ZeroDivisionError):
                    answer = "לא ניתן לחשב את הביטוי שנבחר."
                else:
                    sign = "" if answer.startswith("≈") else "= "
                    answer = f"{expression.strip()} {sign}{answer}"
            else:
                answer = message["content"].strip()
                if self.model == "lfm2.5:8b":
                    answer = re.sub(r"^\s*<think>.*?</think>\s*", "", answer, flags=re.DOTALL)
                    if answer.startswith("<think>") or result.get("done_reason") == "length":
                        raise ModelError("Incomplete model reply")
            return Generation(
                text=answer,
                output_tokens=result.get("eval_count", 0),
                generation_ms=result.get("eval_duration", 0) / 1e6,
                load_ms=result.get("load_duration", 0) / 1e6,
            )
        except httpx.TimeoutException as exc:
            raise ModelTimeout from exc
        except (httpx.HTTPError, KeyError, TypeError, AttributeError, ValueError, ValidationError) as exc:
            raise ModelError from exc
