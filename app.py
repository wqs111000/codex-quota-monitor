#!/usr/bin/env python3
"""Local ChatGPT/Codex quota monitor. Uses only the Python standard library."""
from __future__ import annotations

import argparse, json, os, subprocess, threading, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
DATA = Path(os.environ.get("CHATGPT_QUOTA_DATA_DIR", ROOT / "data"))
DEVICE = os.environ.get("CHATGPT_QUOTA_DEVICE", "this-mac")
POLL_SECONDS = int(os.environ.get("CHATGPT_QUOTA_POLL_SECONDS", "300"))
last_collect = 0.0

def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def load_json(path: Path) -> dict[str, Any] | None:
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError): return None

def auth() -> tuple[str | None, str | None, str]:
    candidates: list[tuple[dict[str, Any], str]] = []
    security = Path("/usr/bin/security")
    if security.exists():
        try:
            p = subprocess.run([str(security), "find-generic-password", "-s", "Codex Auth", "-w"], capture_output=True, text=True, timeout=5)
            if p.returncode == 0 and p.stdout.strip(): candidates.append((json.loads(p.stdout), "macOS Keychain"))
        except (OSError, subprocess.SubprocessError, json.JSONDecodeError): pass
    path = Path.home() / ".codex" / "auth.json"
    value = load_json(path)
    if value: candidates.append((value, str(path)))
    for item, source in candidates:
        if item.get("auth_mode") != "chatgpt": continue
        tokens = item.get("tokens") or {}
        token, account = tokens.get("access_token"), tokens.get("account_id")
        if isinstance(token, str) and token: return token, account if isinstance(account, str) else None, source
    return None, None, "未找到 Codex OAuth 登录态"

def unix_iso(value: Any) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec="seconds") if isinstance(value, (int, float)) else None

def window_name(seconds: int) -> str:
    return {18000: "5小时", 604800: "7天", 2592000: "30天"}.get(seconds, f"{seconds // 3600}小时")

def query() -> dict[str, Any]:
    token, account, source = auth()
    if not token: return {"ok": False, "captured_at": iso_now(), "error": "未找到 Codex OAuth 登录态，请先在 Codex 中使用 ChatGPT 登录。", "credential_source": source}
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli", "Accept": "application/json"}
    if account: headers["ChatGPT-Account-Id"] = account
    req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as response: body = json.loads(response.read())
    except urllib.error.HTTPError as e:
        msg = "登录态已过期，请重新登录 Codex。" if e.code in (401, 403) else f"额度接口返回 HTTP {e.code}。"
        return {"ok": False, "captured_at": iso_now(), "error": msg}
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return {"ok": False, "captured_at": iso_now(), "error": "额度接口暂时不可用，请稍后重试。"}
    windows = []
    for key in ("primary_window", "secondary_window"):
        w = (body.get("rate_limit") or {}).get(key)
        if not isinstance(w, dict) or w.get("used_percent") is None: continue
        used, seconds = float(w["used_percent"]), int(w.get("limit_window_seconds") or 0)
        windows.append({"id": key, "name": window_name(seconds), "used_percent": round(used, 3), "remaining_percent": round(max(0, 100 - used), 3), "limit_window_seconds": seconds, "reset_at": unix_iso(w.get("reset_at"))})
    return {"ok": True, "captured_at": iso_now(), "device": DEVICE, "windows": windows}

def file_for() -> Path:
    return DATA / "devices" / DEVICE / datetime.now().strftime("%Y-%m.jsonl")

def append(item: dict[str, Any]) -> None:
    path = file_for(); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f: f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

def history() -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=90); root = DATA / "devices"; result = []
    if not root.exists(): return result
    for path in root.glob("*/*.jsonl"):
        try: lines = path.read_text(encoding="utf-8").splitlines()
        except OSError: continue
        for line in lines:
            try:
                item = json.loads(line); captured = datetime.fromisoformat(item["captured_at"])
                if captured >= cutoff: result.append(item)
            except (KeyError, TypeError, ValueError, json.JSONDecodeError): pass
    return sorted(result, key=lambda x: x.get("captured_at", ""))

def latest() -> dict[str, Any] | None:
    items = history(); return items[-1] if items else None

def collect(force=False) -> dict[str, Any]:
    global last_collect
    if not force and time.time() - last_collect < POLL_SECONDS: return latest() or {"ok": False, "error": "等待下一次采样。"}
    item = query(); last_collect = time.time()
    if item.get("ok"): append(item)
    return item

def background_collector() -> None:
    """Keep sampling even when the dashboard tab is closed."""
    while True:
        try: collect()
        except Exception: pass
        time.sleep(POLL_SECONDS)

def signal() -> dict[str, Any] | None:
    return load_json(DATA / "signals" / "reset-forecast.json")

def save_signal(value: dict[str, Any]) -> dict[str, Any]:
    path = DATA / "signals" / "reset-forecast.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return value

def velocity(window_id: str, items: list[dict[str, Any]]) -> float | None:
    values = []
    for item in items[-1000:]:
        for w in item.get("windows", []):
            if w.get("id") == window_id: values.append((datetime.fromisoformat(item["captured_at"]), float(w.get("used_percent", 0))))
    if len(values) < 2: return None
    hours = (values[-1][0] - values[0][0]).total_seconds() / 3600
    return (values[-1][1] - values[0][1]) / hours if hours > 0 and values[-1][1] >= values[0][1] else None

def metrics(w: dict[str, Any], items: list[dict[str, Any]], forecast: dict[str, Any] | None) -> dict[str, Any]:
    now = datetime.now(timezone.utc); reset = None
    try: reset = datetime.fromisoformat(w["reset_at"]) if w.get("reset_at") else None
    except ValueError: pass
    length = max(int(w.get("limit_window_seconds") or 1), 1)
    base_remaining = 100.0
    if reset: base_remaining = max(0.0, min(100.0, 100 - ((now - (reset - timedelta(seconds=length))).total_seconds() / length * 100)))
    probability = 0.0; forecast_status = "无预测信号"
    if forecast and forecast.get("reset_type") == "global_hard_reset":
        try:
            fresh = now - datetime.fromisoformat(forecast["forecast_updated_at"]) <= timedelta(hours=6)
            if fresh: probability = max(0.0, min(1.0, float(forecast.get("probability_24h", 0)))); forecast_status = f"提前重置概率 {round(probability * 100)}%"
        except (KeyError, TypeError, ValueError): pass
    target = max(5.0, base_remaining - probability * min(base_remaining, 40.0)); remaining = float(w.get("remaining_percent", 0))
    hours = max(0, (reset - now).total_seconds() / 3600) if reset else None; speed = velocity(w["id"], items)
    return {**w, "recommended_remaining": round(target, 2), "base_target_remaining": round(base_remaining, 2), "pace_gap": round(remaining - target, 2), "hours_to_reset": round(hours, 2) if hours is not None else None, "velocity_per_hour": round(speed, 3) if speed else None, "depletion_hours": round(remaining / speed, 2) if speed and speed > 0 else None, "forecast_status": forecast_status, "recommendation": "可以适度增加使用" if remaining - target > 8 else "按当前节奏使用" if remaining - target > -8 else "建议降低使用强度"}

def payload() -> dict[str, Any]:
    items, last, fc = history(), latest(), signal(); windows = [metrics(w, items, fc) for w in (last or {}).get("windows", [])]
    return {"latest": last, "windows": windows, "history": items, "forecast": fc, "data_dir": str(DATA), "poll_seconds": POLL_SECONDS}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def json(self, value: dict[str, Any], status=200):
        data = json.dumps(value, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/api/status": return self.json(payload())
        if self.path == "/api/collect": return self.json({**collect(True), "dashboard": payload()})
        request_path = self.path.split("?", 1)[0]; path = STATIC / ("index.html" if request_path == "/" else request_path.lstrip("/"))
        if path.is_file() and STATIC in path.parents:
            data = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", {".html":"text/html; charset=utf-8", ".css":"text/css; charset=utf-8", ".js":"application/javascript; charset=utf-8"}.get(path.suffix, "application/octet-stream")); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_error(404)

    def do_POST(self):
        if self.path != "/api/forecast":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            if body.get("clear"):
                path = DATA / "signals" / "reset-forecast.json"
                try: path.unlink()
                except FileNotFoundError: pass
                self.json({"ok": True, "cleared": True})
                return
            probability = float(body.get("probability_24h"))
            reset_at = str(body.get("forecast_reset_at", "")).strip()
            if not 0 <= probability <= 1:
                raise ValueError("probability_24h must be between 0 and 1")
            if not reset_at:
                raise ValueError("forecast_reset_at is required")
            datetime.fromisoformat(reset_at.replace("Z", "+00:00"))
            value = {
                "reset_type": "global_hard_reset",
                "probability_24h": probability,
                "forecast_reset_at": reset_at,
                "forecast_updated_at": iso_now(),
                "source": "manual"
            }
            self.json(save_signal(value))
        except (TypeError, ValueError, json.JSONDecodeError, UnicodeDecodeError) as error:
            self.json({"error": f"预测输入无效：{error}"}, 400)

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--host", default="0.0.0.0"); parser.add_argument("--port", type=int, default=5077); parser.add_argument("--collect", action="store_true"); args = parser.parse_args(); DATA.mkdir(parents=True, exist_ok=True)
    if args.collect: print(json.dumps(collect(True), ensure_ascii=False, indent=2)); return
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ChatGPT quota monitor: http://{args.host}:{args.port}", flush=True)
    threading.Thread(target=background_collector, daemon=True, name="quota-collector").start()
    server.serve_forever()

if __name__ == "__main__": main()
