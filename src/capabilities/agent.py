"""
Optional project capability: Agent Workflows.

Implement this module only if this capability is relevant to your application's
user problem. Remove the file if the capability is not used.

Conceptual Overview:
-------------------
Practical agentic behavior involves iterative execution loops where the model selects actions
based on observations, while application code maintains control over available actions,
validation rules, and stopping conditions.

Key concepts when implementing agentic workflows:
1. Model-Selected Actions: The LLM analyzes the current state/observation and chooses
   the next step from a defined set of actions.
2. Controlled Loop: Application code manages the execution loop (Observation -> Decision -> Action -> Observation).
3. Explicit Boundaries & Stopping Conditions: Application code enforces maximum iterations,
   error limits, and final completion conditions to prevent infinite loops.

Note:
-----
An agent pattern should only be used when dynamic, model-selected sequence of actions is
genuinely required by the user problem. Avoid unneeded complexity.
"""

# Implement custom agent loop logic below if selected.
