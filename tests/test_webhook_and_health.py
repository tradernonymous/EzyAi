"""The HTTP side of main.py: Stripe webhook ordering and the health probe.
A real ThreadingHTTPServer is started on a free port for each test."""
import http.client
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import main  # noqa: E402
from app import billing, health  # noqa: E402
from app.signals.scheduler import Service, StateError  # noqa: E402


class _Server:
    def __init__(self, service, secret="whsec_test", stale_s=180):
        handler = main.build_handler(service, None, secret, stale_s)
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        return resp.status, data

    def close(self):
        self.srv.shutdown()
        self.srv.server_close()


def _event(event_id="evt_1", chat_id=555, tier="1mo", paid="paid"):
    return {"id": event_id, "type": "checkout.session.completed",
            "data": {"object": {"payment_status": paid,
                                "metadata": {"order": billing.encode_payload(tier, "card", chat_id)}}}}


def _svc(tmp_path):
    return Service(object(), tmp_path / "state.json")


def test_wrong_path_and_missing_secret_are_404(tmp_path):
    svc = _svc(tmp_path)
    s = _Server(svc, secret="")
    try:
        assert s.request("POST", "/webhook/stripe", b"{}")[0] == 404
        assert s.request("POST", "/other", b"{}")[0] == 404
    finally:
        s.close()


def test_oversized_body_is_rejected_before_reading(tmp_path):
    svc = _svc(tmp_path)
    s = _Server(svc)
    try:
        status, _ = s.request("POST", "/webhook/stripe", b"x",
                              {"Content-Length": str(main.MAX_WEBHOOK_BODY + 1)})
        assert status == 413
    finally:
        s.close()


def test_bad_signature_is_400(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(billing, "verify_stripe_event", lambda *a: None)
    s = _Server(svc)
    try:
        assert s.request("POST", "/webhook/stripe", b"{}")[0] == 400
        assert not svc.is_pro(555)
    finally:
        s.close()


def test_fulfilment_happens_before_ack_and_is_idempotent(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(billing, "verify_stripe_event", lambda *a: _event())
    sent = []
    monkeypatch.setattr(main, "notify_pro_active",
                        lambda app, chat, tier, until: sent.append(chat))
    s = _Server(svc)
    try:
        status, body = s.request("POST", "/webhook/stripe", b"{}")
        assert status == 200 and body == b"ok"
        assert svc.is_pro(555) and svc.already_processed("evt_1")
        assert sent == [555]
        until = svc.plan_status(555)["until"]
        status, _ = s.request("POST", "/webhook/stripe", b"{}")  # Stripe retry
        assert status == 200
        assert svc.plan_status(555)["until"] == until  # no second period
        assert sent == [555]
    finally:
        s.close()


def test_unpersisted_fulfilment_returns_500_so_stripe_retries(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(billing, "verify_stripe_event", lambda *a: _event("evt_2"))

    def refuse(*a, **k):
        raise StateError("disk full")

    monkeypatch.setattr(svc, "activate_pro", refuse)
    s = _Server(svc)
    try:
        status, _ = s.request("POST", "/webhook/stripe", b"{}")
        assert status == 500
        assert not svc.already_processed("evt_2")
    finally:
        s.close()


def test_unpaid_session_is_acked_but_not_granted(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    monkeypatch.setattr(billing, "verify_stripe_event",
                        lambda *a: _event("evt_3", paid="unpaid"))
    s = _Server(svc)
    try:
        assert s.request("POST", "/webhook/stripe", b"{}")[0] == 200
        assert not svc.is_pro(555)
    finally:
        s.close()


def test_health_reports_scheduler_liveness(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    s = _Server(svc, stale_s=10)
    try:
        # inside the start-up grace period: healthy without any beat
        monkeypatch.setattr(health, "_started", health.time.time())
        status, body = s.request("GET", "/")
        assert status == 200 and json.loads(body)["ok"] is True
        # grace over, no tick ever completed -> unhealthy
        monkeypatch.setattr(health, "_started", health.time.time() - 1000)
        health._beats.pop("tick", None)
        assert s.request("GET", "/")[0] == 503
        health.beat("tick")
        assert s.request("GET", "/")[0] == 200
        # a tick older than the threshold -> unhealthy again
        health._beats["tick"] = health.time.time() - 60
        assert s.request("GET", "/")[0] == 503
    finally:
        health._beats.pop("tick", None)
        s.close()
