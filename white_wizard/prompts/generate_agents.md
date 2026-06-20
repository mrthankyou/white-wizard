You are being invoked by White Wizard to generate a working multi-agent orchestration system.

## Approved agent plan

{plan_json}

## Architecture

Generate three kinds of files:

1. **`mcp_state_machine.py`** — an MCP server that is the state machine
2. **`agents/<snake_case_name>.py`** — one module per agent in the plan
3. **`agents/__init__.py`** — empty package init
4. **`run_orch.py`** — the orchestrator entry point

---

### 1. mcp_state_machine.py

Use the `mcp` package (FastMCP). Install with: `pip install mcp`

The server must:
- Embed the full transition table from the plan above
- Persist state to `wizard_state.json` in the same directory
- Expose two tools:

```
report_result(agent: str, status: str, output: str) -> str
  Called by an agent when it finishes its task.
  status must be "pass" or "fail".
  Advances the state machine according to the transition table.
  Returns a message like "Next state: <name>" or "Orchestration complete."

get_current_state() -> dict
  Returns {"current": "<agent_name_or_done>", "history": [...]}
```

- On startup, if `wizard_state.json` does not exist, write initial state `{"current": "<first_agent_name>", "history": []}`

---

### 2. agents/<snake_case_name>.py (one per agent)

Each agent file must contain:

```python
SYSTEM_PROMPT = "..."   # The agent's role and responsibilities
TASK = "..."            # The specific task this agent performs

def run(context: dict) -> dict:
    """Execute this agent via claude -p and return {"status": "pass"|"fail", "output": str}"""
```

Inside `run()`:
- Build a prompt from SYSTEM_PROMPT, TASK, context (prior history), and an explicit instruction:
  "When you have completed your work, call the report_result MCP tool with:
   - agent: '<this agent name>'
   - status: 'pass' if successful, 'fail' if not
   - output: a brief summary of what you did"
- Call `claude -p <prompt>` via subprocess (inherit the current directory so `.mcp.json` is found)
- Return `{"status": "pass" if returncode == 0 else "fail", "output": stdout}`

---

### 3. run_orch.py

The orchestrator:

1. On startup, write `.mcp.json` to the current directory:
```json
{
  "mcpServers": {
    "wizard-state-machine": {
      "command": "python3",
      "args": ["./mcp_state_machine.py"]
    }
  }
}
```

2. Delete `wizard_state.json` if it exists (fresh run)

3. Start the state machine MCP server as a background subprocess (it runs persistently)

4. Loop:
   - Read `wizard_state.json` to get `current`
   - If `current == "done"`: print success summary and exit
   - Derive the module name from `current` (e.g. "Code Reviewer" → `agents.code_reviewer`)
   - Import the module and call `module.run(context)` where `context = {"history": [...from state file...]}`
   - Print the result
   - Wait briefly for `wizard_state.json` to update (the MCP call inside claude -p updates it)
   - Continue

5. On exit, terminate the MCP server subprocess

Use only Python stdlib (no extra packages in run_orch.py). The `mcp` package is only needed by `mcp_state_machine.py`.

---

## Output format

Output each file using EXACTLY this format — no text between files, no preamble, no explanation after the last file:

### FILE: <relative/path/to/file.py>
```python
<complete file contents>
```

Generate all files now.

## Project context

