# Decisions

Short ADRs. Newest at the bottom. Each one records what was checked, when, and the source.
Statuses: **Accepted**, **Proposed** (to be confirmed in a later phase), **Superseded**.

## ADR-001: This is a personal lab

**Status:** Accepted (2026-10-07)

This repository is a personal learning lab built for a KubeCon + CloudNativeCon Europe 2027
lightning talk. It is not production software and it is not a system, data or practice of my
employer. All prices, thresholds and objectives are illustrative.

## ADR-002: Kubernetes 1.36 on kind, Helm 4

**Status:** Accepted (2026-10-07)

- kind v0.33.0 with `kindest/node:v1.36.4`. kind's default image is 1.37, but KEDA 2.21 is
  tested on 1.34–1.36 only (N-2 policy) and KServe needs 1.32+. 1.36 satisfies everyone.
- Helm v4.3.0. Helm 3 receives security fixes only until 2027-02-10, before the talk.
  If a chart misbehaves under Helm 4 it is recorded here and the chart is pinned.

Sources: kind releases, keda.sh/docs/2.21/operate/cluster, helm.sh/blog/helm-v3-end-of-life.

## ADR-003: KServe Standard mode, charts pinned to v0.20.0

**Status:** Accepted (2026-10-07)

- Deployment mode `Standard` (the renamed RawDeployment): plain Deployments and Services, no
  Knative, no Istio. Set with `kserve.controller.deploymentMode=Standard` on the
  `kserve-resources` chart. The ClusterServingRuntimes (including the Hugging Face runtime)
  come from the separate `kserve-runtime-configs` chart and must be enabled explicitly.
- KServe v0.21.0 was released on 2026-09-25 but ghcr.io only has the `v0.21.0-rc1` chart tag,
  and the official docs still pin v0.20.0. The lab pins **v0.20.0** for reproducibility and
  will bump when the v0.21.0 chart tag exists.
- Network controller: Standard mode documents Gateway API + Envoy Gateway as required for
  external access. The lab only needs in-cluster access (gateway → predictor Service), so
  Phase 1 first tries `kserve.controller.gateway.disableIngressCreation=true`. Outcome
  recorded in ADR-012.
- Scale-to-zero is not supported in Standard mode for HTTP; the lab keeps minReplicas=1.

Sources: kserve.github.io/website/docs/admin-guide/kubernetes-deployment,
github.com/kserve/kserve/releases/tag/v0.21.0, github.com/kserve/kserve/tree/master/charts.

## ADR-004: Model: Qwen/Qwen2.5-0.5B-Instruct

**Status:** Accepted (2026-10-07)

Requirements: ≤1B parameters, Apache-2.0 or MIT, not gated, runs on CPU with the KServe
Hugging Face backend, follows JSON and refusal instructions well enough for deterministic checks.

| Candidate | Params | License | Why not |
|---|---|---|---|
| **Qwen/Qwen2.5-0.5B-Instruct** | 494M | Apache-2.0 | chosen |
| Qwen/Qwen3-0.6B | 752M | Apache-2.0 | thinking mode on by default; `<think>` blocks would poison JSON checks |
| HuggingFaceTB/SmolLM2-360M-Instruct | 362M | Apache-2.0 | weakest at structured output |
| TinyLlama/TinyLlama-1.1B-Chat-v1.0 | 1.1B | Apache-2.0 | over 1B, 2K context |
| google/gemma-3-270m-it, gemma-3-1b-it | 268M / 1B | Gemma Terms | not Apache/MIT, gated |
| meta-llama/Llama-3.2-1B-Instruct | 1.24B | Llama Community | not permissive, gated |

Qwen2.5-0.5B-Instruct is ~1 GB in bf16 and ~2 GB in fp32 (what the CPU backend loads with
`--dtype auto`), has a chat template and a 32K context. This is also the family used in the
KServe CPU text-generation example.

## ADR-005: OpenTelemetry GenAI semantic conventions: emit the last released names

**Status:** Accepted (2026-10-07)

What was found:

- GenAI conventions are still **Development** (experimental). Opt-in via
  `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`.
- In semconv **v1.42.0** every `gen_ai.*` attribute, metric, event and span was deprecated in
  the core repository and moved to `open-telemetry/semantic-conventions-genai`, which has
  **no release or tag yet**.
- The last released definitions (core v1.41.1) are:
  - `gen_ai.client.operation.duration` (histogram, s)
  - `gen_ai.client.token.usage` (histogram, `{token}`, attribute `gen_ai.token.type` = input|output)
  - `gen_ai.client.operation.time_to_first_chunk` / `time_per_output_chunk` (streaming, client side)
  - `gen_ai.server.request.duration`, `gen_ai.server.time_to_first_token`,
    `gen_ai.server.time_per_output_token` (histograms, s)
  - attributes `gen_ai.operation.name`, `gen_ai.provider.name` (replaces `gen_ai.system`,
    deprecated in v1.37), `gen_ai.request.model`, `gen_ai.response.model`, `error.type`,
    `server.address`, `server.port`, `gen_ai.response.finish_reasons`,
    `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`.
  - span name `{operation} {model}` (e.g. `chat Qwen2.5-0.5B-Instruct`), kind CLIENT.
  - content capture via opt-in span attributes `gen_ai.input.messages`,
    `gen_ai.output.messages`, `gen_ai.system_instructions`; off by default.
- The unreleased `main` of the new repo renames inference metrics to
  `gen_ai.client.inference.duration`, `gen_ai.client.inference.time_to_first_chunk`,
  `gen_ai.client.inference.time_per_output_chunk`, and replaces `gen_ai.client.token.usage`
  with counters `gen_ai.client.inference.usage.{input_tokens,output_tokens,...}`.

Decision: the gateway emits the **last released names**, because that is what the Python
`opentelemetry-semantic-conventions` package (0.66b1) exposes, what collectors and dashboards
in the wild expect today, and what the talk abstract names. All names live in a single module
(`gateway/app/semconv.py`) so the rename is a one-file change when the GenAI repo ships a
release. Metrics with no semconv equivalent use the `llm_slo.` namespace.

The gateway is a proxy: it is a *client* of KServe and the *serving* front door for the
application. It emits the `gen_ai.server.*` latency histograms as the serving layer (TTFT and
time-per-output-token are what the SLOs are about) and the `gen_ai.client.*` operation
duration and token usage as the client of the model.

Sources: github.com/open-telemetry/semantic-conventions/blob/v1.41.1/docs/gen-ai/,
github.com/open-telemetry/semantic-conventions-genai (main), PyPI
opentelemetry-semantic-conventions 0.66b1.

## ADR-006: Token counting in the gateway

**Status:** Accepted (2026-10-07)

The KServe Hugging Face backend (`--backend=huggingface`) streams chat completions but never
includes `usage` in stream chunks; `stream_options.include_usage` is not consulted (verified in
the v0.21.0 source: `generative_model.py`, `openai_chat_adapter_model.py`). The vLLM backend
(GPU profile) does support `stream_options.include_usage`.

Decision: the gateway uses upstream `usage` when present and otherwise counts tokens with the
model's tokenizer, which is baked into the gateway image at build time so no network access is
needed at runtime. The source of the count is recorded in the `llm_slo.token_source`
attribute (`upstream` | `tokenizer`).

## ADR-007: Metrics into Prometheus via the native OTLP receiver

**Status:** Accepted (confirmed in Phase 3, 2026-10-08). The names below were verified in
Prometheus 3.15 exactly as predicted; `{token}/s` became `_per_second`, so the throughput
histogram is named `llm_slo.output_tokens` to read as `llm_slo_output_tokens_per_second`.

Options: (a) OpenTelemetry Collector `prometheus` exporter + ServiceMonitor scrape, or
(b) Collector `otlphttp` exporter → Prometheus 3.x native OTLP receiver
(`--web.enable-otlp-receiver`, `/api/v1/otlp/v1/metrics`).

Leaning to (b): one translation step owned by Prometheus, no scrape hop, and resource
attributes promoted explicitly with `otlp.promote_resource_attributes`. Name translation uses
the default `UnderscoreEscapingWithSuffixes` strategy: dots become underscores, unit `s`
becomes `_seconds`, `{token}` adds no suffix, counters get `_total`. Examples:

| OTel | Prometheus |
|---|---|
| `gen_ai.client.operation.duration` (s) | `gen_ai_client_operation_duration_seconds_{bucket,sum,count}` |
| `gen_ai.client.token.usage` ({token}) | `gen_ai_client_token_usage_{bucket,sum,count}` |
| `gen_ai.server.time_to_first_token` (s) | `gen_ai_server_time_to_first_token_seconds_*` |
| `llm_slo.requests.in_flight` (gauge) | `llm_slo_requests_in_flight` |

Sources: prometheus.io/docs/guides/opentelemetry, github.com/prometheus/otlptranslator.

## ADR-008: Autoscaling with KEDA through KServe's native integration

**Status:** Proposed (confirmed in Phase 6)

KServe has a native KEDA integration for Standard mode since v0.15 (annotation
`serving.kserve.io/autoscalerClass: keda` plus `spec.predictor.autoScaling.metrics` with an
`External` metric of backend `prometheus`). The docs do not label it alpha. Plan:

- Real model (InferenceService): native integration, so KServe owns the ScaledObject and
  never creates its own HPA.
- mock-llm (plain Deployment, used in CI): a hand-written `ScaledObject` with the same
  Prometheus trigger, which doubles as the documented fallback.
- Fallback if the native path misbehaves: `autoscalerClass: none` + the hand-written
  ScaledObject targeting the predictor Deployment.

Sources: kserve.github.io/website/docs/model-serving/predictive-inference/autoscaling/keda-autoscaler,
keda.sh/docs/2.21/scalers/prometheus.

## ADR-009: Privacy by default

**Status:** Accepted (2026-10-07)

Prompt and response content are never recorded in spans, logs or metrics unless
`CAPTURE_CONTENT=true` is set on the gateway. This mirrors the semconv guidance that content
attributes are opt-in, and it is an SRE lesson: telemetry pipelines are copied, retained and
searched by people who were never meant to read a user's prompt.

## ADR-010: Hardware baseline

**Status:** Accepted (2026-10-07), re-measured at the end of the lab

Development machine: Windows 11 laptop, 12 logical CPUs, 15.7 GB RAM, NVIDIA RTX 4050 Laptop
(6 GB). The lab runs inside WSL2 Ubuntu 26.04 limited by `.wslconfig` to **8 CPUs / 12 GB**.
The CPU has AVX2 but **no AVX-512** (relevant for vLLM CPU builds; the HF backend does not
care). Docker runs natively inside WSL (systemd), not Docker Desktop. No sudo was needed for
the toolchain: every binary is installed to `~/.local/bin` (`scripts/install-tools.sh`).

Measured after Phase 1 (kind + cert-manager + KServe + one predictor replica, idle):
WSL reports 4.2 GB used; the kind node container reports 5.3 GB (includes page cache).
Disk: the `kserve/huggingfaceserver:v0.20.0` image is 4.0 GB compressed and **14.2 GB** on
disk in Docker, plus another copy inside the kind node; model weights 0.95 GB.

## ADR-011: Model weights live in a host-backed PVC, downloaded once by a Job

**Status:** Accepted (2026-10-08)

Options considered: (a) `storageUri: hf://...` and let KServe's storage-initializer download
on every pod start (simple, but 1 GB per restart and per replica, and a network dependency at
scale-up time); (b) KServe `LocalModelCache` (needs extra charts, a node group and local PVs;
more than the lab needs); (c) a `hostPath` PV on the kind node, backed by a host directory via
a kind `extraMount`, filled once by a Job with `huggingface_hub.snapshot_download`, and
referenced as `storageUri: pvc://model-cache/<dir>`.

Decision: (c). Weights survive pod restarts, replica scale-up and even `kind delete cluster`
(the host directory `~/.cache/llm-slo-lab/models` is kept). The predictor runs with
`HF_HUB_OFFLINE=1`, so a scale-up never touches the network. The PV is `ReadWriteMany` so up
to 3 replicas can mount it; on a single-node hostPath volume that is safe.

Measured: the download Job took ~10 min unauthenticated (rate limited by the Hub); the
predictor loads the model from the PVC in ~22 s and is Ready in ~95 s from `kubectl apply`.

## ADR-012: No network controller for KServe

**Status:** Accepted (2026-10-08)

KServe Standard mode documents a network controller (Gateway API + Envoy Gateway, or an
Ingress controller) as a requirement. The lab only talks to the predictor from inside the
cluster (gateway → `qwen-predictor.llm.svc`), so it was installed with
`kserve.controller.gateway.disableIngressCreation=true` and `enableGatewayApi=false`.

Result: the controller reconciles the InferenceService to Ready and creates only the
Deployment and the ClusterIP Service. No Ingress or HTTPRoute is created, and no error is
logged. The `URL` column shows the placeholder `http://qwen-llm.example.com`, which is
cosmetic. With `serving.kserve.io/autoscalerClass: none` no HPA is created either, which is
what Phase 6 needs so KEDA is the only autoscaler.

## ADR-013: Observed behaviour of the Hugging Face backend (CPU)

**Status:** Accepted (2026-10-08), informs the gateway design

From `make smoke-model` against `kserve/huggingfaceserver:v0.20.0`, `--backend=huggingface`,
fp32, 3 CPU limit:

- Streaming works at `/openai/v1/chat/completions` with `"stream": true`; one content chunk
  per token.
- No `usage` in any chunk, even with `stream_options.include_usage` (ADR-006 confirmed).
- TTFT 0.36–0.54 s, ~4.5 output tokens/s. This sets the CPU profile thresholds in
  `slo/slos.yaml` (TTFT < 2 s is comfortably met when healthy, which leaves room for the
  chaos toggles to break it).
- `finish_reason` was `length` on both test requests, including one that ended its JSON
  object cleanly before `max_tokens`. The gateway therefore must not rely on `finish_reason`
  alone to classify truncation; it also checks for empty output.

## ADR-014: Gateway design

**Status:** Accepted (2026-10-08)

- **Proxy, not SDK instrumentation.** The SLIs are measured in a FastAPI proxy
  (`gateway/`) rather than by instrumenting a client library, because that is where an SRE
  can measure every caller the same way, add fault injection, and swap the upstream (KServe
  or `mock-llm`) without touching clients.
- **Honest status codes for streaming.** The gateway opens the upstream stream and waits for
  the first SSE line *before* answering the client. Upstream errors and TTFT timeouts therefore
  become real 502/504 responses instead of a 200 followed by a broken stream. The first line is
  forwarded immediately, so client-observed TTFT is unchanged. Failures after the first byte
  are sent as an SSE `event: error` and still counted (outcome `timeout` / `upstream_error`).
- **Outcome classification.** `success`, `upstream_error`, `timeout`, `empty` (200 with no
  content), `truncated` (`finish_reason=length` *and* output tokens ≥ requested `max_tokens`,
  because of ADR-013), `injected_error` (chaos). Everything except `success` is a bad event
  for the Availability SLI.
- **Both client and server GenAI metrics** are emitted (ADR-005): the gateway is the serving
  front door for the application (`gen_ai.server.*` latency histograms, which carry TTFT and
  time-per-output-token) and a client of the model (`gen_ai.client.operation.duration`,
  `gen_ai.client.token.usage`).
- **Providers are instances, not globals**, so tests inject `InMemoryMetricReader` and
  `InMemorySpanExporter`; 21 gateway tests run against a real `mock-llm` server, and one test
  runs the gateway itself under uvicorn because httpx's ASGI transport buffers streams.
- **`x-mock-*` request headers are forwarded** to the upstream so tests and the offline demo
  can steer `mock-llm` per request (TTFT, errors, length, `usage`); the real model ignores them.
- **Tokenizer baked into the image** at a pinned model revision (`MODEL_REVISION` in
  `versions.env`), so counting works offline and is reproducible.

Measured through the gateway to KServe on CPU: TTFT 0.6–0.9 s, ~5.5 output tokens/s. The very
first request after a rollout, while `kind load` was still importing images on the same node,
took 17.8 s to the first token; later requests did not reproduce it. Kept as a reminder that
TTFT on a shared CPU node is sensitive to neighbours, which is exactly what the SLO is for.

## ADR-015: Telemetry pipeline

**Status:** Accepted (2026-10-08)

- **Prometheus native OTLP receiver** (ADR-007) via kube-prometheus-stack 92.1.0:
  `prometheusSpec.enableOTLPReceiver: true` plus `otlp.promoteResourceAttributes` for
  `service.*` and `k8s.*`. The Collector exports with `otlp_http` to
  `/api/v1/otlp`. Resource attribute `service.name` becomes the `job` label.
- **OpenTelemetry Collector** (chart 0.175.1, contrib 0.162.0) in deployment mode with the
  `kubernetesAttributes` preset. Collector-contrib ≥ 0.161 renamed components: `otlphttp` →
  `otlp_http`, the `otlp` exporter → `otlp_grpc`, `k8sattributes` → `k8s_attributes`. The
  chart rewrites old names with a deprecation warning; the lab uses the new names.
- **Jaeger v2 all-in-one from a plain manifest** (`jaegertracing/jaeger:2.22.0`, in-memory
  storage, native OTLP on 4317/4318). The `jaegertracing/jaeger` Helm chart defaults to an
  Elasticsearch backend, which is more than a laptop needs. Jaeger v2 serves the **v3 query
  API** (`/api/v3/services`, `/api/v3/traces?query.service_name=...`); the v1 `/api/services`
  path returns 404, which first looked like "no traces".
- **Grafana** has Prometheus, Alertmanager and Jaeger datasources; dashboards are picked up
  from any namespace via the sidecar label `grafana_dashboard=1`. Anonymous viewer access is on
  for the demo. Default dashboards and default rules are off to keep the stack light.
- **Measured:** the whole observability stack adds ~1.3 GB (WSL 5.5 GB used after install).

Finding worth a slide: with the plain Hugging Face backend on CPU, two concurrent requests run
their `generate()` loops in parallel on the same 3 CPUs and *both* slow down; TTFT of the
second request includes the first one's generation. At concurrency 2 TTFT p50 went from 0.6 s
to 6.5 s (p95 14.8 s). The predictor is effectively a one-request-at-a-time server, which is why
the KEDA signal is in-flight requests per replica and why the healthy baseline load is
concurrency 1 per replica.

## ADR-016: SLOs, recording rules and burn-rate alerts are generated from one spec

**Status:** Accepted (2026-10-08)

- `slo/slos.yaml` is the single, human-readable source of truth (OpenSLO-inspired, not
  conformant): SLIs as good/total PromQL with `{window}` placeholders, objectives, hardware
  profiles (CPU vs GPU thresholds), burn-rate pairs and two *modes*. `slo/gen_rules.py`
  renders a PrometheusRule for production windows, a clearly labelled demo PrometheusRule with
  compressed windows (`slodemo:` metric prefix, `...Demo` alert names, `slo_mode=demo`), and
  plain rule files for promtool. CI fails if the generated files are stale.
- Recording rules per SLI and window: `slo:sli_error:ratio_rate<W>`, `slo:burn_rate:rate<W>`
  (= error ratio / (1 − objective)), `slo:objective:ratio`, `slo:error_budget_remaining:ratio`
  (over the 30-day budget window; 1 h in demo mode) and `slo:cost_per_1k_requests:rate<W>`.
- Alerts follow the Google SRE Workbook multi-window, multi-burn-rate pattern: page on
  1h/5m at 14.4× or 6h/30m at 6×, ticket on 1d/2h at 3× or 3d/6h at 1×. **Exception, and
  the reason it is in the spec rather than hidden in code:** a burn rate above
  1/(1 − objective) is impossible (it would need more than 100% bad events). For the 90%
  objectives (throughput, quality) the budget is 10%, so 14.4× cannot happen; those two SLOs
  page at 8×/5× instead. 99% availability and 95% responsiveness keep the Workbook factors.
- Thresholds are histogram bucket boundaries (`le="2"`, `le="3"`), so changing a threshold
  means picking another bucket or changing the gateway's buckets, which the spec says.
- Demo windows: 5m→1m, 30m→2m, 1h→3m, 2h→3m, 6h→6m, 1d→10m, 3d→20m, `for: 0m`, group
  interval 15 s. A 1-minute window pages on noise; the file says "DEMO ONLY" for a reason.
- `promtool test rules` covers each SLI's page alert firing and resolving, the ticket alert,
  the cost budget, the healthy-service case and the production 1h/5m pair.
- Observed right after loading: the *production* rules fired `LLMAvailabilityBurnRatePage`
  (20×) and `LLMResponsivenessBurnRatePage` (6.7×) on the lab's own history of the last hour:
  the concurrency-2 load test (TTFT 6–30 s) and the truncated answers at `max_tokens=64`. The
  error budget went negative because the "30-day" window held one hour of data with a bad
  half hour in it. Nothing was tuned to make this happen.

## ADR-017: Quality signal from deterministic checks, run as a rotating CronJob

**Status:** Accepted (2026-10-08)

- `eval/golden.jsonl`: 40 items, each with one or more checks (`contains`, `json` + JSON
  Schema, `refuse`, `max_words`); an item passes when all its checks pass. The JSON check
  tolerates code fences and surrounding prose, because small models add both.
- The evaluator is a **CronJob every 2 minutes on a rotating 12-item slice** (a full pass
  every 4 runs, ~25–55 s per run). A full 40-item run every time would take 2–3 minutes of a
  model that serves one request at a time; the slice keeps the quality signal fresh enough for
  the demo windows (6m/2m) without starving user traffic.
- Metrics are cumulative counters that reset every run. To make `rate()` work across runs,
  the evaluator pins `service.instance.id=evaluator`, exports every 10 s, and Prometheus does
  **not** promote `k8s.pod.name` to a label (ADR-015 amended): CronJob pods would otherwise
  start a new series on every run. Traces keep every attribute.
- Evaluator requests are tagged `x-llm-slo-client: evaluator`; the gateway records it as
  `llm_slo.client` and the user-facing SLIs exclude it.
- Quality pages on the slower 6h/30m pair only (demo 6m/2m); a batch signal has nothing
  meaningful to say in a 5-minute window.
- Calibration: the first full run scored 75.5 %. All five refusal items failed (the 0.5B
  model complies with harmful requests), and six items were beyond the model or flaky at
  temperature 0 (12×12, √81, first three primes, "the letter B"). The set was recalibrated to
  what the model can do, keeping two refusal items as a visible, documented gap. The healthy
  pass ratio is 93–95 %; a few items still flip between runs because fp32 inference on CPU is
  not bit-exact. Content never leaves the evaluator unless `CAPTURE_CONTENT=true`.
