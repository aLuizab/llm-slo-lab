"""The names in app/semconv.py must match the opentelemetry-semantic-conventions package.

If this fails after a package bump, the GenAI conventions changed under us (ADR-005):
update app/semconv.py, slo/ rules and dashboards together.
"""

from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as attrs
from opentelemetry.semconv._incubating.metrics import gen_ai_metrics as metrics
from opentelemetry.semconv.attributes import error_attributes, server_attributes

from app import semconv


def test_metric_names_match_package():
    assert semconv.METRIC_CLIENT_OPERATION_DURATION == metrics.GEN_AI_CLIENT_OPERATION_DURATION
    assert semconv.METRIC_CLIENT_TOKEN_USAGE == metrics.GEN_AI_CLIENT_TOKEN_USAGE
    assert semconv.METRIC_SERVER_REQUEST_DURATION == metrics.GEN_AI_SERVER_REQUEST_DURATION
    assert semconv.METRIC_SERVER_TIME_TO_FIRST_TOKEN == metrics.GEN_AI_SERVER_TIME_TO_FIRST_TOKEN
    assert (
        semconv.METRIC_SERVER_TIME_PER_OUTPUT_TOKEN == metrics.GEN_AI_SERVER_TIME_PER_OUTPUT_TOKEN
    )


def test_attribute_names_match_package():
    assert semconv.ATTR_OPERATION_NAME == attrs.GEN_AI_OPERATION_NAME
    assert semconv.ATTR_PROVIDER_NAME == attrs.GEN_AI_PROVIDER_NAME
    assert semconv.ATTR_REQUEST_MODEL == attrs.GEN_AI_REQUEST_MODEL
    assert semconv.ATTR_RESPONSE_MODEL == attrs.GEN_AI_RESPONSE_MODEL
    assert semconv.ATTR_REQUEST_MAX_TOKENS == attrs.GEN_AI_REQUEST_MAX_TOKENS
    assert semconv.ATTR_REQUEST_TEMPERATURE == attrs.GEN_AI_REQUEST_TEMPERATURE
    assert semconv.ATTR_RESPONSE_FINISH_REASONS == attrs.GEN_AI_RESPONSE_FINISH_REASONS
    assert semconv.ATTR_USAGE_INPUT_TOKENS == attrs.GEN_AI_USAGE_INPUT_TOKENS
    assert semconv.ATTR_USAGE_OUTPUT_TOKENS == attrs.GEN_AI_USAGE_OUTPUT_TOKENS
    assert semconv.ATTR_TOKEN_TYPE == attrs.GEN_AI_TOKEN_TYPE
    assert semconv.ATTR_INPUT_MESSAGES == attrs.GEN_AI_INPUT_MESSAGES
    assert semconv.ATTR_OUTPUT_MESSAGES == attrs.GEN_AI_OUTPUT_MESSAGES
    assert semconv.ATTR_SYSTEM_INSTRUCTIONS == attrs.GEN_AI_SYSTEM_INSTRUCTIONS
    assert semconv.ATTR_ERROR_TYPE == error_attributes.ERROR_TYPE
    assert semconv.ATTR_SERVER_ADDRESS == server_attributes.SERVER_ADDRESS
    assert semconv.ATTR_SERVER_PORT == server_attributes.SERVER_PORT
    assert semconv.TOKEN_TYPE_INPUT == attrs.GenAiTokenTypeValues.INPUT.value
    assert semconv.TOKEN_TYPE_OUTPUT == attrs.GenAiTokenTypeValues.OUTPUT.value
    assert semconv.OPERATION_CHAT == attrs.GenAiOperationNameValues.CHAT.value
