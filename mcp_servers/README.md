# MCP servers (optional)

This folder is an **optional** teaching exhibit. It is not part of the
customer-ticket workflow. The orchestrator (`scripts/orchestrator.py`)
still drives the demo end-to-end on its own; you can ignore everything
in here and the rest of the repo works.

## What this is

Two small [Model Context Protocol](https://modelcontextprotocol.io/)
servers — deliberately split by concern:

| Server | Module | What it serves |
| --- | --- | --- |
| **Data** | `mcp_servers.data_server` | Read-only access to the workflow's data files (raw tickets, FAQ KB, reference dictionaries, working CSVs) via URI templates. |
| **Skills** | `mcp_servers.skills_server` | The two LLM-judgement skills as MCP tools (subprocess wrappers around `skills/check-faq-resolution` and `skills/investigate-specialist-solution`). |

Keeping them separate makes the teaching point obvious: a server that
**serves data** is a different concern from a server that **does work**.
Students can run one without the other.

## What each server exposes

### Data server — resources

| URI | What it returns |
| --- | --- |
| `tickets://raw` | The submitted-tickets CSV (`data/raw/submitted_tickets.csv`). |
| `tickets://{ticket_id}` | One ticket row as JSON, or a not-found error. |
| `faq://kb` | The FAQ knowledge base. **Local CSV by default; reads from `FAQ_KB_URI` when that env var is set** (see below). |
| `dictionaries://{name}` | A reference dictionary CSV. Allowed names: `categories`, `priority_rules`, `status_codes`, `systems`. |
| `working://{filename}` | A CSV from `data/working/` (e.g. `ticket_action_log.csv`). Filename is restricted to `^[A-Za-z0-9_-]+\.csv$`. |

All resources are read-only. The server cannot write to `data/working/`
— that stays the orchestrator's job.

### Skills server — tools

| Tool | What it does |
| --- | --- |
| `check_faq_resolution(ticket_id)` | Wraps `skills/check-faq-resolution`. Asks the LLM whether the FAQ KB resolves the given ticket. |
| `investigate_specialist_solution(ticket_id)` | Wraps `skills/investigate-specialist-solution`. Asks the LLM to act as an IT specialist for an escalated ticket. |

Both tools run the underlying skill in `--mode demo` and write to a
private temp directory (`/tmp/mcp_servers_working/`), so calling them
from an MCP client cannot corrupt a real orchestrator run.

## The local-vs-remote switch: `FAQ_KB_URI`

This is the one piece of "remote data" in the exhibit. Set
`FAQ_KB_URI` to a database connection URI and the FAQ knowledge base is
read from that database instead of `data/raw/faq_knowledge_base.csv`.
The URI is anything `polars.read_database_uri` accepts:

```bash
# In your shell, or in .env at the repo root
export FAQ_KB_URI="postgresql://team_default:PASSWORD@host.example.com:5432/genai4biz_data?options=-csearch_path%3Dteam_default"
```

The target database is expected to have a `faq_knowledge_base` table
whose columns mirror the local CSV. With the env var set:

- `faq://kb` on the data server returns rows from the database.
- `skills/check-faq-resolution` reads its FAQ table from the database
  in both terminal and orchestrator runs.

When the env var is unset (or empty), both fall back to the local CSV.
The switch lives in one helper, `_load_faq_kb()`, defined once in each
of:

- `mcp_servers/data_server.py`
- `skills/check-faq-resolution/scripts/check_faq_resolution.py`

`polars.read_database_uri` needs a driver — `connectorx` is already
pinned in `pyproject.toml`.

## Wire both servers into Claude Desktop

Edit your Claude Desktop config (`~/Library/Application Support/Claude/claude_desktop_config.json`
on macOS, or the equivalent path on your OS) and add both entries under
`mcpServers`:

```json
{
  "mcpServers": {
    "customer-ticket-data": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/customer-ticket-process",
        "run", "python", "-m", "mcp_servers.data_server"
      ]
    },
    "customer-ticket-skills": {
      "command": "uv",
      "args": [
        "--directory", "/absolute/path/to/customer-ticket-process",
        "run", "python", "-m", "mcp_servers.skills_server"
      ]
    }
  }
}
```

Restart Claude Desktop. You should see both servers' tools and resources
in the MCP panel.

## Wire both servers into Claude Code

Create a project-level `.mcp.json` at the repo root:

```json
{
  "mcpServers": {
    "customer-ticket-data": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_servers.data_server"]
    },
    "customer-ticket-skills": {
      "command": "uv",
      "args": ["run", "python", "-m", "mcp_servers.skills_server"]
    }
  }
}
```

Then run `/mcp` inside Claude Code to confirm both servers are listed
and their tools / resources are available.

You can run just one — e.g. only the data server, or only the skills
server — by removing the other entry.

## Two things to try

**Tool call.** With the skills server connected, ask:

> Use the `check_faq_resolution` tool on ticket `TKT-00042`. Show me the
> JSON envelope it returns.

You should see the same envelope the orchestrator's web demo shows for
step 3.

**Resource read.** With the data server connected, ask:

> Read `tickets://raw` and tell me how many tickets reference the email
> system.

The client will fetch the CSV via the MCP resource and answer from it,
no glue code required. Then set `FAQ_KB_URI` and ask:

> Read `faq://kb` again — these rows came from Postgres, not the local
> CSV.

## Why this matters

The orchestrator passes `--data-dir <path>` to every skill it runs.
That works fine for one local demo, but it scales poorly — every
consumer needs to know where the data lives.

The MCP path inverts that: the **server** is the one place that knows.
Every client (Claude Desktop on your laptop, a teammate's Claude Code,
an automated agent on a build box) gets the same view of the data
without its own `--data-dir`. And by setting one env var
(`FAQ_KB_URI`), the same MCP URIs can point at a remote database
instead of local files — without any changes to the consumers. That's
the seam this exhibit is built to make visible.

For the audience of this repo (graduate business-school students), the
teaching point is: when you have one consumer, parameterized CLI is
fine; when you have many consumers or a third party in the loop, MCP
plus URI-addressable data is the seam.
