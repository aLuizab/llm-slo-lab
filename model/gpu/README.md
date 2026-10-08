# GPU profile (documented, not required)

The lab runs on CPU by default. This directory holds the same `InferenceService` for a node
with an NVIDIA GPU, using the vLLM backend of the KServe Hugging Face runtime. The served model
name is identical to the CPU profile, so the gateway, SLO rules, dashboard and chaos toggles are
unchanged; only the SLO thresholds differ (`slo/slos.yaml` has a `gpu` profile).

## What changes vs. CPU

| | CPU (`model/cpu/`) | GPU (`model/gpu/`) |
|---|---|---|
| Backend | `--backend=huggingface` (transformers) | `--backend=vllm` |
| dtype | float32 | bfloat16 |
| Image | `kserve/huggingfaceserver:<ver>` | `kserve/huggingfaceserver:<ver>-gpu` (KServe picks it when `nvidia.com/gpu` is requested) |
| Streaming `usage` | not sent by the HF backend; gateway counts with the tokenizer | sent in the final chunk with `--enable-force-include-usage` |
| TTFT objective | ~2 s | ~500 ms |

## Requirements

- A Kubernetes node with the NVIDIA device plugin (`nvidia.com/gpu` resource) and the label
  `nvidia.com/gpu.present=true` (set by the GPU Operator / node-feature-discovery; adjust the
  `nodeSelector` if your cluster labels differ).
- The model-cache PVC populated as in the CPU profile (`make model-cache`).

## Apply

```bash
kubectl apply -f model/namespace.yaml -f model/model-cache.yaml
make model-cache
kubectl apply -f model/gpu/inferenceservice.yaml
```

## Why it was not run here

The development laptop has an RTX 4050 (6 GB) visible inside WSL2, but a kind node can only
use it with the NVIDIA Container Toolkit configured on the host container runtime, which needs
root. The profile was written from the KServe docs and the v0.21.0 source and is untested.
