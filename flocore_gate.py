"""FLOCORE human-approval gate for any agent code. Standard library only to call the server; `pip install cryptography` to verify a receipt offline.

The flow: propose an action, a named person approves it, and you get back a receipt that anyone can check offline.
In the sandbox (a token that starts with fc_test_) you can play the person yourself with sandbox_approve; a sandbox receipt is signed
as a REHEARSAL and can never be passed off as a real approval. Get a sandbox token by applying (see README).
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import urllib.error
import urllib.request

MCP_URL = "https://fo.flocore.tech/mcp"
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


class Parked(Exception):
    """The action is waiting for a person. `approval_id` is what to approve and what to redeem."""

    def __init__(self, approval_id: str, message: str):
        super().__init__(message)
        self.approval_id = approval_id


class GateError(Exception):
    pass


class Declined(GateError):
    """A person said no, or the approval expired or was already used. The action must NOT run."""


def args_digest(arguments: dict) -> str:
    """Lowercase hex SHA-256 of the arguments as sorted compact JSON, so the receipt binds to them without FLOCORE seeing them."""
    return hashlib.sha256(json.dumps(arguments, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class FlocoreGate:
    def __init__(self, token: str, url: str = MCP_URL):
        self.token, self.url = token, url

    def _call(self, name: str, arguments: dict) -> tuple[bool, object]:
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}}).encode()
        req = urllib.request.Request(self.url, body, {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                                                      "Authorization": "Bearer " + self.token})
        try:
            raw = urllib.request.urlopen(req, timeout=60).read().decode()
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
        data = [line[5:] for line in raw.splitlines() if line.startswith("data:")] or [raw]
        result = json.loads(data[0])["result"]
        text = result["content"][0]["text"]
        try:
            return bool(result.get("isError")), json.loads(text)
        except ValueError:
            return bool(result.get("isError")), text

    def propose(self, action_type: str, summary: str, arguments: dict, reversibility: str = "unknown") -> dict:
        """Ask for approval. Returns the receipt at once if already approved; raises Parked while a person has not decided."""
        payload = {"action_type": action_type, "summary": summary, "args_digest": args_digest(arguments), "reversibility": reversibility}
        return self._finish(payload)

    def redeem(self, action_type: str, summary: str, arguments: dict, approval_id: str, reversibility: str = "unknown") -> dict:
        """After a person approved, call again with the same arguments plus the approval id to receive the signed receipt."""
        payload = {"action_type": action_type, "summary": summary, "args_digest": args_digest(arguments), "reversibility": reversibility,
                   "approval_id": approval_id}
        return self._finish(payload)

    def _finish(self, payload: dict) -> dict:
        is_error, body = self._call("flocore_gate_action", payload)
        if is_error:
            text = str(body).lower()
            if any(word in text for word in ("denied", "declined", "expired", "already used", "refused")):
                raise Declined(str(body))
            found = _UUID.search(str(body))
            if found and "approv" in text:
                raise Parked(found.group(0), str(body))
            raise GateError(str(body))
        if isinstance(body, dict) and body.get("executed") is True and isinstance(body.get("result"), dict):
            return body["result"]                      # the signed receipt
        if isinstance(body, dict) and body.get("decision") == "would_have_been_held":
            return {"shadow": True, "note": "shadow mode: nothing was held and no receipt was issued", "reply": body}
        raise GateError(f"unexpected reply: {str(body)[:300]}")

    def sandbox_approve(self, approval_id: str, decision: str = "approve", reason: str = "") -> dict:
        """Sandbox tokens only: play the human. A real token is refused."""
        args = {"approval_id": approval_id, "decision": decision, **({"reason": reason} if reason else {})}
        is_error, body = self._call("flocore_sandbox_approve", args)
        if is_error:
            raise GateError(str(body))
        return body

    def request_and_get_receipt(self, action_type: str, summary: str, arguments: dict, wait_for_person=None) -> dict:
        """One call for the common path. `wait_for_person(approval_id)` must return once a person decided (in the sandbox pass
        `lambda a: gate.sandbox_approve(a)`). Returns the receipt, or raises GateError if the person declined."""
        try:
            return self.propose(action_type, summary, arguments)
        except Parked as parked:
            if wait_for_person is None:
                raise
            wait_for_person(parked.approval_id)
            return self.redeem(action_type, summary, arguments, parked.approval_id)


def verify_receipt_offline(receipt: dict) -> dict:
    """Check the signature over `canonical` with the key in the receipt. Needs `pip install cryptography`.
    A valid signature proves the receipt is unaltered. To confirm the key is FLOCORE's, compare receipt['public_key'] with the x value of
    publicKeyJwk in https://humanseal.world/.well-known/did.json. `mode` says whether it is a rehearsal."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    b64d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    Ed25519PublicKey.from_public_bytes(b64d(receipt["public_key"])).verify(b64d(receipt["signature_ed25519"]), receipt["canonical"].encode())
    fields = dict(kv.split("=", 1) for kv in receipt["canonical"].split("|")[1:] if "=" in kv)
    return {"signature": "valid", "mode": fields.get("mode", receipt.get("mode")), "approver": fields.get("approver"),
            "action_type": fields.get("action_type"), "decided_at": fields.get("decided_at")}
