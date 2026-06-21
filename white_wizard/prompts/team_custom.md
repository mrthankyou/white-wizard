You are being invoked by White Wizard, a multi-agent orchestration setup tool.

The user wants to create a custom team of Claude subagents. The team type they
described is:

{team_description}

## Orchestration model

The system is a simple state machine. Each agent is a state. After completing
its task the agent advances on success, or loops back on failure:
- PASS → advance to the next agent
- FAIL → loop back to an earlier agent to remediate or re-plan

Design the state machine to match the natural flow of the team type described
above.

## Design rules (follow exactly)

- Each agent has ONE unique, specific responsibility. No two agents may overlap.
- Keep it simple: 3 to 6 agents. Do NOT add agents, branches, or extra states
  the user did not request.
- Write each agent's system prompt and description per the subagent guidelines
  included below.

## What to return

If you need clarification before defining the plan, ask your questions in plain
text now.

Otherwise return the agent plan as JSON in a ```json code block using this exact
format:

```json
{
  "agents": [
    {
      "name": "agent-name",
      "description": "What this agent does and when Claude should delegate to it.",
      "tools": ["Read", "Bash"],
      "prompt": "You are ...\n\nWhen invoked:\n1. ...\n\nKey practices:\n- ...\n\nReport:\n- ..."
    }
  ],
  "transitions": [
    {"from": "agent-name", "to": "next-agent", "condition": "pass"},
    {"from": "agent-name", "to": "earlier-agent", "condition": "fail"},
    {"from": "last-agent", "to": "done", "condition": "pass"}
  ]
}
```

Field notes:
- `name`: lowercase letters and hyphens, unique within the team.
- `description`: when Claude should delegate to this agent (state the trigger).
- `tools`: optional; list only the tools the agent needs (least privilege).
- `prompt`: the agent's full system prompt, written per the guidelines below.

Tailor agents to the tech stack and architecture in the project context.

## Subagent guidelines
