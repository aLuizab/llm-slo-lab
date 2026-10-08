# Chaos toggles: measured (2026-10-08)

`scripts/chaos-verify.sh` with `loadgen` at concurrency 1 and the demo rules. Four runs were
needed; the raw logs are `chaos-run1.log` … `chaos-run4.log`. The final mechanism of every
toggle is the one documented in `chaos/README.md`.

| Toggle | Demo alert | Time to fire | Time to resolve after `make heal` | Run |
|---|---|---|---|---|
| `break-errors` (`FAULT_ERROR_RATE=0.5`) | `LLMAvailabilityBurnRatePageDemo` | 61 s | 136 s | 1 |
| `break-latency` (`FAULT_EXTRA_LATENCY_MS=3000`) | `LLMResponsivenessBurnRatePageDemo` | 75 s | 150 s | 1 |
| `break-throughput` (`FAULT_INTER_TOKEN_DELAY_MS=400`) | `LLMThroughputBurnRatePageDemo` | 106 s | < 4 min (resolved by the time heal's two evaluator runs finished) | 3 |
| `break-quality` (`FAULT_PROMPT_TEMPLATE=broken` + degraded system prompt) | `LLMQualityBurnRatePageDemo` | 127 s (two evaluator runs: 0/13 checks passed) | ~2 min | 4 |

What did not work, and why it is kept in the logs:

- Run 1, `break-throughput` as "predictor CPU limit 3 → 1": the patch set only `limits.cpu`,
  so `requests > limits` made the Deployment invalid and KServe never rolled the pod.
- Run 2, same toggle with requests and limits patched: the predictor did roll to 1 core, but
  on this laptop most requests then hit the 60 s TTFT timeout, so **availability** paged and
  throughput had too few samples. The CPU variant survives as `make break-cpu`, documented as
  "breaks availability first".
- Runs 1–3, `break-quality` as a system-prompt swap only: a 0.5B model does not follow a
  system prompt strongly enough to be broken by one (8/17 and 11/13 checks still passed under
  "poetry only" and "reply BANANA"). The gateway's broken-template fault (the user's question
  is dropped, a realistic incident) fails 13/13.
- Run 3 ran the quality toggle before the template fault was wired into the gateway (a missed
  edit; the unit test caught it: 1 failed, 23 passed).
