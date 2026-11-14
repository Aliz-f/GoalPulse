"""GoalPulse Goal Intelligence service (Developer 2).

Turns natural-language goal descriptions into revisioned, schema-checked
GoalDraft.v1 objects through a bounded clarification loop. It never activates
goals, buys anything, or calls Context.dev; Goal Management alone confirms.
"""
