# Hermes MCP → VS Code MCP Sync

## Problem

Hermes Agent has 6 working MCP servers configured in `~/.hermes/config.yaml`.
VS Code (local + remote SSH) has its own MCP config files in different formats
and locations. Manual sync is error-prone.

## Existing MCP Servers (Hermes)

All via stdio transport:

| # | Name | Command | Args | Platform Notes |
|---|------|---------|------|----------------|
| 1 | nixos | uvx | [mcp-nixos] | Pure Python, universal |
| 2 | github | npx | [-y, @modelcontextprotocol/server-github] | Needs GITHUB_PERSONAL_ACCESS_TOKEN env |
| 3 | context7 | npx | [-y, @upstash/context7-mcp@latest] | Node.js, universal |
| 4 | playwright | npx | [-y, @playwright/mcp@latest] | Needs browser; headless chromium on headless Linux |
| 5 | sequential-thinking | npx | [-y, @modelcontextprotocol/server-sequential-thinking] | Node.js, universal |
| 6 | qt-rules | /Users/qi/.local/bin/qt-rules-mcp | [] | Path is macOS-specific; on remote, use `uv tool install` and adjust path |

## VS Code MCP Config Locations

Three tools, three formats, three file paths. Each MCP client reads from a
different JSON file with a slightly different schema.

### Format comparison (nixos server example)

```
# Hermes (YAML) — single source of truth
mcp_servers:
  nixos:
    command: uvx
    args: [mcp-nixos]

# Copilot (JSON) — has extra "type": "stdio" field
{"servers": {"nixos": {"type": "stdio", "command": "uvx", "args": ["mcp-nixos"]}}}

# Roo Cline (JSON) — standard mcpServers key, no "type"
{"mcpServers": {"nixos": {"command": "uvx", "args": ["mcp-nixos"]}}}

# Cursor (JSON) — same format as Roo Cline
{"mcpServers": {"nixos": {"command": "uvx", "args": ["mcp-nixos"]}}}
```

Key differences:
- Copilot requires `"type": "stdio"` explicitly; others don't
- Copilot uses `"servers"` key; others use `"mcpServers"`
- Copilot preserves unknown fields (`gallery`, `version`) for server discovery features
- All use JSON arrays for args (not YAML flow sequences)

### File paths

**macOS local VS Code:**

| Tool | Path |
|------|------|
| Copilot | `~/Library/Application Support/Code/User/mcp.json` |
| Roo Cline | `~/Library/Application Support/Code/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` |
| Cursor | `~/.cursor/mcp.json` |

**VS Code Server (remote SSH target — Linux):**

| Tool | Path |
|------|------|
| Copilot | `~/.vscode/mcp.json` |
| Roo Cline | `~/.vscode-server/data/User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json` |
| Cursor | `~/.cursor/mcp.json` |

**VS Code Server (remote SSH target — macOS, this machine):**
Same as local VS Code paths above. No `.vscode-server/` diversion for macOS
remote targets (VS Code reuses the native app data paths).

## Remote Scenario

This machine (handyMini, macOS arm64) is the SSH target. When VS Code connects
via Remote SSH:
- VS Code Server runs on this Mac
- MCP servers spawn on this Mac (same binaries: npx/uvx via nix)
- Config paths are the same as local VS Code

All 6 servers work on this remote because:
- npx and uvx are available via nix (`/etc/profiles/per-user/qi/bin/`)
- Playwright has browser on macOS desktop
- qt-rules binary path is correct

For a Linux remote target, the main differences:
- qt-rules needs separate `uv tool install` + path adjustment
- playwright needs `npx playwright install chromium` in headless mode
- Config paths differ (see table above)

## Architecture Principle

MCP is transport-agnostic at the client level. Each MCP client (Hermes,
Copilot, Roo Cline) just:
1. Reads config
2. Spawns the stdio subprocess
3. Communicates via JSON-RPC over stdin/stdout

There's no shared runtime — each client spawns its own instance. So the only
thing to share is the *configuration*, not the process.

## Sync Strategy

Single source of truth: `~/.hermes/config.yaml` → generate all VS Code MCP JSONs.

The script (`sync-mcp-to-vscode.py`) does:
1. Parse Hermes YAML config
2. Extract mcp_servers section
3. For each target (copilot/roocline/cursor), translate to their JSON format
4. Merge with existing config (preserve non-Hermes servers, update Hermes ones)
5. Write output files

Environment variables (like `GITHUB_PERSONAL_ACCESS_TOKEN`) are carried over
from the Hermes config's `env` block.

## Implementation Notes

- Uses PyYAML for parsing (installed via pip)
- Preserves existing servers in target files that aren't in Hermes (e.g. markitdown)
- Dry-run mode (`--dry-run`) to preview before writing
- `--target` flag to select specific tools
- Backs up existing files before overwriting
