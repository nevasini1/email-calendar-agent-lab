# Local Langfuse Tracing

This project uses Langfuse as the default eval trace backend. Normal JSON logs still work as a local mirror if Langfuse is not reachable or credentials are missing.

## 1. Start Langfuse Locally

```bash
cd /Users/okay/Documents/email-calendar-agent-lab
bash scripts/start_langfuse_local.sh
```

This clones the official Langfuse repository into `.langfuse/langfuse` and runs its Docker Compose setup. Langfuse recommends replacing all `CHANGEME` secrets in the compose file before using it beyond local testing.

Open `http://localhost:3000`, create an account/project, then copy the project keys.

## 2. Configure This Lab

```bash
cp .env.langfuse.example .env.langfuse.local
```

Fill in:

```bash
LANGFUSE_HOST=http://localhost:3000
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
```

Install the project dependencies:

```bash
python3 -m pip install -e .
```

## 3. Run With Tracing

```bash
/usr/bin/env PYTHONPATH=src python3 -m email_calendar_lab.run_cycle
```

`run_cycle` auto-loads `.env.langfuse.local`. You can also run `bash scripts/run_with_langfuse.sh` if you prefer to source the env file explicitly.

Each harness session is exported as a Langfuse trace with:

- Root span: scenario/session execution.
- Generation: deterministic `gpt-5.4-mini` policy output.
- Tool spans: Gmail/Calendar tool calls with arguments, result counts, and evidence IDs.
- Eval span: pass/fail, failure reason, root cause, and expected evidence.

The run summary still writes `logs/run_latest.json` and `logs/sessions/*.json` as the JSON mirror. `logs/run_latest.json` includes `default_eval.backend = "langfuse"` and the Langfuse export status.

To disable Langfuse export for a run:

```bash
LANGFUSE_TRACING_ENABLED=false /usr/bin/env PYTHONPATH=src python3 -m email_calendar_lab.run_cycle
```

