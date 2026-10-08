# Lightning talk run of show (5:00)

**SLOs for LLMs: What an SRE Measures When the Output Is Probabilistic**
KubeCon + CloudNativeCon Europe 2027, AI Inference and Infrastructure track.

Setup before walking on stage (see "Pre-flight" below): cluster up, demo rules applied,
`make loadgen CONCURRENCY=1 DURATION=1200` running in a hidden terminal, `make ui` running,
Grafana "LLM SLOs" open in kiosk mode with the last 15 minutes, a second terminal ready in
the repo. Fallbacks: `talk/recordings/*.cast` and `make use-mock`.

| Time | Segment | On screen | Say |
|---|---|---|---|
| 0:00–1:00 | **Problem** | Title slide, then the dashboard's SLI row | "We serve a model on Kubernetes. The platform team asks the SRE question: is it OK? For a web service I'd answer with availability and latency. For a model, the output is probabilistic, it streams, and a 200 can carry an empty or cut-off answer. I needed SLIs a user would recognise. This is what I measure, with CNCF projects, on a laptop." |
| 1:00–3:00 | **Framework** | `docs/framework.md` table (one slide) | Walk the five rows, ~20 s each. **Availability**: good means non-error *and* non-empty *and* not truncated. **Responsiveness**: time to first token, the queueing signal. **Throughput**: tokens per second after the first one; below 3 a stream feels broken. **Quality**: deterministic checks on a golden set, a proxy that catches regressions — it caught that "temperature 0" was sampling. **Cost**: a budget, not an SLO. Then the plumbing in one breath: OpenTelemetry GenAI conventions out of a gateway, Prometheus' native OTLP receiver, burn-rate rules generated from one YAML spec, KEDA scaling on in-flight requests because a CPU-bound model is always at 100 % CPU when it works. |
| 3:00–4:30 | **Live: break → page → heal** | Terminal + dashboard side by side | `make break-errors` (half the requests get a 503). Point at the "Requests by outcome" bars turning red and the demo burn rate climbing. Within ~1–2 min `LLMAvailabilityBurnRatePageDemo` shows in "Firing alerts". "That's the Workbook's multi-window burn rate: a short window to react, a long window to avoid flapping — compressed for the demo, the production rules are 1h/5m." `make heal`. Burn rate drops, alert resolves while you talk. If time allows, `make chaos-status` to show the resolved state. |
| 4:30–5:00 | **Takeaway** | Final slide: three lines | "Nothing here is new SRE. The SLIs are what changed: first token, tokens per second, a repeatable quality check, and a cost budget — all from one gateway, in open conventions, on open projects. The spec moved under me, the backend ignored temperature 0, and synthetic eval traffic counted as load. The SLOs are how I found out. Repo link." |

## Pre-flight (15 minutes before)

```bash
make tools-check                      # toolchain OK
kubectl get pods -A | grep -v Running # nothing pending
make slo-demo                         # demo windows loaded
make heal                             # known-good state
make ui                               # port-forwards (keep running)
make loadgen CONCURRENCY=1 DURATION=1200   # in its own terminal, keep running
make chaos-status                     # no firing alerts, burn rates < 1
```

Open `http://localhost:3000/d/llm-slos/llm-slos?orgId=1&kiosk&refresh=15s&from=now-15m&to=now`.

## Fallbacks

1. **Venue Wi-Fi is irrelevant**: everything runs on the laptop; nothing is downloaded during
   the talk. The model weights are in `~/.cache/llm-slo-lab/models` and the runtime image is in
   kind.
2. **Laptop too slow / model misbehaving**: `make use-mock` switches the gateway to `mock-llm`
   (same metrics, same alerts, same toggles; the quality SLI is meaningless there and that is
   fine for the live break). Switch back with `make use-model`.
3. **Cluster is dead**: play `talk/recordings/break-errors.cast` with
   `asciinema play -s 2 talk/recordings/break-errors.cast` and show
   `docs/evidence/chaos.log` for the measured timings. Screenshots of the dashboard are in
   `docs/evidence/`.

## Timing notes

The live segment relies on the demo rules (`slo/rules-demo.yaml`): windows 3m/1m, `for: 0m`.
With `FAULT_ERROR_RATE=0.5` the error ratio is 50 % against a 1 % budget, a burn rate of 50×,
far over the 14.4× threshold; the 1-minute window reacts after ~1 minute of traffic, the
3-minute window needs ~40 s of errors to cross 14.4×. Measured end-to-end: `break-errors`
pages after 61 s and resolves 136 s after heal; `break-latency` 75 s / 150 s;
`break-throughput` 106 s; `break-quality` ~2 min / ~2 min (`docs/evidence/chaos-summary.md`).
Do not run `break-cpu` live: it reloads the model (1.5 min each way) and pages availability
before throughput.
