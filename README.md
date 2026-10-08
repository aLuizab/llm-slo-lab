# llm-slo-lab

**SLOs for LLM inference on Kubernetes**: time to first token, tokens per second, cost per
request and an evaluation-based quality signal, measured with OpenTelemetry GenAI semantic
conventions, stored in Prometheus, alerted on with multi-window multi-burn-rate rules, and
scaled with KEDA. Companion lab for the KubeCon + CloudNativeCon Europe 2027 lightning talk
**"SLOs for LLMs: What an SRE Measures When the Output Is Probabilistic"**.

> **This is a personal lab.** It is not production software, it is not a system of my
> employer, and it contains no employer data, prices or practices. All costs, thresholds and
> objectives are illustrative assumptions chosen for a demo on a laptop.

Português (Brasil): [docs/pt-br/README.md](docs/pt-br/README.md)

## Status

| Phase | Scope | Status |
|---|---|---|
| 0 | Toolchain, repo, skeleton, research ADRs | done |
| 1 | kind cluster, cert-manager, KServe Standard mode, Qwen2.5-0.5B on CPU | done |
| 2 | mock-llm and llm-gateway (OTel GenAI metrics, traces, cost, fault injection) | pending |
| 3 | kube-prometheus-stack, OpenTelemetry Collector, Jaeger | pending |
| 4 | SLIs, SLOs, recording rules, burn-rate alerts, promtool tests | pending |
| 5 | Quality signal: golden dataset and evaluator CronJob | pending |
| 6 | KEDA autoscaling on in-flight requests | pending |
| 7 | Chaos toggles | pending |
| 8 | Grafana "LLM SLOs" dashboard | pending |
| 9 | CI, docs, PT-BR, lightning script, recordings | pending |

## Quickstart

_Filled in when the phases above are done. Target: three commands._ Today:

```bash
scripts/install-tools.sh   # kind, kubectl, helm, promtool, kubeconform, jq, make, uv — no sudo
make cluster platform      # kind + cert-manager + KServe (Standard mode)
make kind-load-hf          # pull the 4 GB runtime image once and load it into kind
make model-cache model     # download Qwen2.5-0.5B-Instruct into a PVC, deploy the InferenceService
make smoke-model           # stream a chat completion straight from KServe
```

Measured on the development laptop (WSL2, 8 CPUs / 12 GB): the model is Ready ~95 s after
`kubectl apply`, TTFT is 0.4–0.5 s and output is ~4.5 tokens/s on CPU. Idle memory after
Phase 1 is ~4.2 GB. The runtime image needs ~14 GB of disk in Docker plus a copy in the kind
node.

## Architecture

_Mermaid diagram added in Phase 3._

## The framework

_The SLI → why → how → which CNCF project table lives in [docs/framework.md](docs/framework.md)._

## Cost assumptions

_All cost figures are assumptions, not real prices. Documented in Phase 2._

## Privacy by default

The gateway never records prompt or response content in traces, logs or metrics unless
`CAPTURE_CONTENT=true` is set. Telemetry is copied, retained and searched by people and
systems that were never meant to read a user's prompt; keeping content out of the pipeline
by default is itself an SRE control. See ADR-009 in [docs/decisions.md](docs/decisions.md).

## Limitations

- CPU inference with a 0.5B model is slow (~4.5 tokens/s) and the model is small; the point is
  the measurement, not the answers.
- The KServe Hugging Face backend does not report `usage` in streamed responses, so the
  gateway counts tokens with the tokenizer (ADR-006). The vLLM/GPU profile does report it.
- The GPU profile (`model/gpu/`) is documented but was not run (ADR-010).
- `finish_reason` from the HF backend is not reliable for detecting truncation (ADR-013).

## Decisions and versions

Every pinned version is in [versions.env](versions.env); the reasoning, with what was checked
against upstream docs and when, is in [docs/decisions.md](docs/decisions.md).

## License

Apache-2.0. Model weights are not part of this repository; Qwen2.5-0.5B-Instruct is
distributed by Alibaba Cloud under Apache-2.0.
