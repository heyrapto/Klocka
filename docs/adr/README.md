# Architecture Decision Records (ADRs)

> **Context:** This directory documents the foundational architectural decisions made for the Prediction Intelligence Platform. Each record describes the context, decision, consequences, trade-offs, and compliance constraints governing the system.

## ADR Index

| ADR | Title | Status | Date | Primary Topics |
|---|---|---|---|---|
| [ADR 0000](file:///Users/mac/prediction-market/docs/adr/0000-stack-choices.md) | Monorepo and Technology Stack Selection | Accepted | 2026-11-04 | Next.js, FastAPI, Supabase, PostgreSQL, Redis, Celery |
| [ADR 0001](file:///Users/mac/prediction-market/docs/adr/0001-server-is-the-referee.md) | Server-Side Authoritative Verification (Server is the Referee) | Accepted | 2026-11-04 | Screenshot OCR as candidate only; platform API as authority |
| [ADR 0002](file:///Users/mac/prediction-market/docs/adr/0002-asynchronous-job-orchestration.md) | Asynchronous Job Orchestration with SSE Progress Replay | Accepted | 2026-11-04 | 202 Accepted, Celery + Redis, Server-Sent Events |
| [ADR 0003](file:///Users/mac/prediction-market/docs/adr/0003-forecasting-and-decision-separation.md) | Strict Decoupling of Forecasting and Decision Engine | Accepted | 2026-11-04 | $P(\text{YES})$ vs EV execution, `NO_TRADE` gating |
| [ADR 0004](file:///Users/mac/prediction-market/docs/adr/0004-point-in-time-truth-and-anti-leakage.md) | Point-in-Time Data Immutability and Anti-Leakage Controls | Accepted | 2026-11-04 | Immutable forecast snapshots, purged walk-forward splits |
| [ADR 0005](file:///Users/mac/prediction-market/docs/adr/0005-vision-ocr-hybrid-pipeline.md) | Hybrid PaddleOCR and Vision-Language Model Pipeline | Accepted | 2026-11-04 | Bounding box OCR + VLM schema parsing |
| [ADR 0006](file:///Users/mac/prediction-market/docs/adr/0006-untrusted-data-boundary-and-prompt-injection.md) | Untrusted External Content Boundary & Prompt Injection Defense | Accepted | 2026-11-04 | External web/PDF/OCR data isolation, tool allowlists |

---

## ADR Template Reference

Every subsequent ADR added to this directory MUST adhere to the following standard structure:

```markdown
# ADR [Number]: [Title]

- **Status:** [Proposed | Accepted | Superseded | Deprecated]
- **Date:** [YYYY-MM-DD]
- **Deciders:** [Engineering Team / Architecture Review]
- **Consulted:** [Domain Specialists, Security, ML Engineers]

## 1. Context and Problem Statement
What business or engineering problem are we trying to solve? What are the constraints?

## 2. Decision Drivers
- Key driver 1 (e.g. latency, safety, reproducibility)
- Key driver 2 (e.g. developer velocity, cost)

## 3. Considered Options
- Option 1: ...
- Option 2: ...

## 4. Decision Outcome
Chosen option and rationale.

## 5. Consequences & Trade-offs
- **Positive:** Benefits gained.
- **Negative / Risks:** Known downsides and mitigations.
- **Compliance:** Impact on security, RLS, auditability.

## 6. Revisit Triggers
Under what future conditions should this decision be reconsidered?
```
