"""capacity-forge -- Flask API + static UI.

Local-first capacity and SLO planning workbench. No API keys, no network calls.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict

from flask import Flask, jsonify, render_template, request, Response

import db as store
import engine
import exporters
import samples as sample_lib

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, static_folder="static", template_folder="templates")
store.init_db()


@app.after_request
def _no_store(resp):
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify({"ok": True, "engine": "capacity-forge", "version": engine.ENGINE_VERSION})


@app.route("/api/fields")
def fields():
    """Field metadata so the UI can build the override editor without hardcoding."""
    meta = {
        "peak_rps": ("Peak requests per second", "number"),
        "avg_rps": ("Average requests per second", "number"),
        "growth_pct_12mo": ("Traffic growth over 12 months (%)", "number"),
        "cpu_ms_per_request": ("CPU ms per request", "number"),
        "ram_mb_per_request": ("Memory MB per in-flight request", "number"),
        "max_concurrency_per_instance": ("Max concurrency per instance", "number"),
        "instance_vcpu": ("vCPU per instance", "number"),
        "instance_ram_gb": ("RAM GB per instance", "number"),
        "instances_min": ("Min replicas", "number"),
        "instances_current": ("Running replicas", "number"),
        "instances_max": ("Max replicas", "number"),
        "startup_seconds": ("Startup seconds (cold start)", "number"),
        "readiness_seconds": ("Readiness probe seconds", "number"),
        "spike_window_minutes": ("Spike ramp window (minutes)", "number"),
        "p50_latency_ms": ("Observed p50 latency (ms)", "number"),
        "p99_latency_ms": ("Observed p99 latency (ms)", "number"),
        "latency_budget_ms": ("Latency budget / SLO (ms)", "number"),
        "downstream_latency_ms": ("Total downstream latency (ms)", "number"),
        "downstream_calls": ("Downstream calls per request", "number"),
        "db_reads_per_request": ("DB reads per request", "number"),
        "db_writes_per_request": ("DB writes per request", "number"),
        "db_query_ms": ("DB query time (ms)", "number"),
        "cache_hit_ratio": ("Cache hit ratio (0-1)", "number"),
        "payload_kb_req": ("Request payload (KB)", "number"),
        "payload_kb_resp": ("Response payload (KB)", "number"),
        "db_max_connections": ("DB max_connections", "number"),
        "rows_per_day": ("Rows written per day", "number"),
        "bytes_per_row": ("Bytes per row", "number"),
        "availability_target": ("Availability target (%)", "number"),
        "current_error_rate_pct": ("Current error rate (%)", "number"),
        "az_count": ("Availability zones", "number"),
        "region_count": ("Regions", "number"),
        "cost_per_instance_hour": ("Cost per instance hour ($)", "number"),
        "egress_per_gb": ("Egress cost per GB ($)", "number"),
        "service_kind": ("Service kind", "select:api,worker,streaming,batch,webhook,realtime"),
        "traffic_pattern": ("Traffic pattern", "select:steady,spiky,ramp,batch"),
        "has_rate_limit": ("Rate limiting", "bool"),
        "has_circuit_breaker": ("Circuit breaker", "bool"),
        "has_retry_budget": ("Retry budget", "bool"),
        "is_stateless": ("Stateless", "bool"),
        "db_replicated": ("DB replicated / failover", "bool"),
    }
    return jsonify({"fields": meta, "defaults": engine.DEFAULTS, "kinds": list(engine.SERVICE_KINDS)})


@app.route("/api/samples")
def list_samples():
    return jsonify({"samples": [{"id": s["id"], "name": s["name"], "description": s["description"]} for s in sample_lib.SAMPLES]})


@app.route("/api/sample/<sid>")
def get_sample(sid: str):
    s = sample_lib.by_id(sid)
    if not s:
        return jsonify({"error": "sample not found"}), 404
    return jsonify(s)


def _peak_of(result: Dict[str, Any]) -> float:
    f = result.get("inputs", {})
    return float(f.get("peak_rps") or f.get("avg_rps") or 0)


def _avg_of(result: Dict[str, Any]) -> float:
    f = result.get("inputs", {})
    return float(f.get("avg_rps") or (f.get("peak_rps") or 0) * 0.2)


def _title_from(brief: str) -> str:
    for line in (brief or "").strip().splitlines():
        line = line.strip()
        if len(line) > 3:
            return line[:90]
    return "Untitled capacity brief"


@app.route("/api/analyze", methods=["POST"])
def analyze():
    payload = request.get_json(silent=True) or {}
    brief = str(payload.get("brief", ""))
    overrides = payload.get("overrides") or {}
    if not isinstance(overrides, dict):
        overrides = {}
    clean: Dict[str, Any] = {}
    for k, v in overrides.items():
        if k in engine.DEFAULTS:
            clean[k] = v
    try:
        result = engine.analyze(brief, clean)
    except Exception as exc:  # never 500 on a weird brief
        return jsonify({"error": "analysis failed: %s" % exc}), 400
    result["meta"]["peak_rps"] = round(_peak_of(result), 3)
    result["meta"]["avg_rps"] = round(_avg_of(result), 3)
    title = payload.get("title") or _title_from(brief)
    try:
        result["session_id"] = store.save_session(title, brief, result)
    except Exception:
        result["session_id"] = None
    return jsonify(result)


@app.route("/api/sessions")
def sessions():
    return jsonify({"sessions": store.list_sessions(100)})


@app.route("/api/sessions/<int:sid>")
def session(sid: int):
    data = store.get_session(sid)
    if not data:
        return jsonify({"error": "not found"}), 404
    return jsonify(data)


@app.route("/api/sessions/<int:sid>", methods=["PATCH"])
def patch_session(sid: int):
    payload = request.get_json(silent=True) or {}
    rating = payload.get("self_rating")
    notes = payload.get("notes")
    if rating is not None:
        try:
            rating = int(rating)
        except (TypeError, ValueError):
            rating = None
    ok = store.update_session(sid, rating, notes)
    if not ok:
        return jsonify({"error": "nothing to update"}), 400
    return jsonify({"ok": True})


@app.route("/api/sessions/<int:sid>", methods=["DELETE"])
def delete_session(sid: int):
    return jsonify({"ok": store.delete_session(sid)})


@app.route("/api/export/<int:sid>")
def export(sid: int):
    fmt = request.args.get("fmt", "md")
    data = store.get_session(sid)
    if not data:
        return jsonify({"error": "not found"}), 404
    result = data.get("result") or {}
    if fmt == "json":
        body = json.dumps(result, indent=2)
        mime = "application/json"
        ext = "json"
    else:
        body = exporters.to_markdown(result, data.get("brief", ""))
        mime = "text/markdown"
        ext = "md"
    fname = "capacity-report-%s.%s" % (sid, ext)
    return Response(body, mimetype=mime, headers={"Content-Disposition": 'attachment; filename="%s"' % fname})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8788"))
    app.run(host="127.0.0.1", port=port, debug=False)
