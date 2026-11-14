## GoalPulse PR checklist

**Task ID:**
**Service owner:**
**Allowed write paths:**

### Outcome
- [ ] Observable behavior described, including error paths
- [ ] Changes limited to assigned service / approved shared areas
- [ ] No secrets or sensitive sample data committed

### Contracts and data ownership
- [ ] Contract and API schemas followed exactly
- [ ] `contracts/` unchanged, OR approved ICP/ADR/versioned migration linked
- [ ] No cross-service DB access or private-module imports
- [ ] Events validated; event_id idempotency and stale goal version handled, when applicable
- [ ] Unknown or stale external evidence cannot produce verified opportunities
- [ ] No automatic payment, purchase, booking, or third-party action

### Verification
- [ ] `python scripts/validate_contracts.py`
- [ ] `python -m pytest -q`
- [ ] Service-specific tests and mocked provider failure paths
- [ ] Actual Docker/integration tests run, OR marked NOT RUN with reason
- [ ] Producer/consumer compatibility checked when interfaces are affected

### Handoff
- **Changed files / rationale:**
- **New environment variables / dependencies:**
- **Events or API behavior changed:**
- **Manual test instructions:**
- **Known risks / follow-ups:**

### Reviews required
- [ ] Service owner approval
- [ ] Developer 1 approval for shared/contract changes
- [ ] Affected downstream owner approval for integration-affecting changes
