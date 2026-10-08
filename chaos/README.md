# Chaos toggles

Each toggle breaks exactly one SLI and makes its **demo** burn-rate alert fire within a few
minutes; `make heal` restores everything. Apply the demo rules first (`make slo-demo`) and keep
some traffic flowing (`make loadgen CONCURRENCY=1 DURATION=900` in another terminal), because
an SLI with no events has no burn rate.

| Toggle | What it does | SLI broken | Demo alert | Expected time to fire |
|---|---|---|---|---|
| `make break-errors` | gateway `FAULT_ERROR_RATE=0.5`: half of the requests get a 503 before reaching the model | Availability | `LLMAvailabilityBurnRatePageDemo` (3m/1m at 14.4×) | 1–3 min |
| `make break-latency` | gateway `FAULT_EXTRA_LATENCY_MS=3000`: every request waits 3 s before the model, so TTFT > 2 s | Responsiveness | `LLMResponsivenessBurnRatePageDemo` (3m/1m at 14.4×) | 1–3 min |
| `make break-throughput` | gateway `FAULT_INTER_TOKEN_DELAY_MS=400`: the stream is throttled to ~2 tokens/s (counted after the delay, so the user-visible rate is what is measured) | Throughput | `LLMThroughputBurnRatePageDemo` (3m/1m at 8×) | 1–3 min |
| `make break-cpu` | predictor CPU 3 → 1 core (requests and limits): the realistic version, but on this laptop most requests then hit the 60 s TTFT timeout, so **availability** pages first and throughput has few samples | Availability, then Throughput | `LLMAvailabilityBurnRatePageDemo` | 2–4 min after the model reload |
| `make break-quality` | gateway `FAULT_PROMPT_TEMPLATE=broken` (the user's question is replaced, like a template or RAG regression) plus a degraded system prompt ConfigMap; runs the evaluator twice | Quality | `LLMQualityBurnRatePageDemo` (3m/1m at 5×) | 2–4 min |
| `make heal` | fault rates to 0, original system prompt, original predictor resources, two evaluator runs | all | alerts resolve as the short windows drain | 1–6 min |

Every toggle restarts the gateway Deployment (a few seconds); `break-cpu` and `heal` after it
roll the predictor (about 1.5 min on CPU). `make chaos-status` shows the current fault
settings, burn rates and firing alerts.

Why the quality toggle is a gateway fault and not only a prompt swap: a 0.5B model does not
follow a system prompt strongly enough to be reliably broken by one (under "reply BANANA to
everything" a slice still passed 11 of 13 checks). Dropping the user's question in the
template is both reliable and a realistic incident.

These are demo mechanics, not a chaos-engineering practice: the point is to show the loop
*SLI → burn rate → page → fix → resolve* with the real pipeline, not to find unknown failure
modes.
