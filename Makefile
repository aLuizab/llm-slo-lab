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
platform: platform-cert-manager platform-kserve ## Install every platform component

platform-cert-manager: ## cert-manager (KServe webhook certs)
	@scripts/platform.sh cert-manager

platform-kserve: ## KServe CRDs, controller (Standard mode) and serving runtimes
	@scripts/platform.sh kserve

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
