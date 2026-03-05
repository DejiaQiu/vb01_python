python3 - <<'PY'
from pathlib import Path
import time, requests

ZC_SECRET = "zc123"  # 要和你启动转发器时 export 的一致
URL = "http://127.0.0.1:18080/in"

p = Path("vib_diagnosis_report_20260228_160606.md")
text = p.read_text(encoding="utf-8", errors="ignore").strip()

MAX_CHUNK = 1500  # 保守分段，避免钉钉长度限制
chunks = [text[i:i+MAX_CHUNK] for i in range(0, len(text), MAX_CHUNK)] or ["(empty)"]

for i, c in enumerate(chunks, 1):
    msg = f"[诊断报告] {p.name} ({i}/{len(chunks)})\n\n{c}"
    r = requests.post(
        URL,
        json={"message": msg},
        headers={"X-ZC-Secret": ZC_SECRET},
        timeout=10
    )
    r.raise_for_status()
    time.sleep(0.35)  # 轻微节流，降低风控概率

print("OK, sent", len(chunks), "chunks")
PY
