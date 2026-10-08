SHELL := /bin/bash
.DEFAULT_GOAL := help

include versions.env
export

CLUSTER_NAME ?= llm-slo-lab
NAMESPACE    ?= llm

.PHONY: help tools-check

help: ## Show targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

tools-check: ## Verify local toolchain against versions.env
	@scripts/tools-check.sh
