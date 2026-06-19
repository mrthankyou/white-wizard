# White Wizard — Todo

## Planned features

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
