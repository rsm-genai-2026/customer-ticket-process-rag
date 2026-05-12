# MCP exhibit (optional)

This folder is a small, **optional** teaching exhibit. It is *not* part of
the customer-ticket workflow. The orchestrator (`scripts/orchestrator.py`)
still drives the actual demo end-to-end; you can ignore everything in
here and the rest of the repo still works.

## What this is

A single [Model Context Protocol](https://modelcontextprotocol.io/) server
that exposes the repo's two LLM-judgment skills, plus the workflow's data
files, to an MCP client (Claude Desktop or Claude Code). It exists so
students can see a second front door to the same work:

- The **orchestrator path** — a CLI / web demo runs the skill scripts as
  subprocesses, passes a `--data-dir`, and reads JSON envelopes from
  stdout.
- The **MCP path** — an LLM client connects once to this server and then
  calls tools or reads resources by name. The server is the only thing
  that needs to know where the data lives.

The two paths produce identical decisions because the MCP tools wrap the
same skill scripts.

## What this server exposes

### Tools

| Tool | What it does |
| --- | --- |
| `check_faq_resolution(ticket_id)` | Wraps `skills/check-faq-resolution`. Asks the LLM whether the FAQ KB resolves the given ticket and returns the structured decision. |
| `investigate_specialist_solution(ticket_id)` | Wraps `skills/investigate-specialist-solution`. Asks the LLM to act as an IT specialist for an escalated ticket and return a root cause, diagnostic steps, and customer-safe solution. |

Both tools run the underlying skill in `--mode demo` and write to a
private temp directory (`/tmp/mcp_exhibit_working/`), so calling them
from Claude Desktop will not corrupt a real orchestrator run.

### Resources

| URI | What it returns |
| --- | --- |
| `tickets://raw` | The submitted-tickets CSV (`data/raw/submitted_tickets.csv`). |
| `tickets://{ticket_id}` | One ticket row as JSON, or a not-found error. |
| `faq://kb` | The FAQ knowledge base (`data/raw/faq_knowledge_base.csv`). |
| `dictionaries://{name}` | A reference dictionary CSV. Allowed names: `categories`, `priority_rules`, `status_codes`, `systems`. |
| `working://{filename}` | A CSV from `data/working/` (e.g. `ticket_action_log.csv`). Filename is restricted to `^[A-Za-z0-9_-]+\.csv$`. |

All resources are read-only. The server cannot write to `data/working/`
— that stays the orchestrator's job.

## Wire it into Claude Desktop

Edit your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, or the equivalent path on your OS) and add an entry under
`mcpServers`:

```json
{
  "mcpServers": {
    "customer-ticket-process-exhibit": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/customer-ticket-process",
        "run", "python", "-m", "mcp_exhibit.server"
      ]
    }
  }
}
```

Restart Claude Desktop. You should see the server's tools and resources
in the MCP panel.

## Wire it into Claude Code

Create a project-level `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "customer-ticket-process-exhibit": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_exhibit.server"]
    }
  }
}
```

Then run `/mcp` inside Claude Code to confirm the server is listed and
its tools are available.

## Two things to try

**Tool call.** With Claude Desktop connected, ask:

> Use the `check_faq_resolution` tool on ticket `TKT-00042`. Show me the
> JSON envelope it returns.

You should see the same envelope shape the orchestrator's web demo
displays for step 3.

**Resource read.** Ask:

> Read `tickets://raw` and tell me how many tickets reference the email
> system.

The client will fetch the CSV via the MCP resource and answer from it,
no glue code required.

## Why this matters

The orchestrator passes `--data-dir <path>` to every skill it runs.
Every step gets the path threaded through its CLI. That is fine for one
local demo but it scales poorly — anything that wants to call a skill
needs to know where the data lives.

This MCP server is the alternative: the **server** is the one place that
knows the path. Every client (Claude Desktop on your laptop, a teammate's
Claude Code, an automated agent on a build box) gets the same view of
the data without each one needing its own `--data-dir`. That's the
problem MCP is designed to solve.

For the audience of this repo (graduate business-school students), the
teaching point is: when you have one consumer, parameterized CLI is
fine; when you have many consumers or a third party in the loop, MCP is
the seam.
