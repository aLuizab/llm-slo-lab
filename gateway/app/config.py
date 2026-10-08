"""Gateway settings, all from environment variables (set via the ConfigMap in k8s/)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from app.cost import CostModel

# Histogram buckets. Tuned for CPU inference (TTFT from 0.1 s to 30 s); override with
# GW_BUCKETS_<NAME> as a comma-separated list. Documented in the README.
DEFAULT_BUCKETS = {
    "ttft": "0.1,0.25,0.5,0.75,1,1.5,2,3,5,7.5,10,15,20,30",
    "tpot": "0.01,0.025,0.05,0.1,0.15,0.2,0.3,0.4,0.5,0.75,1,2.5",
    "duration": "0.25,0.5,1,2,4,8,16,32,64,128",
    "tokens": "1,4,16,64,256,1024,4096,16384",
    "cost": "0.00001,0.00005,0.0001,0.0005,0.001,0.005,0.01,0.05,0.1",
    "token_rate": "0.5,1,2,3,4,5,7.5,10,15,20,30,50,100,200",
}


def _env_bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


def _buckets(name: str) -> list[float]:
    raw = os.environ.get(f"GW_BUCKETS_{name.upper()}", DEFAULT_BUCKETS[name])
    return [float(x) for x in raw.split(",") if x.strip()]


@dataclass
class Buckets:
    ttft: list[float] = field(default_factory=lambda: _buckets("ttft"))
    tpot: list[float] = field(default_factory=lambda: _buckets("tpot"))
    duration: list[float] = field(default_factory=lambda: _buckets("duration"))
    tokens: list[float] = field(default_factory=lambda: _buckets("tokens"))
    cost: list[float] = field(default_factory=lambda: _buckets("cost"))
    token_rate: list[float] = field(default_factory=lambda: _buckets("token_rate"))


@dataclass
class Settings:
    # Upstream base URL; the gateway appends /v1/chat/completions and /v1/models.
    upstream_url: str = "http://qwen-predictor.llm.svc/openai"
    model_name: str = "qwen2.5-0.5b-instruct"  # used when the request has no `model`
    provider_name: str = "kserve"  # gen_ai.provider.name
    service_name: str = "llm-gateway"
    tokenizer_path: str | None = None
    capture_content: bool = False
    fault_error_rate: float = 0.0
    fault_extra_latency_ms: float = 0.0
    # "broken": the last user message is replaced by an unrelated one, simulating a prompt
    # template / RAG regression. Requests still succeed and are fast; only quality breaks.
    fault_prompt_template: str = ""
    # Throttles the stream: the gateway waits this long before counting and forwarding each
    # chunk, so the user-visible tokens/s drops (break-throughput) without touching the model.
    fault_inter_token_delay_ms: float = 0.0
    system_prompt: str | None = None
    cost_model: CostModel = field(default_factory=CostModel)
    connect_timeout_s: float = 5.0
    ttft_timeout_s: float = 60.0  # read timeout until the first chunk, and between chunks
    total_timeout_s: float = 180.0  # hard deadline for a whole request
    otlp_enabled: bool = False  # true when OTEL_EXPORTER_OTLP_ENDPOINT is set
    metric_export_interval_ms: int = 10_000
    buckets: Buckets = field(default_factory=Buckets)

    @classmethod
    def from_env(cls) -> Settings:
        env = os.environ.get
        system_prompt = env("SYSTEM_PROMPT")
        prompt_file = env("SYSTEM_PROMPT_FILE")
        if not system_prompt and prompt_file and Path(prompt_file).is_file():
            system_prompt = Path(prompt_file).read_text(encoding="utf-8").strip() or None
        cost_file = env("COST_MODEL_FILE")
        cost_model = (
            CostModel.from_file(cost_file)
            if cost_file and Path(cost_file).is_file()
            else CostModel()
        )
        return cls(
            upstream_url=env("UPSTREAM_URL", cls.upstream_url).rstrip("/"),
            model_name=env("MODEL_NAME", cls.model_name),
            provider_name=env("PROVIDER_NAME", cls.provider_name),
            service_name=env("OTEL_SERVICE_NAME", cls.service_name),
            tokenizer_path=env("TOKENIZER_PATH") or None,
            capture_content=_env_bool("CAPTURE_CONTENT", False),
            fault_error_rate=_env_float("FAULT_ERROR_RATE", 0.0),
            fault_extra_latency_ms=_env_float("FAULT_EXTRA_LATENCY_MS", 0.0),
            fault_prompt_template=env("FAULT_PROMPT_TEMPLATE", "").strip().lower(),
            fault_inter_token_delay_ms=_env_float("FAULT_INTER_TOKEN_DELAY_MS", 0.0),
            system_prompt=system_prompt,
            cost_model=cost_model,
            connect_timeout_s=_env_float("UPSTREAM_CONNECT_TIMEOUT_S", 5.0),
            ttft_timeout_s=_env_float("UPSTREAM_TTFT_TIMEOUT_S", 60.0),
            total_timeout_s=_env_float("UPSTREAM_TOTAL_TIMEOUT_S", 180.0),
            otlp_enabled=bool(env("OTEL_EXPORTER_OTLP_ENDPOINT")),
            metric_export_interval_ms=int(env("OTEL_METRIC_EXPORT_INTERVAL", "10000")),
        )

    @property
    def upstream_host(self) -> tuple[str, int]:
        u = urlparse(self.upstream_url)
        return u.hostname or "", u.port or (443 if u.scheme == "https" else 80)
