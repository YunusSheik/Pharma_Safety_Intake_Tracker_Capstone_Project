# Workflow and architecture diagrams

All diagrams are Mermaid, which GitHub renders inline.

## 1. Report lifecycle with the HITL gate

```mermaid
stateDiagram-v2
    [*] --> Reserved: Start new report — REF reserved (REFERENCE_RESERVED)
    Reserved --> Reserved: Validation failed (VALIDATION_FAILED, field names only)
    Reserved --> New: Valid submission (REPORT_CREATED)
    New --> InReview: Operator advances (STATUS_CHANGED)
    InReview --> PendingClosure: Close requested — report frozen (CLOSURE_REQUESTED)
    PendingClosure --> InReview: Reviewer rejects + reason (CLOSURE_REJECTED)
    PendingClosure --> Closed: Reviewer approves with attestation (CLOSURE_APPROVED)
    Closed --> [*]: Final, read-only

    state "In Review" as InReview
    state "In Review (pending closure)" as PendingClosure
```

Everything not drawn is rejected: skipping New → Closed, moving backwards, any change after Closed, and any change while frozen. Each rule is enforced twice: in `workflow.py`, and by SQLite triggers in `db.py`. Only `REV-xx` identities can decide a closure, and only with the attestation ticked. The agent's tools can *request* a closure but have no approve capability.

## 2. Control plane — one assistant turn

```mermaid
sequenceDiagram
    autonumber
    actor U as Operator
    participant A as Assistant
    participant G as Guardrails
    participant R as Router
    participant B as Circuit breaker
    participant T as ToolExecutor
    participant M as Short-term memory
    participant DB as SQLite (long-term memory)

    U->>A: message
    A->>M: Thought: message received
    A->>G: check_input()
    alt causality / treatment / reportability
        G-->>A: blocked (reason)
        A->>DB: GUARDRAIL_BLOCKED
        A-->>U: refusal (no tool ran)
    else allowed
        A->>B: is_open?
        A->>R: route() — rules, then fixed exemplars, then default
        A->>M: Thought: route + rule
        A->>T: call(tool, raw_args)
        T->>T: auto-repair → Pydantic validation
        alt invalid
            T->>B: record_failure(signature)
            T->>DB: TOOL_CALL_FAILED (+ CIRCUIT_BREAKER_TRIPPED on the 3rd identical call)
        else valid
            T->>DB: domain operation + audit row (one transaction)
            T->>B: record_success()
        end
        A->>M: Action / Observation
        A-->>U: reply (+ diagnostics, ReAct trace)
    end
```

## 3. 4-stage hybrid RAG

```mermaid
flowchart TB
    Q[Query] --> S1[Stage 1 · BM25<br/>k1=1.5, b=0.75<br/>top-50]
    Q --> S2[Stage 2 · bi-encoder<br/>bge-small-en-v1.5 · cosine<br/>512/64-token chunks · top-50]
    C[(Corpus<br/>5 SOPs by section<br/>+ past reports from SQLite)] --> S1
    C --> S2
    S1 --> S3["Stage 3 · RRF<br/>Σ 1 / (60 + rank)"]
    S2 --> S3
    S3 --> S4[Stage 4 · cross-encoder<br/>mxbai-rerank-xsmall-v1 · sigmoid<br/>keep ≥ 0.40, top ≤ 5]
    S4 -->|0 chunks| NA[No sufficiently relevant record found]
    S4 -->|1–5 chunks| P[Grounded prompt<br/>numbered sources, non-clinical rules]
    P --> GEN[Generator<br/>extractive quotes · or local Ollama]
    GEN --> OG{Output guardrail}
    OG -->|clinical conclusion| W[Answer withheld]
    OG -->|ok| ANS[Answer + citations]
```

## 4. AI-assisted development governance

```mermaid
flowchart LR
    CM[CLAUDE.md<br/>always-on contract] --> AG[AI coding agent]
    PM[Plan Mode<br/>docs/PLAN.md approved] --> AG
    AG -->|Edit / Write / MultiEdit / NotebookEdit| FS[(Repository files)]
    AG -.->|PostToolUse| HK[.claude/hooks/log_edit.py<br/>deterministic, no model]
    HK --> LOG[(logs/edit_audit.log<br/>ts · tool · relative path · session)]
    SK[pharma-intake-review skill<br/>4 checklists] -->|on demand| RV[docs/review-evidence.md]
    AG --> SK
```
