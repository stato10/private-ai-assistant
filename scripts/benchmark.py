import argparse
import json
import os
import re
import shutil
import subprocess
from time import perf_counter

import httpx


CASES = [
    ("fact", "מהי בירת צרפת? ענה במילה אחת.", lambda s: "פריז" in s and len(s.split()) <= 2),
    ("arithmetic", "מה התוצאה של 18 כפול 7? ענה במספר בלבד.", lambda s: s.strip() == "126"),
    ("grounding", "הפגישה מחר ב-14:30 בחדר אלון. ציין רק את השעה ואת שם החדר.",
     lambda s: "14:30" in s and "אלון" in s),
    ("hebrew", "הסבר במשפט אחד בעברית מדוע כדאי לגבות קבצים.",
     lambda s: len(re.findall(r"[א-ת]", s)) >= 20),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--rounds", type=int, default=2, choices=range(1, 11))
    args = parser.parse_args()
    token = os.environ.get("GLASSOS_TOKEN")
    if not token:
        parser.error("Set GLASSOS_TOKEN to the server's access token.")
    gpu = shutil.which("nvidia-smi")
    sampler = None
    rows = []
    os.makedirs(".runtime", exist_ok=True)
    with open(".runtime/gpu.csv", "w", encoding="utf-8") as samples:
        try:
            if gpu:
                sampler = subprocess.Popen([
                    gpu, "--query-gpu=timestamp,utilization.gpu,memory.used",
                    "--format=csv,noheader,nounits", "-lms", "250",
                ], stdout=samples)
            with httpx.Client(
                base_url="http://127.0.0.1:8765", trust_env=False, timeout=190,
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                for iteration in range(1, args.rounds + 1):
                    for name, prompt, check in CASES:
                        start = perf_counter()
                        response = client.post("/api/v1/chat", json={
                            "messages": [{"role": "user", "content": prompt}],
                        })
                        response.raise_for_status()
                        row = response.json()
                        row.update(case=name, iteration=iteration, check=check(row["answer"]),
                                   round_trip_ms=round((perf_counter() - start) * 1000, 1))
                        row["tokens_per_second"] = round(
                            1000 * row["output_tokens"] / row["generation_ms"], 1
                        ) if row["generation_ms"] else None
                        rows.append(row)
        finally:
            if sampler:
                sampler.terminate()
                sampler.wait()
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
