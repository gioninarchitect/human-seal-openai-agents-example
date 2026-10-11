# Agent payments with a person in the loop (sandbox examples)

Two runnable examples from FLOCORE / Human Seal (https://humanseal.world/payments.html). MIT licence. Sandbox only: no money moves, no live tenant is touched, and every receipt is marked as a rehearsal inside what is signed. No outside team has used this yet; feedback is welcome in the issues.

    pip install cryptography
    python3 agent_payment_with_human_seal.py you@example.com
    python3 agent_spend_envelope_with_human_seal.py you@example.com

Use your own email address: applying registers a sandbox rehearsal under it.

1. `agent_payment_with_human_seal.py`: an agent proposes an exact cart, a (simulated) person approves it, and the agent gets a signed receipt it checks offline. The receipt is bound to the exact cart through a chain of hashes anyone holding the cart can recompute.
2. `agent_spend_envelope_with_human_seal.py`: a person approves one spending limit that names the agent's key, using the constraint names of the AP2 specification (payment.allowed_payees, payment.amount_range, payment.budget, payment.execution_date). The agent then pays inside it, and a verifier checks each payment offline. In the last run, 1 payment was accepted and 6 were refused (over the per-payment limit, over the total budget, a payee not on the list, a date outside the window, a payment signed by the wrong key, and a limit changed after approval).

What this is not: it is AP2-shaped, not AP2. AP2 mandates are SD-JWT verifiable credentials signed through a trusted surface; these are not. It is not certified or listed with AP2, x402 or any card network. A sandbox receipt is never proof that a human approved anything. What is built and what is not: https://humanseal.world/limitations.html
