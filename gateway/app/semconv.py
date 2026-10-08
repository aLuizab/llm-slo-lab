"""Every OpenTelemetry metric, span and attribute name used by the gateway.

GenAI names follow the last *released* GenAI semantic conventions (core semconv v1.41.1, as
exposed by opentelemetry-semantic-conventions 0.66b1). The conventions moved to the
semantic-conventions-genai repository, which has not released yet and renames the inference
metrics (`gen_ai.client.inference.*`). When that lands, this is the only file to change.
See docs/decisions.md, ADR-005. A test asserts these match the Python package.

Names without a semconv equivalent live under `llm_slo.`.
"""

# --- GenAI metrics (histograms) ---------------------------------------------------------
METRIC_CLIENT_OPERATION_DURATION = "gen_ai.client.operation.duration"  # s
METRIC_CLIENT_TOKEN_USAGE = "gen_ai.client.token.usage"  # {token}, attr gen_ai.token.type
METRIC_SERVER_REQUEST_DURATION = "gen_ai.server.request.duration"  # s
METRIC_SERVER_TIME_TO_FIRST_TOKEN = "gen_ai.server.time_to_first_token"  # s
METRIC_SERVER_TIME_PER_OUTPUT_TOKEN = "gen_ai.server.time_per_output_token"  # s

# --- Lab metrics (no semconv equivalent) ------------------------------------------------
METRIC_REQUESTS = "llm_slo.requests"  # counter, {request}, attr llm_slo.outcome
METRIC_REQUESTS_IN_FLIGHT = "llm_slo.requests.in_flight"  # up-down counter, {request}
METRIC_REQUEST_COST = "llm_slo.request.cost"  # histogram, {USD}, attr llm_slo.cost_model
# histogram, unit {token}/s -> Prometheus llm_slo_output_tokens_per_second_*
METRIC_OUTPUT_TOKEN_RATE = "llm_slo.output_tokens"

# --- GenAI attributes ---------------------------------------------------------------------
ATTR_OPERATION_NAME = "gen_ai.operation.name"
ATTR_PROVIDER_NAME = "gen_ai.provider.name"
ATTR_REQUEST_MODEL = "gen_ai.request.model"
ATTR_RESPONSE_MODEL = "gen_ai.response.model"
ATTR_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
ATTR_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
ATTR_RESPONSE_FINISH_REASONS = "gen_ai.response.finish_reasons"
ATTR_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
ATTR_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
ATTR_TOKEN_TYPE = "gen_ai.token.type"  # input | output
ATTR_INPUT_MESSAGES = "gen_ai.input.messages"  # opt-in, CAPTURE_CONTENT=true
ATTR_OUTPUT_MESSAGES = "gen_ai.output.messages"  # opt-in
ATTR_SYSTEM_INSTRUCTIONS = "gen_ai.system_instructions"  # opt-in

# --- General attributes ------------------------------------------------------------------
ATTR_ERROR_TYPE = "error.type"
ATTR_SERVER_ADDRESS = "server.address"
ATTR_SERVER_PORT = "server.port"

# --- Lab attributes ------------------------------------------------------------------------
# success | upstream_error | timeout | empty | truncated | injected_error
ATTR_OUTCOME = "llm_slo.outcome"
ATTR_STREAM = "llm_slo.stream"  # bool
# who sent the request: "user" (default) or the value of the x-llm-slo-client header, e.g.
# "evaluator" or "loadgen". SLIs exclude synthetic evaluator traffic.
ATTR_CLIENT = "llm_slo.client"
ATTR_TOKEN_SOURCE = "llm_slo.token_source"  # upstream | tokenizer | estimate
ATTR_COST_MODEL = "llm_slo.cost_model"  # amortized | per_token

OPERATION_CHAT = "chat"
TOKEN_TYPE_INPUT = "input"
TOKEN_TYPE_OUTPUT = "output"

OUTCOME_SUCCESS = "success"
OUTCOME_UPSTREAM_ERROR = "upstream_error"
OUTCOME_TIMEOUT = "timeout"
OUTCOME_EMPTY = "empty"
OUTCOME_TRUNCATED = "truncated"
OUTCOME_INJECTED_ERROR = "injected_error"
BAD_OUTCOMES = frozenset(
    {
        OUTCOME_UPSTREAM_ERROR,
        OUTCOME_TIMEOUT,
        OUTCOME_EMPTY,
        OUTCOME_TRUNCATED,
        OUTCOME_INJECTED_ERROR,
    }
)
