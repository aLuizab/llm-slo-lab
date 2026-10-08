# The framework: what an SRE measures when the output is probabilistic

One slide. Classic SRE practice says: pick a few user-facing SLIs, set objectives, alert on
how fast you burn the error budget. An LLM endpoint does not change that; it changes *what*
is user-facing. Users of a streaming model feel four things: whether an answer came at all,
how long until it started, how fast it flowed, and whether it was any good. Cost is what the
business feels.

| SLI | Why it matters to a user | How it is measured here | CNCF project doing it |
|---|---|---|---|
| **Availability** — good response ratio, objective 99 % | A 200 with an empty or cut-off answer is a failure to the user, not a success. Errors, timeouts, empty and truncated answers are all bad events. | Gateway classifies every request (`llm_slo.requests` by `llm_slo.outcome`). | OpenTelemetry (counter) → Prometheus (ratio over windows) |
| **Responsiveness** — time to first token, objective 95 % < 2 s on CPU | TTFT is the moment the user sees the model is alive. It is the queueing signal: it grows when requests wait for a busy replica. | `gen_ai.server.time_to_first_token` histogram (GenAI semconv), good = bucket `le="2"`. | OpenTelemetry (semconv histogram) → Prometheus (`histogram_quantile`, bucket ratio) |
| **Throughput** — output tokens/s per request, objective 90 % > 3 tok/s | Reading speed. Below ~3 tokens/s a stream feels broken even if it finishes. | Gateway measures tokens after the first one (`llm_slo.output_tokens`, unit `{token}/s`); tokens counted with the model tokenizer when the server sends no `usage`. | OpenTelemetry → Prometheus; model served by KServe |
| **Quality** — evaluation checks passing, objective 90 % | The output is probabilistic; the only honest signal is a repeatable test. Deterministic checks catch regressions (bad prompt, bad rollout, bad quantization). | CronJob runs a golden set at greedy decoding, emits `llm_slo.eval.checks` pass/fail over OTLP. | Kubernetes CronJob → OpenTelemetry → Prometheus |
| **Cost** — notional cost per 1k requests, a budget not an SLO | Tokens and node-hours are money; slow answers cost more node-time. | Gateway computes amortized node-time and per-token cost per request (`llm_slo.request.cost`, assumptions in a ConfigMap). | OpenTelemetry → Prometheus (ticket alert, no page) |

How the loop closes:

- **Alerting**: multi-window, multi-burn-rate rules (Google SRE Workbook): page at 1h/5m
  14.4× or 6h/30m 6×, ticket at 1d/2h 3× or 3d/6h 1×. For 90 % objectives the page factors
  are capped at 8×/5× because a burn rate cannot exceed 1/(1 − objective). Generated from one
  spec (`slo/slos.yaml`), tested with `promtool test rules`. **Prometheus + Alertmanager.**
- **Capacity**: the predictor scales on queue depth (in-flight requests per replica), not on
  CPU, because a CPU-bound model is always at 100 % CPU when it is working. **KEDA**, through
  KServe's native integration.
- **Serving**: a small open model behind an OpenAI-compatible, streaming endpoint. **KServe.**
- **Tracing**: one span per request with the GenAI attributes (model, tokens, finish reason,
  outcome), no prompt content unless opted in. **OpenTelemetry → Jaeger.**
- **Dashboards**: every SLI against its objective, error budget, burn rate, TTFT heatmap,
  tokens/s, cost, eval pass ratio, replicas vs in-flight, firing alerts. **Grafana.**

Three things the lab taught that the slide cannot:

1. **The spec moves under you.** GenAI semantic conventions are still "Development" and were
   moved to a new repository mid-lab, with renamed metrics in `main`. Keep every name in one
   module and test it against the package.
2. **"Temperature 0" is not a promise.** The serving backend treated 0 as "unset" and the
   model's own sampling config applied. The evaluation signal is what caught it.
3. **Synthetic traffic is traffic.** Evaluator runs count as load for the autoscaler and
   compete with users on a one-request-at-a-time model. Tag it, exclude it from user SLIs,
   keep it short.
