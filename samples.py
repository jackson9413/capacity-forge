"""Built-in sample briefs for capacity-forge."""
from __future__ import annotations

from typing import Any, Dict, List

SAMPLES: List[Dict[str, Any]] = [
    {
        "id": "checkout-spike",
        "name": "Checkout API under a flash sale",
        "description": "A stateless API that is fine at average load and falls apart at peak.",
        "brief": """
Checkout API for a retail flash sale. The API is stateless and sits behind an ALB.

Traffic is spiky: average traffic is 120 rps, but peak traffic hits 1200 rps during the
sale window. Traffic pattern is spiky and the spike arrives within 2 minutes.

We are currently running 8 instances and autoscaling between 8 and 20 replicas.
Each instance is 2 vCPU with 8 GB ram, max concurrency 200 per instance.
CPU cost is 14 ms per request. Cold start is 45 seconds.

p50 latency is 120ms and p99 latency is 900ms. Our latency budget is 500ms.
There are 3 downstream calls to payment, inventory and tax, 180ms total.
The database does 4 reads and 2 writes per request at 8ms per query.
Cache hit ratio is 30%. Payload is 2kb request and 16kb response.

We target 99.9% availability. Current error rate is 0.4%.
Running in 3 AZ across 1 region. The database has a read replica but no failover.

Storage: 2M rows per day, 400 bytes per row. Growth of 50% over 12 months.
Instance cost is $0.09 per hour and egress is $0.09 per GB.
There is no rate limit on the public endpoints and no circuit breaker.
""",
    },
    {
        "id": "webhook-fanout",
        "name": "Order event webhook fanout",
        "description": "Reliability-critical event delivery with retry storms and a tight SLO.",
        "brief": """
Webhook fanout service that delivers order events to about 300 merchant endpoints.
This is a webhook service. Traffic is steady with a diurnal ramp.

Average traffic is 900 rps, peak traffic is 1400 rps. Growth of 20% over 12 months.

We run 24 replicas today, autoscaling 12 to 60. Instances are 4 vCPU with 16 GB ram
and max concurrency 400. CPU cost is 6 ms per request. Startup takes 25 seconds.

p50 latency is 45ms and p99 latency is 260ms, latency budget 300ms.
There are 2 downstream calls at 60ms. The database does 1 read and 1 write per request
at 4ms per query. Cache hit ratio is 85%. Payload 1kb request, 3kb response.

Availability target 99.95%, current error rate 0.02%. Two availability zones, one region.
The database is replicated with failover. There is a rate limit per merchant and a
circuit breaker, but no retry budget. Storage 400k rows per day, 900 bytes per row.
Cost is $0.22 per instance hour, egress $0.09 per GB.
""",
    },
    {
        "id": "nightly-batch",
        "name": "Nightly reconciliation batch",
        "description": "A batch job that over-subscribes its database and misses its window.",
        "brief": """
Nightly reconciliation batch job over the billing database. This is a batch job.
Traffic pattern is batch. Peak traffic is 90 rps equivalent, average traffic is 15 rps.

We run 4 workers, autoscale 2 to 12. Each worker is 4 vCPU with 8 GB ram,
max concurrency 64, CPU cost 120 ms per request. Startup is 20 seconds.

p50 latency is 400ms, p99 latency is 2600ms. Latency budget is 3000ms.
One downstream call at 250ms. The database does 12 reads and 5 writes per request
at 25ms per query. Cache hit ratio is 0%. Payload 0.5kb request, 2kb response.

Availability target 99.5%, current error rate 0.6%. One AZ, one region, single primary
database with no failover and no replica. There is no rate limit and no circuit breaker.
Storage 20M rows per day at 1.2 kb per row. Cost $0.18 per worker hour, egress $0.09 per GB.
""",
    },
    {
        "id": "media-metadata",
        "name": "Media metadata API with a growing catalogue",
        "description": "A read-heavy API that is cheap to run but quietly leaking connections.",
        "brief": """
Media metadata API for a streaming catalogue. Traffic ramps seasonally with new releases.

Average traffic is 400 rps, peak traffic is 950 rps. Traffic pattern is ramp.
Growth of 120% over 12 months as the catalogue grows.

Currently 16 instances, autoscaling 8 to 32. 2 vCPU, 8 GB ram, max concurrency 150.
CPU cost is 9 ms per request. Startup is 35 seconds.

p50 latency is 55ms, p99 latency is 340ms, latency budget 400ms.
No downstream calls. Database does 6 reads per request at 6ms per query and no writes.
Cache hit ratio is 70%. Payload 1kb request, 8kb response.

Availability target 99.9%, error rate 0.04%. Three AZs across two regions.
Database is replicated with failover. There is a rate limit but no circuit breaker.
Storage 8M rows per day at 600 bytes per row. Cost $0.10 per instance hour, egress $0.09 per GB.
""",
    },
]


def by_id(sid: str) -> Dict[str, Any]:
    for s in SAMPLES:
        if s["id"] == sid:
            return s
    return {}
