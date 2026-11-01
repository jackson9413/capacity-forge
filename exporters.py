"""Markdown / JSON export for capacity-forge."""
from __future__ import annotations

from typing import Any, Dict, List

FENCE = chr(96) * 3


def _n(v: float, d: int = 1) -> str:
    if abs(v) >= 100:
        return "%0.0f" % v
    return ("%0." + str(d) + "f") % v


def _usd(v: float) -> str:
    if v >= 100:
        return "$%0.0f" % v
    if v >= 10:
        return "$%0.1f" % v
    if v >= 1:
        return "$%0.2f" % v
    return "$%0.3f" % v


def _pct(v: float) -> str:
    return "%0.0f%%" % (v * 100.0)


def _peak(result: Dict[str, Any]) -> float:
    return float(result.get("meta", {}).get("peak_rps", 0) or 0)


def _avg(result: Dict[str, Any]) -> float:
    return float(result.get("meta", {}).get("avg_rps", 0) or 0)


def to_markdown(result: Dict[str, Any], brief: str = "") -> str:
    f = result.get("inputs", {})
    cap = result.get("capacity", {})
    lat = result.get("latency", {})
    rel = result.get("reliability", {})
    data = result.get("data", {})
    auto = result.get("autoscale", {})
    cost = result.get("cost", {})
    sto = result.get("storage", {})
    axes = result.get("axis_scores", {})
    weights = result.get("weights", {})
    comp = lat.get("components", {})

    L: List[str] = []
    L.append("# Capacity report -- %s" % f.get("service_kind", "api"))
    L.append("")
    L.append("**Score: %s / 100 -- %s**" % (_n(result.get("overall", 0)), result.get("verdict", "")))
    L.append("")
    L.append("%s" % result.get("verdict_note", ""))
    L.append("")

    if result.get("caps"):
        L.append("> Hard limits applied: " + "; ".join(result["caps"]) + ".")
        L.append("")

    L.append("## Axis scores")
    L.append("")
    L.append("| Axis | Score | Weight |")
    L.append("| --- | ---: | ---: |")
    for axis in ("throughput", "latency", "reliability", "scalability", "cost"):
        L.append("| %s | %s | %s |" % (axis, _n(axes.get(axis, 0)), weights.get(axis, 0)))
    L.append("")

    L.append("## Capacity")
    L.append("")
    L.append("- Peak: %s rps | Average: %s rps | Growth: %s%%"
             % (_n(_peak(result)), _n(_avg(result)), _n(f.get("growth_pct_12mo", 0), 0)))
    L.append("- Per-instance ceiling: %s rps (binding constraint: %s)"
             % (_n(cap.get("mu_instance", 0)), cap.get("binding_constraint", "n/a")))
    L.append("- Fleet ceiling: %s rps across %s instances | Running: %s instances"
             % (_n(cap.get("max_cluster_rps", 0)), f.get("instances_max", 0), f.get("instances_current", 0)))
    L.append("- Utilisation at peak: %s | At growth: %s"
             % (_pct(cap.get("util_at_peak", 0)), _pct(cap.get("util_at_growth", 0))))
    L.append("- Little's Law: %s concurrent needed, %s provided"
             % (_n(cap.get("required_concurrency", 0)), _n(cap.get("provided_concurrency", 0))))
    L.append("- Recommended fleet: %s instances now, %s instances at 12 months"
             % (cap.get("recommended_instances", 0), cap.get("recommended_instances_12mo", 0)))
    L.append("")

    L.append("## Latency")
    L.append("")
    L.append("- Budget: %sms | Modelled p99: %sms | Headroom: %sms"
             % (_n(lat.get("budget_ms", 0), 0), _n(lat.get("p99_model_ms", 0), 0), _n(lat.get("headroom_ms", 0), 0)))
    L.append("- Breakdown: CPU %sms | DB %sms | downstream %sms | queue %sms"
             % (_n(comp.get("cpu_ms", 0), 0), _n(comp.get("db_ms", 0), 0),
                _n(comp.get("downstream_ms", 0), 0), _n(comp.get("queue_ms", 0), 0)))
    L.append("- Queue model: rho %s, p(wait) %s, tail amplification %sx"
             % (_n(lat.get("rho", 0), 2), _pct(lat.get("queue_prob_wait", 0)), _n(lat.get("tail_amp", 0), 1)))
    L.append("")

    L.append("## Reliability")
    L.append("")
    L.append("- Target: %s%% | Error budget: %s%% (%s min/month allowed)"
             % (_n(rel.get("target", 0), 2), _n(rel.get("error_budget_pct", 0), 2), _n(rel.get("allowed_downtime_min", 0), 0)))
    L.append("- Consumed: %s%% | Budget days left: %s"
             % (_n(rel.get("consumed_pct", 0), 0), _n(rel.get("days_left", 0), 1)))
    L.append("- Topology: %s AZ(s), %s region(s)" % (f.get("az_count", 0), f.get("region_count", 0)))
    L.append("")

    L.append("## Data")
    L.append("")
    L.append("- Pool per instance: %s | Total at full scale: %s of %s (%s)"
             % (data.get("pool_per_instance", 0), data.get("total_connections", 0),
                data.get("db_max_connections", 0), _pct(data.get("saturation", 0))))
    L.append("- DB time per request: %sms | Cache hit ratio: %s"
             % (_n(data.get("db_time_ms", 0), 0), _pct(data.get("cache_hit_ratio", 0))))
    L.append("")

    L.append("## Autoscale")
    L.append("")
    L.append("- Min %s / current %s / max %s | Pattern: %s"
             % (auto.get("instances_min", 0), auto.get("instances_current", 0),
                auto.get("instances_max", 0), auto.get("traffic_pattern", "steady")))
    L.append("- Scale-out window: %ss | Can add %s rps/s, needs %s rps/s -- %s"
             % (_n(auto.get("scale_out_s", 0), 0), _n(auto.get("max_scale_rate", 0), 1),
                _n(auto.get("required_scale_rate", 0), 1),
                "feasible" if auto.get("feasible") else "NOT feasible"))
    L.append("")

    L.append("## Cost")
    L.append("")
    L.append("- Monthly requests: %s | Egress: %s GB (%s)"
             % (_n(cost.get("monthly_requests", 0), 0), _n(cost.get("egress_gb", 0), 0), _usd(cost.get("egress_cost", 0))))
    L.append("- Compute: %s/mo | Total: %s/mo | Cost per million requests: %s"
             % (_usd(cost.get("compute_cost", 0)), _usd(cost.get("total_cost", 0)), _usd(cost.get("cost_per_million", 0))))
    L.append("- Idle capacity: %s | Storage growth: %s GB/yr"
             % (_pct(cost.get("idle_ratio", 0)), _n(sto.get("year_gb", 0), 0)))
    L.append("")

    findings = result.get("findings", [])
    L.append("## Findings (%d)" % len(findings))
    L.append("")
    if not findings:
        L.append("_No findings._")
    else:
        for fnd in findings:
            L.append("### [%s] %s" % (fnd["severity"].upper(), fnd["title"]))
            L.append("")
            L.append("- **Evidence:** %s" % fnd["evidence"])
            L.append("- **Fix:** %s" % fnd["fix"])
            if fnd.get("impact"):
                L.append("- **Impact:** %s" % fnd["impact"])
            L.append("")

    strengths = result.get("strengths", [])
    if strengths:
        L.append("## What is working")
        L.append("")
        for s in strengths:
            L.append("- %s" % s)
        L.append("")

    plan = result.get("fix_plan", [])
    L.append("## Prioritised fix plan")
    L.append("")
    if not plan:
        L.append("_Nothing to do._")
    else:
        for item in plan:
            L.append("%d. **%s**" % (item["step"], item["action"]))
            L.append("   - Why: %s" % item["rationale"])
            L.append("   - Effect: %s" % item["impact"])
    L.append("")

    if brief:
        L.append("---")
        L.append("")
        L.append("### Original brief")
        L.append("")
        L.append(FENCE)
        L.append(brief.strip())
        L.append(FENCE)
        L.append("")

    L.append("_Generated by capacity-forge. Model: Little's Law + M/M/c Erlang-C queueing + "
             "empirical tail-amplification curve + availability error budget._")
    return "\n".join(L)
