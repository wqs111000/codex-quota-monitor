#!/usr/bin/env python3
"""Local ChatGPT/Codex quota monitor. Uses only the Python standard library."""
from __future__ import annotations

import argparse, json, math, os, subprocess, tempfile, threading, time, urllib.error, urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
STATIC_ROOT = STATIC.resolve()
DATA = Path(os.environ.get("CHATGPT_QUOTA_DATA_DIR", ROOT / "data"))
DEVICE = os.environ.get("CHATGPT_QUOTA_DEVICE", "this-mac")
POLL_SECONDS = int(os.environ.get("CHATGPT_QUOTA_POLL_SECONDS", "300"))
RETRY_SECONDS = max(10, min(POLL_SECONDS, int(os.environ.get("CHATGPT_QUOTA_RETRY_SECONDS", "30"))))
BARK_SERVER_URL = os.environ.get("CHATGPT_QUOTA_BARK_URL", "https://api.day.app").rstrip("/")
BARK_KEY = os.environ.get("CHATGPT_QUOTA_BARK_KEY", "").strip().strip("/")
QUERY_ATTEMPTS = 3
last_collect = 0.0
last_attempt_at: str | None = None
last_success_at: str | None = None
last_error: str | None = None
collect_lock = threading.Lock()
notification_lock = threading.Lock()
notification_wakeup = threading.Event()
notification_error: str | None = None
BARK_RETRY_MAX_SECONDS = 3600

class NotificationStateError(RuntimeError):
    """A sanitized error: never discard an unreadable or unwritable outbox."""

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
    path = Path(os.environ.get("CODEX_AUTH_FILE", Path.home() / ".codex" / "auth.json"))
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
    if seconds <= 0: return "额度窗口"
    return {18000: "5小时", 604800: "7天", 2592000: "30天"}.get(seconds, f"{seconds // 3600}小时")

def query() -> dict[str, Any]:
    token, account, source = auth()
    if not token: return {"ok": False, "captured_at": iso_now(), "error": "未找到 Codex OAuth 登录态，请先在 Codex 中使用 ChatGPT 登录。", "credential_source": source}
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "codex-cli", "Accept": "application/json"}
    if account: headers["ChatGPT-Account-Id"] = account
    req = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers=headers)
    body = None
    failure = "额度接口暂时不可用"
    for attempt in range(QUERY_ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=15) as response: body = json.loads(response.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                failure = "登录态已过期，请重新登录 Codex。"
                return {"ok": False, "captured_at": iso_now(), "error": failure}
            elif e.code == 429:
                failure = "额度接口限流，请稍后重试。"
            elif e.code >= 500:
                failure = f"额度接口暂时不可用（HTTP {e.code}）。"
            else:
                failure = f"额度接口返回 HTTP {e.code}。"
                return {"ok": False, "captured_at": iso_now(), "error": failure}
        except (urllib.error.URLError, TimeoutError, OSError):
            failure = "网络或代理连接失败，请稍后重试。"
        except json.JSONDecodeError:
            failure = "额度接口返回异常数据，请稍后重试。"
        if attempt < QUERY_ATTEMPTS - 1: time.sleep(2 ** attempt)
    if body is None:
        return {"ok": False, "captured_at": iso_now(), "error": f"{failure}（已自动重试 {QUERY_ATTEMPTS} 次）"}
    if not isinstance(body, dict) or not isinstance(body.get("rate_limit"), dict):
        return {"ok": False, "captured_at": iso_now(), "error": "额度接口返回异常数据，请稍后重试。"}
    windows = []
    for key, w in body["rate_limit"].items():
        if not isinstance(w, dict) or w.get("used_percent") is None: continue
        try: used, seconds = float(w["used_percent"]), int(w.get("limit_window_seconds") or 0)
        except (TypeError, ValueError): continue
        windows.append({"id": key, "name": window_name(seconds), "used_percent": round(used, 3), "remaining_percent": round(max(0, 100 - used), 3), "limit_window_seconds": seconds, "reset_at": unix_iso(w.get("reset_at"))})
    return {"ok": True, "captured_at": iso_now(), "device": DEVICE, "windows": windows}

def file_for() -> Path:
    return DATA / "devices" / DEVICE / datetime.now().strftime("%Y-%m.jsonl")

def append(item: dict[str, Any]) -> None:
    path = file_for(); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f: f.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")

def notification_state_path() -> Path:
    return DATA / "signals" / "bark-state.json"

def read_notification_state() -> dict[str, Any]:
    """Read the outbox, including the legacy {notified: [...]} format."""
    try:
        state = json.loads(notification_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    except (OSError, ValueError, UnicodeError) as error:
        raise NotificationStateError("Bark 状态文件无法读取，请检查本地文件。") from error
    if not isinstance(state, dict):
        raise NotificationStateError("Bark 状态文件格式无效，请检查本地文件。")
    notified, pending = state.get("notified", []), state.get("pending", {})
    attempts = state.get("attempts", 0)
    if (not isinstance(notified, list) or any(not isinstance(key, str) for key in notified)
            or not isinstance(pending, dict)
            or any(not isinstance(event, dict)
                   or not isinstance(event.get("detail"), str)
                   or not isinstance(event.get("captured_at"), str) for event in pending.values())
            or type(attempts) is not int or attempts < 0):
        raise NotificationStateError("Bark 状态文件格式无效，请检查本地文件。")
    for key in ("next_retry_at", "last_attempt_at", "last_success_at"):
        value = state.get(key)
        if value is not None:
            try:
                if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
                    raise ValueError
            except ValueError as error:
                raise NotificationStateError("Bark 状态文件时间无效，请检查本地文件。") from error
    return {**state, "notified": notified, "pending": pending, "attempts": attempts}

def save_notification_state(state: dict[str, Any]) -> None:
    """Replace atomically so a crash cannot leave a partially written JSON file."""
    global notification_error
    path = notification_state_path()
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".bark-state-", delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        notification_error = None
    except (OSError, TypeError, ValueError) as error:
        notification_error = "Bark 待发状态无法保存，请检查数据目录权限和磁盘空间。"
        raise NotificationStateError(notification_error) from error
    finally:
        if temporary is not None:
            try: temporary.unlink(missing_ok=True)
            except OSError: pass

def send_bark(message: str, title: str = "Codex Quota Reset") -> tuple[bool, str]:
    """Require a positive Bark acknowledgement; never expose raw response/errors."""
    if not BARK_KEY:
        return False, "未配置 Bark Key"
    try:
        request = urllib.request.Request(
            f"{BARK_SERVER_URL}/push",
            data=json.dumps({"device_key": BARK_KEY, "title": title, "body": message, "group": "Codex Quota"}).encode("utf-8"),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                return False, f"Bark 返回 HTTP {response.status}"
            acknowledgement = json.loads(response.read(65536))
        if not isinstance(acknowledgement, dict) or acknowledgement.get("code") != 200:
            return False, "Bark 未确认接收通知，请检查推送配置。"
        return True, ""
    except urllib.error.HTTPError as error:
        return False, f"Bark 返回 HTTP {error.code}"
    except (ValueError, UnicodeError):
        return False, "Bark 地址或响应格式无效，请检查推送配置。"
    except (urllib.error.URLError, TimeoutError, OSError):
        return False, "Bark 网络或代理连接失败，稍后自动重试。"

def notify_resets(previous: dict[str, Any] | None, current: dict[str, Any]) -> None:
    """Persist detected events before advancing the quota-history checkpoint."""
    if not BARK_KEY or not previous or not previous.get("ok") or not current.get("ok"):
        return
    previous_windows = {w.get("id"): w for w in previous.get("windows", []) if w.get("id")}
    with notification_lock:
        state = read_notification_state()
        known = set(state["notified"]) | set(state["pending"])
        changed = False
        for window in current.get("windows", []):
            window_id, prior = window.get("id"), previous_windows.get(window.get("id"))
            if not window_id or not prior:
                continue
            try:
                remaining = float(window.get("remaining_percent", 0))
                prior_remaining = float(prior.get("remaining_percent", 0))
                if not math.isfinite(remaining) or not math.isfinite(prior_remaining):
                    continue
            except (TypeError, ValueError):
                continue
            try:
                reset_moved = bool(window.get("reset_at") and prior.get("reset_at") and (datetime.fromisoformat(window["reset_at"]) - datetime.fromisoformat(prior["reset_at"])).total_seconds() > 60)
            except (TypeError, ValueError):
                reset_moved = False
            if reset_moved or remaining > prior_remaining + 20:
                event_key = f"{window_id}:{window.get('reset_at') or current.get('captured_at')}"
                if event_key not in known:
                    state["pending"][event_key] = {
                        "detail": f"{window.get('name', '额度窗口')}：{round(remaining)}%",
                        "captured_at": current.get("captured_at") or iso_now(),
                    }
                    known.add(event_key)
                    changed = True
        if changed:
            save_notification_state(state)
            notification_wakeup.set()

def deliver_pending_notifications(now: datetime | None = None) -> None:
    """Retry independently of quota sampling; preserve events until acknowledged."""
    global notification_error
    if not BARK_KEY:
        return
    now = now or datetime.now(timezone.utc)
    with notification_lock:
        try:
            state = read_notification_state()
            if not state["pending"]:
                notification_error = None
                return
            if state.get("next_retry_at") and now < datetime.fromisoformat(state["next_retry_at"]):
                return
            state["attempts"] += 1
            delay = min(BARK_RETRY_MAX_SECONDS, RETRY_SECONDS * 2 ** min(state["attempts"] - 1, 12))
            state["last_attempt_at"] = now.isoformat(timespec="seconds")
            state["next_retry_at"] = (now + timedelta(seconds=delay)).isoformat(timespec="seconds")
            # Persist the retry checkpoint BEFORE network I/O, including crash recovery.
            save_notification_state(state)
            body = "额度窗口检测到重置或额度回升\n" + "\n".join(
                f"{event['detail']}（采样时间 {event['captured_at']}）" for event in state["pending"].values())
            ok, error = send_bark(body)
            state["last_error"] = None if ok else error
            if ok:
                state["notified"] = list(dict.fromkeys(state["notified"] + list(state["pending"])))[-100:]
                state["pending"] = {}
                state["attempts"] = 0
                state["next_retry_at"] = None
                state["last_success_at"] = now.isoformat(timespec="seconds")
            save_notification_state(state)
        except NotificationStateError as error:
            notification_error = str(error)

def notification_status() -> dict[str, Any]:
    """Only expose delivery health, never keys, server URLs or pending messages."""
    status = {"enabled": bool(BARK_KEY), "pending_count": None, "attempts": 0,
              "last_attempt_at": None, "last_success_at": None, "next_retry_at": None,
              "last_error": notification_error}
    try:
        state = read_notification_state()
        status.update({key: state.get(key) for key in ("last_attempt_at", "last_success_at", "next_retry_at")})
        status.update(pending_count=len(state["pending"]), attempts=state["attempts"],
                      last_error=notification_error or state.get("last_error"))
    except NotificationStateError as error:
        status["last_error"] = str(error)
    return status

def background_notifier() -> None:
    """Restore pending events at startup and retry even if quota collection fails."""
    global notification_error
    while True:
        try:
            deliver_pending_notifications()
        except Exception:
            notification_error = "Bark 通知处理异常，稍后自动重试。"
        notification_wakeup.wait(RETRY_SECONDS)
        notification_wakeup.clear()

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
    global last_collect, last_attempt_at, last_success_at, last_error
    with collect_lock:
        if not force and time.time() - last_collect < POLL_SECONDS: return latest() or {"ok": False, "error": "等待下一次采样。"}
        last_attempt_at = iso_now()
        previous = latest()
        item = query()
        if item.get("ok"):
            try:
                notify_resets(previous, item)
            except NotificationStateError as error:
                # Do not lose the reset edge by advancing history without a durable event.
                last_collect = time.time() - max(0, POLL_SECONDS - RETRY_SECONDS)
                last_error = str(error)
                return {"ok": False, "captured_at": item.get("captured_at"), "error": last_error}
            append(item)
            last_collect = time.time()
            last_success_at = item.get("captured_at")
            last_error = None
        else:
            # Keep the failure visible, but allow the next background pass to recover quickly.
            last_collect = time.time() - max(0, POLL_SECONDS - RETRY_SECONDS)
            last_error = item.get("error", "额度采样失败")
        return item

def background_collector() -> None:
    """Keep sampling even when the dashboard tab is closed."""
    global last_collect, last_attempt_at, last_error
    while True:
        try: collect()
        except Exception:
            last_attempt_at = iso_now()
            last_collect = time.time() - max(0, POLL_SECONDS - RETRY_SECONDS)
            last_error = "采样器内部异常，请稍后重试。"
        time.sleep(RETRY_SECONDS if last_error else POLL_SECONDS)

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
    last_good = last.get("captured_at") if last else None
    stale = bool(last_good and (datetime.now(timezone.utc) - datetime.fromisoformat(last_good)).total_seconds() > POLL_SECONDS * 2.5)
    return {"latest": last, "windows": windows, "history": items, "forecast": fc, "data_dir": str(DATA), "poll_seconds": POLL_SECONDS, "notifications": {"bark": notification_status()}, "collector": {"last_attempt_at": last_attempt_at, "last_success_at": last_success_at or last_good, "last_error": last_error, "stale": stale}}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass
    def json(self, value: dict[str, Any], status=200):
        data = json.dumps(value, ensure_ascii=False).encode(); self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8"); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data)
    def do_GET(self):
        if self.path == "/api/status": return self.json(payload())
        if self.path == "/api/collect": return self.json({**collect(True), "dashboard": payload()})
        request_path = self.path.split("?", 1)[0]
        path = (STATIC / ("index.html" if request_path == "/" else request_path.lstrip("/"))).resolve()
        try:
            path.relative_to(STATIC_ROOT)
        except ValueError:
            self.send_error(404)
            return
        if path.is_file():
            data = path.read_bytes(); self.send_response(200); self.send_header("Content-Type", {".html":"text/html; charset=utf-8", ".css":"text/css; charset=utf-8", ".js":"application/javascript; charset=utf-8", ".webmanifest":"application/manifest+json; charset=utf-8"}.get(path.suffix, "application/octet-stream")); self.send_header("Content-Length", str(len(data))); self.end_headers(); self.wfile.write(data); return
        self.send_error(404)

    def do_POST(self):
        if self.path == "/api/notify/test":
            ok, error = send_bark("额度监控通知链路正常。")
            self.json({"ok": ok, "error": error} if not ok else {"ok": True}, 200 if ok else 400)
            return
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
    if args.collect:
        item = collect(True)
        deliver_pending_notifications()
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"ChatGPT quota monitor: http://{args.host}:{args.port}", flush=True)
    threading.Thread(target=background_notifier, daemon=True, name="bark-notifier").start()
    threading.Thread(target=background_collector, daemon=True, name="quota-collector").start()
    server.serve_forever()

if __name__ == "__main__": main()
