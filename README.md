# capacity-forge

**Local-first capacity & SLO planning workbench.** Paste a service brief in plain English,
get back fleet sizing, queueing math, SLO fit, connection-pool sizing, cost per million
requests and a prioritised fix plan.

No API keys. No network calls. No LLM. Flask + SQLite + vanilla JS, standard library only.

---

## Why it exists

Most capacity conversations are run with a spreadsheet nobody can reproduce, or with vendor
calculators that answer a different question. capacity-forge takes the brief you already
wrote and turns it into numbers you can argue with:

- **Fleet sizing** -- how many instances the peak actually needs at a target utilisation,
  and what that fleet costs per month.
- **Queueing math** -- a real M/M/c Erlang-C queue, so the report tells you the wait time
  the peak will produce, not just "you are at 80% utilisation".
- **Little's Law** -- the concurrency the load actually needs versus the slots you have.
- **Latency budget** -- CPU + database + downstream + queue wait against your p99 SLO.
- **Error budget** -- how much of this month's availability budget your current error rate
  has already spent.
- **Connection pool sizing** -- per-instance pool, total connections against
  max_connections, and the pooler you are about to need.
- **Scale-out response** -- whether the autoscaler can add capacity faster than the traffic
  arrives, including cold start and readiness probe time.
- **Cost** -- compute, egress, cost per million requests, idle capacity, storage growth.
- **45+ risk rules**, each with the evidence that triggered it and a concrete fix.

## Quick start

``bash
cd capacity-forge
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
# open http://127.0.0.1:8788
``

Or with the helper script:

``bash
./run.sh
``

Run the smoke tests:

``bash
python3 -m unittest discover -s tests -v
``

## How to write a brief

Plain prose works. The parser looks for these phrases (and you can correct anything it
misses in the **Detected inputs** panel before re-running):

| Signal | Example |
| --- | --- |
| Peak / average load | `peak traffic is 1200 rps`, `average traffic is 120 rps` |
| Fleet | `running 8 instances`, `autoscaling between 8 and 20` |
| Instance shape | `2 vCPU with 8 GB ram`, `max concurrency 200` |
| Compute cost | `cpu cost is 14 ms per request` |
| Cold start | `cold start is 45 seconds` |
| Latency | `p50 120ms`, `p99 900ms`, `latency budget 500ms` |
| Dependencies | `3 downstream calls, 180ms total` |
| Data layer | `4 db reads and 2 writes per request at 8ms`, `cache hit ratio 30%` |
| Payload | `2kb request and 16kb response` |
| Reliability | `target 99.9%`, `error rate 0.4%`, `3 AZ across 1 region` |
| Topology | `stateless`, `replicated with failover`, `no rate limit` |
| Money | `$0.09 per hour`, `egress $0.09 per GB` |
| Growth / storage | `growth of 50%`, `2M rows per day, 400 bytes per row` |

Load one of the four built-in samples from the dropdown for a working example.

## The model

Everything is deterministic arithmetic:

1. **Per-instance throughput ceiling** = min(CPU bound, concurrency bound).
   CPU bound = vcpu x 1000 / cpu_ms x 0.75 (25% left for the OS, GC and the tail).
   Concurrency bound = max_concurrency / p50_latency.
2. **Queueing** -- M/M/c Erlang-C, computed in log space so large fleets do not overflow.
   Returns mean wait, probability of waiting and utilisation.
3. **Tail amplification** -- an empirical p99/p50 curve: ~1.4x idle, ~1.8x at 60%
   utilisation, ~4.6x at 80%, above 10x as utilisation approaches 1.0.
4. **Error budget** -- (100 - target)% of the month, consumed linearly by the current
   error rate.
5. **Connection pool** -- 20% of per-instance concurrency (floor 4, ceiling 50), times the
   fleet at full scale, against max_connections.
6. **Cost** -- instances x 730 hours x unit price, plus egress bytes x egress price,
   expressed as cost per million requests.

The five axes (throughput, latency, reliability, scalability, cost) are weighted per service
kind -- a batch job cares about throughput and scale-out, a webhook cares about reliability,
a realtime API cares about latency. A hard failure (fleet cannot serve the peak, error
budget spent, modelled p99 breaches the SLO) caps the overall score.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| GET | / | the UI |
| POST | /api/analyze | {"brief": "...", "overrides": {...}} -> full report |
| GET | /api/samples | list built-in sample briefs |
| GET | /api/sample/<id> | one sample brief |
| GET | /api/fields | field metadata + defaults |
| GET | /api/sessions | saved runs |
| GET | /api/sessions/<id> | one saved run |
| PATCH | /api/sessions/<id> | set self_rating (1-5) and notes |
| GET | /api/export/<id>?fmt=md or json | download the report |
| GET | /api/health | health check |

## Layout

``
capacity-forge/
  app.py          Flask routes + static serving
  engine.py       brief parser, queueing math, scoring
  findings.py     45+ risk rules, strengths, fix plan
  exporters.py    Markdown / JSON report export
  db.py           SQLite session log
  samples.py      four built-in sample briefs
  templates/      index.html
  static/         app.js, styles.css
  tests/          test_engine.py
``

## Notes

- The report never silently invents a database limit: if the brief states max_connections
  the pool finding is scored normally, otherwise the common default of 100 is assumed and
  the finding is marked low severity with the assumption spelled out.
- Features that are simply not mentioned (rate limiting, circuit breakers, retry budgets)
  do not produce findings -- only an explicit "no rate limit" does.
- The modelled p99 covers CPU + database + downstream + queue wait. If the observed p99 in
  the brief is much worse than the model predicts, capacity-forge flags the gap, because
  that gap is where GC pauses, lock waits and dependency variance live.
