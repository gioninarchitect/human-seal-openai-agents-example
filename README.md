# Human approval and signed receipts for the OpenAI Agents SDK

Example from FLOCORE / Human Seal (https://humanseal.world). MIT licence. This is an early example: no outside team has used it yet, and feedback is welcome in the issues.

The SDK can pause a risky tool call (`needs_approval`). Its own documentation says the pause carries no identity and no signature, so
you cannot show a third party who approved. This example plugs a named person's approval and a signed receipt into that pause.

What you get back is a receipt anyone can verify offline, with no call to FLOCORE:

    {'signature': 'valid', 'mode': 'sandbox', 'approver': 'sandbox:simulated', 'action_type': 'pay_invoice', ...}

## Try it in five minutes, no human waiting

1. Get a rehearsal token. It is free, lasts about an hour and can never touch a live tenant. Apply once (use a real owner email; a person at
   FLOCORE reviews real applications):

       curl -s https://fo.flocore.tech/mcp -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
         -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"flocore_onboard_request","arguments":{"agent":"my-agent","owner_contact":"you@example.com","source":"openai-agents-example"}}}'

   The reply has `sandbox.token` (starts with `fc_test_`) and `tell_your_human`, a note to forward to the person behind the agent.
2. `pip install openai-agents cryptography pytest`
3. `export FLOCORE_SANDBOX_TOKEN=fc_test_...`
4. `python example.py --offline` (a stand-in model, no OpenAI key needed). With a key, drop `--offline`.
5. `python -m pytest test_flocore_gate.py` (offline tests always; a live test when the token is set).

## How it fits

    result = await Runner.run(agent, "Please pay invoice 1042.")
    while result.interruptions:
        state = result.to_state()
        for item in result.interruptions:
            receipt = gate.request_and_get_receipt(item.name, summary, args, wait_for_person=...)
            state.approve(item)          # reject with state.reject(item) if the person declines
        result = await Runner.run(agent, state)

`flocore_gate.py` is about 100 lines, standard library only for the calls. The receipt binds to a SHA-256 of the arguments, so what was
approved cannot be swapped afterwards, and FLOCORE never sees the arguments.

## What is and is not proven (checked 2026-10-03)

- Proven against the live server and the SDK (version 0.23.1): the SDK pauses, the gate parks the action, the sandbox person approves,
  the receipt verifies offline, and the tool then runs (it recorded invoice 1042). Offline unit tests cover the declined, expired and
  already-used cases using the server's real messages.
- Decline path: with the real SDK and a fake gate that declines, the tool does NOT run and the agent reports that the payment was not made. The server's real "denied" message is covered by tests. NOT yet run end to end against the live server (the sandbox allows 3 tokens an hour per caller and the limit was reached).
- A sandbox receipt is a rehearsal: its signed text starts `flocore.gate-action/v1|mode=sandbox` and it is never proof that a person approved.
  A real approval needs your agent approved by FLOCORE and a live token.
- Shadow mode: with a live token the gate starts in shadow mode, which records what would have been held and returns no receipt until a
  person with approval rights switches it to enforce.
- This has not been tested with a real OpenAI model, only the stand-in.

Docs: https://humanseal.world/quickstart.html . Limits: https://humanseal.world/limitations.html . Verify a receipt in the browser: https://humanseal.world/verify.html
