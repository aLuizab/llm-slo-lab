# Recordings (on-stage fallback)

Terminal casts recorded with [asciinema](https://asciinema.org) on the lab itself, for the case
where the laptop or the cluster refuses to cooperate during the talk. Play with:

```bash
asciinema play -s 2 talk/recordings/break-errors.cast   # 2x speed, ~3 min
asciinema play talk/recordings/smoke.cast
```

| File | What it shows |
|---|---|
| `smoke.cast` | `make smoke`: a streamed chat completion through the gateway, with TTFT and tokens/s printed at the end |
| `break-errors.cast` | `make chaos-status` (healthy) → `make break-errors` → `make slo-status` until `LLMAvailabilityBurnRatePageDemo` fires → `make heal` → `make slo-status` until it resolves |

Re-record with `scripts/record-demo.sh` (cluster up, demo rules applied, `make loadgen`
running). The measured timings of every toggle are in `docs/evidence/chaos.log`; the dashboard
screenshots in `docs/evidence/`. The other fallback is `make use-mock`, which points the
gateway at `mock-llm` so the live break works without the model.
