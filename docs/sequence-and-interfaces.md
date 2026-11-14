# Sequence & interface design (Phase 1)

## Goal creation and confirmation

```mermaid
sequenceDiagram
 participant U as User
 participant FE as Frontend
 participant GI as Goal Intelligence
 participant GM as Goal Management
 participant MQ as RabbitMQ
 U->>FE: "Find RTX 5070 under EUR 600"
 FE->>GI: POST /v1/goal-sessions
 GI-->>FE: GoalDraft(rev 1, missing fields, question)
 FE-->>U: Clarifying question
 U->>FE: Shipping to FI included; new condition
 FE->>GI: POST /sessions/:id/messages (expected_revision=1)
 GI-->>FE: GoalDraft(rev 2, ready_for_confirmation=true)
 FE-->>U: Show canonical goal summary
 U->>FE: Confirm
 FE->>GM: POST /sessions/:id/confirm(rev 2, approved=true)
 GM->>GI: Authenticated INTERNAL GET candidate rev 2
 GI-->>GM: Candidate payload + ownership metadata
 Note over GM: Validate schema; persist version 1 + outbox atomically
 GM-->>FE: 201 GoalSpec (ACTIVE)
 GM->>MQ: goal.activated.v1
```

Important: User identity is checked at both services; passing a `session_id` alone does not grant permission. The candidate is validated and normalized by the server and the approved final canonical summary is recorded.

## Monitoring and notification

```mermaid
sequenceDiagram
 participant MQ as RabbitMQ
 participant DM as Discovery & Monitoring
 participant CT as Context.dev
 participant DE as Data & Evidence
 participant GE as Goal Evaluation
 participant NS as Notification
 MQ->>DM: goal.activated.v1
 DM->>CT: Initial search/scrape + schedule monitor
 CT-->>DM: Evidence / future signed webhook
 DM->>DE: Normalize observed product record
 DE->>MQ: evidence.updated.v1
 MQ->>GE: evidence.updated.v1
 GE->>GE: Match subscribed active goal version; evaluate fresh evidence
 GE->>MQ: goal.evaluated.v1
 opt Every required condition verified
  GE->>MQ: goal.opportunity_verified.v1
  MQ->>NS: goal.opportunity_verified.v1
  NS->>NS: Deduplicate per user/goal/version/opportunity fingerprint
  NS-->>NS: Queue user notification
 end
```

The service-to-service transport for `DM → DE` and `GM → GI` is authenticated internal HTTP for the hackathon. All other listed cross-service state changes use RabbitMQ events. Choose implementation details consistently; never silently switch ownership to shared SQL.

## Four crucial validations

- **Draft readiness:** No `missing_fields`, valid candidate convertible to final GoalSpec, no unsupported domain silently accepted.
- **User confirmation:** Confirm only exact revision that user saw; stale revision returns HTTP 409.
- **Evidence evaluation:** Price + shipping + taxes must be known for cost comparison; stock and destination eligibility cannot be `unknown` if verifying.
- **Goal version:** When criteria change, old events may still arrive. Reject stale-version opportunity notifications.
