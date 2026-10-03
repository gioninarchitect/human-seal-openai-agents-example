"""Run: python -m pytest test_flocore_gate.py
Offline tests use a stand-in server. The live test runs only when FLOCORE_SANDBOX_TOKEN is set (apply once, see the README)."""
import os

import pytest

import flocore_gate as fg


class _Fake(fg.FlocoreGate):
    def __init__(self, replies):
        super().__init__("fc_test_x")
        self.replies, self.sent = list(replies), []

    def _call(self, name, arguments):
        self.sent.append((name, arguments))
        return self.replies.pop(0)


APPROVAL = "11111111-2222-3333-4444-555555555555"


def test_the_digest_binds_the_arguments_and_ignores_key_order():
    assert fg.args_digest({"a": 1, "b": 2}) == fg.args_digest({"b": 2, "a": 1})
    assert fg.args_digest({"a": 1}) != fg.args_digest({"a": 2})


def test_a_parked_action_raises_with_the_approval_id():
    gate = _Fake([(True, f"approval {APPROVAL} required (sandbox rehearsal): a human approves")])
    with pytest.raises(fg.Parked) as err:
        gate.propose("send_invoice", "Send invoice 1042", {"n": 1042})
    assert err.value.approval_id == APPROVAL


def test_request_and_get_receipt_redeems_with_the_same_arguments_and_the_id():
    receipt = {"canonical": "flocore.gate-action/v1|mode=sandbox", "signature_ed25519": "s", "public_key": "k"}
    gate = _Fake([(True, f"approval {APPROVAL} required"), (False, {"status": "approved"}), (False, {"executed": True, "result": receipt})])
    got = gate.request_and_get_receipt("send_invoice", "Send invoice 1042", {"n": 1042}, wait_for_person=lambda a: gate.sandbox_approve(a))
    assert got == receipt
    redeem = gate.sent[-1][1]
    assert redeem["approval_id"] == APPROVAL and redeem["args_digest"] == fg.args_digest({"n": 1042})


def test_a_refusal_that_is_not_an_approval_is_an_error_not_a_hang():
    with pytest.raises(fg.GateError):
        _Fake([(True, "authentication required")]).propose("x", "y", {})


@pytest.mark.parametrize("text", [f"sandbox approval {APPROVAL} is denied, not approved", f"approval {APPROVAL} expired before it was used",
                                  f"sandbox approval {APPROVAL} was already used; a new action needs a new approval"])
def test_a_declined_expired_or_used_approval_is_declined_never_pending(text):
    with pytest.raises(fg.Declined):
        _Fake([(True, text)]).redeem("x", "y", {}, APPROVAL)


def test_shadow_mode_is_reported_as_shadow_not_as_a_receipt():
    out = _Fake([(False, {"decision": "would_have_been_held"})]).propose("x", "y", {})
    assert out["shadow"] is True and "canonical" not in out


@pytest.mark.skipif(not os.environ.get("FLOCORE_SANDBOX_TOKEN"), reason="needs a sandbox token")
def test_live_sandbox_loop_gives_a_receipt_that_verifies_offline():
    gate = fg.FlocoreGate(os.environ["FLOCORE_SANDBOX_TOKEN"])
    receipt = gate.request_and_get_receipt("send_invoice", "Example invoice 1042", {"invoice": 1042}, wait_for_person=lambda a: gate.sandbox_approve(a))
    checked = fg.verify_receipt_offline(receipt)
    assert checked["signature"] == "valid" and checked["mode"] == "sandbox" and checked["approver"] == "sandbox:simulated"
