"""Smoke tests for capacity-forge."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import engine  # noqa: E402
import exporters  # noqa: E402
import samples  # noqa: E402


WELL_SIZED = """
Well sized read heavy API. Traffic is steady. Average traffic is 200 rps, peak traffic is 260 rps.
We run 10 instances, autoscaling 5 to 30. Each instance is 2 vcpu with 4 gb ram,
max concurrency 100, cpu cost 8 ms per request, startup 20 seconds.
p50 latency is 40ms, p99 latency is 120ms, latency budget 250ms.
No downstream calls. The database does 2 reads per request at 5ms per query, no writes.
Cache hit ratio is 90%. Payload is 1kb request and 4kb response.
Availability target 99.95%, current error rate 0.01%. Three AZs across two regions.
The database is replicated with failover. There is a rate limit and a circuit breaker,
and there is a retry budget. Storage 100k rows per day at 500 bytes per row.
Cost is $0.05 per instance hour, egress is $0.02 per GB.
"""

OVERLOADED = """
Tiny API that cannot take its own traffic. Traffic is spiky.
Average traffic is 10 rps, peak traffic is 900 rps.
We run 2 instances, autoscaling 2 to 2. Each instance is 1 vcpu with 2 gb ram,
max concurrency 20, cpu cost 50 ms per request.
p50 latency is 300ms, p99 latency is 4000ms, latency budget 500ms.
One downstream call at 200ms. The database does 6 reads and 3 writes per request at 20ms.
Cache hit ratio is 0%. Payload is 4kb request and 200kb response.
Availability target 99.99%, current error rate 2%. One AZ, one region.
The database is a single primary with no replica. There is no rate limit and no circuit breaker.
Storage 5M rows per day at 2kb per row. Cost is $0.20 per instance hour, egress is $0.09 per GB.
"""


class TestQueueingMath(unittest.TestCase):
    def test_m_m_1_wait_matches_closed_form(self):
        wait, p_wait, rho = engine.erlang_c(4.0, 5.0, 1)
        self.assertAlmostEqual(rho, 0.8, places=6)
        self.assertAlmostEqual(wait, 0.8, places=4)
        self.assertAlmostEqual(p_wait, 0.8, places=6)

    def test_unstable_when_offered_load_exceeds_service(self):
        wait, p_wait, rho = engine.erlang_c(10.0, 5.0, 1)
        self.assertEqual(wait, float("inf"))
        self.assertGreater(rho, 1.0)

    def test_no_wait_when_capacity_is_huge(self):
        wait, p_wait, rho = engine.erlang_c(10.0, 100.0, 50)
        self.assertLess(wait, 0.01)
        self.assertLess(rho, 0.01)

    def test_tail_amplification_is_bounded_and_monotonic(self):
        self.assertAlmostEqual(engine.tail_amplification(0.0), 1.0, places=6)
        prev = 0.0
        for rho in (0.1, 0.3, 0.5, 0.7, 0.9):
            val = engine.tail_amplification(rho)
            self.assertGreater(val, prev)
            prev = val
        self.assertLessEqual(engine.tail_amplification(0.999), 25.0)

    def test_cost_per_million_curve_is_monotonic(self):
        lo = engine.score_cpm(0.2)
        mid = engine.score_cpm(5)
        hi = engine.score_cpm(500)
        self.assertGreater(lo, mid)
        self.assertGreater(mid, hi)
        self.assertLessEqual(hi, 40.0)
        self.assertEqual(engine.score_cpm(5000), 15.0)

    def test_littles_law_concurrency(self):
        res = engine.analyze("peak traffic is 100 rps. p50 latency is 50ms. cpu cost is 1 ms.")
        self.assertAlmostEqual(res["capacity"]["required_concurrency"], 5.0, places=2)


class TestParser(unittest.TestCase):
    def test_parses_key_numbers(self):
        fields, log = engine.parse_brief(WELL_SIZED)
        self.assertEqual(fields["peak_rps"], 260.0)
        self.assertEqual(fields["avg_rps"], 200.0)
        self.assertEqual(fields["instances_current"], 10)
        self.assertEqual(fields["instances_min"], 5)
        self.assertEqual(fields["instances_max"], 30)
        self.assertEqual(fields["az_count"], 3)
        self.assertEqual(fields["region_count"], 2)
        self.assertEqual(fields["availability_target"], 99.95)
        self.assertEqual(fields["cache_hit_ratio"], 0.90)
        self.assertEqual(fields["p50_latency_ms"], 40.0)
        self.assertEqual(fields["p99_latency_ms"], 120.0)
        self.assertEqual(fields["latency_budget_ms"], 250.0)
        self.assertTrue(len(log) > 10)

    def test_k_suffix_is_scaled(self):
        fields, _ = engine.parse_brief("peak traffic is 1.2k rps")
        self.assertAlmostEqual(fields["peak_rps"], 1200.0)

    def test_negative_and_positive_feature_detection(self):
        fields, _ = engine.parse_brief("There is no rate limit and no circuit breaker. It is stateless.")
        self.assertIs(fields["has_rate_limit"], False)
        self.assertIs(fields["has_circuit_breaker"], False)
        self.assertIs(fields["is_stateless"], True)

    def test_empty_brief_does_not_crash(self):
        res = engine.analyze("")
        self.assertIn("verdict", res)
        self.assertGreaterEqual(res["overall"], 0)


class TestAnalysis(unittest.TestCase):
    def test_well_sized_brief_scores_well(self):
        res = engine.analyze(WELL_SIZED)
        self.assertGreaterEqual(res["overall"], 70)
        ids = [f["id"] for f in res["findings"]]
        self.assertNotIn("over_capacity", ids)
        self.assertNotIn("single_az", ids)
        self.assertNotIn("error_budget_burned", ids)

    def test_overloaded_brief_is_flagged(self):
        res = engine.analyze(OVERLOADED)
        ids = [f["id"] for f in res["findings"]]
        self.assertIn("over_capacity", ids)
        self.assertIn("single_az", ids)
        self.assertIn("error_budget_burned", ids)
        self.assertIn("no_rate_limit", ids)
        self.assertLess(res["overall"], 45)
        self.assertEqual(res["verdict"], "under_provisioned")

    def test_findings_are_ordered_by_severity(self):
        res = engine.analyze(OVERLOADED)
        order = {"high": 0, "medium": 1, "low": 2}
        severities = [order[f["severity"]] for f in res["findings"]]
        self.assertEqual(severities, sorted(severities))

    def test_every_finding_has_evidence_and_fix(self):
        for sample in samples.SAMPLES:
            res = engine.analyze(sample["brief"])
            for f in res["findings"]:
                self.assertTrue(f["evidence"].strip(), f["id"])
                self.assertTrue(f["fix"].strip(), f["id"])
                self.assertIn(f["severity"], ("high", "medium", "low"))

    def test_axis_scores_are_in_range(self):
        for sample in samples.SAMPLES:
            res = engine.analyze(sample["brief"])
            for name, val in res["axis_scores"].items():
                self.assertGreaterEqual(val, 0, name)
                self.assertLessEqual(val, 100, name)

    def test_weights_sum_to_one(self):
        for kind, weights in engine.AXIS_WEIGHTS.items():
            self.assertAlmostEqual(sum(weights.values()), 1.0, places=6, msg=kind)

    def test_overrides_are_applied(self):
        res = engine.analyze(WELL_SIZED, {"peak_rps": 20000})
        self.assertGreater(res["capacity"]["util_at_peak"], 1.0)
        ids = [f["id"] for f in res["findings"]]
        self.assertIn("over_capacity", ids)

    def test_fix_plan_is_ordered_and_concrete(self):
        res = engine.analyze(OVERLOADED)
        self.assertTrue(res["fix_plan"])
        steps = [p["step"] for p in res["fix_plan"]]
        self.assertEqual(steps, sorted(steps))
        for item in res["fix_plan"]:
            self.assertTrue(item["action"].strip())
            self.assertTrue(item["rationale"].strip())

    def test_all_samples_parse_without_error(self):
        for sample in samples.SAMPLES:
            res = engine.analyze(sample["brief"])
            self.assertIn(res["verdict"], ("sized_ok", "mostly_ready", "needs_tuning",
                                           "tight_but_usable", "under_provisioned"))


class TestExport(unittest.TestCase):
    def test_markdown_export_has_sections(self):
        res = engine.analyze(WELL_SIZED)
        md = exporters.to_markdown(res, WELL_SIZED)
        for heading in ("# Capacity report", "## Capacity", "## Latency", "## Reliability",
                        "## Findings", "## Prioritised fix plan"):
            self.assertIn(heading, md)


class TestApi(unittest.TestCase):
    def setUp(self):
        import app as app_mod
        app_mod.app.config["TESTING"] = True
        self.client = app_mod.app.test_client()

    def test_health(self):
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["ok"])

    def test_index_renders(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"capacity-forge", resp.data)

    def test_analyze_endpoint(self):
        resp = self.client.post("/api/analyze", json={"brief": WELL_SIZED})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn("overall", data)
        self.assertIn("findings", data)
        self.assertIsNotNone(data["session_id"])

    def test_samples_endpoint(self):
        resp = self.client.get("/api/samples")
        self.assertEqual(resp.status_code, 200)
        self.assertGreaterEqual(len(resp.get_json()["samples"]), 4)

    def test_session_lifecycle_and_export(self):
        resp = self.client.post("/api/analyze", json={"brief": WELL_SIZED})
        sid = resp.get_json()["session_id"]
        resp = self.client.patch("/api/sessions/%d" % sid, json={"self_rating": 4, "notes": "solid"})
        self.assertEqual(resp.status_code, 200)
        resp = self.client.get("/api/sessions/%d" % sid)
        data = resp.get_json()
        self.assertEqual(data["self_rating"], 4)
        self.assertEqual(data["notes"], "solid")
        resp = self.client.get("/api/export/%d?fmt=md" % sid)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"Capacity report", resp.data)
        resp = self.client.get("/api/export/%d?fmt=json" % sid)
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"axis_scores", resp.data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
