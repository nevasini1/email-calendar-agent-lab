/**
 * Dashboard client for email-calendar-agent-lab.
 */
(function () {
  const $ = (sel) => document.querySelector(sel);
  /** Rows loaded from logs via API (no fabricated preview data). */
  const RUN_HISTORY_LIMIT = 10;

  let pollTimer = null;
  let progressTimer = null;
  /** Last merged snapshot for cycle graph (persists while pipeline runs so live steps overlay prior run). */
  let lastCycleSnapshot = null;
  /** Unfiltered rows from `/api/run-history`. */
  let lastAllRunHistoryRows = [];
  /** Rows from `/api/run-history` (same window as RUN_HISTORY_LIMIT); drives suite-by-category chart across runs. */
  let lastRunHistoryRows = [];
  let runHistoryFilter = null;
  let lastRunHistoryPath = "";

  function stopProgressPoll() {
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
  }

  async function refreshCycleLiveProgress() {
    try {
      const pr = await fetchJson("/api/run-progress");
      renderCycleFlow(lastCycleSnapshot, pr.exists ? pr.progress : null);
    } catch (e) {
      console.error(e);
    }
  }

  function esc(s) {
    const d = document.createElement("div");
    d.textContent = s == null ? "" : String(s);
    return d.innerHTML;
  }

  async function fetchJson(url, opts) {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  }

  function fmtScore(obj) {
    if (!obj || obj.total == null) return "—";
    return `${obj.passed}/${obj.total} (${obj.score ?? "—"})`;
  }

  /** Merge live Langfuse probe so the UI is not stuck on stale logs/run_latest.json. */
  function mergeLangfuseLive(data, live) {
    if (!data?.default_eval || !live) return data;
    const snap = data.default_eval.langfuse_export || {};
    let statusHint;
    if (snap.enabled === false) {
      statusHint = live.enabled
        ? `Last run tracing disabled: ${snap.reason || "not exported"}. SDK is ready now, but this snapshot exported 0 traces.`
        : snap.reason || live.reason || "Last run tracing disabled.";
    } else if (live.enabled) {
      const n = snap.exported ?? 0;
      statusHint =
        n > 0
          ? `SDK ready · last run exported ${n} trace(s)`
          : "SDK ready · run pipeline again to export traces (snapshot below was from an older run)";
    } else {
      statusHint = live.reason || snap.reason || "—";
    }
    data.default_eval.langfuse_export = {
      ...snap,
      enabled: snap.enabled,
      exported: snap.exported,
      live_enabled: live.enabled,
      snapshot_had_export_enabled: snap.enabled,
      snapshot_reason: snap.reason,
      live_probe_python: live.python,
      status_hint: statusHint,
    };
    data.langfuse_export = data.default_eval.langfuse_export;
    return data;
  }

  const chartInstances = { stage: null, suite: null, cat: null, trace: null, gate: null };

  function killChart(key) {
    if (chartInstances[key]) {
      chartInstances[key].destroy();
      chartInstances[key] = null;
    }
  }

  function passRatePct(node) {
    if (!node || node.total == null || !node.total) return 0;
    return Math.round((1000 * node.passed) / node.total) / 10;
  }

  function score01(node) {
    if (!node) return 0;
    if (node.score != null) {
      const x = Number(node.score);
      if (Number.isFinite(x)) return x;
    }
    const t = Number(node.total);
    const p = Number(node.passed);
    if (Number.isFinite(t) && t > 0 && Number.isFinite(p)) return Math.min(1, Math.max(0, p / t));
    return 0;
  }

  /**
   * Plot position for candidate when it ties baseline: raw lines stack invisibly in Canvas.
   * Nudge Y slightly on-chart only; tooltips still use score01(run[candidateKey]) from real data.
   */
  function candidateScore01Plot(run, baselineKey, candidateKey) {
    const b = score01(run?.[baselineKey]);
    const c = score01(run?.[candidateKey]);
    if (Math.abs(b - c) >= 1e-9) return c;
    const bump = 0.045;
    return Math.min(0.97, c + bump);
  }

  function shortRunLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso.endsWith("Z") ? iso : `${iso}Z`);
    if (Number.isNaN(d.getTime())) return String(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  function runProvider(row) {
    return row?.runtime?.provider || (row?.langfuse_sessions?.enabled ? "openai-live" : "deterministic-local");
  }

  function runModel(row) {
    return row?.runtime?.model || row?.current_config_loaded?.model || row?.current_config_final?.model || "—";
  }

  function runSampleType(row) {
    return row?.runtime?.sample_type || (runProvider(row) === "openai-live" ? "live_openai" : "deterministic");
  }

  function sampleLabel(kind) {
    if (kind === "live_openai") return "Live OpenAI";
    if (kind === "deterministic") return "Archived local";
    return "All samples";
  }

  function runNumber(row, fallbackIndex) {
    return row?.history_index || fallbackIndex + 1;
  }

  function runAxisLabel(row, fallbackIndex) {
    return `#${runNumber(row, fallbackIndex)} ${shortRunLabel(row?.run_at)}`;
  }

  function chartTextDefaults() {
    if (typeof Chart !== "undefined") {
      Chart.defaults.color = "#8fa3bc";
      Chart.defaults.borderColor = "#2d3f56";
    }
  }

  function renderLangfuseIngest(data, liveLf) {
    const host = $("#langfuse-ingest");
    if (!host) return;
    const lf = data?.langfuse_export ?? data?.default_eval?.langfuse_export;
    const rfl = data?.reflective_phase?.langfuse_export;
    const snapSessions = lf?.exported ?? "—";
    const snapReflect = rfl?.exported ?? "—";
    const liveOn = liveLf?.enabled === true;
    const sdkChipClass = liveOn ? " lf-chip-ok" : "";
    const errChip =
      lf?.errors?.length > 0 ? `<span class="lf-chip lf-chip-warn">${esc(String(lf.errors.length))} export error(s)</span>` : "";

    const harnessAria =
      "Harness Langfuse trace per session: trace named email-calendar-lab slash scenario id, then live agent generation, tool spans, and eval span.";
    const reflectAria =
      "Reflective phase export: root observation email-calendar-lab reflective-phase with child reflection observations per lesson type.";

    host.innerHTML = `
      <p class="sr-only">
        Langfuse ingest is shown as two structure diagrams: harness session traces and reflective-phase batches.
        Snapshot counts: ${esc(String(snapSessions))} harness traces, ${esc(String(snapReflect))} reflective batches.
        Live SDK ${liveOn ? "enabled" : esc(liveLf?.reason || "off")}.
      </p>
      <div class="lf-diagrams">
        <figure class="lf-diagram" aria-label="${esc(harnessAria)}">
          <figcaption class="lf-diag-caption">Harness session → Langfuse trace</figcaption>
          <div class="lf-flow-stack">
            <div class="lf-d-node lf-d-node-trace">
              <span class="lf-d-role">trace</span>
              <span class="lf-d-name">email-calendar-lab / &lt;scenario_id&gt;</span>
              <span class="lf-d-meta">tags · mode · provider · model metadata</span>
            </div>
            <div class="lf-d-connector" aria-hidden="true"></div>
            <div class="lf-d-node lf-d-node-span">
              <span class="lf-d-role">generation</span>
              <span class="lf-d-name">openai-agent-generation</span>
              <span class="lf-d-meta">prompt bundle → live model answer</span>
            </div>
            <div class="lf-d-connector" aria-hidden="true"></div>
            <div class="lf-d-node lf-d-node-span lf-d-node-nested">
              <span class="lf-d-role">nested spans</span>
              <span class="lf-d-name">tool:&lt;tool_name&gt;</span>
              <span class="lf-d-meta">arguments · evidence ids returned</span>
            </div>
            <div class="lf-d-connector" aria-hidden="true"></div>
            <div class="lf-d-node lf-d-node-span lf-d-node-eval">
              <span class="lf-d-role">check</span>
              <span class="lf-d-name">eval</span>
              <span class="lf-d-meta">pass/fail · scenario text</span>
            </div>
          </div>
          <div class="lf-diag-foot">
            <span class="lf-chip">snapshot traces · ${esc(String(snapSessions))}</span>
            <span class="lf-chip${sdkChipClass}">live SDK · ${liveOn ? "ready" : esc(liveLf?.reason || "off")}</span>
            ${errChip}
          </div>
        </figure>

        <figure class="lf-diagram" aria-label="${esc(reflectAria)}">
          <figcaption class="lf-diag-caption">Post-cycle reflective batch</figcaption>
          <div class="lf-ref-visual">
            <div class="lf-d-node lf-d-node-root">
              <span class="lf-d-role">root observation</span>
              <span class="lf-d-name">email-calendar-lab / reflective-phase</span>
              <span class="lf-d-meta">evolution summary on span output</span>
            </div>
            <div class="lf-ref-joint" aria-hidden="true">
              <span class="lf-ref-stem"></span>
              <span class="lf-ref-bar"></span>
            </div>
            <div class="lf-ref-leaves">
              <div class="lf-leaf"><span class="lf-leaf-k">child</span> reflection:&lt;lesson_type&gt;</div>
              <div class="lf-leaf"><span class="lf-leaf-k">child</span> reflection:&lt;lesson_type&gt;</div>
              <div class="lf-leaf lf-leaf-more">…</div>
            </div>
          </div>
          <div class="lf-diag-foot">
            <span class="lf-chip">snapshot batches · ${esc(String(snapReflect))}</span>
          </div>
        </figure>
      </div>`;
  }

  async function fetchRunHistoryPayload(limit) {
    const endpoints = [
      `/api/history?limit=${limit}`,
      `/api/history/?limit=${limit}`,
      `/history?limit=${limit}`,
      `/history/?limit=${limit}`,
      `/api/run-history?limit=${limit}`,
      `/api/run-history/?limit=${limit}`,
      `/run-history?limit=${limit}`,
      `/run-history/?limit=${limit}`,
    ];
    let lastStatus = 0;
    let lastBody = "";
    for (const ep of endpoints) {
      const r = await fetch(ep);
      lastStatus = r.status;
      if (r.ok) return { payload: await r.json(), apiPath: ep };
      lastBody = await r.text();
    }
    const mr = await fetch(`/api/meta?history_limit=${limit}`);
    lastStatus = mr.status;
    if (mr.ok) {
      const meta = await mr.json();
      const rh = meta.run_history;
      if (rh && Array.isArray(rh.runs)) {
        return { payload: rh, apiPath: `/api/meta?history_limit=${limit}` };
      }
    } else {
      lastBody = await mr.text();
    }
    throw new Error(
      lastBody ||
        `Run history unreachable (HTTP ${lastStatus}). <strong>Stop every old dashboard process</strong> (Ctrl+C) ` +
          `and start fresh: <code>PYTHONPATH=src python3 -m email_calendar_lab.dashboard.app</code>`
    );
  }

  async function loadRunHistory() {
    chartTextDefaults();
    try {
      const { payload: j } = await fetchRunHistoryPayload(RUN_HISTORY_LIMIT);
      lastAllRunHistoryRows = j.runs || [];
      lastRunHistoryPath = j.path || "";
      runHistoryFilter = lastAllRunHistoryRows.some((r) => runSampleType(r) === "live_openai") ? "live_openai" : "all";
      applyRunHistoryView();
      hydrateCategoryDelta(lastCycleSnapshot);
    } catch (e) {
      console.error(e);
      lastAllRunHistoryRows = [];
      lastRunHistoryRows = [];
      const meta = $("#run-history-meta");
      if (meta) meta.innerHTML = `<span class="demo-chip fail-chip">Run history</span> ${esc(e.message)}`;
      renderRunSampleFilter([], []);
      renderRunHistoryTable([]);
      renderRunTruthStrip([]);
      renderAutoEvalCharts([]);
      renderRunLoopLedger([]);
      killChart("stage");
      killChart("suite");
      hydrateCategoryDelta(lastCycleSnapshot);
    }
  }

  function filteredRunHistoryRows() {
    if (runHistoryFilter === "live_openai") {
      return lastAllRunHistoryRows.filter((r) => runSampleType(r) === "live_openai");
    }
    if (runHistoryFilter === "deterministic") {
      return lastAllRunHistoryRows.filter((r) => runSampleType(r) === "deterministic");
    }
    return lastAllRunHistoryRows;
  }

  function applyRunHistoryView() {
    const visibleRuns = filteredRunHistoryRows();
    lastRunHistoryRows = visibleRuns;
    const meta = $("#run-history-meta");
    if (meta) {
      if (lastAllRunHistoryRows.length) {
        const liveN = lastAllRunHistoryRows.filter((r) => runSampleType(r) === "live_openai").length;
        const detN = lastAllRunHistoryRows.filter((r) => runSampleType(r) === "deterministic").length;
        meta.innerHTML = `${esc(visibleRuns.length)} plotted live OpenAI run(s) · ${esc(liveN)} live retained · ${esc(
          detN
        )} archived local row(s) hidden · <code>${esc(lastRunHistoryPath)}</code>`;
      } else {
        meta.innerHTML =
          "No rows yet. Finish <code>PYTHONPATH=src python3 -m email_calendar_lab.run_cycle</code> a few times to append <code>logs/run_history.jsonl</code>, then refresh.";
      }
    }
    renderRunSampleFilter(lastAllRunHistoryRows, visibleRuns);
    renderRunHistoryTable(visibleRuns);
    renderRunTruthStrip(visibleRuns, lastAllRunHistoryRows);
    renderAutoEvalCharts(visibleRuns);
    renderRunLoopLedger(visibleRuns);
    renderStageCharts(visibleRuns);
  }

  function renderRunSampleFilter(allRuns, visibleRuns) {
    const el = $("#run-sample-filter");
    if (!el) return;
    if (!allRuns.length) {
      el.innerHTML = "";
      return;
    }
    const liveN = allRuns.filter((r) => runSampleType(r) === "live_openai").length;
    const detN = allRuns.filter((r) => runSampleType(r) === "deterministic").length;
    runHistoryFilter = liveN ? "live_openai" : "all";
    const archived = detN ? ` ${detN} archived local row(s) are retained on disk but excluded from these charts.` : "";
    el.innerHTML = `<div class="sample-filter live-only">
      <span class="live-dot" aria-hidden="true"></span>
      <p>Live-only view: plotting ${esc(visibleRuns.length)} OpenAI run(s).${esc(archived)}</p>
    </div>`;
  }

  function renderRunHistoryTable(runs) {
    const el = $("#run-history-table");
    if (!el) return;
    if (!runs.length) {
      el.innerHTML =
        '<div class="empty">No history rows yet. Run <code>PYTHONPATH=src python3 -m email_calendar_lab.run_cycle</code> locally (repeat to stack runs in <code>logs/run_history.jsonl</code>).</div>';
      return;
    }
    const rows = runs
      .map((r, i) => {
        const sb = r.suite_baseline;
        const sc = r.suite_candidate;
        const hb = r.heldout_baseline;
        const hc = r.heldout_candidate;
        const badRejected = r.sanity_gate_bad_accepted === false;
        const lfN = r.langfuse_sessions?.exported;
        return `<tr>
          <td>#${runNumber(r, i)}</td>
          <td>${esc(shortRunLabel(r.run_at))}</td>
          <td><span class="sample-chip ${esc(runSampleType(r))}">${esc(sampleLabel(runSampleType(r)))}</span><br><code>${esc(runProvider(r))}</code> · ${esc(runModel(r))}</td>
          <td>${esc(fmtScore(r.workflow))}</td>
          <td>${esc(fmtScore(r.production_baseline))}</td>
          <td>${esc(fmtScore(sb))} → ${esc(fmtScore(sc))}</td>
          <td>${esc(fmtScore(hb))} → ${esc(fmtScore(hc))}</td>
          <td>${esc(fmtScore(r.sanity_bad_suite))}</td>
          <td class="${badRejected ? "pass" : "fail"}">${badRejected ? "bad cand rejected" : "check"}</td>
          <td class="${r.promotion_accepted ? "pass" : "warn"}">${r.promotion_accepted ? "promoted" : "kept baseline"}</td>
          <td>${esc(lfN != null ? String(lfN) : "—")}</td>
        </tr>`;
      })
      .join("");
    el.innerHTML = `<div class="table-wrap"><table class="data history-table"><thead><tr>
      <th>#</th><th>When</th><th>Sample</th><th>Workflow</th><th>Prod (baseline)</th><th>Suite B→C</th><th>Heldout B→C</th>
      <th>Bad cand suite</th><th>Sanity gate</th><th>Promotion</th><th>LF sessions</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderRunTruthStrip(runs, allRuns) {
    const el = $("#run-truth-strip");
    if (!el) return;
    if (!runs.length) {
      el.innerHTML = '<div class="truth-strip"><div><strong>No plotted samples:</strong> choose a different sample filter or run another cycle.</div></div>';
      return;
    }
    const first = runs[0];
    const promotedAt = runs.findIndex((r) => r.promotion_accepted === true);
    const saturated = runs.filter((r, i) => i > promotedAt && promotedAt >= 0 && score01(r.production_baseline) === 1).length;
    const liveN = (allRuns || runs).filter((r) => runSampleType(r) === "live_openai").length;
    const detN = (allRuns || runs).filter((r) => runSampleType(r) === "deterministic").length;
    const plottedType = "Live OpenAI";
    const invisibleZero =
      score01(first.suite_baseline) === 0
        ? "The suite-baseline bar on run #1 is 0%, so it is invisible on the grouped chart."
        : "";
    let why;
    if (runHistoryFilter === "live_openai") {
      const latest = runs[runs.length - 1];
      why = `${runs.length} live OpenAI sample(s) plotted. Latest run used ${runProvider(latest)} / ${runModel(latest)}, exported ${
        latest.langfuse_sessions?.exported ?? 0
      } Langfuse session trace(s), and ended with: ${latest.promotion_decision || "—"}.`;
    } else {
      why =
        promotedAt >= 0
          ? `Plotted run #${runNumber(runs[promotedAt], promotedAt)} promoted a candidate; ${saturated} later plotted run(s) loaded that accepted config and scored 100% on the retained suite.`
          : "No candidate was promoted in this plotted run window.";
    }
    el.innerHTML = `<div class="truth-strip">
      <div><strong>Sample view:</strong> ${esc(plottedType)} · plotting ${esc(runs.length)} of ${esc(
        liveN
      )} live retained row(s). Archived local rows hidden: ${esc(detN)}.</div>
      <div><strong>Raw first run:</strong> production ${esc(fmtScore(first.production_baseline))}, suite ${esc(
        fmtScore(first.suite_baseline)
      )} → ${esc(fmtScore(first.suite_candidate))}, held-out ${esc(fmtScore(first.heldout_baseline))} → ${esc(
        fmtScore(first.heldout_candidate)
      )}.</div>
      <div><strong>Readout:</strong> ${esc(why)} ${runHistoryFilter === "live_openai" ? "" : esc(invisibleZero)}</div>
    </div>`;
  }

  function renderRunLoopLedger(runs) {
    const el = $("#run-loop-ledger");
    if (!el) return;
    if (!runs.length) {
      el.innerHTML =
        '<div class="empty">No completed cycles yet. Run the pipeline and this ledger will fill from <code>logs/run_history.jsonl</code>.</div>';
      return;
    }
    const rows = runs
      .map((r, idx) => {
        const baseRules = r.baseline_rules || r.current_config_loaded?.prompt_rules || [];
        const candRules = r.candidate_rules || [];
        const added = candRules.filter((rule) => !baseRules.includes(rule));
        const kept = r.promotion_accepted ? "promoted candidate" : "kept loaded config";
        const gateClass = r.promotion_accepted ? "pass" : "warn";
        const badClass = r.sanity_gate_bad_accepted === false ? "pass" : "fail";
        const sources = r.eval_suite_sources || {};
        const cfgName = r.current_config_loaded?.name || "—";
        const finalName = r.current_config_final?.name || "—";
        const generatedBits = [
          `fresh ${numOrDash(r.fresh_generated_eval_count)}`,
          `carried ${numOrDash(r.carried_generated_eval_count)}`,
          `active ${numOrDash(r.active_generated_eval_count)}`,
        ].join(" · ");
        return `<article class="loop-card">
          <header class="loop-card-head">
            <div>
              <div class="loop-run-kicker">Run #${runNumber(r, idx)} · ${esc(shortRunLabel(r.run_at))} · ${esc(sampleLabel(runSampleType(r)))}</div>
              <h3>${esc(kept)}</h3>
            </div>
            <span class="loop-decision ${gateClass}">${esc(r.promotion_accepted ? "accepted" : "not promoted")}</span>
          </header>
          <div class="loop-steps">
            ${loopStep(
              "Loaded config",
              `${cfgName} from ${r.current_config_source || "unknown source"} · ${baseRules.length} prompt constraint(s)`
            )}
            ${loopStep(
              "Production discovery",
              `${fmtScore(r.production_baseline)} · generated evals: ${generatedBits} · sources p/s/h: ${sources.production || "—"}/${sources.stable || "—"}/${sources.heldout || "—"}`
            )}
            ${loopStep("Suite eval", `baseline ${fmtScore(r.suite_baseline)} → candidate ${fmtScore(r.suite_candidate)}`)}
            ${loopStep(
              "Sanity gate",
              `${r.sanity_gate_bad_accepted === false ? "bad candidate rejected" : "bad candidate was not rejected"} · ${r.sanity_gate_decision || "—"}`,
              badClass
            )}
            ${loopStep(
              "Candidate proposal",
              added.length ? `added ${added.map((rule) => `<code>${esc(rule)}</code>`).join(", ")}` : "no new candidate prompt constraints",
              null,
              added.length > 0
            )}
            ${loopStep("Promotion gate", r.promotion_decision || "—", gateClass)}
            ${loopStep(
              "Reflection artifacts",
              `${numOrDash(r.session_log_count)} session traces · ${numOrDash(r.reflection_count)} reflections (${numOrDash(
                r.reflection_generalizable_count
              )} generalizable) · ${numOrDash(r.candidate_skill_count)} candidate skills · final config ${finalName}`
            )}
          </div>
        </article>`;
      })
      .join("");
    el.innerHTML = `<div class="loop-ledger">${rows}</div>`;
  }

  function loopStep(label, body, variant, html) {
    const cls = variant ? ` ${variant}` : "";
    return `<div class="loop-step${cls}"><div class="loop-step-label">${esc(label)}</div><div class="loop-step-body">${
      html ? body : esc(body)
    }</div></div>`;
  }

  function numOrDash(value) {
    return value == null ? "—" : String(value);
  }

  function numberOrZero(value) {
    const n = Number(value);
    return Number.isFinite(n) ? n : 0;
  }

  function renderAutoEvalCharts(runs) {
    killChart("trace");
    killChart("gate");
    const traceCanvas = document.getElementById("chart-trace-funnel");
    const gateCanvas = document.getElementById("chart-gate-deltas");
    const explainer = $("#auto-eval-explainer");
    if (explainer) {
      if (!runs.length) {
        explainer.innerHTML = '<div class="empty">No run history yet. Complete a cycle to render trace and auto-eval charts.</div>';
      } else {
        explainer.innerHTML = renderAutoEvalExplainer(runs);
      }
    }
    if (!runs.length || typeof Chart === "undefined") return;

    const labels = runs.map((r, i) => runAxisLabel(r, i));

    if (traceCanvas) {
      chartInstances.trace = new Chart(traceCanvas, {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "Session traces",
              data: runs.map((r) => numberOrZero(r.session_log_count)),
              backgroundColor: "rgba(91, 159, 255, 0.74)",
            },
            {
              label: "Reflections",
              data: runs.map((r) => numberOrZero(r.reflection_count)),
              backgroundColor: "rgba(62, 207, 142, 0.72)",
            },
            {
              label: "Generalizable reflections",
              data: runs.map((r) => numberOrZero(r.reflection_generalizable_count)),
              backgroundColor: "rgba(251, 191, 36, 0.78)",
            },
            {
              label: "Fresh generated evals",
              data: runs.map((r) => numberOrZero(r.fresh_generated_eval_count)),
              backgroundColor: "rgba(248, 113, 113, 0.75)",
            },
            {
              label: "Carried generated evals",
              data: runs.map((r) => numberOrZero(r.carried_generated_eval_count)),
              backgroundColor: "rgba(167, 139, 250, 0.78)",
            },
            {
              label: "Candidate skills",
              data: runs.map((r) => numberOrZero(r.candidate_skill_count)),
              backgroundColor: "rgba(45, 212, 191, 0.7)",
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { maxRotation: 50, minRotation: 20 } },
            y: { min: 0, title: { display: true, text: "Count from run_history.jsonl" } },
          },
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                afterBody(items) {
                  const r = runs[items[0]?.dataIndex ?? 0];
                  return [
                    `Config: ${r?.current_config_loaded?.name || "—"}`,
                    `Generated evals total: ${numOrDash(r?.generated_eval_count)}`,
                  ];
                },
              },
            },
          },
        },
      });
    }

    if (gateCanvas) {
      chartInstances.gate = new Chart(gateCanvas, {
        type: "bar",
        data: {
          labels,
          datasets: [
            {
              label: "Suite gain",
              data: runs.map((r) => Math.round((score01(r.suite_candidate) - score01(r.suite_baseline)) * 1000) / 1000),
              backgroundColor: "rgba(62, 207, 142, 0.78)",
            },
            {
              label: "Heldout gain",
              data: runs.map((r) => Math.round((score01(r.heldout_candidate) - score01(r.heldout_baseline)) * 1000) / 1000),
              backgroundColor: "rgba(91, 159, 255, 0.76)",
            },
            {
              label: "Promotion accepted",
              data: runs.map((r) => (r.promotion_accepted ? 1 : 0)),
              backgroundColor: "rgba(251, 191, 36, 0.82)",
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: {
            x: { ticks: { maxRotation: 50, minRotation: 20 } },
            y: {
              min: -1,
              max: 1,
              ticks: { stepSize: 0.25 },
              title: { display: true, text: "Delta score / accepted flag" },
            },
          },
          plugins: {
            legend: { position: "bottom" },
            tooltip: {
              callbacks: {
                afterBody(items) {
                  const r = runs[items[0]?.dataIndex ?? 0];
                  return [
                    `Suite: ${fmtScore(r?.suite_baseline)} → ${fmtScore(r?.suite_candidate)}`,
                    `Heldout: ${fmtScore(r?.heldout_baseline)} → ${fmtScore(r?.heldout_candidate)}`,
                    `Decision: ${r?.promotion_decision || "—"}`,
                  ];
                },
              },
            },
          },
        },
      });
    }
  }

  function renderAutoEvalExplainer(runs) {
    const first = runs[0];
    const latest = runs[runs.length - 1];
    const promoted = runs.find((r) => r.promotion_accepted === true);
    const totalFresh = runs.reduce((sum, r) => sum + numberOrZero(r.fresh_generated_eval_count), 0);
    const latestCarried = latest ? numberOrZero(latest.carried_generated_eval_count) : 0;
    const promotedText = promoted
      ? `A candidate was promoted on ${shortRunLabel(promoted.run_at)} after suite gain ${(
          score01(promoted.suite_candidate) - score01(promoted.suite_baseline)
        ).toFixed(3)} and heldout gain ${(
          score01(promoted.heldout_candidate) - score01(promoted.heldout_baseline)
        ).toFixed(3)}.`
      : "No candidate has been promoted in the retained run window.";
    return `<div class="auto-eval-summary">
      <div class="summary-tile"><span>First loaded config</span><strong>${esc(first?.current_config_loaded?.name || "—")}</strong></div>
      <div class="summary-tile"><span>Fresh generated evals</span><strong>${esc(totalFresh)}</strong></div>
      <div class="summary-tile"><span>Latest carried evals</span><strong>${esc(latestCarried)}</strong></div>
      <div class="summary-tile summary-wide"><span>Gate readout</span><strong>${esc(promotedText)}</strong></div>
    </div>`;
  }

  function renderSpecReadout(spec) {
    const el = $("#spec-readout");
    if (!el) return;
    if (!spec) {
      el.innerHTML = '<div class="empty">Spec readout unavailable.</div>';
      return;
    }

    const deliverables = spec.deliverables || [];
    const gaps = deliverables.filter((item) => !item.ok);
    const agent = spec.agent || {};
    const finalConfig = agent.final_config || {};
    const baseline = agent.baseline_config || {};
    const prompt = spec.prompt_change || {};
    const fixtures = spec.fixtures || {};
    const anti = spec.anti_overfit || {};

    const deliverableCards = deliverables
      .map(
        (item) => `<div class="spec-check ${item.ok ? "ok" : "missing"}">
          <span class="spec-check-mark">${item.ok ? "OK" : "GAP"}</span>
          <div><strong>${esc(item.label)}</strong><p>${esc(item.detail || "—")}</p></div>
        </div>`
      )
      .join("");

    const toolRows = (spec.tools || [])
      .map(
        (tool) => `<tr>
          <td><code>${esc(tool.name)}</code></td>
          <td>${esc(tool.description)}</td>
          <td>${(tool.args || []).map((arg) => `<code>${esc(arg)}</code>`).join(" ")}</td>
        </tr>`
      )
      .join("");

    const evalRows = (spec.eval_sets || [])
      .map((set) => {
        const cats = Object.entries(set.categories || {})
          .map(([cat, n]) => `<span class="cat-pill">${esc(cat)} ${esc(n)}</span>`)
          .join("");
        const lifecycle = Object.entries(set.lifecycle || {})
          .map(([name, n]) => `${name}:${n}`)
          .join(" · ");
        return `<tr>
          <td><code>${esc(set.split)}</code></td>
          <td>${esc(set.count)}</td>
          <td><div class="cat-grid">${cats || "—"}</div></td>
          <td>${esc(lifecycle || "—")}</td>
        </tr>`;
      })
      .join("");

    const coverageRows = (spec.coverage || [])
      .map((row) => {
        const total = numberOrZero(row.production) + numberOrZero(row.stable) + numberOrZero(row.generated) + numberOrZero(row.heldout);
        return `<tr class="${total ? "" : "coverage-empty"}">
          <td><code>${esc(row.category)}</code></td>
          <td>${esc(row.production)}</td>
          <td>${esc(row.stable)}</td>
          <td>${esc(row.generated)}</td>
          <td>${esc(row.heldout)}</td>
        </tr>`;
      })
      .join("");

    const generatedRows = (spec.generated_evals || [])
      .slice(0, 8)
      .map(
        (ev) => `<tr>
          <td><code>${esc(ev.id)}</code></td>
          <td>${esc(ev.query)}</td>
          <td><span class="cat-pill">${esc(ev.category || "—")}</span></td>
          <td>${esc(ev.source_failure || "—")}</td>
          <td>${esc(ev.promotion_status || ev.lifecycle || "—")}</td>
        </tr>`
      )
      .join("");

    const added = prompt.added_rules || [];
    const removed = prompt.removed_rules || [];
    const promptRules = (finalConfig.prompt_rules || baseline.prompt_rules || [])
      .map((rule) => `<code>${esc(rule)}</code>`)
      .join(" ");
    const addedRules = added.length ? added.map((rule) => `<code>${esc(rule)}</code>`).join(" ") : "No new prompt constraints in the selected run.";
    const removedRules = removed.length ? removed.map((rule) => `<code>${esc(rule)}</code>`).join(" ") : "None";
    const decisionCounts = anti.decision_counts || {};
    const policyRows = (anti.policy || []).map((line) => `<li>${esc(line)}</li>`).join("");
    const gapNote = gaps.length
      ? `${gaps.length} item(s) need attention: ${gaps.map((g) => g.label).join(", ")}.`
      : "All visible submission criteria have backing artifacts in this workspace.";

    el.innerHTML = `
      <div class="spec-grid">
        <article class="spec-card spec-card-wide">
          <div class="spec-card-head">
            <h3>Deliverable checklist</h3>
            <span class="spec-status ${gaps.length ? "warn" : "ok"}">${esc(gaps.length ? "needs attention" : "artifact-backed")}</span>
          </div>
          <p class="subtle">${esc(gapNote)}</p>
          <div class="spec-check-grid">${deliverableCards}</div>
        </article>

        <article class="spec-card">
          <h3>Agent substrate</h3>
          <dl class="spec-kv">
            <dt>Baseline</dt><dd>${esc(baseline.name || "—")} · ${esc(baseline.model || "—")}</dd>
            <dt>Loaded/current</dt><dd>${esc(finalConfig.name || "—")} · ${esc(finalConfig.model || agent.session_model || "—")}</dd>
            <dt>Provider</dt><dd>${esc(agent.session_provider || "—")} · ${esc(agent.session_mode || "—")}</dd>
            <dt>Prompt constraints</dt><dd class="spec-code-flow">${promptRules || "—"}</dd>
          </dl>
        </article>

        <article class="spec-card">
          <h3>Mock data</h3>
          <dl class="spec-kv">
            <dt>Fixture clock</dt><dd>${esc(fixtures.now || "—")}</dd>
            <dt>Gmail messages</dt><dd>${esc(fixtures.emails ?? "—")} total · ${esc(fixtures.flight_emails ?? "—")} flight</dd>
            <dt>Calendar events</dt><dd>${esc(fixtures.calendar_events ?? "—")} total · ${esc(fixtures.recurring_events ?? "—")} recurring · ${esc(fixtures.cancelled_events ?? "—")} cancelled</dd>
            <dt>Contacts</dt><dd>${esc(fixtures.contacts ?? "—")} · ambiguous: ${esc(JSON.stringify(fixtures.ambiguous_first_names || {}))}</dd>
          </dl>
        </article>

        <article class="spec-card spec-card-wide">
          <h3>MCP-style tools</h3>
          <div class="table-wrap"><table class="data spec-table"><thead><tr><th>Tool</th><th>Description</th><th>Args</th></tr></thead><tbody>${toolRows}</tbody></table></div>
        </article>

        <article class="spec-card spec-card-wide">
          <h3>Eval set inventory</h3>
          <div class="table-wrap"><table class="data spec-table"><thead><tr><th>Split</th><th>Rows</th><th>Categories</th><th>Lifecycle</th></tr></thead><tbody>${evalRows}</tbody></table></div>
        </article>

        <article class="spec-card spec-card-wide">
          <h3>Failure-mode coverage</h3>
          <div class="table-wrap"><table class="data spec-table"><thead><tr><th>Category</th><th>Production</th><th>Stable</th><th>Generated</th><th>Held-out</th></tr></thead><tbody>${coverageRows}</tbody></table></div>
        </article>

        <article class="spec-card spec-card-wide">
          <h3>Failure-derived eval lineage</h3>
          <div class="table-wrap"><table class="data spec-table"><thead><tr><th>Generated eval</th><th>Query</th><th>Category</th><th>Source failure</th><th>Status</th></tr></thead><tbody>${generatedRows || '<tr><td colspan="5">No generated evals yet.</td></tr>'}</tbody></table></div>
        </article>

        <article class="spec-card">
          <h3>Prompt change evidence</h3>
          <dl class="spec-kv">
            <dt>Source run</dt><dd>${esc(prompt.source_run_at ? shortRunLabel(prompt.source_run_at) : "—")}</dd>
            <dt>Decision</dt><dd>${esc(prompt.decision || "—")}</dd>
            <dt>Suite</dt><dd>${esc(fmtScore(prompt.suite_baseline))} → ${esc(fmtScore(prompt.suite_candidate))}</dd>
            <dt>Held-out</dt><dd>${esc(fmtScore(prompt.heldout_baseline))} → ${esc(fmtScore(prompt.heldout_candidate))}</dd>
            <dt>Added constraints</dt><dd class="spec-code-flow">${addedRules}</dd>
            <dt>Removed constraints</dt><dd class="spec-code-flow">${removedRules}</dd>
          </dl>
        </article>

        <article class="spec-card">
          <h3>Anti-overfit guardrails</h3>
          <dl class="spec-kv">
            <dt>Optimizer</dt><dd>${esc(anti.optimizer || "—")}</dd>
            <dt>Artifact decisions</dt><dd>${esc(`accepted ${decisionCounts.accepted ?? 0} · rejected ${decisionCounts.rejected ?? 0} · quarantined ${decisionCounts.quarantined ?? 0}`)}</dd>
            <dt>GEPA bridge</dt><dd>${esc(anti.dspy_gepa?.enabled ? "enabled" : anti.dspy_gepa?.reason || "off")}</dd>
          </dl>
          <ul class="spec-policy">${policyRows}</ul>
        </article>
      </div>`;
  }

  async function loadSpecReadout() {
    const el = $("#spec-readout");
    if (!el) return;
    try {
      const spec = await fetchJson(`/api/spec-readout?history_limit=${RUN_HISTORY_LIMIT}`);
      renderSpecReadout(spec);
    } catch (e) {
      console.error(e);
      el.innerHTML = `<div class="empty fail">${esc(e.message)}</div>`;
    }
  }

  function renderStageCharts(runs) {
    killChart("stage");
    killChart("suite");
    const c1 = document.getElementById("chart-stage-grouped");
    const c2 = document.getElementById("chart-suite-lines");
    if (!c1 || !c2 || typeof Chart === "undefined") return;
    if (!runs.length) return;

    const labels = runs.map((r, i) => runAxisLabel(r, i));

    chartInstances.stage = new Chart(c1, {
      type: "bar",
      data: {
        labels,
        datasets: [
          { label: "Workflow", data: runs.map((r) => passRatePct(r.workflow)), backgroundColor: "rgba(91, 159, 255, 0.75)" },
          {
            label: "Production (baseline)",
            data: runs.map((r) => passRatePct(r.production_baseline)),
            backgroundColor: "rgba(62, 207, 142, 0.75)",
          },
          { label: "Suite baseline", data: runs.map((r) => passRatePct(r.suite_baseline)), backgroundColor: "rgba(251, 191, 36, 0.8)" },
          { label: "Suite candidate", data: runs.map((r) => passRatePct(r.suite_candidate)), backgroundColor: "rgba(248, 113, 113, 0.8)" },
          {
            label: "Held-out baseline",
            data: runs.map((r) => passRatePct(r.heldout_baseline)),
            backgroundColor: "rgba(143, 163, 188, 0.75)",
          },
          {
            label: "Held-out candidate",
            data: runs.map((r) => passRatePct(r.heldout_candidate)),
            backgroundColor: "rgba(167, 139, 250, 0.85)",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        scales: {
          x: { ticks: { maxRotation: 50, minRotation: 25 } },
          y: { min: 0, max: 100, title: { display: true, text: "Pass rate %" } },
        },
        plugins: { legend: { position: "bottom" } },
      },
    });

    const suiteLineSets = [
      {
        label: "Suite baseline",
        key: "suite_baseline",
        borderColor: "rgba(251, 191, 36, 1)",
        bg: "rgba(251, 191, 36, 0.92)",
        pointStyle: "circle",
        borderDash: [],
        pointBoost: 0,
      },
      {
        label: "Suite candidate",
        key: "suite_candidate",
        pairBaselineKey: "suite_baseline",
        borderColor: "rgba(248, 113, 113, 1)",
        bg: "rgba(248, 113, 113, 0.92)",
        pointStyle: "rect",
        borderDash: [8, 5],
        pointBoost: 3,
      },
      {
        label: "Held-out baseline",
        key: "heldout_baseline",
        borderColor: "rgba(143, 163, 188, 1)",
        bg: "rgba(143, 163, 188, 0.95)",
        pointStyle: "triangle",
        borderDash: [],
        pointBoost: 0,
      },
      {
        label: "Held-out candidate",
        key: "heldout_candidate",
        pairBaselineKey: "heldout_baseline",
        borderColor: "rgba(167, 139, 250, 1)",
        bg: "rgba(167, 139, 250, 0.95)",
        pointStyle: "rectRot",
        borderDash: [8, 5],
        pointBoost: 3,
      },
    ];

    const basePt = runs.length <= 2 ? 9 : 7;

    chartInstances.suite = new Chart(c2, {
      type: "line",
      data: {
        labels,
        datasets: suiteLineSets.map((s) => ({
          label: s.label,
          data: runs.map((r) =>
            s.pairBaselineKey ? candidateScore01Plot(r, s.pairBaselineKey, s.key) : score01(r[s.key])
          ),
          borderColor: s.borderColor,
          backgroundColor: s.bg,
          pointStyle: s.pointStyle,
          borderDash: s.borderDash,
          pointRadius: basePt + s.pointBoost,
          pointHoverRadius: basePt + s.pointBoost + 3,
          pointBorderWidth: 2,
          pointBorderColor: "rgba(15, 23, 42, 0.85)",
          borderWidth: 2.5,
          tension: 0.15,
          spanGaps: false,
        })),
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            offset: runs.length === 1,
            ticks: { maxRotation: 45, minRotation: 0 },
          },
          y: {
            min: 0,
            max: 1,
            title: { display: true, text: "Score (passed/total)" },
            ticks: { stepSize: 0.2 },
          },
        },
        plugins: {
          legend: { position: "bottom" },
          tooltip: {
            callbacks: {
              label(ctx) {
                const r = runs[ctx.dataIndex];
                const spec = suiteLineSets[ctx.datasetIndex];
                const bucket = r?.[spec.key];
                const raw = score01(bucket);
                const pct = Math.round(raw * 1000) / 10;
                const frac =
                  bucket && bucket.passed != null && bucket.total != null
                    ? `${bucket.passed}/${bucket.total}`
                    : "—";
                const tie =
                  spec.pairBaselineKey &&
                  Math.abs(score01(r?.[spec.pairBaselineKey]) - raw) < 1e-9
                    ? " · tied (candidate marker nudged on chart)"
                    : "";
                return `${ctx.dataset.label}: ${pct}% (${frac})${tie}`;
              },
            },
          },
        },
      },
    });
  }

  function hydrateCategoryDelta(data) {
    const cats = data?.self_improvement?.category_delta;
    const keys = cats && typeof cats === "object" ? Object.keys(cats) : [];
    renderCategoryDeltaChart(cats || {}, keys);
  }

  function suiteBucketPassed(bucket, cat) {
    const n = bucket?.by_category?.[cat];
    if (!n || n.passed == null) return 0;
    const v = Number(n.passed);
    return Number.isFinite(v) ? v : 0;
  }

  function suiteBucketTotal(bucket, cat) {
    const n = bucket?.by_category?.[cat];
    if (!n || n.total == null) return 0;
    const v = Number(n.total);
    return Number.isFinite(v) ? v : 0;
  }

  /** 0–100 with one decimal; used so single-scenario buckets use full axis (0% vs 100%), not 0 vs 1 px. */
  function passRateFromCounts(passed, total) {
    const t = Number(total);
    const p = Number(passed);
    if (!Number.isFinite(t) || t <= 0) return 0;
    if (!Number.isFinite(p) || p < 0) return 0;
    return Math.round((1000 * Math.min(p, t)) / t) / 10;
  }

  /** Human-readable segment drawn at bar tip (always visible; hover still has full detail). */
  function formatBucketTipLine(passed, total, pct) {
    const t = Number(total);
    const p = Number(passed);
    if (!Number.isFinite(t) || t <= 0) return "no scenarios";
    const pp = Number.isFinite(p) && p > 0 ? Math.min(p, t) : 0;
    return `${pp}/${t} · ${pct}%`;
  }

  /** Draw passed/total · pct at each bar so the chart is readable without hover. */
  const barTipLabelPlugin = {
    id: "emailCalBarTipLabels",
    afterDatasetsDraw(chart) {
      const lines = chart.options.plugins?.emailCalBarTipLabels?.lines;
      if (!Array.isArray(lines)) return;
      const { ctx, chartArea } = chart;
      ctx.save();
      ctx.font = '600 10px "IBM Plex Mono", "SF Mono", ui-monospace, monospace';
      ctx.fillStyle = "#9eb1c8";
      chart.data.datasets.forEach((ds, di) => {
        const tips = lines[di];
        if (!tips?.length) return;
        const meta = chart.getDatasetMeta(di);
        if (meta.hidden) return;
        meta.data.forEach((el, i) => {
          const text = tips[i];
          if (!text || !el || el.skip) return;
          const dataVal = Number(ds.data[i]);
          const props = el.getProps(["x", "y", "base"], true);
          const x = props.x;
          const base = props.base ?? x;
          const y = props.y;
          const thin = !Number.isFinite(dataVal) || dataVal <= 0;
          ctx.textBaseline = "middle";
          let drawX;
          let align = "left";
          if (thin) {
            drawX = base + 6;
          } else {
            drawX = x + 6;
          }
          const tw = ctx.measureText(text).width;
          if (drawX + tw > chartArea.right - 4) {
            align = "right";
            drawX = Math.max(chartArea.left + 4, x - 6);
          }
          ctx.textAlign = align;
          ctx.fillText(text, drawX, y);
        });
      });
      ctx.restore();
    },
  };

  /** Paired baseline/candidate bars: one row per (finished run × suite category) from run_history.jsonl. */
  function renderCategoryChartFromHistory(hr, cats, keys) {
    const canvas = document.getElementById("chart-category-delta");
    const extras = $("#category-delta-extras");
    const card = $("#category-delta-card");
    const wrap = card?.querySelector(".chart-canvas-wrap");
    if (!canvas || typeof Chart === "undefined") return;
    const ctx = canvas.getContext("2d");

    if (wrap) {
      wrap.classList.add("chart-canvas-history");
    }
    if (card) card.classList.remove("category-delta--parity");

    const rowLabels = [];
    const metaRows = [];

    hr.forEach((run) => {
      const runCats = new Set([
        ...Object.keys(run.suite_baseline?.by_category || {}),
        ...Object.keys(run.suite_candidate?.by_category || {}),
      ]);
      Array.from(runCats)
        .sort()
        .forEach((cat) => {
          const bT = suiteBucketTotal(run.suite_baseline, cat);
          const cT = suiteBucketTotal(run.suite_candidate, cat);
          if (bT <= 0 && cT <= 0) return;

          rowLabels.push(`#${runNumber(run, 0)} ${shortRunLabel(run.run_at)} · ${cat}`);
          metaRows.push({
            run_at: run.run_at,
            cat,
            bP: suiteBucketPassed(run.suite_baseline, cat),
            bT,
            cP: suiteBucketPassed(run.suite_candidate, cat),
            cT,
          });
        });
    });

    if (!rowLabels.length) {
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (extras) extras.innerHTML = '<p class="subtle">No per-category suite buckets in run history yet.</p>';
      return;
    }

    const baselinePct = metaRows.map((m) => passRateFromCounts(m.bP, m.bT));
    const candidatePct = metaRows.map((m) => passRateFromCounts(m.cP, m.cT));

    const baselineTips = metaRows.map((m, i) => formatBucketTipLine(m.bP, m.bT, baselinePct[i]));
    const candidateTips = metaRows.map((m, i) => formatBucketTipLine(m.cP, m.cT, candidatePct[i]));

    const px = Math.min(2600, Math.max(420, rowLabels.length * 18 + 140));
    if (wrap) wrap.style.height = `${px}px`;

    chartInstances.cat = new Chart(canvas, {
      plugins: [barTipLabelPlugin],
      type: "bar",
      data: {
        labels: rowLabels,
        datasets: [
          {
            label: "Baseline pass rate",
            data: baselinePct,
            backgroundColor: "rgba(251, 191, 36, 0.82)",
            borderWidth: 0,
            minBarLength: 5,
          },
          {
            label: "Candidate pass rate",
            data: candidatePct,
            backgroundColor: "rgba(248, 113, 113, 0.82)",
            borderWidth: 0,
            minBarLength: 5,
          },
        ],
      },
      options: {
        layout: { padding: { right: 120 } },
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        scales: {
          x: {
            min: 0,
            max: 100,
            ticks: { stepSize: 10 },
            title: { display: true, text: "Pass rate within this bucket (%)" },
            grid: { color: "rgba(45, 63, 86, 0.65)" },
          },
          y: {
            reverse: true,
            grid: { display: false },
            ticks: {
              autoSkip: false,
              font: { size: 10 },
            },
          },
        },
        plugins: {
          legend: { display: true, position: "bottom" },
          emailCalBarTipLabels: {
            lines: [baselineTips, candidateTips],
          },
          tooltip: {
            callbacks: {
              title(items) {
                if (!items.length) return "";
                return String(metaRows[items[0].dataIndex]?.run_at || "");
              },
              label(ctx) {
                const m = metaRows[ctx.dataIndex];
                const pct = ctx.parsed.x;
                if (ctx.datasetIndex === 0) {
                  return `Baseline: ${m.bP}/${m.bT} (${pct}%) · ${m.cat}`;
                }
                return `Candidate: ${m.cP}/${m.cT} (${pct}%) · ${m.cat}`;
              },
              afterBody(items) {
                if (!items.length) return [];
                const m = metaRows[items[0].dataIndex];
                return [`Δ passes (candidate − baseline): ${m.cP - m.bP}`];
              },
            },
          },
        },
      },
    });

    const deltasLatest = keys.map((k) => cats[k]?.passed_delta ?? 0);
    const maxAbsLatest = keys.length ? Math.max(0, ...deltasLatest.map((d) => Math.abs(d))) : 1;
    let latestNote = "";
    if (keys.length && maxAbsLatest === 0) {
      latestNote =
        '<p class="subtle category-delta-note"><strong>Latest snapshot:</strong> Δ = 0 in every category (baseline tied candidate there). Older rows in this chart may still differ.</p>';
    }

    if (extras) {
      extras.innerHTML = `${latestNote}<p class="subtle category-delta-note"><strong>History (${hr.length} run(s)):</strong> each row is only a category that existed in <strong>that</strong> run’s suite (stable ∪ generated). Bars use <code>passed/total · %</code>; 0% rows show a thin stub bar. Hover for Δ passes.</p>`;
    }
  }

  function renderCategoryDeltaChart(cats, keys) {
    killChart("cat");
    const card = $("#category-delta-card");
    const wrap = card?.querySelector(".chart-canvas-wrap");
    if (wrap) {
      wrap.classList.remove("chart-canvas-history");
      wrap.style.height = "";
    }
    if (card) card.classList.remove("category-delta--parity");

    const extras = $("#category-delta-extras");
    if (extras) extras.innerHTML = "";

    const canvas = document.getElementById("chart-category-delta");
    if (!canvas || typeof Chart === "undefined") return;

    if (lastRunHistoryRows.length > 0) {
      renderCategoryChartFromHistory(lastRunHistoryRows, cats || {}, keys);
      return;
    }

    const ctx = canvas.getContext("2d");

    if (!keys.length) {
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      if (extras) {
        extras.innerHTML =
          '<p class="subtle">No <code>self_improvement.category_delta</code> in the latest snapshot yet. Run the pipeline locally, then refresh.</p>';
      }
      return;
    }

    const deltas = keys.map((k) => cats[k]?.passed_delta ?? 0);
    const maxAbs = Math.max(0, ...deltas.map((d) => Math.abs(d)));

    const tableRows = keys
      .map((k) => {
        const b = cats[k]?.before || {};
        const a = cats[k]?.after || {};
        const dlt = cats[k]?.passed_delta ?? 0;
        return `<tr><td>${esc(k)}</td><td>${esc(b.passed ?? "—")}/${esc(b.total ?? "—")}</td><td>${esc(a.passed ?? "—")}/${esc(
          a.total ?? "—"
        )}</td><td class="${dlt > 0 ? "pass" : dlt < 0 ? "fail" : ""}">${esc(dlt)}</td></tr>`;
      })
      .join("");

    if (maxAbs === 0) {
      if (card) card.classList.add("category-delta--parity");
      if (ctx) ctx.clearRect(0, 0, canvas.width, canvas.height);
      const parityBars = keys
        .map((k) => {
          const b = cats[k]?.before || {};
          const passed = Number(b.passed);
          const total = Number(b.total);
          const p = Number.isFinite(passed) ? passed : 0;
          const t = Number.isFinite(total) ? total : 0;
          const pct = t > 0 ? Math.round((100 * p) / t) : 0;
          const label = `${p} of ${t} scenarios passed`;
          let rowTone = "parity-cat-row";
          if (t > 0 && p === 0) rowTone += " parity-cat-row--none-passed";
          else if (t > 0 && p === t) rowTone += " parity-cat-row--all-passed";
          return `<div class="${rowTone}">
            <code class="parity-cat-name">${esc(k)}</code>
            <div class="parity-track-wrap">
              <div class="parity-track" role="img" aria-label="${esc(k)}: ${esc(label)}">
                <div class="parity-fill" style="width:${pct}%"></div>
              </div>
              <span class="parity-pct">${pct}%</span>
            </div>
            <div class="parity-count-block">
              <span class="parity-count-main">${esc(String(p))} of ${esc(String(t))}</span>
              <span class="parity-count-sub">${t === 1 ? "scenario" : "scenarios"} passed</span>
            </div>
          </div>`;
        })
        .join("");
      const catCodes = (arr) => arr.map((k) => `<code>${esc(k)}</code>`).join(", ");
      const fullPass = keys.filter((k) => {
        const b = cats[k]?.before || {};
        const t = Number(b.total);
        const p = Number(b.passed);
        return Number.isFinite(t) && t > 0 && Number.isFinite(p) && p === t;
      });
      const zeroPass = keys.filter((k) => {
        const b = cats[k]?.before || {};
        const t = Number(b.total);
        const p = Number(b.passed);
        return Number.isFinite(t) && t > 0 && Number.isFinite(p) && p === 0;
      });
      const insightParts = [];
      if (fullPass.length) {
        insightParts.push(
          `${catCodes(fullPass)} ${fullPass.length === 1 ? "is" : "are"} at <strong>100%</strong> — every suite scenario in ${fullPass.length === 1 ? "that category" : "those categories"} passed.`
        );
      }
      if (zeroPass.length) {
        insightParts.push(
          `${catCodes(zeroPass)} ${zeroPass.length === 1 ? "is" : "are"} at <strong>0%</strong> — each scenario failed at least one check (answer, tools, evidence, or forbidden text). Inspect <code>logs/sessions/</code> or Langfuse for traces.`
        );
      }
      const catInsightHtml =
        insightParts.length > 0
          ? `<p class="parity-callout-text parity-callout-muted">${insightParts.join(" ")}</p>`
          : `<p class="parity-callout-text parity-callout-muted">Pass rates count suite scenarios per category. To raise them, fix the agent prompts/tool policy or adjust eval expectations, then re-run the pipeline.</p>`;
      if (extras) {
        extras.innerHTML = `
          <div class="parity-callout">
            <span class="parity-callout-badge">Parity</span>
            <div class="parity-callout-body">
              <p class="parity-callout-text">
                Candidate and baseline matched on <strong>passed counts</strong> in every category (Δ = 0 everywhere).
                Overall suite scores can still differ when cases are weighted or pooled across categories.
              </p>
              ${catInsightHtml}
            </div>
          </div>
          <div class="parity-bars-head"><span>Category</span><span>Pass rate (baseline = candidate)</span><span>Suite scenarios</span></div>
          <div class="parity-bars">${parityBars}</div>
          <details class="parity-details">
            <summary>Full breakdown table</summary>
            <div class="table-wrap"><table class="data cat-delta-mini"><thead><tr><th>Category</th><th>Baseline passes</th><th>Candidate passes</th><th>Δ</th></tr></thead><tbody>${tableRows}</tbody></table></div>
          </details>`;
      }
      return;
    }

    const baselinePct = keys.map((k) => {
      const b = cats[k]?.before || {};
      return passRateFromCounts(b.passed, b.total);
    });
    const candidatePct = keys.map((k) => {
      const a = cats[k]?.after || {};
      return passRateFromCounts(a.passed, a.total);
    });
    const baselineTips = keys.map((k, i) => {
      const b = cats[k]?.before || {};
      return formatBucketTipLine(b.passed, b.total, baselinePct[i]);
    });
    const candidateTips = keys.map((k, i) => {
      const a = cats[k]?.after || {};
      return formatBucketTipLine(a.passed, a.total, candidatePct[i]);
    });

    chartInstances.cat = new Chart(canvas, {
      plugins: [barTipLabelPlugin],
      type: "bar",
      data: {
        labels: keys,
        datasets: [
          {
            label: "Baseline pass rate",
            data: baselinePct,
            backgroundColor: "rgba(251, 191, 36, 0.82)",
            borderWidth: 0,
          },
          {
            label: "Candidate pass rate",
            data: candidatePct,
            backgroundColor: "rgba(248, 113, 113, 0.82)",
            borderWidth: 0,
          },
        ],
      },
      options: {
        layout: { padding: { right: 120 } },
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        interaction: {
          mode: "index",
          intersect: false,
        },
        scales: {
          x: {
            min: 0,
            max: 100,
            ticks: { stepSize: 10 },
            title: { display: true, text: "Pass rate within this bucket (%)" },
            grid: { color: "rgba(45, 63, 86, 0.65)" },
          },
          y: { grid: { display: false } },
        },
        plugins: {
          legend: { display: true, position: "bottom" },
          emailCalBarTipLabels: {
            lines: [baselineTips, candidateTips],
          },
          tooltip: {
            callbacks: {
              label(ctx) {
                const k = keys[ctx.dataIndex];
                const row = cats[k] || {};
                const b = row.before || {};
                const a = row.after || {};
                const pct = ctx.parsed.x;
                if (ctx.datasetIndex === 0) {
                  return `Baseline: ${b.passed}/${b.total} (${pct}%)`;
                }
                return `Candidate: ${a.passed}/${a.total} (${pct}%)`;
              },
              afterBody(items) {
                if (!items.length) return [];
                const i = items[0].dataIndex;
                const dlt = cats[keys[i]]?.passed_delta ?? 0;
                return [`Δ passes (candidate − baseline): ${dlt}`];
              },
            },
          },
        },
      },
    });

    if (extras) {
      const changed = keys.filter((_, i) => (deltas[i] ?? 0) !== 0);
      const changedHint =
        changed.length > 0
          ? ` Categories with Δ ≠ 0 this run: ${changed.map((k) => `<code>${esc(k)}</code>`).join(", ")}.`
          : "";
      extras.innerHTML = `<p class="subtle category-delta-note"><strong>Paired bars:</strong> length = pass rate (0–100%). Gray labels show <code>passed/total · %</code>. Hover for Δ.${changedHint}</p>`;
    }
  }

  /** Always-visible pipeline narrative + per-step status from the latest run snapshot. */
  function renderCycleFlow(data, progress) {
    const host = $("#cycle-flow");
    if (!host) return;

    const hasRun = !!data;
    const wf = data?.workflow_reliability?.score;
    const prod = data?.production_failure_discovery?.score;
    const genN = data?.production_failure_discovery?.generated_eval_count;
    const rej = data?.rejected_candidate;
    const imp = data?.self_improvement;
    const lf = data?.langfuse_export ?? data?.default_eval?.langfuse_export;
    const sessions = data?.session_logs?.count;

    /** Horizontal edge between pipeline nodes (vertical layout rotates via CSS). */
    function cycleGraphArrow() {
      return `<svg class="cycle-graph-arrow" viewBox="0 0 52 22" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><path d="M3 11h34m0 0-7.5-7.5M37 11l-7.5 7.5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    }

    function cycleGraphNode(n, title, detailText, variant) {
      const cls = variant ?? "pending";
      const live =
        progress && progress.step != null && Number(progress.step) === n ? " cycle-graph-node-live" : "";
      return `<article class="cycle-graph-node ${cls}${live}" data-step="${n}">
      <div class="cycle-graph-node-num">Step ${n}</div>
      <h4>${esc(title)}</h4>
      <div class="cycle-graph-detail">${esc(detailText)}</div>
    </article>`;
    }

    const wfPerfect = hasRun && wf && wf.total != null && wf.passed === wf.total;
    const wfPartial = hasRun && wf && wf.total != null && wf.passed < wf.total;

    const v1 = wfPerfect ? "ok" : wfPartial ? "warn" : hasRun ? "pending" : "pending";

    const wfEvalMode = data?.workflow_reliability?.eval_mode;
    const d1 = hasRun
      ? wfEvalMode === "openai_tools"
        ? `${fmtScore(wf)} on workflow scenarios via OpenAI tool calls (live LLM).`
        : `${fmtScore(wf)} on local workflow plan checks.`
      : "Three workflow checks: OpenAI tool runs when OPENAI_API_KEY is set, otherwise local plan checks.";

    const d2 = hasRun
      ? `${fmtScore(prod)} on seven production-like scenarios. Failures become rows in generated.jsonl (${genN ?? 0} this run).`
      : "Seven mocked Gmail/Calendar queries; failures are converted into deduped candidate regression evals.";

    const d3 = hasRun
      ? `Baseline prompt rules scored on stable ∪ generated: ${imp?.current_eval_score?.score ?? "—"} (${fmtScore(imp?.current_eval_score)}).`
      : "Evaluate current rules on stable plus freshly generated eval cases.";

    let d4 = "Inject a deliberately weak rule variant; the acceptance gate must not promote it over baseline.";
    let v4 = "pending";
    if (hasRun && rej) {
      if (rej.accepted === false) {
        d4 = `Weak variant correctly rejected (${rej.decision || "gate"}).`;
        v4 = "ok";
      } else {
        d4 = `Unexpected: weak variant accepted (${rej.decision || "gate"}). Investigate harness.`;
        v4 = "bad";
      }
    }

    let d5 = "Mine failures via ImprovementProposer → candidate prompt constraints.";
    let v5 = "pending";
    if (hasRun && imp) {
      const bc = imp.current_prompt_rules?.length ?? 0;
      const cc = imp.candidate_prompt_rules?.length ?? 0;
      const dlt = cc - bc;
      const sign = dlt > 0 ? "+" : "";
      d5 = `${cc} prompt constraints on candidate (${sign}${dlt} vs baseline).`;
      v5 = "ok";
    }

    let d6 = "Require strictly higher suite score and no held-out / per-category regression.";
    let v6 = "pending";
    if (hasRun && imp) {
      d6 = `${imp.accepted ? "Accepted — promoted to prompts/current.md." : "Rejected — kept prior rules."} ${imp.decision || ""}`.trim();
      v6 = imp.accepted ? "ok" : "warn";
    }

    let d7 = "Refresh prompts/*.md, evals/*.jsonl, logs/sessions, SQLite memory, and Langfuse spans.";
    let v7 = "pending";
    if (hasRun) {
      const nx = lf?.exported ?? 0;
      const lfNote = lf?.enabled === false && lf?.reason ? ` Langfuse skipped: ${lf.reason}` : "";
      d7 = `${sessions ?? "—"} session JSON files logged · Langfuse exported ${nx} trace(s).${lfNote}`;
      const lfErr = lf?.errors?.length > 0;
      v7 = lfErr ? "warn" : "ok";
    }

    const steps = [
      [1, "Workflow slice", d1, v1],
      [2, "Production discovery", d2, hasRun ? "ok" : "pending"],
      [3, "Suite baseline", d3, hasRun ? "ok" : "pending"],
      [4, "Sanity gate", d4, v4],
      [5, "Candidate proposal", d5, v5],
      [6, "Promotion gate", d6, v6],
      [7, "Artifacts & traces", d7, v7],
    ];

    const chunks = [];
    steps.forEach(([n, title, detail, variant], idx) => {
      chunks.push(cycleGraphNode(n, title, detail, variant));
      if (idx < steps.length - 1) {
        chunks.push(cycleGraphArrow());
      }
    });

    const banner =
      progress && progress.message
        ? `<div class="cycle-live-banner" role="status" aria-live="polite"><span class="cycle-live-phase">${esc(
            progress.phase || "run"
          )}</span><span class="cycle-live-msg">${esc(progress.message)}</span></div>`
        : "";

    host.innerHTML = `${banner}<div class="cycle-graph-scroll">
      <div class="cycle-graph-row" role="group" aria-label="Self-improvement pipeline graph">${chunks.join("")}</div>
    </div>`;
  }

  function renderMetrics(data) {
    const el = $("#metrics");
    if (!data) {
      el.innerHTML = '<div class="empty">No run yet. Click “Run full pipeline”.</div>';
      return;
    }
    const prod = data.production_failure_discovery?.score;
    const wf = data.workflow_reliability?.score;
    const imp = data.self_improvement;
    const lf = data.default_eval?.langfuse_export;
    const ev = data.eval_validation;

    const lfLine =
      lf?.enabled
        ? lf.status_hint || `Exported ${lf.exported ?? 0} trace(s)`
        : lf?.status_hint || lf?.reason || "—";

    el.innerHTML = `
      <div class="card"><h3>Run time</h3><div class="value">${esc(data.run_at || "—")}</div></div>
      <div class="card"><h3>Production</h3><div class="value">${esc(fmtScore(prod))}</div>
        <div class="hint">Generated evals: ${esc(data.production_failure_discovery?.generated_eval_count ?? "—")}</div></div>
      <div class="card"><h3>Workflow evals</h3><div class="value">${esc(fmtScore(wf))}</div></div>
      <div class="card"><h3>Eval JSONL</h3><div class="value">${esc(ev ? Object.keys(ev).length + " files" : "—")}</div>
        <div class="hint">${esc(ev ? JSON.stringify(ev) : "")}</div></div>
      <div class="card"><h3>Before → after (suite)</h3><div class="value">${esc(
        imp ? `${imp.current_eval_score?.score} → ${imp.candidate_eval_score?.score}` : "—"
      )}</div></div>
      <div class="card"><h3>Heldout</h3><div class="value">${esc(
        imp ? `${imp.current_heldout_score?.score} → ${imp.candidate_heldout_score?.score}` : "—"
      )}</div></div>
      <div class="card"><h3>Langfuse</h3><div class="value">${esc(lf?.enabled ? "on" : "off")}</div>
        <div class="hint">${esc(lfLine)}</div></div>
      <div class="card"><h3>Decision</h3><div class="value" style="font-size:1rem">${esc(imp?.accepted === true ? "Accepted" : imp?.accepted === false ? "Rejected" : "—")}</div>
        <div class="hint">${esc(imp?.decision || "")}</div></div>
    `;
  }

  function renderProduction(data) {
    const el = $("#production-table");
    const runs = data?.production_failure_discovery?.runs;
    if (!runs?.length) {
      el.innerHTML = '<div class="empty">No production runs in snapshot.</div>';
      return;
    }
    const rows = runs
      .map(
        (r) => `
      <tr>
        <td>${esc(r.scenario_id)}</td>
        <td>${esc(r.query)}</td>
        <td class="${r.passed ? "pass" : "fail"}">${r.passed ? "pass" : "fail"}</td>
        <td>${esc(r.failure_reason || "—")}</td>
        <td>${esc((r.tool_calls || []).map((t) => t.tool).join(", "))}</td>
      </tr>`
      )
      .join("");
    el.innerHTML = `
      <div class="table-wrap"><table class="data"><thead><tr>
        <th>Scenario</th><th>Query</th><th>Result</th><th>Reason</th><th>Tools</th>
      </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  function renderImprovement(data) {
    const el = $("#improvement-panel");
    const imp = data?.self_improvement;
    if (!imp) {
      el.innerHTML = '<div class="empty">No improvement block.</div>';
      return;
    }
    const cats = imp.category_delta || {};
    const pills = Object.entries(cats)
      .map(([k, v]) => `<span class="cat-pill">${esc(k)} ${v.passed_delta >= 0 ? "+" : ""}${v.passed_delta}</span>`)
      .join("");
    const brules = (imp.current_prompt_rules || []).map((r) => `<li><code>${esc(r)}</code></li>`).join("");
    const crules = (imp.candidate_prompt_rules || []).map((r) => `<li><code>${esc(r)}</code></li>`).join("");
    el.innerHTML = `
      <div class="improvement-grid">
        <div class="card">
          <h3>Promotion gate</h3>
          <p><strong>Candidate accepted:</strong> ${imp.accepted ? "yes" : "no"}</p>
          <p>${esc(imp.decision || "")}</p>
          <div class="cat-grid">${pills}</div>
        </div>
        <div class="card">
          <h3>Baseline prompt constraints</h3>
          <ul class="rules-mini">${brules || "<li>—</li>"}</ul>
        </div>
        <div class="card">
          <h3>Candidate prompt constraints</h3>
          <ul class="rules-mini">${crules || "<li>—</li>"}</ul>
        </div>
      </div>`;
  }

  function renderWorkflow(data) {
    const el = $("#workflow-panel");
    const runs = data?.workflow_reliability?.runs;
    if (!runs?.length) {
      el.innerHTML = '<div class="empty">No workflow eval snapshot.</div>';
      return;
    }
    const rows = runs
      .map(
        (r) => `
      <tr>
        <td>${esc(r.case?.id)}</td>
        <td class="${r.passed ? "pass" : "fail"}">${r.passed ? "pass" : "fail"}</td>
        <td>${esc(r.failure_reason || "—")}</td>
      </tr>`
      )
      .join("");
    el.innerHTML = `<div class="table-wrap"><table class="data"><thead><tr>
      <th>Case</th><th>Result</th><th>Reason</th>
    </tr></thead><tbody>${rows}</tbody></table></div>`;
  }

  async function loadLatestRun() {
    try {
      const [j, liveLf] = await Promise.all([
        fetchJson("/api/latest-run"),
        fetchJson("/api/langfuse-status").catch(() => null),
      ]);
      if (!j.exists || !j.data) {
        lastCycleSnapshot = null;
        renderMetrics(null);
        renderCycleFlow(null, null);
        $("#production-table").innerHTML = '<div class="empty">No logs/run_latest.json yet.</div>';
        $("#improvement-panel").innerHTML = "";
        $("#workflow-panel").innerHTML = "";
        renderLangfuseIngest(null, liveLf);
      } else {
        const data = mergeLangfuseLive(JSON.parse(JSON.stringify(j.data)), liveLf);
        lastCycleSnapshot = data;
        renderMetrics(data);
        renderCycleFlow(data, null);
        renderProduction(data);
        renderImprovement(data);
        renderWorkflow(data);
        renderLangfuseIngest(data, liveLf);
      }
      await loadSpecReadout();
      await loadRunHistory();
    } catch (e) {
      console.error(e);
      $("#metrics").innerHTML = `<div class="empty fail">${esc(e.message)}</div>`;
      await loadSpecReadout();
      renderCycleFlow(lastCycleSnapshot, null);
      await loadRunHistory();
    }
  }

  async function loadSessions() {
    try {
      const j = await fetchJson("/api/sessions?limit=60");
      $("#session-count").textContent = `${j.sessions?.length ?? 0} files`;
      const body = (j.sessions || [])
        .map((s) => {
          return `<tr><td><button type="button" class="ghost session-link" data-name="${esc(s.name)}">${esc(s.name)}</button></td>
          <td>${new Date(s.mtime * 1000).toLocaleString()}</td><td>${s.size}</td></tr>`;
        })
        .join("");
      $("#sessions-table").innerHTML = body
        ? `<div class="table-wrap"><table class="data"><thead><tr><th>Session</th><th>Modified</th><th>Bytes</th></tr></thead><tbody>${body}</tbody></table></div>`
        : '<div class="empty">No sessions logged.</div>';

      $("#sessions-table").onclick = async (ev) => {
        const btn = ev.target.closest(".session-link");
        if (!btn) return;
        const name = btn.getAttribute("data-name");
        const detail = await fetchJson("/api/sessions/" + encodeURIComponent(name));
        $("#modal-title").textContent = name;
        $("#modal-json").textContent = JSON.stringify(detail.data, null, 2);
        $("#modal-bg").classList.add("open");
      };
    } catch (e) {
      $("#sessions-table").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    }
  }

  async function pollPipeline() {
    try {
      const st = await fetchJson("/api/pipeline/status");
      const badge = $("#job-badge");
      const logEl = $("#pipeline-log");
      const btn = $("#btn-run");

      badge.textContent = st.status;
      badge.className = "badge " + (st.status === "running" ? "running" : st.status === "success" ? "ok" : st.status === "error" ? "err" : "");

      logEl.textContent = st.output || (st.status === "idle" ? "Idle. Output from the last run appears here." : "");

      btn.disabled = st.status === "running";

      if (st.status === "running") {
        await refreshCycleLiveProgress();
        if (!progressTimer) progressTimer = setInterval(refreshCycleLiveProgress, 350);
      } else {
        stopProgressPoll();
      }

      if (st.status === "success" || st.status === "error") {
        if (pollTimer) {
          clearInterval(pollTimer);
          pollTimer = null;
        }
        stopProgressPoll();
        await loadLatestRun();
        await loadSessions();
      }
    } catch (e) {
      console.error(e);
    }
  }

  async function startPipeline() {
    const j = await fetchJson("/api/pipeline/run", { method: "POST" });
    if (!j.started && j.reason === "already_running") {
      alert("Pipeline already running.");
      return;
    }
    $("#btn-run").disabled = true;
    if (!pollTimer) pollTimer = setInterval(pollPipeline, 900);
    pollPipeline();
  }

  function setupModal() {
    $("#modal-close").onclick = () => $("#modal-bg").classList.remove("open");
    $("#modal-bg").onclick = (ev) => {
      if (ev.target.id === "modal-bg") $("#modal-bg").classList.remove("open");
    };
  }

  $("#btn-run").onclick = () => startPipeline().catch((e) => alert(e.message));
  $("#btn-refresh").onclick = () => {
    loadLatestRun();
    loadSpecReadout();
    loadSessions();
    pollPipeline();
  };

  setupModal();

  loadSpecReadout();
  loadLatestRun();
  loadSessions();
  pollPipeline();
})();
