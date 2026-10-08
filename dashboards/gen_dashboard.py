#!/usr/bin/env python3
"""Generate dashboards/llm-slos.json (Grafana) and dashboards/llm-slos-configmap.yaml.

Hand-maintained Grafana JSON is write-only; this keeps every panel as a few lines of Python.
Run `make dashboard-gen` after editing, `make dashboard` to apply. The ConfigMap carries the
label grafana_dashboard=1 so the kube-prometheus-stack sidecar provisions it automatically.
"""

from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
DS = {"type": "prometheus", "uid": "prometheus"}
UID = "llm-slos"
TITLE = "LLM SLOs"

_ids = iter(range(1, 1000))


def target(expr: str, legend: str = "", fmt: str = "time_series", instant: bool = False) -> dict:
    t = {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend or "__auto",
        "refId": chr(65 + next(_ids) % 26),
        "format": fmt,
    }
    if instant:
        t["instant"] = True
        t["range"] = False
    return t


def panel(
    kind: str, title: str, targets: list[dict], w: int, h: int, unit: str = "", **extra
) -> dict:
    p = {
        "id": next(_ids),
        "type": kind,
        "title": title,
        "datasource": DS,
        "targets": targets,
        "gridPos": {"w": w, "h": h},  # x,y filled by layout()
        "fieldConfig": {"defaults": {"unit": unit} if unit else {}, "overrides": []},
        "options": {},
    }
    for k, v in extra.items():
        if k in ("options", "fieldConfig"):
            _deep_merge(p[k], v)
        else:
            p[k] = v
    return p


def _deep_merge(dst: dict, src: dict) -> None:
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict):
            _deep_merge(dst[k], v)
        else:
            dst[k] = v


def thresholds(*steps) -> dict:
    """steps: (color, value) pairs; first value None."""
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for c, v in steps]}


def row(title: str) -> dict:
    return {
        "id": next(_ids),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"w": 24, "h": 1},
        "panels": [],
    }


def layout(panels: list[dict]) -> list[dict]:
    """Place panels left-to-right, wrapping at 24 columns; rows force a new line."""
    x = y = 0
    line_h = 0
    for p in panels:
        w, h = p["gridPos"]["w"], p["gridPos"]["h"]
        if p["type"] == "row" or x + w > 24:
            x = 0
            y += line_h
            line_h = 0
        p["gridPos"].update({"x": x, "y": y})
        x += w
        line_h = max(line_h, h)
        if p["type"] == "row":
            x = 0
            y += 1
            line_h = 0
    return panels


# ----------------------------------------------------------------------------- panels
def sli_stat(sli: str, title: str) -> dict:
    return panel(
        "stat",
        title,
        [
            target(f'1 - slo:sli_error:ratio_rate1h{{sli="{sli}"}}', "SLI (1h)", instant=True),
            target(f'slo:objective:ratio{{sli="{sli}"}}', "objective", instant=True),
        ],
        w=6,
        h=5,
        unit="percentunit",
        options={
            "reduceOptions": {"calcs": ["lastNotNull"]},
            "colorMode": "value",
            "graphMode": "none",
            "textMode": "value_and_name",
        },
        fieldConfig={
            "defaults": {
                "decimals": 1,
                "thresholds": thresholds(("red", None), ("green", 0.0)),
                "color": {"mode": "thresholds"},
            },
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "objective"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": "text"}}
                    ],
                }
            ],
        },
    )


def build() -> dict:
    panels: list[dict] = []

    panels.append(row("SLIs vs objectives (1h window, production rules)"))
    panels += [
        sli_stat("availability", "Availability: good responses"),
        sli_stat("responsiveness", "Responsiveness: TTFT < 2 s"),
        sli_stat("throughput", "Throughput: > 3 tokens/s"),
        sli_stat("quality", "Quality: eval checks passing"),
    ]

    panels.append(row("Error budget and burn rate"))
    panels.append(
        panel(
            "bargauge",
            "Error budget remaining (30 d)",
            [target("slo:error_budget_remaining:ratio", "{{sli}}", instant=True)],
            w=8,
            h=7,
            unit="percentunit",
            options={
                "orientation": "horizontal",
                "displayMode": "lcd",
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "minVizHeight": 16,
            },
            fieldConfig={
                "defaults": {
                    "min": -1,
                    "max": 1,
                    "thresholds": thresholds(("red", None), ("orange", 0.25), ("green", 0.5)),
                }
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Burn rate (1h window; page at 14.4x / 8x)",
            [target("slo:burn_rate:rate1h", "{{sli}}")],
            w=8,
            h=7,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10},
                    "thresholds": thresholds(("transparent", None), ("red", 14.4)),
                    "custom.thresholdsStyle": {"mode": "line"},
                }
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Burn rate, demo windows (3m; page at 14.4x / 8x)",
            [target("slodemo:burn_rate:rate3m", "{{sli}}")],
            w=8,
            h=7,
            unit="none",
            fieldConfig={
                "defaults": {"custom": {"drawStyle": "line", "lineWidth": 2, "fillOpacity": 10}}
            },
        )
    )

    panels.append(row("Latency and throughput"))
    panels.append(
        panel(
            "heatmap",
            "Time to first token (heatmap)",
            [
                target(
                    'sum by (le) (rate(gen_ai_server_time_to_first_token_seconds_bucket{llm_slo_client!="evaluator"}[1m]))',
                    "{{le}}",
                    fmt="heatmap",
                )
            ],
            w=12,
            h=8,
            options={
                "calculate": False,
                "yAxis": {"unit": "s", "decimals": 2},
                "color": {"scheme": "Spectral", "reverse": True},
                "cellGap": 1,
                "legend": {"show": True},
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "TTFT p50 / p95 (1m) vs 2 s objective",
            [
                target(
                    'histogram_quantile(0.5, sum by (le) (rate(gen_ai_server_time_to_first_token_seconds_bucket{llm_slo_client!="evaluator"}[1m])))',
                    "p50",
                ),
                target(
                    'histogram_quantile(0.95, sum by (le) (rate(gen_ai_server_time_to_first_token_seconds_bucket{llm_slo_client!="evaluator"}[1m])))',
                    "p95",
                ),
            ],
            w=6,
            h=8,
            unit="s",
            fieldConfig={
                "defaults": {
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 5,
                        "thresholdsStyle": {"mode": "line"},
                    },
                    "thresholds": thresholds(("transparent", None), ("red", 2)),
                }
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Output tokens/s per request, p50 / p95 (1m) vs 3 tok/s",
            [
                target(
                    'histogram_quantile(0.5, sum by (le) (rate(llm_slo_output_tokens_per_second_bucket{llm_slo_client!="evaluator"}[1m])))',
                    "p50",
                ),
                target(
                    'histogram_quantile(0.95, sum by (le) (rate(llm_slo_output_tokens_per_second_bucket{llm_slo_client!="evaluator"}[1m])))',
                    "p95",
                ),
            ],
            w=6,
            h=8,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {
                        "lineWidth": 2,
                        "fillOpacity": 5,
                        "thresholdsStyle": {"mode": "line"},
                    },
                    "thresholds": thresholds(("red", None), ("transparent", 3)),
                }
            },
        )
    )

    panels.append(row("Traffic, cost and quality"))
    panels.append(
        panel(
            "timeseries",
            "Requests/min by outcome",
            [
                target(
                    "sum by (llm_slo_outcome) (rate(llm_slo_requests_total[1m])) * 60",
                    "{{llm_slo_outcome}}",
                )
            ],
            w=8,
            h=7,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {
                        "drawStyle": "bars",
                        "fillOpacity": 60,
                        "stacking": {"mode": "normal"},
                    }
                }
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Cost per request (notional USD, assumptions)",
            [
                target(
                    "sum by (llm_slo_cost_model) (rate(llm_slo_request_cost_sum[5m])) / sum by (llm_slo_cost_model) (rate(llm_slo_request_cost_count[5m]))",
                    "{{llm_slo_cost_model}}",
                )
            ],
            w=8,
            h=7,
            unit="currencyUSD",
            fieldConfig={"defaults": {"decimals": 5, "custom": {"lineWidth": 2}}},
        )
    )
    panels.append(
        panel(
            "stat",
            "Cost per 1k requests (1h, amortized) vs budget 1.5",
            [
                target(
                    'slo:cost_per_1k_requests:rate1h{llm_slo_cost_model="amortized"}',
                    "per 1k",
                    instant=True,
                )
            ],
            w=4,
            h=7,
            unit="currencyUSD",
            options={
                "reduceOptions": {"calcs": ["lastNotNull"]},
                "colorMode": "value",
                "graphMode": "area",
            },
            fieldConfig={
                "defaults": {"decimals": 2, "thresholds": thresholds(("green", None), ("red", 1.5))}
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Eval pass ratio (checks, 10m) and per run",
            [
                target(
                    'sum(rate(llm_slo_eval_checks_total{llm_slo_eval_result="pass"}[10m])) / sum(rate(llm_slo_eval_checks_total[10m]))',
                    "checks passing (10m)",
                ),
                target("llm_slo_eval_pass_ratio", "last run"),
            ],
            w=4,
            h=7,
            unit="percentunit",
            fieldConfig={
                "defaults": {
                    "min": 0,
                    "max": 1,
                    "custom": {"lineWidth": 2},
                    "thresholds": thresholds(("red", None), ("green", 0.9)),
                    "custom.thresholdsStyle": {"mode": "line"},
                }
            },
        )
    )

    panels.append(row("Autoscaling (KEDA on in-flight requests) and alerts"))
    panels.append(
        panel(
            "timeseries",
            "Predictor replicas vs in-flight requests",
            [
                target("sum(llm_slo_requests_in_flight)", "in-flight requests"),
                target(
                    'kube_deployment_status_replicas_ready{namespace="llm", deployment="qwen-predictor"}',
                    "ready replicas",
                ),
                target(
                    'kube_deployment_spec_replicas{namespace="llm", deployment="qwen-predictor"}',
                    "desired replicas",
                ),
            ],
            w=12,
            h=8,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {"lineWidth": 2, "fillOpacity": 10, "lineInterpolation": "stepAfter"},
                    "decimals": 0,
                },
                "overrides": [
                    {
                        "matcher": {"id": "byName", "options": "in-flight requests"},
                        "properties": [
                            {"id": "custom.lineInterpolation", "value": "linear"},
                            {"id": "custom.fillOpacity", "value": 25},
                        ],
                    }
                ],
            },
        )
    )
    panels.append(
        panel(
            "timeseries",
            "Predictor CPU (cores) by pod",
            [
                target(
                    'sum by (pod) (rate(container_cpu_usage_seconds_total{namespace="llm", pod=~"qwen-predictor.*", container="kserve-container"}[1m]))',
                    "{{pod}}",
                )
            ],
            w=6,
            h=8,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {"lineWidth": 1, "fillOpacity": 15, "stacking": {"mode": "normal"}}
                }
            },
        )
    )
    # Prometheus' own ALERTS series rather than Grafana's alert-list widget: the widget only
    # lists Grafana-managed alerts, and these rules live in Prometheus.
    panels.append(
        panel(
            "timeseries",
            "Firing alerts (LLM*, from Prometheus ALERTS)",
            [
                target(
                    'max by (alertname) (ALERTS{alertstate="firing", alertname=~"LLM.*"})',
                    "{{alertname}}",
                )
            ],
            w=6,
            h=8,
            unit="none",
            fieldConfig={
                "defaults": {
                    "custom": {
                        "drawStyle": "line",
                        "lineInterpolation": "stepAfter",
                        "lineWidth": 1,
                        "fillOpacity": 40,
                        "stacking": {"mode": "normal"},
                    },
                    "min": 0,
                }
            },
            options={
                "legend": {"displayMode": "list", "placement": "bottom", "calcs": []},
                "tooltip": {"mode": "multi"},
            },
        )
    )

    return {
        "uid": UID,
        "title": TITLE,
        "tags": ["llm", "slo", "kubecon"],
        "timezone": "browser",
        "editable": True,
        "graphTooltip": 1,
        "refresh": "15s",
        "time": {"from": "now-30m", "to": "now"},
        "schemaVersion": 39,
        "version": 1,
        "templating": {"list": []},
        "annotations": {"list": []},
        "panels": layout(panels),
    }


def main() -> None:
    dashboard = build()
    (HERE / "llm-slos.json").write_text(json.dumps(dashboard, indent=2) + "\n", encoding="utf-8")
    cm = (
        "---\n"
        "# GENERATED by dashboards/gen_dashboard.py. Do not edit; run `make dashboard-gen`.\n"
        "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: llm-slos-dashboard\n  namespace: llm\n"
        '  labels:\n    grafana_dashboard: "1"\n  annotations:\n    grafana_folder: LLM SLOs\n'
        "data:\n  llm-slos.json: |\n"
        + "".join("    " + line + "\n" for line in json.dumps(dashboard, indent=2).splitlines())
    )
    (HERE / "llm-slos-configmap.yaml").write_text(cm, encoding="utf-8")
    print(f"wrote dashboards/llm-slos.json ({len(dashboard['panels'])} panels) and the ConfigMap")


if __name__ == "__main__":
    main()
