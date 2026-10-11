#!/usr/bin/env python3
"""A person approves a SPENDING LIMIT once; an AI agent then pays inside it; anyone can check each payment offline.

This is the delegated case (the person is not present for each payment). The person approves one envelope with the Human Seal sandbox, and the signed receipt binds the
exact envelope, including the agent's public key. Each later payment is a record signed by that agent key. A verifier accepts a payment only if the envelope receipt is genuine,
the envelope is exactly what was approved, the agent key signed the payment, and the payment is inside the limits.

The limits use the constraint names and shapes of AP2 v0.2 (payment.allowed_payees, payment.amount_range, payment.budget, payment.execution_date). This is an AP2-shaped
example, NOT AP2: AP2 mandates are SD-JWT verifiable credentials signed through a trusted surface, and these are not. No money moves and the receipts are sandbox rehearsals.

    pip install cryptography
    python3 agent_spend_envelope_with_human_seal.py you@example.com
"""
import base64
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from agent_payment_with_human_seal import call, canonical   # the same small MCP helpers as the first example

b64u = lambda raw: base64.urlsafe_b64encode(raw).rstrip(b"=").decode()
b64d = lambda s: base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
sha = lambda obj: hashlib.sha256(canonical(obj).encode()).hexdigest()


def get_receipt(token, args):
    """Propose, play the human (sandbox only), act on the approval: returns the signed receipt."""
    err, text, _ = call("flocore_gate_action", args, token)
    m = re.search(r"\b[0-9a-f]{8}-[0-9a-f-]{27}\b", text)
    if not m:
        sys.exit("expected a parked action with an approval id, got:\n" + text[:500])
    err, text, _ = call("flocore_sandbox_approve", {"approval_id": m.group(0), "decision": "approve"}, token)
    if err:
        sys.exit("sandbox approval failed:\n" + text[:500])
    err, text, parsed = call("flocore_gate_action", {**args, "approval_id": m.group(0)}, token)
    receipt = parsed.get("result") if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict) else parsed
    if err or not isinstance(receipt, dict) or "signature_ed25519" not in receipt:
        sys.exit("no signed receipt:\n" + text[:500])
    return receipt


def check_receipt(receipt, args):
    """The receipt's signature is genuine and it is bound to exactly these action fields."""
    try:
        Ed25519PublicKey.from_public_bytes(b64d(receipt["public_key"])).verify(b64d(receipt["signature_ed25519"]), receipt["canonical"].encode())
    except InvalidSignature:
        return False
    body = {k: args[k] for k in ("action_type", "summary", "args_digest", "reversibility")}
    digest = hashlib.sha256(canonical(body).encode()).hexdigest()
    return receipt["action_digest"] == digest and ("action_digest=" + digest) in receipt["canonical"]


def verify_payment(envelope, envelope_args, envelope_receipt, record, signature, spent_so_far):
    """What a payee or payer runs offline. Returns a list of reasons to REFUSE; an empty list means accept. spent_so_far is the verifier's own tally, never the agent's claim."""
    why = []
    if not check_receipt(envelope_receipt, envelope_args):
        why.append("the envelope receipt is not genuine or does not match the action fields")
    if sha(envelope) != envelope_args["args_digest"]:
        why.append("the envelope shown is not the one the person approved")
    try:
        Ed25519PublicKey.from_public_bytes(b64d(envelope["agent_key"])).verify(b64d(signature), canonical(record).encode())
    except (InvalidSignature, ValueError):
        why.append("the payment was not signed by the agent key named in the approved envelope")
    if record.get("envelope_digest") != envelope_receipt.get("action_digest"):
        why.append("the payment does not cite this envelope's receipt")
    amount, when = Decimal(str(record["amount"])), record["execution_date"]
    for c in envelope["constraints"]:
        t = c["type"]
        if t == "payment.allowed_payees" and record["payee"] not in c["allowed"]:
            why.append("payee is not on the allowed list")
        elif t == "payment.amount_range" and not (Decimal(str(c["min"])) <= amount <= Decimal(str(c["max"])) and record["currency"] == c["currency"]):
            why.append("amount is outside the allowed range")
        elif t == "payment.budget" and spent_so_far + amount > Decimal(str(c["max"])):
            why.append("this payment would exceed the total budget")
        elif t == "payment.execution_date" and not (c.get("not_before", "") <= when <= c.get("not_after", "9999")):
            why.append("the payment date is outside the allowed window")
    return why


def main(owner_email):
    err, text, _ = call("flocore_onboard_request", {"agent": "zzz-agent-spend-envelope-example", "owner_contact": owner_email,
                                                     "intended_use": "rehearse a human-approved spending limit"})
    found = re.search(r"fc_test_[A-Za-z0-9_.\-]+", text)
    if err or not found:
        sys.exit("could not get a sandbox token:\n" + text[:500])
    token = found.group(0)
    print("1. sandbox token received (rehearsal only)")

    # The agent has its own key. The person approves a limit that NAMES this key, so only this agent can spend inside it.
    agent = Ed25519PrivateKey.generate()
    agent_pub = b64u(agent.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
    payee = {"name": "Example Stationers", "website": "https://example-stationers.test"}
    envelope = {"type": "world.humanseal.spend_envelope.1", "agent_key": agent_pub, "constraints": [
        {"type": "payment.allowed_payees", "allowed": [payee]},
        {"type": "payment.amount_range", "min": 1.00, "max": 50.00, "currency": "USD"},
        {"type": "payment.budget", "max": 100.00, "currency": "USD"},
        {"type": "payment.execution_date", "not_before": "2026-10-01T00:00:00Z", "not_after": "2026-12-31T23:59:59Z"}]}
    summary = "Let this agent key spend up to USD 100.00 in total, at most USD 50.00 a payment, only at Example Stationers, until 2026-12-31."
    env_args = {"action_type": "payment.envelope", "summary": summary, "args_digest": sha(envelope), "reversibility": "reversible"}
    env_receipt = get_receipt(token, env_args)
    print("2. a (simulated) person approved the envelope; signed receipt received; genuine and bound:", check_receipt(env_receipt, env_args))

    def pay(amount, p=payee, when="2026-10-15T09:00:00Z", signer=agent, env=envelope):
        rec = {"envelope_digest": env_receipt["action_digest"], "payee": p, "amount": amount, "currency": "USD", "execution_date": when,
               "cart_digest": sha({"merchant": p["name"], "total": amount})}
        return rec, b64u(signer.sign(canonical(rec).encode()))

    print("3. offline checks (no call to FLOCORE):")
    cases = [
        ("a payment of 42.00 inside every limit", pay(42.00), envelope, Decimal("0"), True),
        ("a payment of 75.00 (over the 50.00 per-payment limit)", pay(75.00), envelope, Decimal("0"), False),
        ("a second 42.00 after 84.00 already spent (over the 100.00 budget)", pay(42.00), envelope, Decimal("84"), False),
        ("a payment to a payee that is not allowed", pay(20.00, p={"name": "Somewhere Else", "website": "https://else.test"}), envelope, Decimal("0"), False),
        ("a payment dated after the window closed", pay(20.00, when="2027-01-05T09:00:00Z"), envelope, Decimal("0"), False),
        ("a payment signed by a different key", pay(20.00, signer=Ed25519PrivateKey.generate()), envelope, Decimal("0"), False),
        ("the envelope shown with a larger budget than the one approved", pay(20.00), {**envelope, "constraints": [dict(c, max=100000.0) if c["type"] == "payment.budget" else c for c in envelope["constraints"]]}, Decimal("0"), False),
    ]
    bad = 0
    for label, (rec, sig), env, spent, should_accept in cases:
        why = verify_payment(env, env_args, env_receipt, rec, sig, spent)
        accepted = not why
        ok = accepted == should_accept
        bad += 0 if ok else 1
        print(f"   {'ACCEPT' if accepted else 'REFUSE'}  {label}" + ("" if ok else "   <-- UNEXPECTED") + ("" if accepted else "  [" + why[0] + "]"))
    print("   (Sandbox receipts are rehearsals: a live receipt would show a real person as the approver.)")
    json.dump({"envelope": envelope, "envelope_args": env_args, "envelope_receipt": env_receipt}, open("envelope_receipt.json", "w"), indent=2)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    if len(sys.argv) != 2 or "@" not in sys.argv[1]:
        sys.exit(__doc__)
    main(sys.argv[1])
