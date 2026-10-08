#!/usr/bin/env bash
# Chaos toggles for the demo.
# Usage: scripts/chaos.sh break-errors|break-latency|break-throughput|break-cpu|break-quality|heal|status
set -euo pipefail
cd "$(dirname "$0")/.."
NS=llm
toggle="${1:?toggle}"

patch_env() { # key=value ...
  local json="{"; local sep=""
  for kv in "$@"; do json+="$sep\"${kv%%=*}\":\"${kv#*=}\""; sep=","; done
  json+="}"
  kubectl -n $NS patch cm llm-gateway-env --type merge -p "{\"data\":$json}" >/dev/null
}
restart_gateway() {
  kubectl -n $NS rollout restart deploy/llm-gateway >/dev/null
  kubectl -n $NS rollout status deploy/llm-gateway --timeout=120s | tail -1
}
set_prompt() { # file
  kubectl -n $NS create cm llm-gateway-system-prompt --from-file=system-prompt.txt="$1" \
    --dry-run=client -o yaml | kubectl apply -f - >/dev/null
}
eval_now() { # [wait]
  kubectl -n $NS delete job eval-now --ignore-not-found --wait=true >/dev/null
  kubectl -n $NS create job eval-now --from=cronjob/evaluator >/dev/null
  echo "evaluator run started (job/eval-now)"
  if [ "${1:-}" = wait ]; then
    kubectl -n $NS wait --for=condition=complete job/eval-now --timeout=300s >/dev/null 2>&1 || true
    echo "  $(kubectl -n $NS logs job/eval-now 2>/dev/null | tail -1)"
  fi
}
stamp() { echo "$(date +%H:%M:%S)  $*"; }

case "$toggle" in
  break-errors)
    stamp "BREAK availability: FAULT_ERROR_RATE=0.5"
    patch_env FAULT_ERROR_RATE=0.5; restart_gateway ;;
  break-latency)
    stamp "BREAK responsiveness: FAULT_EXTRA_LATENCY_MS=3000 (TTFT > 2 s)"
    patch_env FAULT_EXTRA_LATENCY_MS=3000; restart_gateway ;;
  break-throughput)
    stamp "BREAK throughput: gateway throttles the stream (FAULT_INTER_TOKEN_DELAY_MS=400, ~2 tok/s)"
    patch_env FAULT_INTER_TOKEN_DELAY_MS=400; restart_gateway ;;
  break-cpu)
    stamp "BREAK cpu: predictor CPU 3 -> 1 core (requests and limits; model reloads, ~1.5 min)"
    # requests must not exceed limits or KServe's Deployment update is rejected and nothing rolls
    kubectl -n $NS patch isvc qwen --type merge \
      -p '{"spec":{"predictor":{"model":{"resources":{"requests":{"cpu":"1"},"limits":{"cpu":"1"}},"env":[{"name":"OMP_NUM_THREADS","value":"1"},{"name":"HF_HUB_OFFLINE","value":"1"}]}}}}' >/dev/null
    sleep 5; kubectl -n $NS rollout status deploy/qwen-predictor --timeout=600s | tail -1
    stamp "predictor rolled to 1 core: $(kubectl -n $NS get pods -l app=isvc.qwen-predictor -o jsonpath='{.items[*].spec.containers[0].resources.limits.cpu}')" ;;
  break-quality)
    stamp "BREAK quality: broken prompt template (user question dropped) + degraded system prompt + two evaluator runs"
    set_prompt chaos/system-prompt-bad.txt; patch_env FAULT_PROMPT_TEMPLATE=broken; restart_gateway
    eval_now wait; eval_now ;;
  heal)
    stamp "HEAL: faults off, original prompt, original predictor resources"
    patch_env FAULT_ERROR_RATE=0 FAULT_EXTRA_LATENCY_MS=0 FAULT_PROMPT_TEMPLATE= FAULT_INTER_TOKEN_DELAY_MS=0
    kubectl apply -f gateway/k8s/configmaps.yaml >/dev/null
    kubectl apply -f model/cpu/inferenceservice.yaml >/dev/null
    restart_gateway
    sleep 5; kubectl -n $NS rollout status deploy/qwen-predictor --timeout=600s | tail -1
    eval_now wait; eval_now ;;
  status)
    echo "gateway faults: $(kubectl -n $NS get cm llm-gateway-env -o jsonpath='FAULT_ERROR_RATE={.data.FAULT_ERROR_RATE} FAULT_EXTRA_LATENCY_MS={.data.FAULT_EXTRA_LATENCY_MS} FAULT_PROMPT_TEMPLATE={.data.FAULT_PROMPT_TEMPLATE} FAULT_INTER_TOKEN_DELAY_MS={.data.FAULT_INTER_TOKEN_DELAY_MS}')"
    echo "system prompt:  $(kubectl -n $NS get cm llm-gateway-system-prompt -o jsonpath='{.data.system-prompt\.txt}' | head -c 60)..."
    echo "predictor cpu limit: $(kubectl -n $NS get isvc qwen -o jsonpath='{.spec.predictor.model.resources.limits.cpu}')"
    scripts/slo-status.sh ;;
  *) echo "unknown toggle: $toggle" >&2; exit 2 ;;
esac
