"""Rule library for capacity-forge.

Every rule produces a finding with: id, severity, axis, title, the evidence
that triggered it, a concrete fix, and the impact of not fixing it.
No network, no LLM -- just arithmetic against the brief.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

SEV_ORDER = {"high": 0, "medium": 1, "low": 2}


def _pct(v: float) -> str:
    return "%0.0f%%" % (v * 100.0)


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


def F(fid: str, severity: str, axis: str, title: str, evidence: str, fix: str, impact: str = "") -> Dict[str, Any]:
    return {
        "id": fid,
        "severity": severity,
        "axis": axis,
        "title": title,
        "evidence": evidence,
        "fix": fix,
        "impact": impact,
    }


# --------------------------------------------------------------------------
# findings
# --------------------------------------------------------------------------
def build_findings(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    f = m["fields"]
    out: List[Dict[str, Any]] = []

    util = m["util_at_peak"]
    util_cur = m["util_current"]
    util_growth = m["util_at_growth"]
    peak = m["peak_rps"]
    mu = m["mu_instance"]
    budget = m["budget_ms"]
    comp = m["latency_components"]

    # ---------------- capacity / throughput ----------------
    if m["queue_unstable"]:
        out.append(F(
            "over_capacity", "high", "throughput",
            "The fleet cannot serve the briefed peak",
            "Peak is %s rps but %d instances x %s rps each = %s rps of ceiling. "
            "Offered load exceeds service rate, so the queue grows without bound."
            % (_n(peak), f["instances_max"], _n(m["max_cluster_rps"] / max(1, f["instances_max"])), _n(m["max_cluster_rps"])),
            "Raise the autoscaler ceiling to at least %d instances (%s rps ceiling) for a "
            "35%% utilisation target, or lower the peak you promise." % (
                m["recommended_instances"], _n(m["recommended_instances"] * mu)),
            "Requests queue indefinitely, latency goes to infinity and the client sees timeouts.",
        ))
    elif util >= 1.0:
        out.append(F(
            "over_capacity", "high", "throughput",
            "Peak demand exceeds the fleet ceiling",
            "Peak %s rps against a ceiling of %s rps (%d instances at %s rps each)."
            % (_n(peak), _n(m["max_cluster_rps"]), f["instances_max"], _n(mu)),
            "Scale the ceiling to %d instances to hold peak near %s%% utilisation."
            % (m["recommended_instances"], _pct(0.65)),
            "Every request past the ceiling adds queue delay instead of throughput.",
        ))
    elif util >= 0.85:
        out.append(F(
            "saturation_zone", "high", "throughput",
            "Peak sits in the queueing knee",
            "Utilisation at peak is %s -- above the ~70%% point where queueing delay starts "
            "growing faster than load." % _pct(util),
            "Add headroom: %d instances holds the same peak at %s%% utilisation."
            % (m["recommended_instances"], _pct(0.65)),
            "Tail latency multiplies (p99 model %sms) before you actually overload."
            % _n(m["p99_model_ms"], 0),
        ))
    elif util >= 0.70:
        out.append(F(
            "thin_headroom", "medium", "throughput",
            "Headroom at peak is thin",
            "Utilisation at peak is %s (target is under 70%% for a queueing-sensitive service)."
            % _pct(util),
            "Pre-scale to %d instances so peak sits at %s%%."
            % (m["recommended_instances"], _pct(0.65)),
            "Any traffic above the brief turns a queue into an outage.",
        ))

    if util_cur >= 1.0 and not m["queue_unstable"]:
        out.append(F(
            "current_below_peak", "high", "throughput",
            "Today's fleet cannot handle today's peak",
            "%d running instances cover %s rps but peak is %s rps."
            % (f["instances_current"], _n(m["current_cluster_rps"]), _n(peak)),
            "Either raise the minimum replica count to %d or let the autoscaler start at a "
            "higher floor and pre-warm it." % m["recommended_instances"],
            "The autoscaler has to react from scratch at the exact moment traffic arrives.",
        ))

    if util_growth >= 1.0:
        out.append(F(
            "growth_overrun", "high", "throughput",
            "Twelve months of growth overflows the ceiling",
            "With %s%% growth the peak becomes %s rps against a %s rps ceiling (%s%% utilisation)."
            % (_n(f["growth_pct_12mo"], 0), _n(peak * (1 + f["growth_pct_12mo"] / 100.0)),
               _n(m["max_cluster_rps"]), _pct(util_growth)),
            "Plan for %d instances at the growth target (%s%% utilisation)."
            % (m["recommended_instances_12mo"], _pct(0.65)),
            "The growth lands as an outage, not as a slow burn.",
        ))
    elif f["growth_pct_12mo"] >= 25 and util_growth > 0.7:
        out.append(F(
            "growth_no_room", "medium", "throughput",
            "Growth eats the headroom you have",
            "At %s%% growth the fleet moves from %s%% to %s%% utilisation with no new hardware."
            % (_n(f["growth_pct_12mo"], 0), _pct(util), _pct(util_growth)),
            "Budget headroom now: %d instances, not %d."
            % (m["recommended_instances_12mo"], m["recommended_instances"]),
            "Headroom that is consumed by growth was never headroom.",
        ))

    if f["instances_max"] <= f["instances_current"]:
        out.append(F(
            "no_autoscale_headroom", "medium", "scalability",
            "There is no autoscaling headroom",
            "Min and max are both %d instances, so the fleet is fixed size."
            % f["instances_current"],
            "Set a ceiling of at least %d instances (%dx the running count)."
            % (max(f["instances_max"] * 3, m["recommended_instances"]),
               max(3, int(m["recommended_instances"] / max(1, f["instances_current"])))),
            "A single traffic surge has nowhere to go.",
        ))

    if m["required_concurrency"] > 0 and m["concurrency_ratio"] < 1.0:
        out.append(F(
            "concurrency_shortfall", "high", "throughput",
            "Not enough in-flight slots for the peak",
            "Little's Law: %s rps x %sms occupancy needs %s concurrent requests; the fleet offers %s."
            % (_n(peak), _n(f["p50_latency_ms"] or comp["total_ms"], 0),
               _n(m["required_concurrency"]), _n(m["provided_concurrency"])),
            "Raise per-instance concurrency or the replica count to %d instances."
            % m["recommended_instances"],
            "Requests are rejected or queued before the CPU is even busy.",
        ))

    if m["queue_wait_ms"] > 0 and budget > 0 and (m["queue_wait_ms"] / budget) > 0.25:
        out.append(F(
            "queue_amplification", "high", "latency",
            "Queueing delay eats the latency budget",
            "Modelled queue wait is %sms at %s%% utilisation (%s%% of the %sms budget)."
            % (_n(m["queue_wait_ms"], 0), _pct(util), _pct(m["queue_wait_ms"] / budget), _n(budget, 0)),
            "Add capacity until utilisation drops under 70%% -- queue wait falls non-linearly.",
            "p99 lands at %sms against a %sms budget." % (_n(m["p99_model_ms"], 0), _n(budget, 0)),
        ))

    if m["tail_amp"] >= 3.0 and not m["queue_unstable"]:
        out.append(F(
            "tail_multiplier", "medium", "latency",
            "The tail is going to amplify",
            "At %s%% utilisation the empirical p99/p50 multiplier is %sx."
            % (_pct(util), _n(m["tail_amp"], 1)),
            "Target under 70%% utilisation, or accept %sms modelled p99 against a %sms budget."
            % (_n(m["p99_model_ms"], 0), _n(budget, 0)),
            "Your p50 looks fine in the dashboard while p99 breaks the SLO.",
        ))

    if m["binding_constraint"] == "cpu":
        out.append(F(
            "cpu_bound", "low", "throughput",
            "CPU is the binding constraint, not concurrency",
            "One instance tops out at %s rps on CPU (%s rps if concurrency were unlimited)."
            % (_n(m["cpu_rps_instance"]), _n(m["conc_rps_instance"])),
            "Profile and cut the %sms of CPU per request before adding more instances."
            % _n(f["cpu_ms_per_request"], 1),
            "More replicas buy linearly more CPU spend for the same throughput.",
        ))
    else:
        out.append(F(
            "concurrency_bound", "low", "throughput",
            "Concurrency is the binding constraint",
            "One instance tops out at %s rps on concurrency (%s rps if CPU were unlimited)."
            % (_n(m["conc_rps_instance"]), _n(m["cpu_rps_instance"])),
            "Raise the worker pool or thread limit, or split the service.",
            "Extra vCPUs sit idle while requests wait for a free slot.",
        ))

    # ---------------- latency ----------------
    if m["budget_util"] >= 1.0:
        out.append(F(
            "latency_budget_exceeded", "high", "latency",
            "Modelled latency breaches the budget",
            "Modelled p99 is %sms against a %sms budget (%s%% of budget used)."
            % (_n(m["p99_model_ms"], 0), _n(budget, 0), _pct(m["budget_util"])),
            "Budget = %sms CPU + %sms DB + %sms downstream + %sms queue. Attack the biggest slice first."
            % (_n(comp["cpu_ms"], 0), _n(comp["db_ms"], 0), _n(comp["downstream_ms"], 0), _n(comp["queue_ms"], 0)),
            "The SLO is missed continuously, not occasionally.",
        ))
    elif m["budget_util"] > 0.8:
        out.append(F(
            "latency_budget_tight", "medium", "latency",
            "The latency budget is nearly used up",
            "Modelled p99 is %sms of a %sms budget (%s%% used)."
            % (_n(m["p99_model_ms"], 0), _n(budget, 0), _pct(m["budget_util"])),
            "Only %sms of headroom left for queueing." % _n(m["budget_headroom_ms"], 0),
            "One slow dependency consumes the remaining headroom.",
        ))

    if budget > 0 and (comp["downstream_ms"] / budget) > 0.5:
        out.append(F(
            "downstream_dominates", "medium", "latency",
            "Downstream calls dominate the budget",
            "Downstream time is %sms of a %sms budget (%s%%)."
            % (_n(comp["downstream_ms"], 0), _n(budget, 0), _pct(comp["downstream_ms"] / budget)),
            "Fan the calls out in parallel, cache them, or move them off the critical path.",
            "You are paying for someone else's latency on every request.",
        ))

    if budget > 0 and (comp["db_ms"] / budget) > 0.4:
        out.append(F(
            "db_dominates", "medium", "latency",
            "Database time dominates the budget",
            "DB time is %sms of a %sms budget (%s%%) across %s reads and %s writes per request."
            % (_n(comp["db_ms"], 0), _n(budget, 0), _pct(comp["db_ms"] / budget),
               _n(f["db_reads_per_request"], 0), _n(f["db_writes_per_request"], 0)),
            "Add indexes on the hot read path, cut N+1 queries, or cache the repeated reads.",
            "The database becomes the ceiling on your whole service.",
        ))

    if f["db_reads_per_request"] >= 2 and (f["cache_hit_ratio"] or 0.0) < 0.5:
        out.append(F(
            "missing_cache", "medium", "latency",
            "Repeated reads are not being cached",
            "%s reads per request at a %s%% cache hit ratio."
            % (_n(f["db_reads_per_request"], 0), _pct(f["cache_hit_ratio"] or 0.0)),
            "A cache at 80%% hit rate removes %sms of DB time per request."
            % _n(f["db_reads_per_request"] * 0.8 * f["db_query_ms"], 0),
            "You pay full query cost on every single request.",
        ))
    elif (f["cache_hit_ratio"] or 0.0) < 0.3 and f["db_reads_per_request"] >= 1:
        out.append(F(
            "cache_hit_low", "low", "latency",
            "Cache hit rate is low",
            "Only %s%% of reads are served from cache." % _pct(f["cache_hit_ratio"] or 0.0),
            "Most of the caching benefit is unrealised; check TTLs and key design.",
            "Cache spend without cache benefit.",
        ))

    tr = m["tail_ratio_observed"]
    if tr > 10:
        out.append(F(
            "tail_ratio_poor", "medium", "latency",
            "The tail is far worse than the median",
            "p99 %sms against p50 %sms is a %sx ratio." % (_n(f["p99_latency_ms"], 0), _n(f["p50_latency_ms"], 0), _n(tr, 1)),
            "Hunt the slow path: cold caches, lock contention, GC pauses, cold starts.",
            "p50 dashboards stay green while users feel the service is slow.",
        ))
    elif f["p99_latency_ms"] > 0 and f["p50_latency_ms"] == 0:
        out.append(F(
            "p50_missing", "low", "latency",
            "No p50 was given",
            "Only p99 (%sms) is briefed, so the tail ratio cannot be computed."
            % _n(f["p99_latency_ms"], 0),
            "Add p50 to the brief -- the ratio between them is where the real problems live.",
            "You cannot tell a queueing problem from a slow dependency without it.",
        ))

    if f["p99_latency_ms"] > 0 and m["p99_model_ms"] > 0 and (f["p99_latency_ms"] / m["p99_model_ms"]) > 4.0:
        out.append(F(
            "unmodelled_tail", "medium", "latency",
            "The real tail is much worse than the brief predicts",
            "Observed p99 is %sms while the brief's own numbers model %sms (CPU + DB + downstream + queue)."
            % (_n(f["p99_latency_ms"], 0), _n(m["p99_model_ms"], 0)),
            "The gap is what the model cannot see: GC pauses, lock waits, connection-pool waits, "
            "cold caches and dependency variance. Profile the tail, do not add replicas first.",
            "p50 stays green while users actually wait %sms." % _n(f["p99_latency_ms"], 0),
        ))

    if f["downstream_calls"] >= 3:
        out.append(F(
            "serial_downstream", "medium", "latency",
            "Several downstream calls on the critical path",
            "%d downstream dependencies are called per request." % f["downstream_calls"],
            "Call them in parallel, or collapse them behind one aggregated endpoint.",
            "Latency stacks up as the sum of every dependency.",
        ))

    if f["downstream_calls"] > 0 and f["has_circuit_breaker"] is False:
        out.append(F(
            "no_circuit_breaker", "medium", "reliability",
            "No circuit breaker on the downstream calls",
            "The brief states there is no circuit breaker while %d downstream dependencies are called."
            % f["downstream_calls"],
            "Open the breaker after a failure threshold so a slow dependency stops burning threads.",
            "One sick dependency consumes the whole worker pool.",
        ))

    if f["downstream_calls"] > 0 and f["has_retry_budget"] is False:
        out.append(F(
            "no_retry_budget", "medium", "reliability",
            "Retries have no budget",
            "The brief states there is no retry budget across %d downstream calls."
            % f["downstream_calls"],
            "Cap retries as a fraction of total requests (5-10%%) and add jitter.",
            "A retry storm multiplies load exactly when the system is already sick.",
        ))

    # ---------------- data / connections ----------------
    if m["conn_saturation"] > 0.8:
        stated = bool(f.get("db_max_connections_stated"))
        ratio = m["conn_saturation"]
        if not stated:
            sev = "low"
        elif ratio >= 2.0:
            sev = "high"
        else:
            sev = "medium"
        assume = "" if stated else " -- the brief states no limit, so the common default of %d is assumed" % f["db_max_connections"]
        out.append(F(
            "pool_saturation", sev, "reliability",
            "The connection pool saturates the database",
            "%d connections at full scale (%d per instance x %d instances) against a %d connection limit (%s)%s."
            % (m["total_connections"], m["pool_per_instance"], f["instances_max"],
               f["db_max_connections"], _pct(m["conn_saturation"]), assume),
            "Use a pooler (PgBouncer), drop the per-instance pool to %d, or raise max_connections."
            % max(2, int(f["db_max_connections"] * 0.7 / max(1, f["instances_max"]))),
            "New connections get rejected and every request fails at once.",
        ))
    elif m["conn_saturation"] > 0.6:
        out.append(F(
            "pool_tight", "low", "reliability",
            "Connections are getting close to the database limit",
            "%d of %d connections (%s%%) at full scale."
            % (m["total_connections"], f["db_max_connections"], _pct(m["conn_saturation"])),
            "Leave at least 20%% of max_connections free for admin and failover.",
            "A rolling deploy doubles connection pressure for a moment.",
        ))

    if m["db_mem_mb"] > m["instance_mem_mb"]:
        out.append(F(
            "db_memory_overcommit", "high", "throughput",
            "Connection memory exceeds the instance memory",
            "At full scale the pool could hold %s MB of request memory against %s MB of RAM per instance."
            % (_n(m["db_mem_mb"], 0), _n(m["instance_mem_mb"], 0)),
            "Cut the pool per instance to %d or move to %s GB instances."
            % (max(2, int(m["instance_mem_mb"] / max(1.0, f["ram_mb_per_request"]))),
               _n(m["db_mem_mb"] / 1024.0, 1)),
            "The process gets OOM-killed exactly when traffic is highest.",
        ))

    if f["db_writes_per_request"] > 0 and f["db_replicated"] is False:
        out.append(F(
            "db_no_replication", "high", "reliability",
            "Writes go to a single primary with no failover",
            "%s writes per request and the brief describes a single primary."
            % _n(f["db_writes_per_request"], 0),
            "Add a standby with automated failover, or move writes to a queue so they can replay.",
            "A primary failover is a full write outage.",
        ))

    if f["db_writes_per_request"] >= 2:
        out.append(F(
            "write_amplification", "medium", "throughput",
            "Several writes per request",
            "%s writes per request multiply connection usage and lock contention."
            % _n(f["db_writes_per_request"], 0),
            "Batch the writes, or do them asynchronously via a durable queue.",
            "Write latency and pool pressure scale together.",
        ))

    # ---------------- reliability ----------------
    if m["consumed_pct"] >= 100:
        out.append(F(
            "error_budget_burned", "high", "reliability",
            "The error budget for this period is already spent",
            "A %s%% error rate against a %s%% budget (%s%% of the monthly budget consumed)."
            % (_n(f["current_error_rate_pct"], 2), _n(m["error_budget_pct"], 2), _n(m["consumed_pct"], 0)),
            "Get the error rate under %s%% to stay inside the budget."
            % _n(m["error_budget_pct"] * 0.5, 3),
            "Every further incident comes out of next month's budget.",
        ))
    elif m["consumed_pct"] >= 75:
        out.append(F(
            "error_budget_tight", "medium", "reliability",
            "Most of the error budget is already gone",
            "%s%% of the monthly error budget is consumed at a %s%% error rate (budget %s%%)."
            % (_n(m["consumed_pct"], 0), _n(f["current_error_rate_pct"], 2), _n(m["error_budget_pct"], 2)),
            "Only %s days of budget remain in the period."
            % _n(m["budget_days_left"], 1),
            "You cannot absorb another incident this month.",
        ))

    if f["current_error_rate_pct"] > 1.0:
        out.append(F(
            "error_rate_high", "high", "reliability",
            "The error rate is above one percent",
            "%s%% of requests are failing." % _n(f["current_error_rate_pct"], 2),
            "Fix the top failure mode before adding capacity -- capacity does not help a broken path.",
            "Users see failures that no amount of scaling will remove.",
        ))

    if f["az_count"] < 2:
        out.append(F(
            "single_az", "high", "reliability",
            "Everything runs in one availability zone",
            "%d availability zone and %d region(s) described."
            % (f["az_count"], f["region_count"]),
            "Spread replicas across at least 3 zones behind one load balancer.",
            "Any zone-level event is a total outage, not a degradation.",
        ))
    elif f["az_count"] == 2:
        out.append(F(
            "two_az", "medium", "reliability",
            "Only two availability zones",
            "%d zones means one zone can be lost without taking the service down." % f["az_count"],
            "Add a third zone so a zone outage keeps headroom.",
            "Losing a zone during a zone outage leaves you at %s%% utilisation."
            % _pct(min(1.5, util * 1.5)),
        ))

    if f["availability_target"] >= 99.99 and f["region_count"] < 2:
        out.append(F(
            "slo_needs_region", "high", "reliability",
            "A four-nines target cannot be met in one region",
            "%s%% availability target with %d region(s)." % (_n(f["availability_target"], 2), f["region_count"]),
            "Active-active across two regions, or relax the target to three nines.",
            "A single region outage breaks the SLO in one event.",
        ))

    if f["has_rate_limit"] is False:
        out.append(F(
            "no_rate_limit", "medium", "reliability",
            "No rate limiting on the service",
            "The brief states requests are not rate limited.",
            "Add a per-tenant token bucket so one client cannot consume the fleet.",
            "A single runaway client can take down capacity for everyone.",
        ))

    if f["is_stateless"] is False:
        out.append(F(
            "stateful_instances", "medium", "scalability",
            "Instances are stateful, so scaling out is lossy",
            "The brief describes stateful instances.",
            "Externalise the session/state (sticky-free) so replicas are interchangeable.",
            "Scale-out loses in-flight state and capacity grows unevenly.",
        ))

    # ---------------- scale out ----------------
    if not m["scale_feasible"]:
        out.append(F(
            "scale_out_too_slow", "high", "scalability",
            "The fleet cannot scale fast enough for the traffic pattern",
            "Scaling needs %s rps/s; the autoscaler can only add %s rps/s (%ss of startup plus readiness)."
            % (_n(m["required_scale_rate"], 1), _n(m["max_scale_rate"], 1), _n(m["scale_out_s"], 0)),
            "Pre-scale before the event, reduce startup time, or add warm capacity.",
            "The surge arrives before the autoscaler does.",
        ))

    if f["traffic_pattern"] == "spiky" and f["startup_seconds"] > 45:
        out.append(F(
            "slow_cold_start", "medium", "scalability",
            "Cold starts are slow on a spiky service",
            "%ss startup plus %ss readiness on a %s traffic pattern."
            % (_n(f["startup_seconds"], 0), _n(f["readiness_seconds"], 0), f["traffic_pattern"]),
            "Keep a warm floor of replicas, or pre-scale on the event schedule.",
            "New replicas arrive after the peak has already peaked.",
        ))

    if f["traffic_pattern"] == "spiky" and f["spike_window_minutes"] > 0 and m["spike_ramp_rate"] > 0:
        out.append(F(
            "spike_estimate", "low", "scalability",
            "The spike ramp is %s rps/s" % _n(m["spike_ramp_rate"], 1),
            "Going from %s to %s rps inside %s minutes." % (_n(m["avg_rps"]), _n(peak), _n(f["spike_window_minutes"], 1)),
            "Scale-out must sustain %s rps/s; your ceiling delivers %s rps/s."
            % (_n(m["spike_ramp_rate"], 1), _n(m["max_scale_rate"], 1)),
            "The gap between ramp rate and scale rate is the queue you will serve.",
        ))

    # ---------------- cost ----------------
    if m["monthly_requests"] > 0 and m["cost_per_million"] > 20:
        out.append(F(
            "cost_per_million_high", "medium", "cost",
            "Cost per million requests is high",
            "%s per million requests (compute %s + egress %s per month)."
            % (_usd(m["cost_per_million"]), _usd(m["compute_cost"]), _usd(m["egress_cost"])),
            "Trim idle capacity or shrink the payload -- the per-request cost is what compounds.",
            "Every volume increase multiplies the bill.",
        ))

    if m["idle_ratio"] > 0.7 and f["traffic_pattern"] in ("steady", "ramp"):
        out.append(F(
            "idle_capacity", "medium", "cost",
            "Most of the fleet sits idle",
            "Average load uses %s of the running fleet (%s%% idle)."
            % (_pct(1 - m["idle_ratio"]), _pct(m["idle_ratio"])),
            "Drop the minimum replica count or shrink the instance type.",
            "You pay for capacity that only a spike would use.",
        ))

    if m["egress_gb"] > 500:
        out.append(F(
            "egress_heavy", "medium", "cost",
            "Egress is a large line item",
            "%s GB per month at %s per GB = %s."
            % (_n(m["egress_gb"], 0), _usd(f["egress_per_gb"]), _usd(m["egress_cost"])),
            "Compress responses, drop unused fields, or serve large payloads from object storage.",
            "Egress usually outgrows compute cost as traffic grows.",
        ))

    if m["storage_year_gb"] > 500:
        out.append(F(
            "storage_growth_unmanaged", "medium", "cost",
            "Storage grows by %s GB a year" % _n(m["storage_year_gb"], 0),
            "%s rows a day at %s bytes a row." % (_n(f["rows_per_day"], 0), _n(f["bytes_per_row"], 0)),
            "Define retention, partition by time, and archive cold data.",
            "Storage cost and backup windows grow without anyone deciding to let them.",
        ))

    if f["payload_kb_resp"] > 100:
        out.append(F(
            "payload_heavy", "low", "cost",
            "Responses are large",
            "%s KB per response on average." % _n(f["payload_kb_resp"], 0),
            "Return only what the caller needs; paginate list responses.",
            "Payload size multiplies both egress and serialisation cost.",
        ))

    # ---------------- brief quality ----------------
    if peak <= 0:
        out.append(F(
            "no_traffic_numbers", "high", "throughput",
            "The brief has no traffic numbers",
            "No peak or average rps could be read from the brief.",
            "Add the peak requests per second -- without it nothing can be sized.",
            "Every other number in the report is a guess.",
        ))

    if f["instances_max"] == f["instances_current"] and f["growth_pct_12mo"] > 0:
        out.append(F(
            "growth_without_plan", "low", "scalability",
            "Growth is expected but the fleet size is fixed",
            "%s%% growth planned against a fixed fleet of %d instances."
            % (_n(f["growth_pct_12mo"], 0), f["instances_current"]),
            "Make the ceiling growth-aware so the autoscaler has somewhere to go.",
            "The ceiling becomes the ceiling on revenue too.",
        ))

    out.sort(key=lambda x: (SEV_ORDER.get(x["severity"], 3), x["id"]))
    return out


# --------------------------------------------------------------------------
# strengths
# --------------------------------------------------------------------------
def build_strengths(m: Dict[str, Any]) -> List[str]:
    f = m["fields"]
    good: List[str] = []
    if 0 < m["util_at_peak"] <= 0.7:
        good.append("Peak utilisation is %s -- inside the safe zone for queueing." % _pct(m["util_at_peak"]))
    if m["concurrency_ratio"] >= 1.5:
        good.append("Concurrency slots are %sx the Little's Law requirement." % _n(m["concurrency_ratio"], 1))
    if m["consumed_pct"] <= 40:
        good.append("Only %s%% of the monthly error budget is consumed." % _n(m["consumed_pct"], 0))
    if f["az_count"] >= 3:
        good.append("Replicas span %d availability zones." % f["az_count"])
    if f["region_count"] >= 2:
        good.append("Running across %d regions." % f["region_count"])
    if m["budget_headroom_ms"] > 0:
        good.append("Latency budget keeps %sms of headroom." % _n(m["budget_headroom_ms"], 0))
    if (f["cache_hit_ratio"] or 0) >= 0.7:
        good.append("Cache hit ratio of %s keeps DB time off the critical path." % _pct(f["cache_hit_ratio"] or 0))
    if m["scale_feasible"] and f["instances_max"] > f["instances_current"]:
        good.append("The autoscaler can add %s rps/s, more than the %s rps/s the traffic pattern needs."
                    % (_n(m["max_scale_rate"], 1), _n(m["required_scale_rate"], 1)))
    if m["monthly_requests"] > 0 and m["cost_per_million"] < 2:
        good.append("Cost per million requests is %s." % _usd(m["cost_per_million"]))
    if f["is_stateless"] is True:
        good.append("Instances are stateless, so replicas are interchangeable.")
    if not good:
        good.append("Nothing stood out as a clear strength -- the numbers themselves are the problem.")
    return good


# --------------------------------------------------------------------------
# fix plan
# --------------------------------------------------------------------------
def build_fix_plan(m: Dict[str, Any]) -> List[Dict[str, Any]]:
    f = m["fields"]
    mu = m["mu_instance"]
    plan: List[Dict[str, Any]] = []

    def add(priority: int, action: str, rationale: str, impact: str) -> None:
        plan.append({"priority": priority, "action": action, "rationale": rationale, "impact": impact})

    if m["queue_unstable"] or m["util_at_peak"] >= 0.85:
        need = m["recommended_instances"]
        add(1,
            "Raise the autoscaler ceiling to %d instances" % need,
            "Peak %s rps at %s%% utilisation needs %s rps of capacity; %d instances deliver %s rps."
            % (_n(m["peak_rps"]), _pct(m["util_at_peak"]), _n(m["peak_rps"] / 0.65), need, _n(need * mu)),
            "Drops peak utilisation to about %s%% and removes the queueing knee." % _pct(0.65))
        add(2,
            "Cut %sms of CPU per request" % _n(f["cpu_ms_per_request"], 0),
            "CPU is the %s constraint at %s rps per instance." % (m["binding_constraint"], _n(mu)),
            "Each 1ms of CPU removed buys roughly %s more rps per instance." % _n(mu / max(1.0, f["cpu_ms_per_request"])))

    if m["consumed_pct"] >= 75:
        add(1,
            "Bring the error rate under %s%%" % _n(m["error_budget_pct"] * 0.5, 3),
            "Budget is %s%% consumed at a %s%% error rate." % (_n(m["consumed_pct"], 0), _n(f["current_error_rate_pct"], 2)),
            "Stops the budget bleeding before the next incident.")

    if f["db_reads_per_request"] >= 2 and (f["cache_hit_ratio"] or 0) < 0.5:
        add(2,
            "Put a cache in front of the read path (target 80%% hit rate)",
            "%s reads per request at %s%% hit rate." % (_n(f["db_reads_per_request"], 0), _pct(f["cache_hit_ratio"] or 0)),
            "Removes about %sms of DB time per request and %s of the connection pressure."
            % (_n(f["db_reads_per_request"] * 0.8 * f["db_query_ms"], 0),
               _pct(0.8 * f["db_reads_per_request"] / max(1.0, f["db_reads_per_request"] + f["db_writes_per_request"]))))

    if m["conn_saturation"] > 0.8:
        add(1,
            "Drop the per-instance pool to %d or put a pooler in front"
            % max(2, int(f["db_max_connections"] * 0.7 / max(1, f["instances_max"]))),
            "%d connections at full scale against a %d limit." % (m["total_connections"], f["db_max_connections"]),
            "Keeps the database from refusing connections during the worst moment.")

    if f["az_count"] < 2:
        add(1,
            "Spread the fleet across at least 3 availability zones",
            "Single zone today.",
            "Turns a zone outage from an outage into a capacity event.")
    elif f["az_count"] == 2:
        add(3,
            "Add a third availability zone",
            "Two zones leave no headroom during a zone outage.",
            "Survives a zone failure at %s%% utilisation." % _pct(min(1.0, m["util_at_peak"] * 1.0)))

    if not m["scale_feasible"]:
        add(2,
            "Pre-scale on the traffic schedule instead of reacting to it",
            "Scale-out delivers %s rps/s but the pattern needs %s rps/s."
            % (_n(m["max_scale_rate"], 1), _n(m["required_scale_rate"], 1)),
            "Removes the ramp gap that becomes a queue.")

    if f["downstream_calls"] >= 2:
        add(3,
            "Parallelise or collapse the %d downstream calls" % f["downstream_calls"],
            "They stack %sms onto the critical path." % _n(m["latency_components"]["downstream_ms"], 0),
            "Latency moves from the sum of the dependencies to the slowest one.")

    if m["idle_ratio"] > 0.7:
        add(4,
            "Reduce the minimum replica count or shrink the instance type",
            "%s%% of the running fleet is idle at average load." % _pct(m["idle_ratio"]),
            "Cuts about %s per month of idle compute."
            % _usd(m["idle_ratio"] * m["compute_cost"]))

    if m["egress_gb"] > 500:
        add(4,
            "Shrink the response payload (fields, compression, pagination)",
            "%s GB of egress a month." % _n(m["egress_gb"], 0),
            "Egress scales with traffic; payload size scales with your API design.")

    if m["storage_year_gb"] > 500:
        add(4,
            "Set a retention policy and archive old partitions",
            "%s GB a year with no stated retention." % _n(m["storage_year_gb"], 0),
            "Caps both storage spend and backup window.")

    if f["growth_pct_12mo"] >= 25:
        add(3,
            "Plan the fleet for %d instances a year out"
            % m["recommended_instances_12mo"],
            "%s%% growth moves utilisation to %s%%." % (_n(f["growth_pct_12mo"], 0), _pct(m["util_at_growth"])),
            "Growth arrives as capacity, not as an incident.")

    if f["has_rate_limit"] is False:
        add(3, "Add per-tenant rate limiting",
            "No rate limit protects the fleet from one noisy client.",
            "Keeps a single tenant from consuming shared capacity.")

    plan.sort(key=lambda x: x["priority"])
    for i, item in enumerate(plan, 1):
        item["step"] = i
    return plan
