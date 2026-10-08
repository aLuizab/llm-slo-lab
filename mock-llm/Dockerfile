FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim
COPY --from=uv /uv /bin/uv
WORKDIR /app
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY mock_llm ./mock_llm
ENV PATH=/app/.venv/bin:$PATH
USER 1000
EXPOSE 8000
CMD ["uvicorn", "mock_llm.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
