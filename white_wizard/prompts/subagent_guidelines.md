# Subagent authoring guidelines (Claude)

White Wizard always follows these rules when designing a Claude orchestration.
They are distilled from Anthropic's guidance on subagents, prompt engineering,
skills, and tools. Keep every generated artifact concise — context is a shared
budget.

## 1. One subagent, one job

- Each subagent has a single, **unique** responsibility. No two subagents in the
  team may overlap. If two would do the same kind of work, merge them.
- Name the responsibility as a verb phrase ("reviews code", "runs the tests").
  If you can't state it in one line, the agent is doing too much — split it.
- Prefer the smallest team that covers the workflow. **3–6 agents.** Do not add
  agents, branches, or states for situations the user did not ask for.

## 2. Keep the state machine simple

- Default to a straight line: each agent runs, then advances on success.
- Add a failure edge only where remediation is genuinely needed (typically loop
  back to the planner/first agent). Avoid webs of conditional transitions.
- Complexity is not the goal. Only introduce extra states, parallelism, or
  branching when the user explicitly requested it.

## 3. Subagent file format

Each subagent is a Markdown file with YAML frontmatter; the body is its system
prompt. Only `name` and `description` are required.

```markdown
---
name: code-reviewer            # lowercase letters and hyphens, unique
description: Reviews code changes for correctness and security. Use proactively after code is written.
tools: Read, Grep, Glob, Bash  # optional — omit to inherit all; grant least privilege
model: claude-sonnet-4-6       # optional
---

You are a senior code reviewer for this project.

When invoked:
1. <first concrete step>
2. <next step>
3. <how to finish / what to hand off>

Key practices:
- <specific, checkable practice>
- <specific, checkable practice>

Report:
- <the exact shape of the output this agent returns>
```

## 4. Write the `description` for delegation

- The `description` is how Claude decides when to hand off. State **when** to use
  the agent, e.g. "Use proactively after code changes" or "Use when tests fail".
- Be specific and concrete. Avoid vague descriptions like "helps with code".

## 5. Write the system prompt (body) clearly

- Open with a one-line role ("You are a …").
- Give direct, numbered steps under "When invoked:". Be explicit about the
  hand-off — what this agent produces and passes on.
- Add a short "Key practices" list of specific, checkable rules.
- Specify the **output format** the agent must return so the next agent (or the
  orchestrator) can consume it.
- Match guidance to the task's fragility: for error-prone or must-be-exact steps,
  give precise instructions and exact commands (low freedom); for open-ended
  work, give direction and trust the model (high freedom). Don't over-specify
  open tasks or under-specify fragile ones.

## 6. Grant least-privilege tools

- List only the tools the agent needs in `tools`. Omitting the field inherits
  everything — only do that when the agent genuinely needs broad access.

## 7. Promote repeated commands to MCP tools

- If several subagents run the same command (build, test, deploy, a query), that
  command is a candidate for a dedicated MCP tool so agents call it directly
  instead of re-deriving it.
- A good tool has a clear name and an **extremely clear description** of what it
  does, when to use it, and its inputs/outputs. Update the subagents that used
  the raw command to use the tool instead.
