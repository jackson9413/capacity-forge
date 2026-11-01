/* capacity-forge UI -- vanilla JS, no build step, no dependencies */
(function () {
  "use strict";

  var state = {
    result: null,
    baseline: null,
    meta: null,
    order: [],
    sevFilter: "all",
    axisFilter: "all",
    sessionId: null
  };

  function $(sel) { return document.querySelector(sel); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined && text !== null) n.textContent = String(text);
    return n;
  }
  function clear(node) { while (node && node.firstChild) node.removeChild(node.firstChild); }
  function num(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    d = (d === undefined) ? 1 : d;
    if (Math.abs(v) >= 10000) return Math.round(v).toLocaleString();
    return Number(v).toFixed(d);
  }
  function pct(v, d) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    return (v * 100).toFixed((d === undefined) ? 0 : d) + "%";
  }
  function usd(v) {
    if (v === null || v === undefined || isNaN(v)) return "-";
    if (v >= 100) return "$" + Math.round(v);
    if (v >= 10) return "$" + v.toFixed(1);
    if (v >= 1) return "$" + v.toFixed(2);
    return "$" + v.toFixed(3);
  }
  function toneFor(score) {
    if (score >= 85) return "good";
    if (score >= 70) return "ok";
    if (score >= 55) return "warn";
    if (score >= 40) return "bad";
    return "crit";
  }
  function toneForPct(v) {
    if (v >= 1) return "crit";
    if (v >= 0.85) return "bad";
    if (v >= 0.7) return "warn";
    return "good";
  }

  function box(k, v, s, tone) {
    var b = el("div", "box");
    b.appendChild(el("div", "k", k));
    var val = el("div", "v", v);
    if (tone) val.style.color = "var(--" + tone + ")";
    b.appendChild(val);
    if (s) b.appendChild(el("div", "s", s));
    return b;
  }

  function gauge(label, valueText, fraction, tone) {
    var g = el("div", "gauge");
    var head = el("div", "gauge-head");
    head.appendChild(el("span", null, label));
    head.appendChild(el("span", null, valueText));
    g.appendChild(head);
    var track = el("div", "track");
    var fill = el("div", "fill " + tone);
    fill.style.width = Math.max(0, Math.min(100, fraction * 100)) + "%";
    track.appendChild(fill);
    g.appendChild(track);
    return g;
  }

  function stackBar(segments, budgetFraction) {
    var wrap = el("div");
    var st = el("div", "stack");
    segments.forEach(function (s) {
      var d = el("div");
      d.style.width = Math.max(0, s.pct) + "%";
      d.style.background = s.color;
      d.title = s.label + " " + s.text;
      st.appendChild(d);
    });
    wrap.appendChild(st);
    var legend = el("div", "legend");
    segments.forEach(function (s) {
      var sp = el("span");
      var sw = el("span", "swatch");
      sw.style.background = s.color;
      sp.appendChild(sw);
      sp.appendChild(el("b", null, s.label + " "));
      sp.appendChild(document.createTextNode(s.text));
      legend.appendChild(sp);
    });
    wrap.appendChild(legend);
    return wrap;
  }

  /* ---------------------------------------------------------------- render */

  function renderScore(r) {
    var card = el("div", "card score-card");
    var left = el("div");
    left.appendChild(el("div", "score-num", r.overall));
    left.appendChild(el("div", "score-max", "/ 100"));
    card.appendChild(left);
    var mid = el("div");
    var pill = el("span", "pill " + toneFor(r.overall), r.verdict.replace(/_/g, " "));
    mid.appendChild(pill);
    mid.appendChild(el("p", "hint", r.verdict_note));
    mid.appendChild(el("p", "hint", "Service kind: " + r.inputs.service_kind +
      "  |  traffic: " + r.inputs.traffic_pattern +
      "  |  peak " + num(r.meta.peak_rps) + " rps"));
    card.appendChild(mid);

    var axes = el("div", "axis-row");
    var names = { throughput: "Throughput", latency: "Latency", reliability: "Reliability",
                  scalability: "Scalability", cost: "Cost efficiency" };
    Object.keys(r.axis_scores).forEach(function (k) {
      var a = el("div", "axis");
      a.appendChild(el("div", "an", names[k] || k));
      var v = el("div", "av", r.axis_scores[k]);
      v.style.color = "var(--" + toneFor(r.axis_scores[k]) + ")";
      a.appendChild(v);
      a.appendChild(el("div", "aw", "weight " + Math.round(r.weights[k] * 100) + "%"));
      axes.appendChild(a);
    });
    card.appendChild(axes);
    if (r.caps && r.caps.length) {
      var note = el("div", "cap-note");
      note.appendChild(el("strong", null, "Hard limits applied: "));
      note.appendChild(document.createTextNode(r.caps.join("; ") + ". These cap the score regardless of the other axes."));
      card.appendChild(note);
    }
    return card;
  }

  function renderCapacity(r) {
    var c = r.capacity, f = r.inputs;
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Capacity"));
    var kv = el("div", "kv");
    kv.appendChild(box("Per-instance ceiling", num(c.mu_instance) + " rps",
      "bound by " + c.binding_constraint, toneForPct(c.util_at_peak)));
    kv.appendChild(box("Fleet ceiling", num(c.max_cluster_rps) + " rps",
      f.instances_max + " instances x " + num(c.mu_instance) + " rps"));
    kv.appendChild(box("Utilisation at peak", pct(c.util_at_peak),
      "at growth " + pct(c.util_at_growth), toneForPct(c.util_at_peak)));
    kv.appendChild(box("Little's Law concurrency", num(c.required_concurrency),
      "needed vs " + num(c.provided_concurrency) + " provided"));
    kv.appendChild(box("Recommended fleet", c.recommended_instances + " instances",
      "now  |  " + c.recommended_instances_12mo + " at 12 months"));
    kv.appendChild(box("Running now", f.instances_current + " instances",
      "covers " + num(c.current_cluster_rps) + " rps",
      c.util_current >= 1 ? "crit" : (c.util_current >= 0.85 ? "bad" : "good")));
    card.appendChild(kv);

    card.appendChild(gauge("Utilisation at peak",
      pct(c.util_at_peak) + " of " + num(c.max_cluster_rps) + " rps",
      c.util_at_peak, toneForPct(c.util_at_peak)));
    card.appendChild(gauge("Utilisation with 12 months of growth",
      pct(c.util_at_growth), c.util_at_growth, toneForPct(c.util_at_growth)));
    card.appendChild(gauge("Concurrency headroom",
      num(c.provided_concurrency) + " slots vs " + num(c.required_concurrency) + " needed",
      c.required_concurrency > 0 ? Math.min(1, c.concurrency_ratio) : 1,
      c.concurrency_ratio >= 1.5 ? "good" : (c.concurrency_ratio >= 1 ? "warn" : "crit")));
    return card;
  }

  function renderLatency(r) {
    var l = r.latency, c = l.components, f = r.inputs;
    var card = el("div", "card");
    var head = el("div", "card-head");
    head.appendChild(el("h2", null, "Latency budget"));
    head.appendChild(el("span", "hint", "budget " + num(l.budget_ms, 0) + "ms"));
    card.appendChild(head);

    var segs = [
      { label: "CPU", text: num(c.cpu_ms, 0) + "ms", pct: (c.cpu_ms / l.budget_ms) * 100, color: "var(--accent)" },
      { label: "Database", text: num(c.db_ms, 0) + "ms", pct: (c.db_ms / l.budget_ms) * 100, color: "var(--violet)" },
      { label: "Downstream", text: num(c.downstream_ms, 0) + "ms", pct: (c.downstream_ms / l.budget_ms) * 100, color: "var(--warn)" },
      { label: "Queue wait", text: num(c.queue_ms, 0) + "ms", pct: (c.queue_ms / l.budget_ms) * 100, color: "var(--bad)" }
    ];
    card.appendChild(stackBar(segs, l.budget_util));

    var kv = el("div", "kv");
    kv.style.marginTop = "14px";
    kv.appendChild(box("Modelled p99", num(l.p99_model_ms, 0) + "ms",
      "amplification " + num(l.tail_amp, 1) + "x at rho " + num(l.rho, 2),
      l.p99_model_ms > l.budget_ms ? "crit" : "good"));
    kv.appendChild(box("Observed p99", num(l.p99_observed_ms, 0) + "ms",
      l.tail_ratio_observed ? "p99/p50 = " + num(l.tail_ratio_observed, 1) + "x" : "no p50 given",
      l.p99_observed_ms > l.budget_ms ? "crit" : "ok"));
    kv.appendChild(box("Budget used", pct(l.budget_util),
      num(l.headroom_ms, 0) + "ms headroom",
      l.budget_util >= 1 ? "crit" : (l.budget_util > 0.8 ? "warn" : "good")));
    kv.appendChild(box("Queue model", "rho " + num(l.rho, 2),
      "p(wait) " + pct(l.queue_prob_wait) + " | " + num(l.queue_wait_ms, 0) + "ms wait",
      l.rho >= 1 ? "crit" : (l.rho >= 0.7 ? "warn" : "good")));
    card.appendChild(kv);
    return card;
  }

  function renderReliability(r) {
    var rel = r.reliability, f = r.inputs;
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Reliability & error budget"));
    var kv = el("div", "kv");
    kv.appendChild(box("Availability target", num(rel.target, 2) + "%",
      "budget " + num(rel.error_budget_pct, 2) + "%"));
    kv.appendChild(box("Budget consumed", pct(rel.consumed_pct / 100),
      num(rel.downtime_min, 0) + " min downtime this month",
      rel.consumed_pct >= 100 ? "crit" : (rel.consumed_pct >= 75 ? "bad" : "good")));
    kv.appendChild(box("Allowed downtime", num(rel.allowed_downtime_min, 0) + " min/mo",
      num(rel.days_left, 1) + " days of budget left"));
    kv.appendChild(box("Topology", f.az_count + " AZ / " + f.region_count + " region",
      f.az_count >= 3 ? "healthy spread" : (f.az_count === 2 ? "one zone of headroom" : "single zone"),
      f.az_count >= 3 ? "good" : (f.az_count === 2 ? "warn" : "crit")));
    card.appendChild(kv);
    card.appendChild(gauge("Error budget consumed", pct(rel.consumed_pct / 100),
      rel.consumed_pct / 100, rel.consumed_pct >= 100 ? "crit" : (rel.consumed_pct >= 75 ? "bad" : "good")));
    return card;
  }

  function renderData(r) {
    var d = r.data, f = r.inputs;
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Data layer & connections"));
    var kv = el("div", "kv");
    kv.appendChild(box("Pool per instance", d.pool_per_instance + " conns",
      "at " + f.instances_max + " instances"));
    kv.appendChild(box("Connections at full scale", num(d.total_connections),
      "of " + d.db_max_connections + " max_connections",
      d.saturation > 2 ? "crit" : (d.saturation > 0.8 ? "warn" : "good")));
    kv.appendChild(box("DB time per request", num(d.db_time_ms, 1) + "ms",
      "cache hit ratio " + pct(d.cache_hit_ratio)));
    kv.appendChild(box("Reads / writes per request",
      num(f.db_reads_per_request) + " / " + num(f.db_writes_per_request),
      num(f.db_query_ms, 1) + "ms per query"));
    card.appendChild(kv);
    card.appendChild(gauge("Connection pool saturation",
      pct(d.saturation) + " of " + d.db_max_connections,
      d.saturation, d.saturation > 2 ? "crit" : (d.saturation > 0.8 ? "warn" : "good")));
    return card;
  }

  function renderAutoscale(r) {
    var a = r.autoscale, f = r.inputs;
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Autoscaling & spike response"));
    var kv = el("div", "kv");
    kv.appendChild(box("Fleet range", a.instances_min + " / " + a.instances_current + " / " + a.instances_max,
      "min / running / max"));
    kv.appendChild(box("Scale-out window", num(a.scale_out_s, 0) + "s",
      num(f.startup_seconds, 0) + "s start + " + num(f.readiness_seconds, 0) + "s readiness"));
    kv.appendChild(box("Scale rate", num(a.max_scale_rate, 1) + " rps/s",
      "needs " + num(a.required_scale_rate, 1) + " rps/s",
      a.feasible ? "good" : "crit"));
    kv.appendChild(box("Traffic pattern", a.traffic_pattern,
      "spike window " + num(f.spike_window_minutes, 1) + " min"));
    card.appendChild(kv);
    var g = el("div", "gauge");
    var head = el("div", "gauge-head");
    head.appendChild(el("span", null, "Replica range"));
    head.appendChild(el("span", null, a.instances_min + " → " + a.instances_max));
    g.appendChild(head);
    var track = el("div", "track");
    var fill = el("div", "fill ok");
    var span = Math.max(1, a.instances_max - a.instances_min);
    fill.style.width = (((a.instances_current - a.instances_min) / span) * 100) + "%";
    track.appendChild(fill);
    g.appendChild(track);
    card.appendChild(g);
    return card;
  }

  function renderCost(r) {
    var c = r.cost, st = r.storage, f = r.inputs;
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Cost & growth"));
    var kv = el("div", "kv");
    kv.appendChild(box("Monthly cost", usd(c.total_cost),
      usd(c.compute_cost) + " compute + " + usd(c.egress_cost) + " egress"));
    kv.appendChild(box("Cost per million requests", usd(c.cost_per_million),
      num(c.monthly_requests / 1000000, 1) + "M requests/mo"));
    kv.appendChild(box("Egress", num(c.egress_gb, 0) + " GB/mo",
      num(f.payload_kb_req, 1) + "KB in + " + num(f.payload_kb_resp, 1) + "KB out",
      c.egress_gb > 500 ? "warn" : "good"));
    kv.appendChild(box("Idle capacity", pct(c.idle_ratio),
      c.idle_ratio > 0.7 ? "most of the fleet is unused" : "fleet is working",
      c.idle_ratio > 0.7 ? "warn" : "good"));
    kv.appendChild(box("Storage growth", num(st.year_gb, 0) + " GB/yr",
      num(st.rows_per_day, 0) + " rows/day",
      st.year_gb > 500 ? "warn" : "good"));
    kv.appendChild(box("Growth to plan for", num(f.growth_pct_12mo, 0) + "%",
      "over 12 months"));
    card.appendChild(kv);
    return card;
  }

  function renderFindings(r) {
    var card = el("div", "card");
    var head = el("div", "card-head");
    head.appendChild(el("h2", null, "Findings (" + r.findings.length + ")"));
    card.appendChild(head);

    var chips = el("div", "chips");
    ["all", "high", "medium", "low"].forEach(function (s) {
      var c = el("button", "chip" + (state.sevFilter === s ? " active" : ""), s);
      c.addEventListener("click", function () { state.sevFilter = s; renderResults(); });
      chips.appendChild(c);
    });
    var axes = ["all"];
    r.findings.forEach(function (f) { if (axes.indexOf(f.axis) < 0) axes.push(f.axis); });
    var sel = el("select");
    sel.style.width = "170px";
    axes.forEach(function (a) {
      var o = el("option", null, a === "all" ? "all axes" : a);
      o.value = a;
      if (state.axisFilter === a) o.selected = true;
      sel.appendChild(o);
    });
    sel.addEventListener("change", function () { state.axisFilter = sel.value; renderResults(); });
    chips.appendChild(sel);
    card.appendChild(chips);

    var shown = 0;
    r.findings.forEach(function (f) {
      if (state.sevFilter !== "all" && f.severity !== state.sevFilter) return;
      if (state.axisFilter !== "all" && f.axis !== state.axisFilter) return;
      shown++;
      var c = el("div", "finding " + f.severity);
      var h = el("h4");
      h.appendChild(el("span", null, f.title));
      h.appendChild(el("span", "tagx", f.severity));
      h.appendChild(el("span", "tagx", f.axis));
      c.appendChild(h);
      c.appendChild(el("div", "ev", f.evidence));
      c.appendChild(el("div", "fx", "Fix: " + f.fix));
      if (f.impact) c.appendChild(el("div", "im", "Impact: " + f.impact));
      card.appendChild(c);
    });
    if (!shown) card.appendChild(el("p", "hint", "No findings match this filter."));
    return card;
  }

  function renderPlan(r) {
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "Prioritised fix plan"));
    if (!r.fix_plan.length) {
      card.appendChild(el("p", "hint", "Nothing flagged -- this brief is in good shape."));
      return card;
    }
    var ol = el("ol", "plan");
    r.fix_plan.forEach(function (p) {
      var li = el("li");
      li.appendChild(el("div", null, p.action));
      li.appendChild(el("div", "why", "Why: " + p.rationale));
      li.appendChild(el("div", "eff", "Effect: " + p.impact));
      ol.appendChild(li);
    });
    card.appendChild(ol);
    return card;
  }

  function renderStrengths(r) {
    var card = el("div", "card");
    card.appendChild(el("div", "card-head")).appendChild(el("h2", null, "What is working"));
    var ul = el("ul", "strengths");
    r.strengths.forEach(function (s) { ul.appendChild(el("li", null, s)); });
    card.appendChild(ul);
    return card;
  }

  function renderResults() {
    var r = state.result;
    var box = $("#results");
    clear(box);
    if (!r) return;
    box.appendChild(renderScore(r));
    var two = el("div", "two-col");
    two.appendChild(renderCapacity(r));
    two.appendChild(renderLatency(r));
    box.appendChild(two);
    var two2 = el("div", "two-col");
    two2.appendChild(renderReliability(r));
    two2.appendChild(renderData(r));
    box.appendChild(two2);
    var two3 = el("div", "two-col");
    two3.appendChild(renderAutoscale(r));
    two3.appendChild(renderCost(r));
    box.appendChild(two3);
    box.appendChild(renderFindings(r));
    box.appendChild(renderPlan(r));
    box.appendChild(renderStrengths(r));
    var exp = el("div", "card");
    var h = el("div", "card-head");
    h.appendChild(el("h2", null, "Export"));
    exp.appendChild(h);
    var links = el("div", "links");
    if (state.sessionId) {
      var a1 = el("a", null, "Download Markdown report");
      a1.href = "/api/export/" + state.sessionId + "?fmt=md";
      links.appendChild(a1);
      var a2 = el("a", null, "Download JSON");
      a2.href = "/api/export/" + state.sessionId + "?fmt=json";
      links.appendChild(a2);
    }
    exp.appendChild(links);
    exp.appendChild(el("p", "hint", "Model: Little's Law for concurrency, an M/M/c Erlang-C queue for wait time, an " +
      "empirical p99/p50 amplification curve, an availability error budget, a connection-pool model and a cost-per-million model. " +
      "Inputs come from the brief -- nothing is fetched from the network."));
    box.appendChild(exp);
  }

  /* --------------------------------------------------------------- fields */

  function renderFields() {
    var grid = $("#fields-grid");
    clear(grid);
    if (!state.meta) return;
    var inputs = (state.result && state.result.inputs) || {};
    state.order.forEach(function (key) {
      var m = state.meta[key];
      if (!m) return;
      var wrap = el("div", "f");
      var lab = el("label", null, m[0]);
      wrap.appendChild(lab);
      var cur = inputs[key];
      if (m[1] === "bool") {
        var s = el("select");
        [["", "not stated"], ["true", "yes"], ["false", "no"]].forEach(function (o) {
          var op = el("option", null, o[1]);
          op.value = o[0];
          if (cur === true && o[0] === "true") op.selected = true;
          if (cur === false && o[0] === "false") op.selected = true;
          if ((cur === null || cur === undefined) && o[0] === "") op.selected = true;
          s.appendChild(op);
        });
        s.dataset.key = key;
        s.addEventListener("change", markChanged);
        wrap.appendChild(s);
      } else if (m[1].indexOf("select:") === 0) {
        var opts = m[1].slice(7).split(",");
        var s2 = el("select");
        opts.forEach(function (o) {
          var op = el("option", null, o);
          op.value = o;
          if (String(cur) === o) op.selected = true;
          s2.appendChild(op);
        });
        s2.dataset.key = key;
        s2.addEventListener("change", markChanged);
        wrap.appendChild(s2);
      } else {
        var inp = el("input");
        inp.type = "number";
        inp.step = "any";
        inp.value = (cur === null || cur === undefined) ? "" : cur;
        inp.dataset.key = key;
        inp.addEventListener("input", markChanged);
        wrap.appendChild(inp);
      }
      grid.appendChild(wrap);
    });
  }

  function markChanged(ev) {
    var node = ev.target;
    var f = node.closest(".f");
    if (!f) return;
    var base = state.baseline || {};
    var key = node.dataset.key;
    var raw = node.value;
    var changed;
    if (node.tagName === "SELECT" && (raw === "true" || raw === "false")) changed = (raw === "true");
    else if (node.tagName === "SELECT") changed = raw;
    else if (raw === "") changed = (base[key] === null || base[key] === undefined) ? null : base[key];
    else changed = Number(raw);
    if (String(changed) !== String(base[key])) f.classList.add("changed");
    else f.classList.remove("changed");
  }

  function collectOverrides() {
    var out = {};
    if (!state.baseline) return out;
    var grid = $("#fields-grid");
    Array.prototype.forEach.call(grid.querySelectorAll("[data-key]"), function (node) {
      var key = node.dataset.key;
      var base = state.baseline[key];
      var raw = node.value;
      var val;
      if (node.tagName === "SELECT") {
        if (raw === "true" || raw === "false") val = (raw === "true");
        else if (raw === "") val = null;
        else val = raw;
      } else {
        if (raw === "") val = (base === null || base === undefined) ? null : base;
        else val = Number(raw);
      }
      if (val === null || val === undefined) return;
      if (String(val) !== String(base)) out[key] = val;
    });
    return out;
  }

  /* -------------------------------------------------------------- history */

  function renderHistory(sessions) {
    var box = $("#history");
    clear(box);
    $("#history-count").textContent = sessions.length + " saved";
    if (!sessions.length) {
      box.appendChild(el("p", "hint", "No saved runs yet."));
      return;
    }
    sessions.forEach(function (s) {
      var row = el("div", "history-row");
      var top = el("div", "ht");
      var name = el("div", "hn", s.title);
      top.appendChild(name);
      var p = el("span", "pill " + toneFor(s.score), Math.round(s.score));
      top.appendChild(p);
      row.appendChild(top);
      row.appendChild(el("div", "hs", s.kind + " | " + s.verdict.replace(/_/g, " ") + " | " + String(s.created_at).replace("T", " ").slice(0, 16)));

      var stars = el("div", "stars");
      for (var i = 1; i <= 5; i++) {
        (function (val) {
          var b = el("button", "star" + (s.self_rating >= val ? " on" : ""), "★");
          b.addEventListener("click", function () {
            patchSession(s.id, { self_rating: val });
          });
          stars.appendChild(b);
        })(i);
      }
      row.appendChild(stars);

      var notes = el("input");
      notes.type = "text";
      notes.placeholder = "notes...";
      notes.value = s.notes || "";
      notes.addEventListener("change", function () { patchSession(s.id, { notes: notes.value }); });
      row.appendChild(notes);

      var links = el("div", "links");
      links.style.marginTop = "6px";
      var open = el("a", null, "open");
      open.href = "#";
      open.addEventListener("click", function (ev) { ev.preventDefault(); loadSession(s.id); });
      links.appendChild(open);
      var md = el("a", null, "md");
      md.href = "/api/export/" + s.id + "?fmt=md";
      links.appendChild(md);
      var js2 = el("a", null, "json");
      js2.href = "/api/export/" + s.id + "?fmt=json";
      links.appendChild(js2);
      row.appendChild(links);
      box.appendChild(row);
    });
  }

  function patchSession(id, body) {
    fetch("/api/sessions/" + id, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function () { loadHistory(); });
  }

  function loadHistory() {
    return fetch("/api/sessions").then(function (r) { return r.json(); })
      .then(function (d) { renderHistory(d.sessions || []); });
  }

  function loadSession(id) {
    return fetch("/api/sessions/" + id).then(function (r) { return r.json(); }).then(function (d) {
      state.result = d.result;
      state.baseline = d.result.inputs;
      state.sessionId = d.id;
      $("#brief").value = d.brief || "";
      renderResults();
      renderFields();
      window.scrollTo({ top: 0, behavior: "smooth" });
    });
  }

  /* ---------------------------------------------------------------- flow */

  function analyze() {
    var brief = $("#brief").value;
    var btn = $("#btn-analyze");
    btn.disabled = true;
    btn.textContent = "Analyzing...";
    var payload = { brief: brief };
    var ov = collectOverrides();
    if (Object.keys(ov).length) payload.overrides = ov;
    return fetch("/api/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) {
        $("#hint").textContent = "Analysis failed: " + d.error;
        return;
      }
      state.result = d;
      state.baseline = d.inputs;
      state.sessionId = d.session_id || null;
      state.sevFilter = "all";
      state.axisFilter = "all";
      renderResults();
      renderFields();
      return loadHistory();
    }).then(function () {
      btn.disabled = false;
      btn.textContent = "Analyze capacity";
    }).catch(function (e) {
      btn.disabled = false;
      btn.textContent = "Analyze capacity";
      $("#hint").textContent = "Request failed: " + e;
    });
  }

  function init() {
    fetch("/api/health").then(function (r) { return r.json(); }).then(function (d) {
      $("#engine-badge").textContent = "engine " + d.version;
    });
    fetch("/api/fields").then(function (r) { return r.json(); }).then(function (d) {
      state.meta = d.fields;
      state.order = Object.keys(d.fields);
      renderFields();
    });
    fetch("/api/samples").then(function (r) { return r.json(); }).then(function (d) {
      var sel = $("#sample-select");
      (d.samples || []).forEach(function (s) {
        var o = el("option", null, s.name);
        o.value = s.id;
        sel.appendChild(o);
      });
    });
    $("#btn-analyze").addEventListener("click", function () { state.baseline = state.result ? state.baseline : null; analyze(); });
    $("#sample-select").addEventListener("change", function (ev) {
      var id = ev.target.value;
      if (!id) return;
      fetch("/api/sample/" + id).then(function (r) { return r.json(); }).then(function (d) {
        $("#brief").value = d.brief;
        state.baseline = null;
        analyze();
      });
    });
    $("#brief").addEventListener("input", function () { state.baseline = null; });
    $("#toggle-fields").addEventListener("click", function () {
      var grid = $("#fields-grid");
      var hidden = grid.style.display === "none";
      grid.style.display = hidden ? "" : "none";
      $("#toggle-fields").textContent = hidden ? "collapse" : "expand";
    });
    loadHistory();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
