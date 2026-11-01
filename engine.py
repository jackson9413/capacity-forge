"""capacity-forge -- capacity, SLO and sizing engine.

Pure standard library. No network calls, no API keys.
Everything here is deterministic: same brief in, same report out.
"""
from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

import findings as rules

SECONDS_PER_MONTH = 30 * 24 * 3600
HOURS_PER_MONTH = 730.0
KB = 1024.0
GB = 1024.0 ** 3
ENGINE_VERSION = "1.0.0"

# --------------------------------------------------------------------------
# defaults
# --------------------------------------------------------------------------
DEFAULTS: Dict[str, Any] = {
    # service shape
    "service_kind": "api",
    "traffic_pattern": "steady",
    # load
    "peak_rps": 0.0,
    "avg_rps": 0.0,
    "growth_pct_12mo": 0.0,
    # compute
    "cpu_ms_per_request": 10.0,
    "ram_mb_per_request": 8.0,
    "max_concurrency_per_instance": 100,
    "instance_vcpu": 2.0,
    "instance_ram_gb": 4.0,
    "instances_current": None,      # None -> falls back to instances_min
    "instances_min": 1,
    "instances_max": 1,
    "startup_seconds": 30.0,
    "readiness_seconds": 10.0,
    "spike_window_minutes": 2.0,
    # latency
    "p50_latency_ms": 0.0,
    "p99_latency_ms": 0.0,
    "latency_budget_ms": 300.0,
    "downstream_latency_ms": 0.0,
    "downstream_calls": 0,
    # data
    "db_reads_per_request": 0.0,
    "db_writes_per_request": 0.0,
    "db_query_ms": 5.0,
    "cache_hit_ratio": None,
    "payload_kb_req": 1.0,
    "payload_kb_resp": 4.0,
    "db_max_connections": 100,
    "db_max_connections_stated": False,
    "rows_per_day": 0.0,
    "bytes_per_row": 500.0,
    # reliability
    "availability_target": 99.9,
    "current_error_rate_pct": 0.1,
    "az_count": 1,
    "region_count": 1,
    # money
    "cost_per_instance_hour": 0.05,
    "egress_per_gb": 0.09,
    # protective features (None = not mentioned -> no finding)
    "has_rate_limit": None,
    "has_circuit_breaker": None,
    "has_retry_budget": None,
    "is_stateless": None,
    "db_replicated": None,
    # meta
    "notes": "",
}

SERVICE_KINDS = ("api", "worker", "streaming", "batch", "webhook", "realtime")

AXIS_WEIGHTS: Dict[str, Dict[str, float]] = {
    "api":       {"throughput": 0.25, "latency": 0.25, "reliability": 0.20, "scalability": 0.15, "cost": 0.15},
    "worker":    {"throughput": 0.35, "latency": 0.10, "reliability": 0.20, "scalability": 0.20, "cost": 0.15},
    "streaming": {"throughput": 0.30, "latency": 0.20, "reliability": 0.25, "scalability": 0.15, "cost": 0.10},
    "batch":     {"throughput": 0.25, "latency": 0.05, "reliability": 0.25, "scalability": 0.30, "cost": 0.15},
    "webhook":   {"throughput": 0.20, "latency": 0.20, "reliability": 0.30, "scalability": 0.15, "cost": 0.15},
    "realtime":  {"throughput": 0.30, "latency": 0.30, "reliability": 0.20, "scalability": 0.10, "cost": 0.10},
}

VERDICT_BANDS = [
    (85, "sized_ok", "Fleet is sized for the peak with headroom to spare"),
    (70, "mostly_ready", "Workable, but there is room to tighten a few things"),
    (55, "needs_tuning", "It will run, but it will run close to the edge"),
    (40, "tight_but_usable", "Only safe if the load stays exactly as briefed"),
    (0,  "under_provisioned", "The brief describes a service that cannot hold its own peak"),
]

SCALE_SUFFIX = {"k": 1e3, "m": 1e6, "b": 1e9}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "single": 1, "multi": 2,
}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v: Any, default: int = 0) -> int:
    try:
        if v is None or v == "":
            return default
        return int(round(float(v)))
    except (TypeError, ValueError):
        return default


def money(v: float) -> str:
    if v >= 100:
        return "$%0.0f" % v
    if v >= 10:
        return "$%0.1f" % v
    if v >= 1:
        return "$%0.2f" % v
    return "$%0.3f" % v


def num(v: float, digits: int = 1) -> str:
    if abs(v) >= 100:
        return "%0.0f" % v
    return ("%0." + str(digits) + "f") % v


# --------------------------------------------------------------------------
# brief parsing
# --------------------------------------------------------------------------
def _pick(text: str, patterns: List[Tuple[str, str]]) -> Optional[Tuple[float, str]]:
    """Return (value, evidence) for the first pattern that matches."""
    for pat, kind in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        try:
            raw = m.group("v")
        except Exception:
            continue
        raw = raw.replace(",", "").replace("$", "").strip()
        if raw == "":
            continue
        try:
            val = float(raw)
        except ValueError:
            continue
        unit = (m.groupdict().get("u") or "").lower()
        if kind == "rps":
            if unit in SCALE_SUFFIX:
                val *= SCALE_SUFFIX[unit]
        elif kind == "ms":
            if unit.startswith("s") and not unit.startswith("ms"):
                val *= 1000.0
        elif kind == "sec":
            if unit.startswith("m"):
                val *= 60.0
        elif kind == "ratio":
            if val > 1.0:
                val = val / 100.0
        elif kind == "bytes":
            if unit.startswith("kb"):
                val *= KB
            elif unit.startswith("mb"):
                val *= KB * KB
            elif unit.startswith("gb"):
                val *= GB
        elif kind == "size_kb":
            if unit.startswith("mb"):
                val *= 1024.0
        return val, m.group(0).strip()
    return None


def _word_count(text: str, patterns: List[str]) -> Optional[Tuple[int, str]]:
    """Match a spelled-out count (one/two/three...) and return it as an int."""
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if not m:
            continue
        raw = m.group("v").strip().lower()
        if raw.isdigit():
            return int(raw), m.group(0).strip()
        if raw in WORD_NUMBERS:
            return WORD_NUMBERS[raw], m.group(0).strip()
    return None


def _tri_state(text: str, positive: List[str], negative: List[str]) -> Optional[bool]:
    low = text.lower()
    for neg in negative:
        if neg in low:
            return False
    for pos in positive:
        if pos in low:
            return True
    return None


def parse_brief(text: str) -> Tuple[Dict[str, Any], List[Dict[str, str]]]:
    """Parse a free-text capacity brief into fields + a log of what was found."""
    text = text or ""
    low = text.lower()
    fields: Dict[str, Any] = dict(DEFAULTS)
    log: List[Dict[str, str]] = []

    def setf(key: str, value: Any, evidence: str = "") -> None:
        fields[key] = value
        log.append({"field": key, "value": str(value), "evidence": evidence[:120]})

    # ---- service kind (earliest explicit marker wins)
    kind_markers = [
        ("webhook", ["webhook", "event fanout", "event fan-out"]),
        ("batch", ["batch job", "nightly job", "backfill", "batch pipeline", "reconciliation batch"]),
        ("streaming", ["kafka consumer", "event stream", "pub/sub", "pubsub", "streaming service", "streaming worker"]),
        ("worker", ["background job", "queue consumer", "job queue", "worker pool"]),
        ("realtime", ["websocket", "real-time", "live update"]),
    ]
    best = None
    for kind, words in kind_markers:
        for w in words:
            i = low.find(w)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, kind, w)
    if best:
        setf("service_kind", best[1], "matched '%s'" % best[2])
    elif "api" in low or "endpoint" in low or "http" in low:
        setf("service_kind", "api", "matched 'api'")

    # ---- traffic pattern (earliest explicit statement wins)
    pat_markers = [
        ("spiky", ["spiky", "spike", "bursty", "burst", "flash crowd", "black friday"]),
        ("batch", ["traffic pattern is batch", "batch pattern", "batch window"]),
        ("ramp", ["ramp", "diurnal", "seasonal", "traffic wave"]),
        ("steady", ["steady", "flat traffic", "constant traffic"]),
    ]
    best = None
    for kind, words in pat_markers:
        for w in words:
            i = low.find(w)
            if i >= 0 and (best is None or i < best[0]):
                best = (i, kind, w)
    if best:
        setf("traffic_pattern", best[1], "matched '%s'" % best[2])

    # ---- load
    got = _pick(text, [
        (r"peak(?:ed)?\s*(?:traffic|load|throughput|volume|rps)?[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?\s*(?:rps|req/s|reqs?/s|requests? per second|qps)", "rps"),
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?\s*(?:rps|req/s|qps)[^\n]{0,12}peak", "rps"),
    ])
    if got:
        setf("peak_rps", got[0], got[1])

    got = _pick(text, [
        (r"(?:average|avg|mean|baseline|steady)\s*(?:traffic|load|rps|throughput|volume)?[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?\s*(?:rps|req/s|reqs?/s|requests? per second|qps)", "rps"),
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?\s*(?:rps|req/s|qps)[^\n]{0,12}(?:average|avg|steady|baseline)", "rps"),
    ])
    if got:
        setf("avg_rps", got[0], got[1])

    got = _pick(text, [
        (r"growth[^\n]{0,26}?(?P<v>\d[\d.]*)\s*%", "pct"),
        (r"(?P<v>\d[\d.]*)\s*%\s*(?:growth|more traffic|increase)", "pct"),
    ])
    if got:
        setf("growth_pct_12mo", got[0], got[1])
    elif "doubling" in low:
        setf("growth_pct_12mo", 100.0, "doubling")

    # ---- compute
    got = _pick(text, [
        (r"cpu[^\n]{0,26}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*ms\b[^\n]{0,12}cpu", "ms"),
    ])
    if got:
        setf("cpu_ms_per_request", got[0], got[1])

    got = _pick(text, [
        (r"(?:max(?:imum)?\s*)?concurren\w+[^\n]{0,16}?(?P<v>\d[\d,]*(?:\.\d+)?)", "plain"),
        (r"thread\s*pool[^\n]{0,16}?(?P<v>\d[\d,]*(?:\.\d+)?)", "plain"),
        (r"(?P<v>\d[\d,]*)\s*(?:concurrent|concurrency|threads?)", "plain"),
    ])
    if got:
        setf("max_concurrency_per_instance", _i(got[0]), got[1])

    got = _pick(text, [(r"(?P<v>\d+(?:\.\d+)?)\s*vcpu", "plain")])
    if got:
        setf("instance_vcpu", got[0], got[1])

    got = _pick(text, [
        (r"(?:ram|memory)[^\n]{0,18}?(?P<v>\d+(?:\.\d+)?)\s*(?P<u>gb|mb)", "plain"),
        (r"(?P<v>\d+(?:\.\d+)?)\s*gb\s*(?:ram|memory)", "plain"),
    ])
    if got:
        val = got[0]
        if "mb" in got[1].lower():
            val = val / 1024.0
        setf("instance_ram_gb", val, got[1])

    # ---- fleet size
    m = re.search(r"autoscal\w+[^\n]{0,24}?(\d+)\s*(?:-|to|and|\u2013)\s*(\d+)", low)
    if m:
        setf("instances_min", int(m.group(1)), m.group(0))
        setf("instances_max", int(m.group(2)), m.group(0))
    else:
        got = _pick(text, [(r"min(?:imum)?[^\n]{0,16}?(?P<v>\d+)\s*(?:replicas|instances|pods|nodes)", "plain")])
        if got:
            setf("instances_min", _i(got[0]), got[1])
        got = _pick(text, [(r"max(?:imum)?[^\n]{0,16}?(?P<v>\d+)\s*(?:replicas|instances|pods|nodes)", "plain")])
        if got:
            setf("instances_max", _i(got[0]), got[1])

    got = _pick(text, [
        (r"(?:current(?:ly)?|now|running|serving with|we run|we have|using)\s+(?P<v>\d+)\s*(?:instances|replicas|pods|servers|workers|nodes|containers)", "plain"),
        (r"(?P<v>\d+)\s*(?:instances|replicas|pods|servers|workers|nodes|containers)\s*(?:currently|now|running)", "plain"),
    ])
    if got:
        setf("instances_current", _i(got[0]), got[1])

    got = _pick(text, [
        (r"(?:cold\s*start|startup|boot|scale[-\s]?up|warm)[^\n]{0,20}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>s|sec|seconds|m|min|minutes)", "sec"),
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>s|sec|seconds|m|min|minutes)\s*(?:to start|startup|cold start)", "sec"),
    ])
    if got:
        setf("startup_seconds", got[0], got[1])

    got = _pick(text, [(r"readiness[^\n]{0,18}?(?P<v>\d[\d,]*)\s*(?P<u>s|sec|seconds)", "sec")])
    if got:
        setf("readiness_seconds", got[0], got[1])

    got = _pick(text, [(r"spike[^\n]{0,26}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>s|sec|seconds|m|min|minutes)", "sec")])
    if got:
        setf("spike_window_minutes", got[0] / 60.0, got[1])

    # ---- latency
    got = _pick(text, [
        (r"(?:latency\s*(?:budget|slo|target)|slo|budget|target\s*latency)[^\n]{0,24}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
        (r"p99\s*(?:target|slo|budget)[^\n]{0,12}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
    ])
    if got:
        setf("latency_budget_ms", got[0], got[1])

    got = _pick(text, [
        (r"p50[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
        (r"median[^\n]{0,14}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
    ])
    if got:
        setf("p50_latency_ms", got[0], got[1])

    got = _pick(text, [
        (r"p99[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
        (r"(?:tail\s*latency|99th\s*percentile)[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
    ])
    if got:
        setf("p99_latency_ms", got[0], got[1])

    if "no downstream" in low or "without downstream" in low:
        setf("downstream_latency_ms", 0.0, "brief says no downstream calls")
        setf("downstream_calls", 0, "brief says no downstream calls")
    else:
        got = _pick(text, [
            (r"downstream[^\n]{0,60}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
            (r"(?:external|third[-\s]party)[^\n]{0,40}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
        ])
        if got:
            setf("downstream_latency_ms", got[0], got[1])

    got = _pick(text, [
        (r"(?P<v>\d+)\s*(?:downstream|external|third[-\s]party)\s*(?:calls?|dependencies|services)", "plain"),
        (r"downstream[^\n]{0,22}?(?P<v>\d+)\s*(?:calls?|dependencies|services)", "plain"),
    ])
    word_calls = _word_count(text, [r"(?P<v>one|two|three|four|five|six)\s+(?:downstream|external)\s+(?:calls?|dependencies|services)"])
    if got:
        setf("downstream_calls", _i(got[0]), got[1])
    elif word_calls:
        setf("downstream_calls", word_calls[0], word_calls[1])

    # ---- data
    got = _pick(text, [
        (r"(?P<v>\d+)\s*(?:db\s*|database\s*)?reads?\s*(?:per request|/\s*req)", "plain"),
        (r"(?:db|database)[^\n]{0,24}?(?P<v>\d+)\s*reads?", "plain"),
        (r"(?P<v>\d+)\s+reads\b", "plain"),
    ])
    if got:
        setf("db_reads_per_request", got[0], got[1])

    got = _pick(text, [
        (r"(?P<v>\d+)\s*writes?\s*(?:per request|/\s*req)", "plain"),
        (r"(?:db|database)[^\n]{0,24}?(?P<v>\d+)\s*writes?", "plain"),
        (r"(?P<v>\d+)\s+writes\b", "plain"),
    ])
    if got:
        setf("db_writes_per_request", got[0], got[1])

    got = _pick(text, [
        (r"(?:db|database|query)[^\n]{0,26}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>ms|s)\b", "ms"),
    ])
    if got:
        setf("db_query_ms", got[0], got[1])

    got = _pick(text, [
        (r"cache[^\n]{0,26}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*%", "ratio"),
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*%\s*(?:cache|cached)", "ratio"),
    ])
    if got:
        setf("cache_hit_ratio", clamp(got[0], 0.0, 1.0), got[1])

    got = _pick(text, [
        (r"payload[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>kb|mb)\b", "size_kb"),
        (r"request\s*(?:size|payload)[^\n]{0,12}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>kb|mb)\b", "size_kb"),
    ])
    if got:
        setf("payload_kb_req", got[0], got[1])

    got = _pick(text, [
        (r"response[^\n]{0,18}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>kb|mb)\b", "size_kb"),
        (r"(?:reply|reply size)[^\n]{0,14}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>kb|mb)\b", "size_kb"),
    ])
    if got:
        setf("payload_kb_resp", got[0], got[1])

    got = _pick(text, [(r"(?:max(?:imum)?\s*)?connections?[^\n]{0,18}?(?P<v>\d+)", "plain")])
    if got:
        setf("db_max_connections", _i(got[0]), got[1])
        setf("db_max_connections_stated", True, "stated in brief")

    got = _pick(text, [
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?\s*rows?\s*(?:per\s*day|/\s*day|\s*a\s*day)", "plain"),
        (r"rows?[^\n]{0,16}?(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>[kKmM])?", "plain"),
    ])
    if got:
        val = got[0]
        mm = re.search(r"([kKmM])", got[1])
        if mm:
            val *= SCALE_SUFFIX[mm.group(1).lower()]
        setf("rows_per_day", val, got[1])

    got = _pick(text, [
        (r"(?P<v>\d[\d,]*(?:\.\d+)?)\s*(?P<u>kb|mb|b|bytes)\s*(?:per\s*row|/\s*row)", "bytes"),
        (r"(?P<v>\d[\d,]*)\s*bytes?\s*(?:per\s*row|/\s*row)", "bytes"),
    ])
    if got:
        setf("bytes_per_row", got[0], got[1])

    # ---- reliability
    got = _pick(text, [
        (r"availability[^.\n]{0,20}?(?P<v>\d[\d.]*)\s*%", "pct"),
        (r"(?:target|slo|uptime)[^.\n]{0,16}?(?P<v>\d[\d.]*)\s*%", "pct"),
        (r"(?P<v>\d[\d.]*)\s*%\s*(?:uptime|availability|slo)", "pct"),
    ])
    if got:
        setf("availability_target", got[0], got[1])
    elif "four nines" in low or "4 nines" in low:
        setf("availability_target", 99.99, "four nines")
    elif "three nines" in low or "3 nines" in low:
        setf("availability_target", 99.9, "three nines")

    got = _pick(text, [
        (r"error\s*rate[^\n]{0,18}?(?P<v>\d[\d.]*)\s*%", "pct"),
        (r"(?P<v>\d[\d.]*)\s*%\s*(?:of\s*)?(?:requests?\s*)?(?:error|fail)", "pct"),
    ])
    if got:
        setf("current_error_rate_pct", got[0], got[1])

    got = _pick(text, [(r"(?P<v>\d+)\s*(?:az|availability\s*zones?)", "plain")])
    word = _word_count(text, [r"(?P<v>one|two|three|four|five|six|seven|eight|nine|ten)\s+(?:az|availability\s*zones?)"])
    if got:
        setf("az_count", _i(got[0]), got[1])
    elif word:
        setf("az_count", word[0], word[1])
    elif "multi-az" in low or "multi az" in low:
        setf("az_count", 2, "multi-az")
    elif "single-az" in low or "single az" in low:
        setf("az_count", 1, "single-az")

    got = _pick(text, [(r"(?P<v>\d+)\s*regions?", "plain")])
    word = _word_count(text, [r"(?P<v>one|two|three|four)\s+regions?"])
    if got:
        setf("region_count", _i(got[0]), got[1])
    elif word:
        setf("region_count", word[0], word[1])
    elif "multi-region" in low or "multi region" in low:
        setf("region_count", 2, "multi-region")
    elif "single region" in low:
        setf("region_count", 1, "single-region")

    # ---- money
    got = _pick(text, [
        (r"(?P<v>\d[\d.]*)\s*(?:per|/)\s*(?:instance\s*)?(?:hour|hr)", "hour_usd"),
        (r"(?:cost|price)[^\n]{0,26}?(?P<v>\d[\d.]*)\s*(?:/\s*hour|per hour|an hour)", "hour_usd"),
    ])
    if got:
        setf("cost_per_instance_hour", got[0], got[1])

    got = _pick(text, [
        (r"(?P<v>\d[\d.]*)\s*(?:per|/)\s*gb\b", "hour_usd"),
        (r"egress[^\n]{0,22}?(?P<v>\d[\d.]*)", "hour_usd"),
    ])
    if got:
        setf("egress_per_gb", got[0], got[1])

    # ---- protective features (tri-state)
    fields["has_rate_limit"] = _tri_state(low, ["rate limit", "rate-limit", "ratelimit", "throttl"], ["no rate limit", "without rate limit", "unlimited"])
    fields["has_circuit_breaker"] = _tri_state(low, ["circuit breaker", "circuit-breaker"], ["no circuit breaker", "without circuit breaker"])
    fields["has_retry_budget"] = _tri_state(low, ["retry budget", "retry-budget"], ["no retry budget", "without retry budget", "infinite retry", "unbounded retry"])
    fields["is_stateless"] = _tri_state(low, ["stateless"], ["stateful"])
    fields["db_replicated"] = _tri_state(low, ["read replica", "replicated", "primary/replica"], ["no replica", "single primary", "no failover"])

    fields["notes"] = text.strip()
    return normalize(fields), log


# --------------------------------------------------------------------------
# normalisation
# --------------------------------------------------------------------------
def normalize(raw: Dict[str, Any]) -> Dict[str, Any]:
    f = dict(DEFAULTS)
    for k, v in (raw or {}).items():
        if k in f:
            f[k] = v
    f["service_kind"] = str(f.get("service_kind") or "api").lower()
    if f["service_kind"] not in SERVICE_KINDS:
        f["service_kind"] = "api"
    f["traffic_pattern"] = str(f.get("traffic_pattern") or "steady").lower()

    for k in ("peak_rps", "avg_rps", "cpu_ms_per_request", "ram_mb_per_request", "p50_latency_ms",
              "p99_latency_ms", "latency_budget_ms", "downstream_latency_ms", "db_reads_per_request",
              "db_writes_per_request", "db_query_ms", "payload_kb_req", "payload_kb_resp",
              "growth_pct_12mo", "cost_per_instance_hour", "egress_per_gb", "rows_per_day",
              "bytes_per_row", "startup_seconds", "readiness_seconds", "spike_window_minutes",
              "instance_vcpu", "instance_ram_gb", "current_error_rate_pct", "availability_target"):
        f[k] = max(0.0, _f(f[k]))

    for k in ("max_concurrency_per_instance", "instances_min", "instances_max", "downstream_calls",
              "db_max_connections", "az_count", "region_count"):
        f[k] = max(0, _i(f[k]))

    f["instances_min"] = max(1, f["instances_min"])
    f["instances_max"] = max(f["instances_min"], f["instances_max"])
    f["instances_current"] = f["instances_min"] if f["instances_current"] in (None, "") else max(1, _i(f["instances_current"]))
    f["az_count"] = max(1, f["az_count"])
    f["region_count"] = max(1, f["region_count"])
    f["db_max_connections"] = max(10, f["db_max_connections"])

    ratio = f.get("cache_hit_ratio")
    f["cache_hit_ratio"] = None if ratio is None else clamp(_f(ratio), 0.0, 1.0)

    f["availability_target"] = clamp(_f(f["availability_target"], 99.9), 0.0, 100.0)
    f["instance_vcpu"] = max(0.25, _f(f["instance_vcpu"], 2.0))
    f["instance_ram_gb"] = max(0.5, _f(f["instance_ram_gb"], 4.0))
    f["latency_budget_ms"] = max(1.0, _f(f["latency_budget_ms"], 300.0))
    return f


# --------------------------------------------------------------------------
# queueing math
# --------------------------------------------------------------------------
def erlang_c(lam: float, mu: float, c: int) -> Tuple[float, float, float]:
    """M/M/c queue. Returns (mean_wait_seconds, prob_of_waiting, rho)."""
    if c <= 0 or mu <= 0 or lam <= 0:
        return 0.0, 0.0, 0.0
    rho = lam / (c * mu)
    if rho >= 1.0:
        return float("inf"), 1.0, rho
    a = c * rho
    log_a = math.log(a)
    logs = [k * log_a - math.lgamma(k + 1) for k in range(c)]
    last = c * log_a - math.lgamma(c + 1) - math.log(1.0 - rho)
    m = max(logs + [last])
    total = math.log(sum(math.exp(t - m) for t in logs) + math.exp(last - m)) + m
    p_wait = math.exp(last - total)
    # mean wait in queue: P(wait) * c / (c*mu - lam)   (standard M/M/c result)
    wq = p_wait * c / (c * mu - lam)
    return wq, p_wait, rho


def tail_amplification(rho: float) -> float:
    """Empirical p99/p50 amplification vs utilisation.

    ~1.4x at idle, ~1.8x at 60% utilisation, ~4.6x at 80%, blowing past 10x as
    utilisation approaches 1.0. Capped at 25x.
    """
    if rho <= 0:
        return 1.0
    if rho >= 0.999:
        return 25.0
    val = 1.4 + 6.0 * (rho ** 6) / max(1e-6, 1.0 - rho ** 3)
    return min(25.0, val)


def score_cpm(cpm: float) -> float:
    """Cost per million requests -> 0-100 efficiency score."""
    pts = [(0.5, 100.0), (2.0, 92.0), (10.0, 78.0), (50.0, 58.0), (200.0, 38.0), (1000.0, 15.0)]
    if cpm <= pts[0][0]:
        return 100.0
    if cpm >= pts[-1][0]:
        return 15.0
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        if x0 <= cpm <= x1:
            return y0 + (y1 - y0) * (cpm - x0) / (x1 - x0)
    return 50.0


# --------------------------------------------------------------------------
# the main computation
# --------------------------------------------------------------------------
def compute(raw: Dict[str, Any]) -> Dict[str, Any]:
    f = normalize(raw)

    peak = f["peak_rps"] or f["avg_rps"]
    avg = f["avg_rps"] or peak * 0.2

    # ---- per instance throughput ceiling
    cpu_rps = (f["instance_vcpu"] * 1000.0 / max(f["cpu_ms_per_request"], 0.01)) * 0.75
    app_latency_s = max(f["p50_latency_ms"], f["cpu_ms_per_request"], 1.0) / 1000.0
    conc_rps = f["max_concurrency_per_instance"] / max(app_latency_s, 1e-6)
    mu = min(cpu_rps, conc_rps)
    binding = "cpu" if cpu_rps <= conc_rps else "concurrency"

    max_cluster_rps = mu * f["instances_max"]
    current_cluster_rps = mu * f["instances_current"]

    util_peak = (peak / max_cluster_rps) if max_cluster_rps > 0 else 9.99
    util_current = (peak / current_cluster_rps) if current_cluster_rps > 0 else 9.99
    util_growth = (peak * (1 + f["growth_pct_12mo"] / 100.0) / max_cluster_rps) if max_cluster_rps > 0 else 9.99

    # ---- latency components
    cache = f["cache_hit_ratio"] if f["cache_hit_ratio"] is not None else 0.0
    db_time_ms = (f["db_reads_per_request"] * (1.0 - cache) + f["db_writes_per_request"]) * f["db_query_ms"]
    service_ms = f["cpu_ms_per_request"] + db_time_ms

    # ---- queueing at peak with the fleet scaled to its ceiling
    wait_s, p_wait, rho = erlang_c(peak, mu, f["instances_max"])
    queue_ms = 0.0 if math.isinf(wait_s) else wait_s * 1000.0
    unstable = math.isinf(wait_s)
    amp = tail_amplification(rho)

    p99_model = service_ms * amp + f["downstream_latency_ms"] + queue_ms
    total_ms = service_ms + f["downstream_latency_ms"] + queue_ms
    budget = f["latency_budget_ms"]
    budget_util = total_ms / budget if budget > 0 else 9.99

    # ---- Little's law
    occupancy_ms = f["p50_latency_ms"] if f["p50_latency_ms"] > 0 else total_ms
    required_concurrency = peak * (occupancy_ms / 1000.0)
    provided_concurrency = f["instances_current"] * f["max_concurrency_per_instance"]
    concurrency_ratio = (provided_concurrency / required_concurrency) if required_concurrency > 0 else 99.0

    # ---- error budget
    target = f["availability_target"]
    err_budget_pct = max(0.0, 100.0 - target)
    allowed_min = (30 * 24 * 60) * (err_budget_pct / 100.0)
    current_err = f["current_error_rate_pct"]
    if err_budget_pct > 0:
        consumed_pct = (current_err / err_budget_pct) * 100.0
    else:
        consumed_pct = 100.0 if current_err > 0 else 0.0
    consumed_pct = min(consumed_pct, 999.0)
    downtime_min = (current_err / 100.0) * (30 * 24 * 60)
    budget_days_left = max(0.0, 30.0 * (1.0 - consumed_pct / 100.0))

    # ---- connection pool sizing
    pool_per_instance = int(clamp(math.ceil(f["max_concurrency_per_instance"] * 0.2), 4, 50))
    pool_per_instance = min(pool_per_instance, max(4, f["max_concurrency_per_instance"]))
    total_connections = pool_per_instance * f["instances_max"]
    conn_saturation = total_connections / f["db_max_connections"]
    db_mem_mb = pool_per_instance * f["ram_mb_per_request"]
    instance_mem_mb = f["instance_ram_gb"] * 1024.0

    # ---- cost
    monthly_requests = avg * SECONDS_PER_MONTH
    egress_gb = monthly_requests * (f["payload_kb_req"] + f["payload_kb_resp"]) / 1024.0 / 1024.0
    egress_cost = egress_gb * f["egress_per_gb"]
    compute_cost = f["instances_current"] * HOURS_PER_MONTH * f["cost_per_instance_hour"]
    max_compute_cost = f["instances_max"] * HOURS_PER_MONTH * f["cost_per_instance_hour"]
    total_cost = compute_cost + egress_cost
    cpm = (total_cost / (monthly_requests / 1e6)) if monthly_requests > 0 else 0.0
    idle_ratio = 1.0 - min(1.0, (avg / current_cluster_rps) if current_cluster_rps > 0 else 0.0)

    # ---- storage
    daily_gb = (f["rows_per_day"] * f["bytes_per_row"]) / GB
    storage_year_gb = daily_gb * 365.0

    # ---- scale out capability
    scale_out_s = max(1.0, f["startup_seconds"] + f["readiness_seconds"])
    required_scale_rate = max(0.0, peak - current_cluster_rps) / scale_out_s
    max_scale_rate = max(0.0, (f["instances_max"] - f["instances_current"]) * mu) / scale_out_s
    scale_feasible = max_scale_rate >= required_scale_rate
    spike_window_s = max(30.0, f["spike_window_minutes"] * 60.0)
    spike_ramp_rate = max(0.0, peak - avg) / spike_window_s

    # ---- recommended fleet
    target_util = 0.65
    recommended_instances = int(math.ceil((peak / max(target_util, 1e-6)) / mu)) if mu > 0 else f["instances_max"]
    recommended_instances = max(recommended_instances, f["instances_min"])
    recommended_instances_12mo = int(math.ceil((peak * (1 + f["growth_pct_12mo"] / 100.0) / target_util) / mu)) if mu > 0 else recommended_instances

    metrics: Dict[str, Any] = {
        "peak_rps": peak,
        "avg_rps": avg,
        "mu_instance": mu,
        "binding_constraint": binding,
        "cpu_rps_instance": cpu_rps,
        "conc_rps_instance": conc_rps,
        "max_cluster_rps": max_cluster_rps,
        "current_cluster_rps": current_cluster_rps,
        "util_at_peak": util_peak,
        "util_current": util_current,
        "util_at_growth": util_growth,
        "required_concurrency": required_concurrency,
        "provided_concurrency": provided_concurrency,
        "concurrency_ratio": concurrency_ratio,
        "rho": rho,
        "queue_wait_ms": queue_ms,
        "queue_prob_wait": p_wait,
        "queue_unstable": unstable,
        "tail_amp": amp,
        "p99_model_ms": p99_model,
        "p99_observed_ms": f["p99_latency_ms"],
        "p50_observed_ms": f["p50_latency_ms"],
        "tail_ratio_observed": (f["p99_latency_ms"] / f["p50_latency_ms"]) if f["p50_latency_ms"] > 0 else 0.0,
        "latency_components": {
            "cpu_ms": f["cpu_ms_per_request"],
            "db_ms": db_time_ms,
            "downstream_ms": f["downstream_latency_ms"],
            "queue_ms": queue_ms,
            "total_ms": total_ms,
        },
        "budget_ms": budget,
        "budget_util": budget_util,
        "budget_headroom_ms": budget - total_ms,
        "error_budget_pct": err_budget_pct,
        "allowed_downtime_min": allowed_min,
        "consumed_pct": consumed_pct,
        "downtime_min": downtime_min,
        "budget_days_left": budget_days_left,
        "pool_per_instance": pool_per_instance,
        "total_connections": total_connections,
        "conn_saturation": conn_saturation,
        "db_mem_mb": db_mem_mb,
        "instance_mem_mb": instance_mem_mb,
        "monthly_requests": monthly_requests,
        "egress_gb": egress_gb,
        "egress_cost": egress_cost,
        "compute_cost": compute_cost,
        "max_compute_cost": max_compute_cost,
        "total_cost": total_cost,
        "cost_per_million": cpm,
        "idle_ratio": idle_ratio,
        "storage_day_gb": daily_gb,
        "storage_year_gb": storage_year_gb,
        "scale_out_s": scale_out_s,
        "required_scale_rate": required_scale_rate,
        "max_scale_rate": max_scale_rate,
        "scale_feasible": scale_feasible,
        "spike_ramp_rate": spike_ramp_rate,
        "recommended_instances": recommended_instances,
        "recommended_instances_12mo": recommended_instances_12mo,
        "fields": f,
    }

    weights = AXIS_WEIGHTS[f["service_kind"]]
    axes = score_axes(metrics)
    overall = sum(axes[k] * weights[k] for k in weights)

    # severity caps: a hard capacity or SLO failure cannot score well
    caps: List[str] = []
    if unstable or util_peak >= 1.0:
        caps.append("the fleet cannot serve the briefed peak")
        overall = min(overall, 45.0)
    if consumed_pct >= 100.0:
        caps.append("the error budget for this period is already spent")
        overall = min(overall, 55.0)
    if budget_util >= 1.0 and f["p99_latency_ms"] > 0:
        caps.append("modelled p99 breaches the latency budget")
        overall = min(overall, 60.0)

    verdict = "under_provisioned"
    verdict_note = ""
    for threshold, name, note in VERDICT_BANDS:
        if overall >= threshold:
            verdict = name
            verdict_note = note
            break

    metrics["axis_scores"] = axes
    metrics["weights"] = weights
    metrics["overall"] = round(overall, 1)
    metrics["verdict"] = verdict
    metrics["verdict_note"] = verdict_note
    metrics["caps"] = caps

    findings = rules.build_findings(metrics)
    metrics["findings"] = findings
    metrics["strengths"] = rules.build_strengths(metrics)
    metrics["fix_plan"] = rules.build_fix_plan(metrics)

    return {
        "meta": {"engine": "capacity-forge", "version": ENGINE_VERSION, "kind": f["service_kind"]},
        "inputs": f,
        "capacity": {
            "mu_instance": mu,
            "binding_constraint": binding,
            "cpu_rps_instance": cpu_rps,
            "conc_rps_instance": conc_rps,
            "max_cluster_rps": max_cluster_rps,
            "current_cluster_rps": current_cluster_rps,
            "util_at_peak": util_peak,
            "util_current": util_current,
            "util_at_growth": util_growth,
            "required_concurrency": required_concurrency,
            "provided_concurrency": provided_concurrency,
            "concurrency_ratio": concurrency_ratio,
            "recommended_instances": recommended_instances,
            "recommended_instances_12mo": recommended_instances_12mo,
        },
        "latency": {
            "components": metrics["latency_components"],
            "budget_ms": budget,
            "budget_util": budget_util,
            "headroom_ms": metrics["budget_headroom_ms"],
            "queue_wait_ms": queue_ms,
            "rho": rho,
            "tail_amp": amp,
            "p99_model_ms": p99_model,
            "p99_observed_ms": f["p99_latency_ms"],
            "tail_ratio_observed": metrics["tail_ratio_observed"],
        },
        "reliability": {
            "target": target,
            "error_budget_pct": err_budget_pct,
            "allowed_downtime_min": allowed_min,
            "consumed_pct": consumed_pct,
            "downtime_min": downtime_min,
            "days_left": budget_days_left,
        },
        "data": {
            "pool_per_instance": pool_per_instance,
            "total_connections": total_connections,
            "db_max_connections": f["db_max_connections"],
            "saturation": conn_saturation,
            "db_mem_mb": db_mem_mb,
            "instance_mem_mb": instance_mem_mb,
            "db_time_ms": db_time_ms,
            "cache_hit_ratio": cache,
        },
        "autoscale": {
            "scale_out_s": scale_out_s,
            "required_scale_rate": required_scale_rate,
            "max_scale_rate": max_scale_rate,
            "feasible": scale_feasible,
            "instances_min": f["instances_min"],
            "instances_current": f["instances_current"],
            "instances_max": f["instances_max"],
            "traffic_pattern": f["traffic_pattern"],
        },
        "cost": {
            "monthly_requests": monthly_requests,
            "egress_gb": egress_gb,
            "egress_cost": egress_cost,
            "compute_cost": compute_cost,
            "max_compute_cost": max_compute_cost,
            "total_cost": total_cost,
            "cost_per_million": cpm,
            "idle_ratio": idle_ratio,
        },
        "storage": {"day_gb": daily_gb, "year_gb": storage_year_gb, "rows_per_day": f["rows_per_day"]},
        "axis_scores": axes,
        "weights": weights,
        "overall": metrics["overall"],
        "verdict": verdict,
        "verdict_note": verdict_note,
        "caps": caps,
        "findings": findings,
        "strengths": metrics["strengths"],
        "fix_plan": metrics["fix_plan"],
    }


# --------------------------------------------------------------------------
# axis scoring
# --------------------------------------------------------------------------
def score_axes(m: Dict[str, Any]) -> Dict[str, float]:
    f = m["fields"]

    # throughput
    util = m["util_at_peak"]
    if util >= 1.0:
        t = max(0.0, 60.0 - (util - 1.0) * 300.0)
    else:
        t = 100.0 - max(0.0, util - 0.60) * 250.0
    if m["util_at_growth"] >= 1.0:
        t -= 15.0
    elif m["util_at_growth"] > 0.85:
        t -= 8.0
    if m["util_current"] >= 1.0:
        t -= 18.0
    if m["concurrency_ratio"] < 1.0:
        t -= 12.0
    throughput = clamp(t, 0.0, 100.0)

    # latency
    bu = m["budget_util"]
    lat = 100.0
    if bu >= 1.0:
        lat -= min(60.0, (bu - 1.0) * 80.0)
    else:
        lat -= max(0.0, bu - 0.70) * 120.0
    if m["budget_ms"] > 0:
        lat -= min(25.0, (m["queue_wait_ms"] / m["budget_ms"]) * 60.0)
    if m["tail_ratio_observed"] > 8:
        lat -= min(20.0, (m["tail_ratio_observed"] - 8) * 3.0)
    obs_p99 = m["p99_observed_ms"]
    budget_ms = m["budget_ms"]
    if obs_p99 > 0 and budget_ms > 0 and obs_p99 > budget_ms:
        lat -= min(45.0, ((obs_p99 / budget_ms) - 1.0) * 45.0)
    if m["budget_ms"] > 0 and (m["latency_components"]["downstream_ms"] / m["budget_ms"]) > 0.5:
        lat -= min(15.0, ((m["latency_components"]["downstream_ms"] / m["budget_ms"]) - 0.5) * 40.0)
    latency = clamp(lat, 0.0, 100.0)

    # reliability
    rel = 100.0
    rel -= min(50.0, m["consumed_pct"] * 0.5)
    if f["az_count"] < 2:
        rel -= 18.0
    elif f["az_count"] < 3:
        rel -= 5.0
    if f["region_count"] >= 2:
        rel += 5.0
    if f["availability_target"] >= 99.99 and f["region_count"] < 2:
        rel -= 15.0
    if f["current_error_rate_pct"] > 1.0:
        rel -= 15.0
    if f["db_writes_per_request"] > 0 and f["db_replicated"] is False:
        rel -= 8.0
    if f["downstream_calls"] >= 3 and f["has_circuit_breaker"] is False:
        rel -= 8.0
    reliability = clamp(rel, 0.0, 100.0)

    # scalability
    sc = 100.0
    if f["instances_max"] <= f["instances_current"]:
        sc -= 25.0
    else:
        ratio = f["instances_max"] / max(1, f["instances_current"])
        sc -= max(0.0, (3.0 - ratio)) * 12.0
    if not m["scale_feasible"]:
        sc -= 20.0
    if f["startup_seconds"] > 45:
        sc -= min(15.0, (f["startup_seconds"] - 45) * 0.5)
    if f["is_stateless"] is False:
        sc -= 12.0
    if f["az_count"] < 2:
        sc -= 8.0
    scalability = clamp(sc, 0.0, 100.0)

    # cost efficiency
    co = score_cpm(m["cost_per_million"]) if m["monthly_requests"] > 0 else 70.0
    co -= m["idle_ratio"] * 25.0
    if m["egress_gb"] > 1000:
        co -= min(20.0, m["egress_gb"] / 500.0)
    cost = clamp(co, 0.0, 100.0)

    return {
        "throughput": round(throughput, 1),
        "latency": round(latency, 1),
        "reliability": round(reliability, 1),
        "scalability": round(scalability, 1),
        "cost": round(cost, 1),
    }


def analyze(brief: str = "", overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    fields, log = parse_brief(brief)
    if overrides:
        merged = dict(fields)
        for k, v in overrides.items():
            if k in DEFAULTS and v is not None:
                merged[k] = v
        fields = merged
    result = compute(fields)
    result["parse_log"] = log
    return result
