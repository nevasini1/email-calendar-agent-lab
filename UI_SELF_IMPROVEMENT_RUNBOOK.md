# UI Self-Improvement Runbook

This file describes how to run the email/calendar agent lab and how the dashboard should explain the self-improvement loop end to end.

## What The Loop Actually Does

1. Load the current prompt config.
   - Preferred: `prompts/current.json`
   - Fallback: `prompts/current.md`
   - Last fallback: `BASELINE_CONFIG` in `agent.py`
2. Run production-like scenarios through `HarnessCore`.
3. Score each answer using answer text, tool calls, evidence IDs, and required tool args.
4. Convert production failures into candidate generated evals.
5. Merge fresh generated evals with carried-forward `evals/generated.jsonl`.
6. Score the loaded config on stable plus generated evals.
7. Score a deliberately bad evidence-skipping candidate as a sanity check.
8. Propose a candidate config from failure root causes.
9. Score candidate on stable plus generated evals and heldout evals.
10. Accept only if suite score improves and heldout does not regress.
11. Save session traces, reflections, memory, generated evals, prompt artifacts, and `logs/run_latest.json`.
12. Append a compact real-data row to `logs/run_history.jsonl`.

## Dashboard Data Sources

- `logs/run_history.jsonl`: multi-run charts and per-run self-improvement ledger.
- `logs/run_latest.json`: latest-run detail panels.
- `logs/run_progress.json`: live pipeline phase while a run is active.
- `logs/sessions/*.json`: per-scenario traces with prompts, tool calls, answers, and eval decisions.
- `evals/generated.jsonl`: carried-forward candidate evals derived from production failures.
- `prompts/current.json`: machine-readable promoted config loaded by the next run.

The dashboard must never hard-code improvement. If the suite is already solved, charts should show flat 100% lines and explain that no further promotion happened.

## How To Run

Start the dashboard server:

```bash
cd /Users/okay/Documents/email-calendar-agent-lab
PYTHONPATH=src python3 -m email_calendar_lab.dashboard
```

Open:

```text
http://127.0.0.1:8765/
```

Do not open `src/email_calendar_lab/dashboard/templates/index.html` with `file://`. That is a Jinja template and cannot call the local APIs. The template now shows a warning if opened directly.

Run one full cycle:

```bash
cd /Users/okay/Documents/email-calendar-agent-lab
PYTHONPATH=src python3 -m email_calendar_lab.run_cycle
```

Run five deterministic local cycles for a reproducible demo:

```bash
cd /Users/okay/Documents/email-calendar-agent-lab
for i in 1 2 3 4 5; do
  PYTHONPATH=src \
  OPENAI_API_KEY= \
  EMAIL_CALENDAR_AGENT_BACKEND=deterministic \
  LANGFUSE_TRACING_ENABLED=false \
  DSPY_GEPA_ENABLED=false \
  PRODUCTION_SCENARIOS_SOURCE=static \
  STABLE_SCENARIOS_SOURCE=static \
  HELDOUT_SCENARIOS_SOURCE=static \
  python3 -m email_calendar_lab.run_cycle
done
```

Optional clean slate command. This deletes local run artifacts and memory:

```bash
rm -f logs/run_latest.json logs/run_history.jsonl logs/run_progress.json \
  evals/generated.jsonl \
  prompts/current.json prompts/current.md prompts/candidate.md prompts/rejected_candidate.md prompts/baseline.md \
  memory/email_calendar_lab.sqlite memory/email_calendar_lab.sqlite-wal memory/email_calendar_lab.sqlite-shm
find logs/sessions -type f -name '*.json' -delete
```

## Dashboard Sections To Maintain

- Stage pass rates by run: workflow, production, suite baseline/candidate, heldout baseline/candidate.
- Suite and heldout score trajectory: raw score path; candidate markers may be nudged only visually when tied.
- Trace to auto-eval pipeline: session traces, reflections, generalizable reflections, fresh generated evals, carried evals, candidate skills.
- Promotion gate deltas: candidate minus baseline suite score, candidate minus baseline heldout score, and accepted/rejected flag.
- Per-run improvement ledger: loaded config, generated eval counts, scoring, sanity gate, candidate rules, promotion decision, reflection artifacts.
- Production discovery table: scenario-level pass/fail and tool calls.
- Recent sessions: inspect raw trace JSON.

## Reading A Typical Five-Run Demo

With static deterministic scenarios, the first clean run usually promotes a candidate because the weak baseline fails several categories. Later runs load the promoted config from `prompts/current.json`, carry generated evals forward, and often stay at 100%. That is not fake improvement; it means the small deterministic suite is saturated. The correct UI behavior is to show the plateau and say why.

## Verification

```bash
PYTHONPATH=src python3 -m compileall -q src
PYTHONPATH=src python3 -m email_calendar_lab.validate_evals
node --check src/email_calendar_lab/dashboard/static/dashboard.js
curl -s 'http://127.0.0.1:8765/api/run-history?limit=5' | jq '.count'
```
