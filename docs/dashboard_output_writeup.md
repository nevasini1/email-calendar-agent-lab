# Dashboard Output Writeup

This dashboard is a local reliability lab for a self-improving email/calendar agent. It is running at `http://127.0.0.1:8765/` and reads real local artifacts from `logs/`, `evals/`, `prompts/`, `memory/`, and Langfuse export results.

## Short Talk Track

This project demonstrates two connected loops. First, the eval-creation loop runs a live OpenAI email/calendar agent on production-like scenarios, records failures, classifies the failure mode, and turns those failures into generated evals. Second, the self-improvement loop scores the current prompt and a proposed candidate prompt against stable plus generated evals, then checks held-out evals before accepting anything. The dashboard is not hard-coded preview data: it is reading JSONL run history, session traces, eval files, prompt files, and Langfuse export counts from the current workspace.

The latest completed run used `openai-live` with `gpt-5.4-mini`. It ran against mocked Gmail and Calendar tools, exported 94 session traces to Langfuse with zero export errors, generated/retained 23 failure-derived evals, and rejected the proposed candidate because the guardrail detected a suite category regression. That rejection is important: the system is not just chasing a higher aggregate score; it blocks changes that improve one area while breaking another.

## Current Dashboard State

- Latest completed run: `2026-05-02T19:27:08Z`
- Provider/model: `openai-live / gpt-5.4-mini`
- Run history rows: `9` total retained rows
- Live OpenAI rows: `4`
- Earlier archived local rows: `5`
- Latest production discovery score: `3/7 (0.429)`
- Latest generated eval count: `23`
- Latest workflow score: `3/3 (1.0)`
- Latest suite baseline to candidate: `1/26 (0.038) -> 2/26 (0.077)`
- Latest held-out baseline to candidate: `0/3 (0.0) -> 1/3 (0.333)`
- Latest Langfuse export: `94` session traces, `0` export errors
- Latest promotion decision: `rejected by guardrail: suite category regression`

One prior live run used `gpt-5.4-nano` because the environment default was not pinned for that one shell run. The later live runs were explicitly pinned to `gpt-5.4-mini`.

## What Each Page Section Means

### Top Summary Cards

These cards summarize the latest completed full pipeline run.

- **Run time**: timestamp of the latest completed run written to `logs/run_latest.json`.
- **Production**: score on production-like discovery scenarios. These are realistic questions such as next meeting, last sync, last flight, free time with Alex, and last Sarah meeting before the offsite.
- **Generated evals**: count of durable evals produced from observed production failures.
- **Workflow evals**: checks that the pipeline can perform the expected workflow steps.
- **Eval JSONL**: confirms the eval files exist and how many rows each contains.
- **Before -> after (suite)**: baseline prompt score compared with candidate prompt score on stable plus generated evals.
- **Heldout**: baseline and candidate score on held-out evals that are not used to generate candidate prompt changes.
- **Langfuse**: whether trace export is enabled and how many session traces were exported.
- **Decision**: the promotion result. The latest candidate was rejected because a category regressed.

### Autonomous Reliability Lab

This section maps the project to the assignment requirements.

- **Base email/calendar agent**: the project has a baseline agent using `gpt-5.4-mini`.
- **Mock Gmail + Calendar tools**: there are mocked Gmail/calendar-style tools, not real account integration.
- **Eval creation loop**: observed failures are converted into generated eval rows.
- **Self-improvement loop**: the current prompt and candidate prompt are scored and compared.
- **Run logs and trace evidence**: each scenario produces a session JSON trace and Langfuse trace.
- **Regression / held-out tracking**: held-out evals are scored to prevent overfitting.
- **Anti-overfitting guardrails**: generated evals stay quarantined/candidate unless promotion rules pass.

### Agent Substrate

This shows the agent configuration used by the latest run.

- Baseline config: `weak-baseline`
- Loaded/current config: `weak-baseline+candidate`
- Provider: `openai-live`
- Mode: `build`
- Prompt constraints include:
  - `minimal_tool_use`
  - `exclude_cancelled_events`
  - `prefer_human_participants`
  - `parse_flight_destination`
  - `preserve_source_timezones`
  - `clarify_ambiguous_contacts`
  - `respect_temporal_anchors`

These are not final proof of success by themselves. They are the prompt/harness changes being tested by the eval loop.

### Mock Data

The project uses synthetic Gmail/calendar fixtures:

- Fixture clock: `2026-05-01T10:00:00-04:00`
- Gmail messages: `8`, including `2` flight emails
- Calendar events: `13`, including `4` recurring events and `2` cancelled events
- Contacts: `6`, including ambiguous names:
  - Alex Chen / Alex Rivera
  - Sarah Patel / Sara Park

This is why the system can test realistic tool-use failures without touching a real account.

### MCP-Style Tools

The agent can call three mocked tools:

- `gmail.search_emails(query, after, before)`
- `calendar.search_events(query, time_min, time_max, attendee, include_cancelled)`
- `calendar.free_busy(attendee, start, end)`

The dashboard tables and session modals show whether the agent called these tools and what evidence IDs came back.

### Eval Set Inventory

The eval set is split into separate files:

- `workflow.jsonl`: `3` workflow checks
- `stable.jsonl`: `3` stable regression evals
- `generated.jsonl`: `23` failure-derived candidate evals
- `heldout.jsonl`: `3` held-out evals

Generated evals are deliberately quarantined. They represent observed failures, but they are not blindly promoted into the stable suite.

### Failure-Mode Coverage

This table shows whether the evals cover the realistic edge cases from the assignment:

- time zones
- recurring meetings
- cancelled events
- ambiguous contacts
- attendees vs senders
- flight emails
- free/busy lookup
- last-before-anchor temporal reasoning

The important point is that the eval set grows around actual failures, not a manually fixed benchmark only.

### Failure-Derived Eval Lineage

This shows examples of generated evals and where they came from. Examples include:

- `generated_prod_next_meeting`: missed “Ops review” at “1:00 PM”
- `generated_prod_last_sync`: missed “Dana Kim”
- `generated_prod_last_flight`: missed “SFO”
- `generated_prod_flight_arrival_timezone`: missed “11:35 AM PT”
- `generated_prod_free_time_alex`: failed to clarify which Alex
- `generated_prod_sarah_before_offsite`: missed “Sarah roadmap review” / “Apr 22”
- `generated_prod_recurring_last_meeting`: missed “Apr 30”

Each row is an observed failure converted into a reusable eval case.

### Prompt Change Evidence

This section shows an earlier accepted prompt change from the initial local run:

- Source run: May 2, 12:52:34 PM
- Suite: `0/9 -> 9/9`
- Held-out: `2/3 -> 3/3`
- Decision: accepted because it improved the generated/stable suite without held-out regression

That explains why the current config has more prompt constraints than the original weak baseline. Later live OpenAI candidates were more cautious and were rejected when guardrails detected regressions.

### Anti-Overfit Guardrails

The system uses several guardrails:

- Stable evals remain fixed regressions.
- Generated evals are failure-derived candidates before promotion.
- Held-out evals are scored but not used to generate prompt changes.
- Candidate prompt changes require suite gain and no held-out/category regression.
- A deliberately bad candidate is also tested and must be rejected.

Latest artifact decision summary:

- accepted: `0`
- rejected: `1`
- quarantined: `30`

This is the main anti-overfitting story: the system can discover failures, but it does not immediately optimize only for those failures.

## Graph-By-Graph Presenter Notes

Use this section when pointing at the visual charts in the dashboard. Every graph below is generated from `logs/run_history.jsonl` or `logs/run_latest.json`; the dashboard is not inventing values.

### Graph 1: Stage Pass Rates By Run

The run history reads from `logs/run_history.jsonl`.

The dashboard is configured to plot live OpenAI rows by default. There are `4` live OpenAI rows and `5` older archived local rows. The older local rows explain why some earlier history looked artificially perfect; the live view avoids mixing those with the live OpenAI samples.

Point to this grouped bar chart and say:

“Each cluster is one completed live OpenAI run. Each colored bar is a stage of the pipeline. The y-axis is pass rate as a percentage, so `3/7` appears as about `42.9%`. This graph lets us compare workflow checks, production discovery, baseline suite, candidate suite, held-out baseline, and held-out candidate across runs.”

The six bars in each cluster mean:

- **Workflow**: whether the pipeline workflow checks passed.
- **Production (baseline)**: how the currently loaded agent performed on production-like discovery scenarios.
- **Suite baseline**: baseline/current prompt score on stable plus generated evals.
- **Suite candidate**: proposed candidate prompt score on the same suite.
- **Held-out baseline**: baseline/current prompt score on held-out evals.
- **Held-out candidate**: proposed candidate prompt score on held-out evals.

Current live clusters:

- Run `#6`: workflow `3/3 = 100%`, production `0/7 = 0%`, suite baseline `3/10 = 30%`, suite candidate `2/10 = 20%`, held-out baseline `0/3 = 0%`, held-out candidate `0/3 = 0%`.
- Run `#7`: workflow `3/3 = 100%`, production `0/7 = 0%`, suite baseline `2/17 = 11.8%`, suite candidate `1/17 = 5.9%`, held-out baseline `1/3 = 33.3%`, held-out candidate `1/3 = 33.3%`.
- Run `#8`: workflow `3/3 = 100%`, production `2/7 = 28.6%`, suite baseline `0/22 = 0%`, suite candidate `0/22 = 0%`, held-out baseline `0/3 = 0%`, held-out candidate `1/3 = 33.3%`.
- Run `#9`: workflow `3/3 = 100%`, production `3/7 = 42.9%`, suite baseline `1/26 = 3.8%`, suite candidate `2/26 = 7.7%`, held-out baseline `0/3 = 0%`, held-out candidate `1/3 = 33.3%`.

What to emphasize:

- The workflow bar stays at `100%`, meaning the pipeline machinery itself is running.
- Production discovery improves from `0/7` to `3/7` across live runs.
- The suite gets harder over time: totals grow from `10` to `26` because generated evals are added from observed failures.
- The candidate sometimes improves held-out while still being rejected, because promotion requires no regressions, not just a higher headline score.

### Graph 2: Suite & Held-Out Score (0-1)

Point to this line chart and say:

“This chart shows the same before/after comparison on a normalized `0-1` score scale. Solid lines are baseline/current prompt scores; dashed lines with larger markers are candidate scores. The dashboard slightly nudges candidate points upward only when a candidate ties the baseline exactly, so both traces remain visible on the canvas. The tooltip still shows the real score.”

The four lines mean:

- **Suite baseline**: current prompt on stable plus generated evals.
- **Suite candidate**: proposed prompt on stable plus generated evals.
- **Held-out baseline**: current prompt on held-out evals.
- **Held-out candidate**: proposed prompt on held-out evals.

Current live line values:

- Run `#6`: suite `0.300 -> 0.200`, held-out `0.000 -> 0.000`.
- Run `#7`: suite `0.118 -> 0.059`, held-out `0.333 -> 0.333`.
- Run `#8`: suite `0.000 -> 0.000`, held-out `0.000 -> 0.333`.
- Run `#9`: suite `0.038 -> 0.077`, held-out `0.000 -> 0.333`.

What to emphasize:

- Run `#9` is the interesting one: the candidate improved both aggregate suite score and held-out score.
- Even with that improvement, the candidate was rejected because a category regressed. This is the clearest evidence that the system is not blindly optimizing a single aggregate line.
- Run `#8` shows why aggregate held-out improvement is not enough by itself: suite gain was `0`, so the candidate was not promoted.

### Graph 3: Suite Passes By Category - Baseline Vs Candidate

Point to this horizontal bar chart and say:

“This is the regression-tracking chart. Each row is a specific run plus a suite category that existed in that run. The amber bar is baseline pass rate inside that category, and the coral bar is candidate pass rate inside that same category. The labels show `passed/total · %`, and hovering shows the candidate-minus-baseline pass delta.”

Why this graph matters:

- It explains why a candidate can be rejected even when the overall suite score improves.
- It avoids hiding regressions inside a single aggregate number.
- It only plots categories that actually existed in that run’s stable plus generated suite, so it does not invent empty categories.

Latest run `#9` category readout:

- `ambiguous_contacts`: baseline `0/5`, candidate `0/5`, no change.
- `cancelled_events`: baseline `1/4`, candidate `0/4`, regression of `-1` pass.
- `flight_emails`: baseline `0/4`, candidate `1/4`, improvement of `+1` pass.
- `attendees_vs_senders`: baseline `0/2`, candidate `1/2`, improvement of `+1` pass.
- `time_zones`: baseline `0/4`, candidate `0/4`, no change.
- `last_before_anchor`: baseline `0/4`, candidate `0/4`, no change.
- `recurring_meetings`: baseline `0/3`, candidate `0/3`, no change.

What to say while pointing at latest run `#9`:

“Here is the reason the gate rejected the candidate. The candidate gained in `flight_emails` and `attendees_vs_senders`, but it lost `cancelled_events`, dropping from `1/4` to `0/4`. The aggregate moved up from `1/26` to `2/26`, but the per-category guardrail caught a regression. That is the anti-overfitting behavior.”

### Graph 4: Trace And Artifact Counts

Point to this bar chart and say:

“This graph shows the amount of real trace evidence and derived artifacts produced by each run. As the generated eval suite grows, each run has more scenarios to evaluate, so the number of session traces and reflections grows too.”

The bars mean:

- **Session traces**: local JSON session files written by the harness and exported to Langfuse.
- **Reflections**: post-run reflection records created from those traces.
- **Generalizable reflections**: reflections judged useful beyond a one-off case.
- **Fresh generated evals**: new evals created from failures in that run.
- **Carried generated evals**: previously generated evals retained into this run.
- **Candidate skills**: mined candidate lessons/artifacts from reflections.

Current live values:

- Run `#6`: `46` session traces, `46` reflections, `45` generalizable reflections, `7` fresh generated evals, `6` carried generated evals, `4` candidate skills.
- Run `#7`: `67` session traces, `67` reflections, `64` generalizable reflections, `7` fresh generated evals, `7` carried generated evals, `4` candidate skills.
- Run `#8`: `82` session traces, `82` reflections, `81` generalizable reflections, `5` fresh generated evals, `14` carried generated evals, `5` candidate skills.
- Run `#9`: `94` session traces, `94` reflections, `92` generalizable reflections, `4` fresh generated evals, `19` carried generated evals, `7` candidate skills.

What to emphasize:

- The trace count grows from `46` to `94` because the suite expands as failures become evals.
- Fresh generated evals decrease from `7` to `4` by the latest run, which suggests fewer brand-new unique failures were added in that cycle.
- Carried generated evals rise from `6` to `19`, which shows the durable eval set is accumulating rather than being overwritten.
- Langfuse export count matches the session trace count in the latest run: `94` traces exported, `0` errors.

### Graph 5: Promotion Gate Deltas

Point to this graph and say:

“This chart is the promotion decision compressed into three bars per run. Green is suite gain, blue is held-out gain, and yellow would be `1` if the candidate was promoted. In the live runs, promotion stays at `0`, meaning every live candidate was rejected.”

The three bars mean:

- **Suite gain**: `candidate suite score - baseline suite score`.
- **Heldout gain**: `candidate held-out score - baseline held-out score`.
- **Promotion accepted**: `1` when promoted, `0` when rejected.

Current live gate deltas:

- Run `#6`: suite gain `-0.100`, held-out gain `0.000`, promotion `0`, rejected for suite category regression.
- Run `#7`: suite gain `-0.059`, held-out gain `0.000`, promotion `0`, rejected for suite category regression.
- Run `#8`: suite gain `0.000`, held-out gain `+0.333`, promotion `0`, rejected because suite gain was below `0.02`.
- Run `#9`: suite gain `+0.039`, held-out gain `+0.333`, promotion `0`, rejected for suite category regression.

What to emphasize:

- This graph proves the gate can reject candidates even when held-out improves.
- Run `#9` is the strongest demo example: both suite and held-out gains are positive, but promotion remains `0` because category regression is not allowed.
- The graph separates measurement from decision: a candidate can show gains and still fail the safety criteria.

### Diagram: Langfuse Ingest

This is a diagram rather than a numeric chart. Point to it and say:

“Each agent scenario becomes a Langfuse trace. Inside that trace we store the model generation, the mocked Gmail/Calendar tool calls, and the eval result. That gives us an audit trail from prompt to tool evidence to final pass/fail.”

The diagram means:

- Root trace: `email-calendar-lab/<scenario_id>`.
- Generation span: the live model answer for that scenario.
- Tool spans: each `gmail.search_emails`, `calendar.search_events`, or `calendar.free_busy` call.
- Eval span: pass/fail, failure reason, root cause, and expected evidence.
- Latest run: `94` session traces exported to Langfuse with `0` errors.

### Diagram: Self-Improvement Cycle

This is the process graph for the full pipeline. Point to it and say:

“This is the order of operations for one full run. The run starts with workflow checks, then production discovery, then eval creation, then baseline scoring, then candidate scoring, then promotion or rejection, then artifact export.”

The nodes mean:

1. **Workflow evals**: check that the pipeline can execute the expected workflow.
2. **Production-like discovery**: run realistic email/calendar questions and collect failures.
3. **Baseline suite**: score the current prompt on stable plus generated evals and held-out evals.
4. **Sanity gate**: score an intentionally weak candidate and ensure it is rejected.
5. **Candidate suite**: propose a real candidate and score it on the same suite plus held-out.
6. **Promotion gate**: accept only if score improves without held-out or per-category regression.
7. **Artifacts**: write eval JSONL, prompts, session traces, Langfuse exports, reflections, memory, and run history.

Important caveat:

- If a run is killed mid-flight, the progress node may show the last heartbeat from the killed process. The authoritative completed-run metrics come from `logs/run_latest.json` and `logs/run_history.jsonl`.

### Per-Run Improvement Loop

Each card is one full pipeline cycle:

1. Load current config.
2. Run production discovery.
3. Convert failures to generated evals.
4. Score baseline.
5. Score intentionally bad candidate.
6. Propose real candidate.
7. Score candidate.
8. Apply promotion/rejection gate.
9. Write sessions, Langfuse traces, reflections, memory, and run history.

This is the end-to-end loop the assignment asks for.

### Langfuse Ingest

Each session is exported as a Langfuse trace:

- root trace: `email-calendar-lab/<scenario_id>`
- generation span: model answer
- tool spans: each Gmail/calendar tool call
- eval span: pass/fail decision and failure reason

The latest completed run exported `94` traces with `0` errors.

### Self-Improvement Cycle

This is the staged narrative of the pipeline:

1. Workflow evals
2. Production-like discovery
3. Baseline scoring
4. Bad-candidate sanity gate
5. Candidate proposal and scoring
6. Promotion decision
7. Artifact writing

If the page shows a stale progress message after a killed terminal process, the authoritative source is the latest completed run in `logs/run_latest.json` and `logs/run_history.jsonl`.

## What To Say In A Demo

“This is a small autonomous reliability lab for an email/calendar agent. It uses mocked Gmail and Calendar tools, but the agent runs are live OpenAI calls. The system starts with production-like questions, finds concrete failures, converts those failures into generated evals, then tests prompt or harness candidates against stable, generated, and held-out evals.

The dashboard is reading real artifacts, not hard-coded numbers. The latest completed live run used `gpt-5.4-mini`, passed `3/7` production discovery scenarios, retained `23` generated evals, and exported `94` Langfuse traces with zero export errors. The candidate improved aggregate suite score from `1/26` to `2/26` and held-out from `0/3` to `1/3`, but the gate rejected it because one suite category regressed. That is exactly the anti-overfitting behavior we want: do not promote changes just because one number improves.

The most important design choice is the split between generated evals and held-out evals. Generated evals come from real observed failures and stay quarantined until there is enough evidence. Held-out evals are scored but not used to generate prompt changes. This lets the system learn from failures while reducing overfitting to a fixed benchmark.”

## Honest Caveats

- Gmail and Calendar are mocked with synthetic data; no real account integration is used.
- The evaluator is deterministic because pass/fail and regression detection need to be reproducible.
- There are older local deterministic rows retained in history, but the dashboard defaults to live OpenAI rows.
- One aborted run left a stale progress heartbeat; completed-run metrics come from `run_latest.json` and `run_history.jsonl`.
- The current live agent still has low absolute scores. The project demonstrates the reliability loop and guardrails more than a fully solved calendar assistant.
