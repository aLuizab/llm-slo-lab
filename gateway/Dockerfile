FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

# Tokenizer files for offline token counting (ADR-006). Pinned to a model revision.
FROM python:3.12-slim AS tokenizer
ARG MODEL_ID=Qwen/Qwen2.5-0.5B-Instruct
ARG MODEL_REVISION=main
ARG HF_HUB_VERSION=2.1.1
RUN pip install --no-cache-dir "huggingface_hub==${HF_HUB_VERSION}" \
 && python -c "from huggingface_hub import hf_hub_download; import os; \
[hf_hub_download(os.environ['MODEL_ID'], f, revision=os.environ['MODEL_REVISION'], local_dir='/tokenizer') \
 for f in ('tokenizer.json', 'tokenizer_config.json')]" \
 && rm -rf /tokenizer/.cache

FROM python:3.12-slim
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY app ./app
COPY --from=tokenizer /tokenizer /opt/tokenizer
ENV PATH=/app/.venv/bin:$PATH \
    TOKENIZER_PATH=/opt/tokenizer/tokenizer.json \
    TOKENIZERS_PARALLELISM=false
USER 1000
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
