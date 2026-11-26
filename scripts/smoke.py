"""Start a temporary localhost API and check it against the running Ollama."""
import argparse
import os
import secrets
import subprocess
import sys
from time import monotonic, sleep

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args()
    base_url = "http://127.0.0.1:8765"
    env = {**os.environ, "GLASSOS_TOKEN": secrets.token_urlsafe(32), "PYTHONIOENCODING": "utf-8"}
    with httpx.Client(base_url=base_url, trust_env=False, timeout=2) as client:
        try:
            client.get("/healthz")
        except (httpx.ConnectError, httpx.ConnectTimeout):
            pass
        else:
            raise SystemExit("Stop the existing Local Assistant server before running the smoke test.")
        with subprocess.Popen([
            sys.executable, "-m", "uvicorn", "glassos.app:app", "--host", "127.0.0.1",
            "--port", "8765", "--no-access-log", "--no-proxy-headers",
        ], env=env) as server:
            try:
                deadline = monotonic() + 10
                while monotonic() < deadline and server.poll() is None:
                    try:
                        client.get("/healthz").raise_for_status()
                        break
                    except (httpx.ConnectError, httpx.ConnectTimeout):
                        sleep(0.2)
                else:
                    raise RuntimeError("Local Assistant API did not start")
                assert 'lang="he"' in client.get("/").raise_for_status().text
                client.get("/assets/chat.js").raise_for_status()
                assert client.get("/api/v1/session").status_code == 401
                headers = {"Authorization": f"Bearer {env['GLASSOS_TOKEN']}"}
                assert client.get("/api/v1/session", headers=headers).status_code == 204
                subprocess.run([
                    sys.executable, "scripts/benchmark.py", "--rounds", str(args.rounds),
                ], env=env, check=True)
                response = client.post("/api/v1/chat", headers=headers, timeout=190, json={"messages": [
                    {"role": "user", "content": "מילת הקוד שלי היא סחלב."},
                    {"role": "assistant", "content": "הבנתי."},
                    {"role": "user", "content": "מה מילת הקוד? ענה במילה אחת."},
                ]}).raise_for_status()
                assert "סחלב" in response.json()["answer"]
                print("Multi-turn Hebrew chat passed.")
                ollama_port = int(os.getenv("GLASSOS_OLLAMA_PORT", "11434"))
                with httpx.Client(base_url=f"http://127.0.0.1:{ollama_port}", trust_env=False, timeout=2) as ollama:
                    for model in ollama.get("/api/ps").raise_for_status().json()["models"]:
                        print({key: model.get(key) for key in ("name", "size", "size_vram", "context_length")})
            finally:
                server.terminate()
                server.wait()


if __name__ == "__main__":
    main()
