# llm-slo-lab

Personal lab for the KubeCon + CloudNativeCon Europe 2027 lightning talk
"SLOs for LLMs: What an SRE Measures When the Output Is Probabilistic".
Not production. Not an employer system. See README.md and docs/decisions.md.

## What it is

A small instruct model served by KServe (Standard mode, CPU) behind a FastAPI gateway that
emits OpenTelemetry GenAI metrics and traces. Metrics go to Prometheus (native OTLP receiver),
traces to Jaeger, SLO recording rules and multi-window multi-burn-rate alerts live in `slo/`,
KEDA scales the predictor on in-flight requests, and chaos toggles break each SLI for the demo.
`mock-llm` is an OpenAI-compatible fake used in CI and as the offline demo fallback.

## Layout

- `cluster/` kind config · `platform/` Helm values per component · `model/` InferenceService (cpu, gpu)
- `gateway/` FastAPI proxy + tests · `mock-llm/` fake streaming server · `loadgen/` async load generator
- `eval/` golden dataset + evaluator CronJob · `slo/` slos.yaml, rules.yaml, rules-demo.yaml, promtool tests
- `autoscaling/` KEDA · `chaos/` break/heal · `dashboards/` Grafana JSON · `docs/` ADRs, framework, pt-br
- `talk/` script and recordings · `scripts/` helpers · `.github/workflows/` CI

## Conventions

- Phase-gated work (phases 0–9 in README). Finish a phase, run its acceptance check, show the
  output, commit, push. Never claim something works without running it.
- Conventional Commits. English in code, docs and commits; `docs/pt-br/` holds translations.
- Versions are pinned in `versions.env` and justified in `docs/decisions.md` (short ADRs).
  Research the current official docs before changing KServe, OTel GenAI or KEDA config.
- Vendor-neutral: CNCF projects first; Grafana is the only non-CNCF UI. No SaaS, no API keys.
- Privacy by default: no prompt/response content in telemetry unless `CAPTURE_CONTENT=true`.
- OTel metric and attribute names live only in `gateway/app/semconv.py` (ADR-005).
- Python 3.12, `uv` for dependencies, `ruff` for lint/format, `pytest` for tests.
- YAML passes `yamllint` and `kubeconform`; rules pass `promtool check rules` and `promtool test rules`.

## Commands

```
make tools-check     # verify local toolchain versions
make cluster         # kind cluster (cluster/kind-config.yaml)
make platform        # cert-manager, KServe, kube-prometheus-stack, otel-collector, jaeger, keda
make model           # InferenceService (CPU profile)
make smoke-model     # stream a chat completion straight from KServe
make gateway mock    # build+deploy gateway / mock-llm
make smoke           # chat completion through the gateway
make loadgen         # run load through the gateway
make ui              # port-forward Grafana, Prometheus, Jaeger and print URLs
make test lint       # pytest + ruff + yamllint + kubeconform
make slo-gen slo-check slo slo-demo slo-status   # generate rules from slo/slos.yaml, promtool, apply, inspect
make verify-telemetry                            # PromQL for every signal + a Jaeger trace
make break-latency | break-errors | break-quality | heal
```

Never edit slo/rules*.yaml or slo/prometheus/*.yaml by hand: change slo/slos.yaml and run
`make slo-gen`.

## Environment notes (this machine)

- Everything runs in WSL2 Ubuntu (8 CPUs / 12 GB via `.wslconfig`), Docker inside WSL, no sudo.
  Tools are in `~/.local/bin` (`scripts/install-tools.sh`). From Windows, run commands with
  `wsl.exe -d Ubuntu -- bash -l <script>`; write multi-line scripts to a file, do not inline-quote.
- No AVX-512 on this CPU. GPU (RTX 4050, 6 GB) is visible in WSL but the GPU profile is
  documented only, unless the NVIDIA container toolkit gets installed (needs sudo: ask).
