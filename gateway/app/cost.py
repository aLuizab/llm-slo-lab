"""Cost per request. Two models, both emitted for every request, labelled `llm_slo.cost_model`.

THESE ARE ASSUMPTIONS, NOT REAL PRICES. The numbers live in a ConfigMap
(gateway/k8s/configmaps.yaml) and exist so the lab can show cost as an SLI-adjacent signal.

amortized   The node is paid for by the hour whether it is busy or not. A request's share is
            the node-time it occupied: (node_cost_per_hour / 3600) * duration_s, divided by
            the number of requests in flight at the time (they share the same node-seconds).
per_token   Marketplace style:
            input_tokens/1000 * input_per_1k + output_tokens/1000 * output_per_1k.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class CostModel:
    node_cost_per_hour: float = 0.40  # notional USD/h for a 4 vCPU / 16 GB node
    input_per_1k: float = 0.00015  # notional USD per 1k input tokens
    output_per_1k: float = 0.0006  # notional USD per 1k output tokens
    currency: str = "USD"

    @classmethod
    def from_file(cls, path: str | Path) -> CostModel:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        amortized = data.get("amortized", {})
        per_token = data.get("per_token", {})
        return cls(
            node_cost_per_hour=float(amortized.get("node_cost_per_hour", cls.node_cost_per_hour)),
            input_per_1k=float(per_token.get("input_per_1k", cls.input_per_1k)),
            output_per_1k=float(per_token.get("output_per_1k", cls.output_per_1k)),
            currency=str(data.get("currency", cls.currency)),
        )

    def amortized(self, duration_s: float, concurrency: int) -> float:
        share = max(1, concurrency)
        return self.node_cost_per_hour / 3600.0 * max(0.0, duration_s) / share

    def per_token(self, input_tokens: int, output_tokens: int) -> float:
        return (
            input_tokens / 1000.0 * self.input_per_1k + output_tokens / 1000.0 * self.output_per_1k
        )
