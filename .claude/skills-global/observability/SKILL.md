---
name: observability
description: "Wire up dashboards, alerts, and health checks for a module or service. Triggered by 'add observability to', 'set up monitoring for', 'create a health check', 'wire up alerts', 'observability pass'."
allowed-tools: Read, Edit, Write, Grep, Glob
---

# Skill: /observability

## Purpose
Wire up dashboards, alerts, and health checks so a module's runtime behavior is visible from outside the code — without touching the module's internal logic.

## When to Use
- A module has shipped and needs monitoring before it goes to production
- An incident revealed that no one knew the service was degraded until a user complained
- A health check endpoint is missing or incomplete
- Metrics exist in logs but aren't surfaced anywhere queryable
- The user says "add observability to X", "set up monitoring", or "I want alerts when X fails"

## Steps

1. **Identify the target.** If invoked with no argument, ask: "Which module or service should I add observability to?" If a path is given, read the relevant files.

2. **Assess what already exists.** Check for:
   - Existing health check endpoints (`/health`, `/status`, `/ready`)
   - Log aggregation setup (structured JSON logs, log levels)
   - Metrics emission (Prometheus counters/gauges, StatsD, OpenTelemetry)
   - Existing dashboards or alert rules

3. **Apply the 6 observability techniques as appropriate.** Use the question→technique matrix:

   | Question | Technique |
   |----------|-----------|
   | "Is the module alive?" | Health check endpoint returning `{"status": "ok", "checks": {...}}` |
   | "What is it doing?" | Structured logging with `logging.getLogger(__name__)` at INFO level on entry/exit |
   | "How fast is it?" | Timing with `time.perf_counter()` logged at DEBUG; or `opentelemetry.trace.get_tracer(__name__)` for spans |
   | "Is it failing?" | Exception logging with `logging.exception("context: %s", value)` + raise chain preservation via `raise X from e` |
   | "What state is it in?" | `__repr__` on key model objects so log lines are meaningful |
   | "Is it healthy over time?" | Counters/gauges via `opentelemetry.metrics.get_meter(__name__)` or Prometheus client |

   Python examples for each:

   ```python
   # Health check
   def health() -> dict:
       return {"status": "ok", "queue_depth": Queue.count(), "last_run": last_run_ts}

   # Structured logging
   logger = logging.getLogger(__name__)
   logger.info("Processing job", extra={"job_id": job.id, "type": job.type})

   # Timing with OpenTelemetry
   tracer = opentelemetry.trace.get_tracer(__name__)
   with tracer.start_as_current_span("process_job") as span:
       span.set_attribute("job.id", job.id)

   # Exception chain preservation
   try:
       result = external_call()
   except ExternalError as e:
       raise ProcessingError("job failed") from e

   # Meaningful repr
   class Job:
       def __repr__(self) -> str:
           return f"Job(id={self.id!r}, status={self.status!r})"

   # Counter metric
   meter = opentelemetry.metrics.get_meter(__name__)
   job_counter = meter.create_counter("jobs_processed_total")
   job_counter.add(1, {"status": "success"})
   ```

4. **Write the health check endpoint** if one is missing and the module is a service.

5. **Update or create dashboard configuration** (e.g., Grafana JSON, a `ui/` template, or a Jinja snippet) to surface the new metrics.

6. **Document what to alert on.** Add a comment block near the metric definitions listing:
   - Alert name
   - Threshold
   - Severity
   - Runbook pointer

## Output
Health check endpoint, metric instrumentation code, and updated dashboard configuration — all wired into the existing monitoring stack.

## Anti-Patterns
- Use /deepen when the goal is adding logging and tracing to code — /observability is for external monitoring surfaces.
- Do not add monitoring without first checking what already exists — duplicate health checks create confusion.
- Do not use `print()` or `echo=True` (SQLAlchemy debug) as a substitute for structured logging in production.
- Do not create a new monitoring stack if one already exists — extend the existing one.
- Do not skip the alert documentation step — metrics without alert thresholds are decoration.
