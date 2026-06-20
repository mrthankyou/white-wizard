You are being invoked by White Wizard, a multi-agent orchestration setup tool.

The user wants to create a devops team of AI subagents for their project.

## Orchestration model

The system is built as a state machine. Each agent is a state. After completing its task, the agent transitions based on pass or fail:
- PASS → advance to the next agent
- FAIL → loop back to an earlier agent to remediate

Example devops team flow:
  Build → Integration Tests → Staging Deploy → Smoke Tests → Production Deploy → DONE
  Integration Tests: PASS → Staging Deploy,      FAIL → Build
  Smoke Tests:       PASS → Production Deploy,   FAIL → Staging Deploy
  Production Deploy: PASS → DONE,                FAIL → Rollback Agent

Keep it simple — 3 to 6 agents. Focus on the CI/CD pipeline, deployment safety, and infrastructure reliability.

## What to return

If you need clarification (e.g. cloud provider, deployment targets, existing pipeline tooling) before defining the plan, ask your questions in plain text now.

Otherwise return the agent plan as JSON in a ```json code block using this exact format:

```json
{
  "agents": [
    {
      "name": "Agent Name",
      "description": "One-line description of this agent's role",
      "inputs": ["what it receives"],
      "outputs": ["what it produces"]
    }
  ],
  "transitions": [
    {"from": "Agent Name", "to": "Next Agent Name", "condition": "always"},
    {"from": "Agent Name", "to": "Next Agent Name", "condition": "pass"},
    {"from": "Agent Name", "to": "Earlier Agent Name", "condition": "fail"},
    {"from": "Last Agent", "to": "done", "condition": "pass"}
  ]
}
```

Tailor agents to the tech stack and architecture in the project context below.

## Project context
