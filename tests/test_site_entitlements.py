import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import site_entitlements as se  # noqa: E402
from app.signals.scheduler import Service  # noqa: E402


class FakeClient:
    """Stands in for SiteClient: rows keyed by handle, records claims."""

    enabled = True

    def __init__(self, rows, fail_claim=False):
        self.rows = rows
        self.claims = []
        self.fail_claim = fail_claim

    def fetch(self, username=None):
        if username is None:
            return list(self.rows)
        return [r for r in self.rows if r["telegram_username"] == username]

    def claim(self, ent_id, chat_id):
        if self.fail_claim:
            raise RuntimeError("boom")
        self.claims.append((ent_id, int(chat_id)))
        self.rows = [r for r in self.rows if r["id"] != ent_id]
        return True


def _row(i, handle="alice", months=6):
    return {"id": f"00000000-0000-0000-0000-00000000000{i}", "sku": "ezyai_pro_6m",
            "months": months, "telegram_username": handle,
            "stripe_session_id": f"cs_test_{i}"}


def _svc(tmp_path):
    return Service(object(), tmp_path / "state.json")


def test_redeem_for_user_activates_and_claims(tmp_path):
    svc = _svc(tmp_path)
    client = FakeClient([_row(1)])
    granted = se.redeem_for_user(client, svc, 42, "@Alice")
    assert len(granted) == 1 and svc.is_pro(42)
    assert client.claims == [(_row(1)["id"], 42)]
    # a second pull finds nothing and grants nothing
    assert se.redeem_for_user(client, svc, 42, "alice") == []


def test_redeem_is_idempotent_when_claim_fails(tmp_path):
    svc = _svc(tmp_path)
    client = FakeClient([_row(2, months=1)], fail_claim=True)
    first = se.redeem_for_user(client, svc, 7, "alice")
    until = svc.plan_status(7)["until"]
    assert len(first) == 1
    # site still returns the row (claim never landed): no second month, no
    # second confirmation message
    again = se.redeem_for_user(client, svc, 7, "alice")
    assert again == []
    assert svc.plan_status(7)["until"] == until
    assert svc.already_processed("site:cs_test_2")


def test_sweep_matches_known_handles_only(tmp_path):
    svc = _svc(tmp_path)
    svc.remember_user(100, "Alice")
    client = FakeClient([_row(3, "alice"), _row(4, "stranger")])
    out = se.sweep(client, svc)
    assert [(c, r["id"]) for c, r, _ in out] == [(100, _row(3)["id"])]
    assert svc.is_pro(100)
    # stranger's row is untouched until they /start
    assert [r["id"] for r in client.rows] == [_row(4)["id"]]


def test_users_persist_and_normalize(tmp_path):
    svc = _svc(tmp_path)
    svc.remember_user(5, "@Bob_Trader")
    assert svc.chat_for_username("bob_trader") == 5
    assert svc.chat_for_username("BOB_TRADER") == 5
    assert svc.chat_for_username(None) is None
    svc2 = Service(object(), tmp_path / "state.json")
    assert svc2.chat_for_username("bob_trader") == 5


def test_disabled_client_is_noop(tmp_path):
    svc = _svc(tmp_path)
    client = se.SiteClient("", "")
    assert not client.enabled
    assert se.redeem_for_user(client, svc, 1, "alice") == []
    assert se.sweep(client, svc) == []
    assert client.fetch("alice") == [] and client.claim("x", 1) is False


def test_invalid_rows_are_dropped():
    assert se._valid({"id": "a", "stripe_session_id": "cs", "months": 1})
    assert not se._valid({"id": "a", "stripe_session_id": "cs", "months": 0})
    assert not se._valid({"id": "a", "months": 1})
    assert not se._valid("junk")


# -- redeem codes ---------------------------------------------------------------

class CodeClient(FakeClient):
    def fetch_by_code(self, code):
        return [r for r in self.rows if r.get("redeem_code") == code]


def _coded(i, code, months=6):
    row = _row(i, handle="nobody", months=months)
    row["redeem_code"] = code
    return row


def test_normalize_code_tolerates_user_input():
    assert se.normalize_code("ezy-ab12-cd34") == "EZY-AB12-CD34"
    assert se.normalize_code(" EZY AB12 CD34 ") == "EZY-AB12-CD34"
    assert se.normalize_code("ab12cd34") == "EZY-AB12-CD34"
    assert se.normalize_code("EZY-AB1O-CDI4") == "EZY-AB10-CD14"  # O/0, I/1 folded
    assert se.normalize_code("EZY-AB12-CD34-EF56") == "EZY-AB12-CD34-EF56"
    for bad in ("", "abc", "EZY-AB12", "EZY-AB12-CD3", "<b>x</b>", "A" * 40):
        assert se.normalize_code(bad) is None


def test_redeem_code_grants_once_and_claims(tmp_path):
    svc = _svc(tmp_path)
    client = CodeClient([_coded(1, "EZY-AB12-CD34")])
    status, granted = se.redeem_code(client, svc, 555, "ezy ab12 cd34")
    assert status == "ok" and len(granted) == 1
    assert svc.is_pro(555)
    assert client.claims == [(_coded(1, "x")["id"], 555)]
    until = svc.plan_status(555)["until"]
    # same code again: no second period, clear status
    client.rows = [_coded(1, "EZY-AB12-CD34")]  # site failed to mark claimed
    status, granted = se.redeem_code(client, svc, 555, "EZY-AB12-CD34")
    assert status == "already" and granted == []
    assert svc.plan_status(555)["until"] == until


def test_redeem_code_statuses(tmp_path):
    svc = _svc(tmp_path)
    client = CodeClient([])
    assert se.redeem_code(client, svc, 1, "abc")[0] == "bad_format"
    assert se.redeem_code(client, svc, 1, "EZY-ZZZZ-ZZZZ")[0] == "not_found"

    class Down(CodeClient):
        def fetch_by_code(self, code):
            raise RuntimeError("503")

    assert se.redeem_code(Down([]), svc, 1, "EZY-AB12-CD34")[0] == "error"

    class Off(CodeClient):
        enabled = False

    assert se.redeem_code(Off([]), svc, 1, "EZY-AB12-CD34")[0] == "disabled"
    assert not svc.is_pro(1)
