# goal-management — GoalPulse service

**Implementation status: foundation in progress.** The following documents remain the development specification; only the implemented files and tests define current behavior.

- [Service specification](../../docs/implementation/01-goal-management.md)
- [All seven services and integration constraints](../../docs/implementation/README.md)
- [Master task backlog](../../tasks/MASTER_BACKLOG.md)
- [Coding-agent rules](../../AGENTS.md)

**Before coding:** locate this service's task IDs in its assigned developer's task file. Follow versioned frozen contracts. `internal.proposed.v1.yaml` is accepted under ADR-0002 despite its historical filename; do not guess interfaces or read other services' databases.

The API process and RabbitMQ workers are separate deployable processes. Run the
outbox publisher with `python -m goal_management.worker` and the manual-ACK
`goal.evaluated.v1` consumer with `python -m goal_management.consumer` from an
environment where `services/goal-management/src` is on `PYTHONPATH` and the
documented database/broker variables are configured.
