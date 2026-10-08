# Contributing

This is a personal lab, but issues and pull requests are welcome, especially corrections to
how KServe, the OpenTelemetry GenAI conventions or KEDA are used, since all three move fast.

- Open an issue first for anything bigger than a typo so we can agree on the direction.
- Use Conventional Commits (`feat:`, `fix:`, `docs:`, `chore:`, ...).
- Run `make lint test` before pushing. CI runs the same checks plus an end-to-end job on kind
  with `mock-llm`.
- Pin versions in `versions.env` and explain non-obvious choices in `docs/decisions.md`.
- Keep it vendor-neutral and key-free: no SaaS, no paid APIs.
- Keep it private by default: nothing may log prompt or response content unless
  `CAPTURE_CONTENT=true`.

By participating you agree to the [Code of Conduct](CODE_OF_CONDUCT.md).
