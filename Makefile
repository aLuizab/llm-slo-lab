SHELL := /bin/bash
.DEFAULT_GOAL := help

include versions.env
export

CLUSTER_NAME     ?= llm-slo-lab
NAMESPACE        ?= llm
HOST_MODEL_CACHE ?= $(HOME)/.cache/llm-slo-lab/models
MODEL_DIRNAME    ?= qwen2.5-0.5b-instruct

.PHONY: help tools-check cluster cluster-down kind-load-hf platform platform-cert-manager platform-kserve \
        model-cache model model-wait smoke-model

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

tools-check: ## Verify local toolchain against versions.env
	@scripts/tools-check.sh

# ---------------------------------------------------------------- cluster
cluster: ## Create the kind cluster (model cache mounted from $(HOST_MODEL_CACHE))
	@mkdir -p "$(HOST_MODEL_CACHE)"
	@sed 's#$${HOST_MODEL_CACHE}#$(HOST_MODEL_CACHE)#' cluster/kind-config.yaml | kind create cluster --config - --wait 120s
	@kubectl get nodes

cluster-down: ## Delete the kind cluster (model cache on the host is kept)
	kind delete cluster --name $(CLUSTER_NAME)

kind-load-hf: ## Pull the KServe HF runtime image and load it into kind (4 GB, do it once)
	docker pull $(KSERVE_HF_IMAGE)
	kind load docker-image $(KSERVE_HF_IMAGE) --name $(CLUSTER_NAME)

# ---------------------------------------------------------------- platform
.PHONY: platform-monitoring platform-otel platform-jaeger platform-keda ui loadgen verify-telemetry

platform: platform-cert-manager platform-kserve platform-monitoring platform-jaeger platform-otel platform-keda ## Install every platform component

platform-keda: ## KEDA (scales the predictor on in-flight requests)
	@scripts/platform.sh keda

platform-cert-manager: ## cert-manager (KServe webhook certs)
	@scripts/platform.sh cert-manager

platform-kserve: ## KServe CRDs, controller (Standard mode) and serving runtimes
	@scripts/platform.sh kserve

platform-monitoring: ## kube-prometheus-stack (Prometheus w/ OTLP receiver, Alertmanager, Grafana)
	@scripts/platform.sh kube-prometheus-stack

platform-jaeger: ## Jaeger v2 all-in-one
	@scripts/platform.sh jaeger

platform-otel: ## OpenTelemetry Collector (OTLP in; Prometheus + Jaeger out)
	@scripts/platform.sh otel-collector

ui: ## Port-forward Grafana, Prometheus, Alertmanager, Jaeger and print URLs
	@scripts/ui.sh

CONCURRENCY ?= 2
DURATION    ?= 60
RAMP        ?=
loadgen: ## Run load through the gateway (CONCURRENCY, DURATION or RAMP=1:60,4:120,1:60)
	cd loadgen && uv run --frozen python loadgen.py --url http://127.0.0.1:30080 \
	  $(if $(RAMP),--ramp $(RAMP),--concurrency $(CONCURRENCY) --duration $(DURATION))

verify-telemetry: ## Print PromQL results for every SLI signal and one Jaeger trace
	@scripts/verify-telemetry.sh

# ---------------------------------------------------------------- autoscaling
.PHONY: autoscaling-mock keda-status keda-ramp

autoscaling-mock: ## KEDA ScaledObject for mock-llm (CI / offline demo)
	kubectl apply -f autoscaling/scaledobject-mock-llm.yaml

keda-status: ## ScaledObjects, HPAs and current replicas
	kubectl -n $(NAMESPACE) get scaledobject,hpa 2>/dev/null || true
	kubectl -n $(NAMESPACE) get deploy -o custom-columns=NAME:.metadata.name,DESIRED:.spec.replicas,READY:.status.readyReplicas

keda-ramp: ## Ramp load and record replicas vs in-flight to docs/evidence/keda-ramp.log
	@scripts/keda-ramp.sh $(RAMP)

# ---------------------------------------------------------------- chaos (see chaos/README.md)
.PHONY: break-errors break-latency break-throughput break-quality heal chaos-status

break-errors: ## Availability: 50% of requests get a 503 (FAULT_ERROR_RATE=0.5)
	@scripts/chaos.sh break-errors
break-latency: ## Responsiveness: +3 s before every request (FAULT_EXTRA_LATENCY_MS=3000)
	@scripts/chaos.sh break-latency
break-throughput: ## Throughput: predictor CPU limit 3 -> 1 core (model reloads)
	@scripts/chaos.sh break-throughput
break-quality: ## Quality: poetry-only system prompt + immediate evaluator run
	@scripts/chaos.sh break-quality
heal: ## Restore everything (faults off, prompt, predictor resources) + evaluator run
	@scripts/chaos.sh heal
chaos-status: ## Current fault settings, burn rates and firing alerts
	@scripts/chaos.sh status

# ---------------------------------------------------------------- dashboard
.PHONY: dashboard-gen dashboard screenshot

dashboard-gen: ## Generate dashboards/llm-slos.json + ConfigMap from gen_dashboard.py
	cd dashboards && uv run --frozen python gen_dashboard.py

dashboard: ## Apply the Grafana dashboard ConfigMap (sidecar provisions it)
	kubectl apply -f dashboards/llm-slos-configmap.yaml

OUT   ?= docs/evidence/dashboard.png
PANEL ?=
screenshot: ## Screenshot the dashboard (OUT=file.png PANEL=id FROM=now-30m); needs make ui
	@scripts/screenshot.sh $(OUT) "$(PANEL)" "$(or $(FROM),now-30m)" "$(or $(TO),now)"

# ---------------------------------------------------------------- SLOs
.PHONY: slo-gen slo-check slo slo-demo slo-status

slo-gen: ## Generate slo/rules*.yaml and slo/prometheus/*.yaml from slo/slos.yaml
	cd slo && uv run --frozen python gen_rules.py

slo-check: ## promtool check + unit tests, and fail if generated rules are stale
	cd slo && uv run --frozen python gen_rules.py --check
	promtool check rules slo/prometheus/rules.yaml slo/prometheus/rules-demo.yaml
	promtool test rules slo/tests/*.test.yaml

slo: ## Apply the production PrometheusRule (30-day windows)
	kubectl apply -f slo/rules.yaml

slo-demo: ## Apply the DEMO PrometheusRule (compressed windows; alerts fire within minutes)
	kubectl apply -f slo/rules-demo.yaml

slo-status: ## Show rule groups and active alerts from Prometheus
	@scripts/slo-status.sh

# ---------------------------------------------------------------- model
model-cache: ## Create the model-cache PV/PVC and download the weights into it (idempotent)
	kubectl apply -f model/namespace.yaml -f model/model-cache.yaml
	@kubectl -n $(NAMESPACE) delete job model-download --ignore-not-found >/dev/null
	@sed -e 's#$${HF_HUB_VERSION}#$(HF_HUB_VERSION)#g' -e 's#$${PYTHON_VERSION}#$(PYTHON_VERSION)#g' \
	     -e 's#$${MODEL_ID}#$(MODEL_ID)#g' -e 's#$${MODEL_DIRNAME}#$(MODEL_DIRNAME)#g' \
	     model/download-job.yaml | kubectl apply -f -
	kubectl -n $(NAMESPACE) wait --for=condition=complete job/model-download --timeout=20m
	kubectl -n $(NAMESPACE) logs job/model-download | tail -20

model: ## Deploy the CPU InferenceService
	kubectl apply -f model/namespace.yaml -f model/cpu/inferenceservice.yaml
	$(MAKE) --no-print-directory model-wait

model-wait: ## Wait for the InferenceService to be Ready
	kubectl -n $(NAMESPACE) wait --for=condition=Ready inferenceservice/qwen --timeout=15m
	kubectl -n $(NAMESPACE) get inferenceservice,pods -o wide

smoke-model: ## Stream a chat completion straight from KServe
	@scripts/smoke-model.sh

# ---------------------------------------------------------------- services
.PHONY: images image-gateway image-mock gateway mock use-model use-mock smoke test lint

image-gateway: ## Build the llm-gateway image and load it into kind
	docker build -t llm-slo-lab/llm-gateway:dev \
	  --build-arg MODEL_ID=$(MODEL_ID) --build-arg MODEL_REVISION=$(MODEL_REVISION) \
	  --build-arg HF_HUB_VERSION=$(HF_HUB_VERSION) gateway
	kind load docker-image llm-slo-lab/llm-gateway:dev --name $(CLUSTER_NAME)

image-mock: ## Build the mock-llm image and load it into kind
	docker build -t llm-slo-lab/mock-llm:dev mock-llm
	kind load docker-image llm-slo-lab/mock-llm:dev --name $(CLUSTER_NAME)

image-eval: ## Build the evaluator image and load it into kind
	docker build -t llm-slo-lab/evaluator:dev eval
	kind load docker-image llm-slo-lab/evaluator:dev --name $(CLUSTER_NAME)

images: image-gateway image-mock image-eval ## Build and load all images

.PHONY: image-eval eval eval-run eval-local
eval: ## Deploy the evaluator CronJob (quality signal)
	kubectl apply -f eval/k8s/cronjob.yaml

eval-run: ## Start one evaluator run now and show its result
	@kubectl -n $(NAMESPACE) delete job eval-now --ignore-not-found >/dev/null
	kubectl -n $(NAMESPACE) create job eval-now --from=cronjob/evaluator
	kubectl -n $(NAMESPACE) wait --for=condition=complete job/eval-now --timeout=300s
	kubectl -n $(NAMESPACE) logs job/eval-now

eval-local: ## Run the whole golden set from the host against localhost:30080 (no OTLP)
	cd eval && uv run --frozen python -m evaluator.main --url http://127.0.0.1:30080 --verbose

gateway: ## Deploy (or redeploy) the llm-gateway
	kubectl apply -f model/namespace.yaml -f gateway/k8s/
	kubectl -n $(NAMESPACE) rollout restart deploy/llm-gateway
	kubectl -n $(NAMESPACE) rollout status deploy/llm-gateway --timeout=120s

mock: ## Deploy (or redeploy) mock-llm
	kubectl apply -f model/namespace.yaml -f mock-llm/k8s/
	kubectl -n $(NAMESPACE) rollout restart deploy/mock-llm
	kubectl -n $(NAMESPACE) rollout status deploy/mock-llm --timeout=120s

use-model: ## Point the gateway at the KServe model
	kubectl -n $(NAMESPACE) patch cm llm-gateway-env --type merge \
	  -p '{"data":{"UPSTREAM_URL":"http://qwen-predictor.llm.svc/openai","MODEL_NAME":"qwen2.5-0.5b-instruct","PROVIDER_NAME":"kserve"}}'
	kubectl -n $(NAMESPACE) rollout restart deploy/llm-gateway
	kubectl -n $(NAMESPACE) rollout status deploy/llm-gateway --timeout=120s

use-mock: ## Point the gateway at mock-llm (CI / offline demo)
	kubectl -n $(NAMESPACE) patch cm llm-gateway-env --type merge \
	  -p '{"data":{"UPSTREAM_URL":"http://mock-llm.llm.svc","MODEL_NAME":"mock-llm","PROVIDER_NAME":"mock"}}'
	kubectl -n $(NAMESPACE) rollout restart deploy/llm-gateway
	kubectl -n $(NAMESPACE) rollout status deploy/llm-gateway --timeout=120s

smoke: ## Stream a chat completion through the gateway (localhost:30080)
	@scripts/smoke.sh

PY_PROJECTS := gateway mock-llm eval loadgen slo

test: ## Unit tests (gateway, mock-llm, evaluator)
	cd mock-llm && uv run --frozen pytest -q
	cd gateway && uv run --frozen pytest -q
	cd eval && uv run --frozen pytest -q

lint: ## ruff + yamllint + kubeconform
	@for p in $(PY_PROJECTS); do (cd $$p && uv run --frozen ruff check . && uv run --frozen ruff format --check .) || exit 1; done
	yamllint -s .
	kubeconform -strict -summary -ignore-missing-schemas model/ cluster/ gateway/k8s/ mock-llm/k8s/ eval/k8s/
