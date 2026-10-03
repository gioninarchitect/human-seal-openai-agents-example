"""OpenAI Agents SDK + FLOCORE: the SDK pauses a risky tool call; instead of a bare approve, a named person approves through FLOCORE and
the run keeps a signed receipt that anyone can verify offline.

    pip install openai-agents cryptography
    export FLOCORE_SANDBOX_TOKEN=fc_test_...        # see README: apply once for a rehearsal token
    export OPENAI_API_KEY=...                       # a real run; without it use:  python example.py --offline
    python example.py --offline --decline           # the person says no: the tool must NOT run

In the sandbox the "person" is simulated and the receipt is signed as a REHEARSAL. With a real FLOCORE token, a person on your side approves
and the receipt names them.
"""
import asyncio
import json
import os
import sys

from agents import Agent, Runner, function_tool

from flocore_gate import FlocoreGate, GateError, verify_receipt_offline

receipts = []          # keep these: this is your proof that a person said yes
paid = []              # what the tool really did


@function_tool(needs_approval=True)
def pay_invoice(invoice: int, amount: float) -> str:
    """Pay a supplier invoice."""
    paid.append(invoice)
    return f"Paid invoice {invoice} for {amount}"


def build_offline_model():
    """A stand-in model so the example runs with no API key: it asks for pay_invoice once, then answers."""
    from agents.items import ModelResponse
    from agents.models.interface import Model
    from agents.usage import Usage
    from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

    class OfflineModel(Model):
        async def get_response(self, system_instructions, input, model_settings, tools, output_schema, handoffs, tracing, **kwargs):
            outputs = [i for i in (input if isinstance(input, list) else [])
                       if (i.get("type") if isinstance(i, dict) else getattr(i, "type", "")) == "function_call_output"]
            if outputs:
                last = outputs[-1]
                shown = str(last.get("output") if isinstance(last, dict) else getattr(last, "output", ""))
                text = "Done: the invoice was paid." if "Paid invoice" in shown else "The payment was NOT made: " + shown[:120]
                msg = ResponseOutputMessage(id="m1", role="assistant", status="completed", type="message",
                                            content=[ResponseOutputText(text=text, type="output_text", annotations=[])])
                return ModelResponse(output=[msg], usage=Usage(), response_id="r2")
            call = ResponseFunctionToolCall(id="fc1", call_id="call1", name="pay_invoice", type="function_call",
                                            arguments=json.dumps({"invoice": 1042, "amount": 1200.0}))
            return ModelResponse(output=[call], usage=Usage(), response_id="r1")

        def stream_response(self, *a, **k):
            raise NotImplementedError

    return OfflineModel()


async def main(offline: bool, decline: bool = False) -> None:
    gate = FlocoreGate(os.environ["FLOCORE_SANDBOX_TOKEN"])
    agent = Agent(name="Payer", instructions="Pay invoice 1042 for 1200.", tools=[pay_invoice],
                  model=build_offline_model() if offline else "gpt-4.1")
    result = await Runner.run(agent, "Please pay invoice 1042.")
    while result.interruptions:
        state = result.to_state()
        for item in result.interruptions:
            args = json.loads(item.arguments) if isinstance(item.arguments, str) else dict(item.arguments or {})
            try:
                receipt = gate.request_and_get_receipt(item.name, f"{item.name} {args}", args,
                                                       wait_for_person=lambda approval_id: gate.sandbox_approve(approval_id, "decline" if decline else "approve",
                                                                                                        "declined in the example" if decline else ""))
                receipts.append(receipt)
                state.approve(item)                      # the SDK may now run the tool
            except GateError as err:
                state.reject(item)                       # a person declined, or the gate refused
                print("not approved:", err)
        result = await Runner.run(agent, state)
    print("tool executed for invoices:", paid or "none")
    print("agent said:", result.final_output)
    for r in receipts:
        print("receipt check:", verify_receipt_offline(r))


if __name__ == "__main__":
    asyncio.run(main(offline="--offline" in sys.argv, decline="--decline" in sys.argv))
