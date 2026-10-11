#!/usr/bin/env python3
"""An AI agent wants to pay for something. A person approves the exact cart. The agent gets a signed receipt anyone can verify offline.

This runs the Human Seal sandbox: no real money moves, no live tenant is touched, and the receipt is marked as a rehearsal inside what is signed. It shows where the
Human Seal sits in an agent payment flow such as Google's AP2 or x402 (see https://humanseal.world/payments.html for the honest mapping).

    pip install cryptography
    python3 agent_payment_with_human_seal.py you@example.com

Pass your own email address: applying registers a sandbox rehearsal under it. Nothing is sent to you unless a person later approves a real application.
"""
import base64
import hashlib
import json
import re
import sys
import urllib.request

MCP = "https://fo.flocore.tech/mcp"


def rpc(method, params, token=None, rid=1):
    headers = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
    if token:
        headers["authorization"] = "Bearer " + token
    body = json.dumps({"jsonrpc": "2.0", "id": rid, "method": method, "params": params}).encode()
    with urllib.request.urlopen(urllib.request.Request(MCP, data=body, headers=headers), timeout=60) as r:
        raw = r.read().decode()
    if raw.lstrip().startswith("event:"):                     # server-sent event framing
        raw = next(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
    return json.loads(raw)


def call(tool, args, token=None):
    """Returns (is_error, text, parsed_json_or_None) for one tool call."""
    res = rpc("tools/call", {"name": tool, "arguments": args}, token).get("result", {})
    text = "".join(c.get("text", "") for c in res.get("content", []))
    try:
        parsed = json.loads(text)
    except ValueError:
        parsed = None
    return bool(res.get("isError")), text, parsed


def canonical(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def main(owner_email):
    # 1. Apply. This needs no token and gives a sandbox token that lasts about an hour.
    err, text, _ = call("flocore_onboard_request", {"agent": "zzz-agent-payment-example", "owner_contact": owner_email,
                                                     "intended_use": "rehearse a human approval before an agent payment"})
    found = re.search(r"fc_test_[A-Za-z0-9_.\-]+", text)           # the token is fc_test_ plus a JWT, which contains dots
    token = found.group(0) if found else None
    if err or not token:
        sys.exit("could not get a sandbox token:\n" + text[:600])
    print("1. sandbox token received (rehearsal only)")

    # 2. The agent's intent and the exact cart it wants to pay for. The intent stays with the agent (its own spending rules).
    #    The cart is hashed, so the approval is bound to these exact items and this exact price without FLOCORE ever seeing them.
    intent = {"purpose": "restock label paper", "max_amount": "50.00", "currency": "USD", "expires": "2026-12-31"}
    cart = {"merchant": "Example Stationers", "items": [{"sku": "LBL-4x6", "qty": 10, "unit_price": "4.20"}], "total": "42.00", "currency": "USD"}
    assert float(cart["total"]) <= float(intent["max_amount"]), "the agent's own rule: the cart must be inside the intent"
    digest = hashlib.sha256(canonical(cart).encode()).hexdigest()
    args = {"action_type": "payment.release", "summary": "Pay Example Stationers USD 42.00 for 10 label packs (inside the 50.00 limit).",
            "args_digest": digest, "reversibility": "irreversible"}
    print("2. cart hashed:", digest[:16] + "...")

    # 3. Propose. The action is parked: the reply carries an approval id and says a person approves.
    err, text, parsed = call("flocore_gate_action", args, token)
    m = re.search(r"apr_[A-Za-z0-9_\-]+|\b[0-9a-f]{8}-[0-9a-f-]{27}\b", text)
    if not m:
        sys.exit("expected a parked action with an approval id, got:\n" + text[:600])
    approval_id = m.group(0)
    print("3. parked, waiting for a person. approval id:", approval_id)

    # 4. In the sandbox only, play the human. In production a real person approves in their own screen.
    err, text, _ = call("flocore_sandbox_approve", {"approval_id": approval_id, "decision": "approve"}, token)
    if err:
        sys.exit("the sandbox approval failed:\n" + text[:600])
    print("4. a simulated person approved (sandbox:simulated)")

    # 5. Act on the approval: same arguments plus the approval id. This returns the signed receipt.
    err, text, parsed = call("flocore_gate_action", {**args, "approval_id": approval_id}, token)
    receipt = (parsed or {}).get("result") if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict) else parsed
    if err or not isinstance(receipt, dict) or "signature_ed25519" not in receipt:
        sys.exit("no signed receipt came back:\n" + text[:800])
    print("5. executed, signed receipt received")

    # 6. Verify the receipt offline, with no call to FLOCORE. Then compare the key with the one published at did:web:humanseal.world to rely on our key publication.
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    b64d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    Ed25519PublicKey.from_public_bytes(b64d(receipt["public_key"])).verify(b64d(receipt["signature_ed25519"]), receipt["canonical"].encode())
    print("6. signature OK")
    print("   mode is inside the signed text:", receipt["canonical"].startswith("flocore.gate-action/v1|mode=sandbox"))
    # The receipt signs a digest of the action's four fields. Anyone holding the cart can recompute the whole chain: cart -> cart digest -> action digest -> signed text.
    body = {k: args[k] for k in ("action_type", "summary", "args_digest", "reversibility")}
    action_digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    bound = receipt["action_digest"] == action_digest and ("action_digest=" + action_digest) in receipt["canonical"]
    print("   the signed receipt is bound to this exact cart:", bound)
    if not bound:
        sys.exit("the receipt does not match the cart: do not pay")
    print("   (A sandbox receipt is never proof that a human approved anything. A live one would be.)")
    json.dump(receipt, open("receipt.json", "w"), indent=2)
    print("   saved receipt.json")


if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit(__doc__)
    main(sys.argv[1])
