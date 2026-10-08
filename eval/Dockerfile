FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY evaluator ./evaluator
COPY golden.jsonl ./golden.jsonl
ENV PATH=/app/.venv/bin:$PATH GOLDEN_PATH=/app/golden.jsonl
USER 1000
CMD ["python", "-m", "evaluator.main"]
