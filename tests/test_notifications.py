"""Offline regressions for reset detection, durable delivery and the HTTP surface."""
import copy
import http.client
import importlib.util
import io
import json
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

import app

REAL_SEND_BARK = app.send_bark
NOW = datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz) if tz else NOW.replace(tzinfo=None)


def sample(remaining=95, *, reset_hours=6, window_id="primary_window", minutes=0):
    return {
        "ok": True, "captured_at": (NOW + timedelta(minutes=minutes)).isoformat(),
        "device": "test-device", "windows": [{
            "id": window_id, "name": "5小时", "remaining_percent": remaining,
            "used_percent": 100 - remaining, "limit_window_seconds": 18000,
            "reset_at": (NOW + timedelta(hours=reset_hours)).isoformat(),
        }],
    }


class NotificationFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        settings = {
            "DATA": Path(self.temp.name), "BARK_KEY": "private-test-key",
            "BARK_SERVER_URL": "https://private-bark.invalid/secret-path",
            "datetime": FrozenDateTime, "last_collect": 0.0,
            "last_attempt_at": None, "last_success_at": None, "last_error": None,
            "notification_error": None, "notification_lock": threading.Lock(),
            "notification_wakeup": threading.Event(), "RETRY_SECONDS": 30,
        }
        for name, value in settings.items():
            self.start_patch(patch.object(app, name, value))
        self.start_patch(patch.object(app.time, "time", return_value=NOW.timestamp()))
        # Any accidental external request fails the test, without reading OAuth files.
        self.start_patch(patch.object(app.urllib.request, "urlopen", side_effect=AssertionError("Unexpected network request")))
        self.start_patch(patch.object(app, "auth", side_effect=AssertionError("Unexpected credential access")))
        self.sender = self.start_patch(patch.object(app, "send_bark", return_value=(True, "")))
        self.previous = sample(10, reset_hours=1, minutes=-5)
        self.current = sample()
        self.key = "primary_window:" + self.current["windows"][0]["reset_at"]

    def start_patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def queue(self):
        app.notify_resets(self.previous, self.current)

    def state(self):
        return app.read_notification_state()

    def retry_at(self):
        return datetime.fromisoformat(self.state()["next_retry_at"])


class NotificationTests(NotificationFixture):
    def test_first_sample_and_disabled_notifications_do_not_notify(self):
        app.notify_resets(None, self.current)
        app.notify_resets({"ok": False}, self.current)
        app.notify_resets(self.previous, {"ok": False})
        with patch.object(app, "BARK_KEY", ""):
            self.queue()
            app.deliver_pending_notifications(NOW)
        self.assertFalse(app.notification_state_path().exists())
        self.sender.assert_not_called()

    def test_regular_consumption_is_not_a_reset(self):
        app.notify_resets(self.current, sample(90, minutes=5))
        self.assertFalse(self.state()["pending"])
        self.sender.assert_not_called()

    def test_reset_time_move_and_quota_jump_are_both_detected(self):
        for current in (sample(10), sample(95, reset_hours=1)):
            with self.subTest(current=current):
                app.notify_resets(self.previous, current)
                self.assertEqual(len(self.state()["pending"]), 1)
                app.notification_state_path().unlink()

    def test_thresholds_are_strict_and_invalid_numbers_are_ignored(self):
        current = sample(30, reset_hours=1)
        current["windows"][0]["reset_at"] = (NOW + timedelta(hours=1, seconds=60)).isoformat()
        app.notify_resets(self.previous, current)
        self.assertFalse(self.state()["pending"])
        for number in (None, "invalid", float("nan"), float("inf")):
            current = copy.deepcopy(self.current)
            current["windows"][0]["remaining_percent"] = number
            app.notify_resets(self.previous, current)
        self.assertFalse(self.state()["pending"])

    def test_missing_reset_time_uses_capture_time_for_deduplication(self):
        self.current["windows"][0]["reset_at"] = None
        self.queue()
        self.queue()
        self.assertEqual(list(self.state()["pending"]), ["primary_window:" + self.current["captured_at"]])

    def test_enqueue_is_durable_and_does_not_send_synchronously(self):
        self.queue()
        self.assertIn(self.key, self.state()["pending"])
        self.assertTrue(app.notification_wakeup.is_set())
        self.sender.assert_not_called()

    def test_success_acknowledges_once_and_repeated_samples_do_not_resend(self):
        self.queue()
        self.queue()
        app.deliver_pending_notifications(NOW)
        self.queue()
        app.deliver_pending_notifications(NOW + timedelta(hours=1))
        self.sender.assert_called_once()
        state = self.state()
        self.assertFalse(state["pending"])
        self.assertIn(self.key, state["notified"])
        self.assertEqual(state["attempts"], 0)
        self.assertIsNone(state["next_retry_at"])
        self.assertIsNone(state["last_error"])
        self.assertIsNotNone(state["last_success_at"])

    def test_multiple_windows_are_batched_with_original_sample_times(self):
        self.previous["windows"].extend(sample(10, reset_hours=1, window_id="secondary_window")["windows"])
        self.current["windows"].extend(sample(window_id="secondary_window")["windows"])
        self.queue()
        app.deliver_pending_notifications(NOW)
        self.sender.assert_called_once()
        body = self.sender.call_args.args[0]
        self.assertEqual(body.count("采样时间"), 2)
        self.assertIn(self.current["captured_at"], body)
        self.assertEqual(len(self.state()["notified"]), 2)

    def test_failed_delivery_is_retried_after_unchanged_successful_sample(self):
        app.append(self.previous)
        self.sender.side_effect = [(False, "temporary failure"), (True, "")]
        with patch.object(app, "query", return_value=self.current):
            self.assertTrue(app.collect(True)["ok"])
            app.deliver_pending_notifications(NOW)
            due = self.retry_at()
            self.assertTrue(app.collect(True)["ok"])
            self.assertEqual(len(app.history()), 3)
            self.assertIn(self.key, self.state()["pending"])
            app.deliver_pending_notifications(due)
        self.assertEqual(self.sender.call_count, 2)
        self.assertFalse(self.state()["pending"])

    def test_retry_survives_a_fresh_module_instance(self):
        self.queue()
        self.sender.return_value = (False, "temporary failure")
        app.deliver_pending_notifications(NOW)
        due = self.retry_at()
        spec = importlib.util.spec_from_file_location("restarted_quota", app.__file__)
        restarted = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(restarted)
        restarted.DATA = app.DATA
        restarted.BARK_KEY = "private-test-key"
        with patch.object(restarted, "send_bark", return_value=(True, "")) as sender:
            restarted.deliver_pending_notifications(due)
            sender.assert_called_once()
        self.assertFalse(self.state()["pending"])

    def test_backoff_is_persistent_exponential_and_capped(self):
        self.queue()
        self.sender.return_value = (False, "temporary failure")
        now = NOW
        for attempt in range(1, 14):
            app.deliver_pending_notifications(now)
            due = self.retry_at()
            self.assertEqual((due - now).total_seconds(), min(3600, 30 * 2 ** (attempt - 1)))
            self.assertEqual(self.state()["attempts"], attempt)
            app.deliver_pending_notifications(due - timedelta(seconds=1))
            self.assertEqual(self.sender.call_count, attempt)
            now = due

    def test_disabled_key_preserves_pending_events(self):
        self.queue()
        with patch.object(app, "BARK_KEY", ""):
            app.deliver_pending_notifications(NOW)
            status = app.notification_status()
        self.assertFalse(status["enabled"])
        self.assertEqual(status["pending_count"], 1)
        self.sender.assert_not_called()

    def test_delivery_does_not_require_successful_quota_collection(self):
        self.queue()
        with patch.object(app, "query", return_value={"ok": False, "error": "quota offline"}):
            self.assertFalse(app.collect(True)["ok"])
            app.deliver_pending_notifications(NOW)
        self.sender.assert_called_once()
        self.assertFalse(self.state()["pending"])
        self.assertEqual(app.last_error, "quota offline")

    def test_background_worker_retries_without_invoking_collector(self):
        with patch.object(app, "deliver_pending_notifications") as deliver, \
                patch.object(app, "collect", side_effect=AssertionError("Must not sample")), \
                patch.object(app.notification_wakeup, "wait", side_effect=[True, StopIteration]):
            with self.assertRaises(StopIteration):
                app.background_notifier()
        self.assertEqual(deliver.call_count, 2)

    def test_legacy_deduplication_file_is_compatible(self):
        app.notification_state_path().parent.mkdir(parents=True)
        app.notification_state_path().write_text(json.dumps({"notified": [self.key]}))
        self.queue()
        app.deliver_pending_notifications(NOW)
        self.sender.assert_not_called()
        self.assertEqual(app.notification_status()["pending_count"], 0)
        self.current["windows"][0]["reset_at"] = (NOW + timedelta(hours=11)).isoformat()
        self.queue()
        app.deliver_pending_notifications(NOW)
        self.assertIn(self.key, self.state()["notified"])
        self.sender.assert_called_once()

    def test_dedup_history_is_bounded_by_delivery_order_not_lexical_order(self):
        app.save_notification_state({"notified": [f"z:{i}" for i in range(100)], "pending": {}})
        self.queue()
        app.deliver_pending_notifications(NOW)
        notified = self.state()["notified"]
        self.assertEqual(len(notified), 100)
        self.assertEqual(notified[-1], self.key)
        self.assertNotIn("z:0", notified)

    def test_atomic_replace_failure_preserves_existing_file_and_removes_temp(self):
        self.queue()
        before = app.notification_state_path().read_bytes()
        with patch.object(app.os, "replace", side_effect=OSError("private-test-key")):
            with self.assertRaises(app.NotificationStateError):
                app.save_notification_state({"notified": [], "pending": {}})
        self.assertEqual(app.notification_state_path().read_bytes(), before)
        self.assertEqual(list(app.notification_state_path().parent.glob(".bark-state-*")), [])
        self.assertNotIn("private-test-key", app.notification_status()["last_error"])

    def test_unreadable_state_is_not_silently_overwritten(self):
        for raw in ('{', '[]', '{"pending": []}', '{"notified": [5]}',
                    '{"attempts": -1}', '{"next_retry_at": "2026-09-17T08:00:00"}'):
            with self.subTest(raw=raw):
                app.notification_state_path().parent.mkdir(parents=True, exist_ok=True)
                app.notification_state_path().write_text(raw)
                app.deliver_pending_notifications(NOW)
                self.assertEqual(app.notification_state_path().read_text(), raw)
                self.assertIsNotNone(app.notification_status()["last_error"])
        self.sender.assert_not_called()

    def test_repaired_state_clears_transient_health_error(self):
        app.notification_state_path().parent.mkdir(parents=True)
        app.notification_state_path().write_text("invalid JSON")
        app.deliver_pending_notifications(NOW)
        self.assertIsNotNone(app.notification_status()["last_error"])
        app.notification_state_path().write_text('{"notified": []}')
        app.deliver_pending_notifications(NOW)
        self.assertIsNone(app.notification_status()["last_error"])

    def test_service_starts_independent_notifier_and_collector(self):
        with patch("sys.argv", ["app.py"]), patch.object(app, "ThreadingHTTPServer"), \
                patch.object(app.threading, "Thread") as thread, redirect_stdout(io.StringIO()):
            app.main()
        targets = {call.kwargs["target"] for call in thread.call_args_list}
        self.assertEqual(targets, {app.background_notifier, app.background_collector})

    def test_enqueue_write_failure_does_not_advance_history_checkpoint(self):
        app.append(self.previous)
        with patch.object(app, "query", return_value=self.current):
            with patch.object(app, "save_notification_state", side_effect=app.NotificationStateError("disk unavailable")):
                self.assertFalse(app.collect(True)["ok"])
                self.assertEqual(len(app.history()), 1)
            self.assertTrue(app.collect(True)["ok"])
        self.assertIn(self.key, self.state()["pending"])
        self.assertEqual(len(app.history()), 2)

    def test_failed_presend_checkpoint_prevents_network_send(self):
        self.queue()
        with patch.object(app.os, "replace", side_effect=OSError("disk unavailable")):
            app.deliver_pending_notifications(NOW)
        self.sender.assert_not_called()
        self.assertIn(self.key, self.state()["pending"])

    def test_failed_postsend_checkpoint_keeps_event_for_at_least_once_delivery(self):
        self.queue()
        replace = app.os.replace
        with patch.object(app.os, "replace") as mocked:
            # First replacement must really happen, second simulates a crash boundary.
            def write_or_fail(source, destination):
                if mocked.call_count == 1:
                    return replace(source, destination)
                raise OSError("disk unavailable")
            mocked.side_effect = write_or_fail
            app.deliver_pending_notifications(NOW)
        self.sender.assert_called_once()
        self.assertIn(self.key, self.state()["pending"])
        app.deliver_pending_notifications(self.retry_at())
        self.assertEqual(self.sender.call_count, 2)
        self.assertFalse(self.state()["pending"])

    def test_concurrent_delivery_calls_cannot_send_same_event_twice(self):
        self.queue()
        entered, release = threading.Event(), threading.Event()
        def slow_send(_body):
            entered.set()
            if not release.wait(3):
                raise AssertionError("Test release timed out")
            return True, ""
        self.sender.side_effect = slow_send
        workers = [threading.Thread(target=app.deliver_pending_notifications, args=(NOW,)) for _ in range(2)]
        workers[0].start()
        try:
            self.assertTrue(entered.wait(3))
            workers[1].start()
        finally:
            release.set()
            for worker in workers:
                if worker.ident is not None:
                    worker.join(3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.sender.assert_called_once()
        self.assertFalse(self.state()["pending"])

    def test_health_is_credential_free_and_separate_from_quota_errors(self):
        self.queue()
        self.sender.return_value = (False, "Bark 网络或代理连接失败，稍后自动重试。")
        app.deliver_pending_notifications(NOW)
        dashboard = app.payload()
        status = dashboard["notifications"]["bark"]
        self.assertEqual(status["pending_count"], 1)
        self.assertEqual(status["attempts"], 1)
        self.assertIsNotNone(status["next_retry_at"])
        self.assertIsNone(dashboard["collector"]["last_error"])
        encoded = json.dumps(dashboard)
        self.assertNotIn(app.BARK_KEY, encoded)
        self.assertNotIn(app.BARK_SERVER_URL, encoded)
        self.assertNotIn("detail", json.dumps(status))

    def test_one_shot_collection_attempts_delivery(self):
        app.append(self.previous)
        output = io.StringIO()
        with patch("sys.argv", ["app.py", "--collect"]), patch.object(app, "query", return_value=self.current), redirect_stdout(output):
            app.main()
        self.assertTrue(json.loads(output.getvalue())["ok"])
        self.sender.assert_called_once()
        self.assertFalse(self.state()["pending"])


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(app, "BARK_KEY", "private-test-key"),
                        patch.object(app, "BARK_SERVER_URL", "https://bark.invalid/private-path")]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def response(self, body, status=200):
        response = Mock(status=status)
        response.read.return_value = body
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        return response

    def test_positive_acknowledgement_sends_only_expected_fields(self):
        with patch.object(app.urllib.request, "urlopen", return_value=self.response(b'{"code":200}')) as send:
            self.assertEqual(app.send_bark("test notification"), (True, ""))
        request = send.call_args.args[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(set(json.loads(request.data)), {"device_key", "title", "body", "group"})
        self.assertNotIn("Authorization", request.headers)
        self.assertEqual(send.call_args.kwargs["timeout"], 10)

    def test_http_success_without_positive_acknowledgement_is_not_delivery(self):
        for body in (b'{"code":400,"message":"private-test-key"}', b'{}', b'[]', b'null', b'not json', b'', b'\xff'):
            with self.subTest(body=body), patch.object(app.urllib.request, "urlopen", return_value=self.response(body)):
                ok, error = app.send_bark("test")
                self.assertFalse(ok)
                self.assertNotIn("private-test-key", error)

    def test_network_and_http_errors_are_sanitized(self):
        errors = [urllib.error.URLError("private-test-key"), TimeoutError("private-test-key"),
                  urllib.error.HTTPError("https://bark.invalid/private-test-key", 503, "private-test-key", {}, None),
                  ValueError("private-test-key")]
        for failure in errors:
            with self.subTest(failure=failure), patch.object(app.urllib.request, "urlopen", side_effect=failure):
                ok, error = app.send_bark("test")
                self.assertFalse(ok)
                self.assertNotIn("private-test-key", error)

    def test_disabled_key_makes_no_request(self):
        with patch.object(app, "BARK_KEY", ""), patch.object(app.urllib.request, "urlopen") as send:
            self.assertFalse(app.send_bark("test")[0])
            send.assert_not_called()


class HttpTests(NotificationFixture):
    def start_server(self):
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        def stop():
            server.shutdown()
            server.server_close()
            thread.join(3)
        self.addCleanup(stop)
        connection = http.client.HTTPConnection(*server.server_address, timeout=3)
        self.addCleanup(connection.close)
        return connection

    def test_loopback_bark_failure_then_recovery_end_to_end(self):
        requests = []
        class BarkStub(app.BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                code = 503 if len(requests) == 1 else 200
                body = json.dumps({"code": code}).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        server = app.ThreadingHTTPServer(("127.0.0.1", 0), BarkStub)
        worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        opener = app.urllib.request.build_opener(app.urllib.request.ProxyHandler({}))
        try:
            with patch.object(app, "BARK_SERVER_URL", f"http://127.0.0.1:{server.server_port}"), \
                    patch.object(app, "send_bark", REAL_SEND_BARK), \
                    patch.object(app.urllib.request, "urlopen", side_effect=opener.open):
                self.queue()
                app.deliver_pending_notifications(NOW)
                self.assertEqual(app.notification_status()["pending_count"], 1)
                self.assertIn("503", app.notification_status()["last_error"])
                app.deliver_pending_notifications(self.retry_at())
                app.deliver_pending_notifications(NOW + timedelta(hours=2))
                self.assertEqual(app.notification_status()["pending_count"], 0)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(3)
        self.assertEqual(len(requests), 2)
        self.assertTrue(all(path == "/push" for path, _, _ in requests))
        self.assertTrue(all("Authorization" not in headers for _, headers, _ in requests))
        self.assertEqual(requests[0][2], requests[1][2])

    def test_status_endpoint_reports_pending_delivery(self):
        self.queue()
        connection = self.start_server()
        connection.request("GET", "/api/status")
        response = connection.getresponse()
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(body)["notifications"]["bark"]["pending_count"], 1)
        self.assertNotIn(app.BARK_KEY.encode(), body)
        self.assertNotIn(app.BARK_SERVER_URL.encode(), body)

    def test_test_notification_is_not_queued_and_does_not_acknowledge_real_events(self):
        self.queue()
        before = app.notification_state_path().read_bytes()
        connection = self.start_server()
        for successful in (False, True):
            self.sender.return_value = (successful, "sanitized test failure")
            connection.request("POST", "/api/notify/test")
            response = connection.getresponse()
            self.assertEqual(response.status, 200 if successful else 400)
            self.assertEqual(json.loads(response.read())["ok"], successful)
            self.assertEqual(app.notification_state_path().read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
