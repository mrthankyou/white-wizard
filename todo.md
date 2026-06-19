# White Wizard — Todo

## Packaging

### 2. Installable bash command
Package White Wizard as a proper installable tool so it runs as a single shell command (e.g. `wizard` or `white-wizard`) without needing `python3 white_wizard.py`.

**Approach**: Add a `pyproject.toml` with a `[project.scripts]` entry point, publish to PyPI or install locally with `pip install -e .`. The entry point calls `main()` from `white_wizard.py`.

**Notes**:
- Bundled `prompts/` directory must be included in the package (use `package_data` or `importlib.resources`)
- `ai_client.py` can be an internal module rather than a sibling file

## Planned features

### 2. Stream mode
A recurring AI-driven review loop that runs a prioritized checklist against the live codebase and evolves its own questions over time.

**How it works**:
- `wizard --stream` launches stream mode
- An AI agent works through a prioritized list of questions saved in `wizard.yaml` under a `stream` key, executing them highest-to-lowest importance
- Results are surfaced to the user as findings, proposed changes, or no-ops

**Default question set (stored in `wizard.yaml`):**

```yaml
stream:
  questions:
    - priority: 1
      id: orch_drift
      prompt: >
        Review the git diff and latest code. Have there been any changes to the
        codebase that require updating how the orchestration system works, or that
        expose missing components necessary to the success and evolution of the
        codebase?
    - priority: 2
      id: bug_scan
      prompt: >
        Are there any bugs present in the codebase that should be surfaced as a
        proposed bug fix?
    - priority: 3
      id: stream_self_improve
      prompt: >
        Review the questions currently saved in stream mode (wizard.yaml). Are
        any improvements, new questions, or refactorings needed to better identify
        issues, streamline codebase management, and simplify processes — without
        unnecessarily adding complexity? If so, return the revised question list.
```

**Self-improvement loop**: Question 3 can propose edits to the `stream.questions` list itself. If approved by the user, wizard writes the updated questions back to `wizard.yaml` — so stream mode gets smarter over time.

**Notes**:
- `git diff` output is injected into question 1's context automatically
- Each question runs as a separate `claude -p` call; findings are shown one at a time with an approve/skip/quit menu
- Approved findings can optionally be written as GitHub issues or local markdown notes

### 1. MCP server generation
When proposing an orchestration system, give the user an option for Wizard to generate an MCP server alongside the agent plan.

**Trigger**: Wizard detects that one or more agents will need to run commands — either referenced in the project's README.md or provided by a known tool in the tech stack.

**Behavior**:
- Analyze README.md and tech stack for runnable commands (e.g. `npm test`, `docker build`, CLI tools)
- Propose an MCP server that exposes those commands as tools
- Show the MCP server as a distinct node in the infographic diagram (different box style or label)
- When the user approves, include the MCP server definition in wizard.yaml alongside the orchestration

**Notes**:
- MCP server can be shared across multiple agents in the same orchestration
- Should indicate in the diagram which agents consume the MCP server
