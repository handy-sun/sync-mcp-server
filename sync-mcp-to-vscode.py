#!/usr/bin/env python3
"""
Sync MCP server config from Hermes (YAML) to VS Code / Cursor MCP configs.

Auto-detects installed MCP-capable extensions and only syncs to those.
Reads ~/.hermes/config.yaml mcp_servers as the single source of truth.

Usage:
    sync-mcp-to-vscode.py                    # dry-run, all detected targets
    sync-mcp-to-vscode.py --write            # write files
    sync-mcp-to-vscode.py --target copilot   # only specific target
    sync-mcp-to-vscode.py --remote           # VS Code Server remote paths
"""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip3 install pyyaml", file=sys.stderr)
    sys.exit(1)


## ── Platform paths ────────────────────────────────────────────────────

HOME = Path.home()
HERMES_CONFIG = HOME / ".hermes" / "config.yaml"

if sys.platform == "darwin":
    LOCAL_DATA = HOME / "Library/Application Support/Code"
    LOCAL_EXT = HOME / ".vscode/extensions"
    REMOTE_DATA = HOME / ".vscode-server/data"
    REMOTE_EXT = HOME / ".vscode-server/extensions"
    CURSOR_DATA = HOME / "Library/Application Support/Cursor"
    CURSOR_EXT = HOME / ".cursor/extensions"
else:
    LOCAL_DATA = HOME / ".config/Code"
    LOCAL_EXT = HOME / ".vscode/extensions"
    REMOTE_DATA = HOME / ".vscode-server/data"
    REMOTE_EXT = HOME / ".vscode-server/extensions"
    CURSOR_DATA = HOME / ".config/Cursor"
    CURSOR_EXT = HOME / ".cursor/extensions"


## ── Known MCP-capable extensions ──────────────────────────────────────
##
## Each entry maps extension ID → {name, config_relpath, server_key, format}
##   config_relpath: relative to DATA dir (e.g. "User/mcp.json")
##   server_key:     JSON key for server list ("servers" or "mcpServers")
##   format:         "copilot" (has "type":"stdio") or "standard"

MCP_EXTENSIONS = {
    "github.copilot-chat": {
        "name": "GitHub Copilot",
        "config_relpath": "User/mcp.json",
        "server_key": "servers",
        "format": "copilot",
    },
    "rooveterinaryinc.roo-cline": {
        "name": "Roo Cline",
        "config_relpath": "User/globalStorage/rooveterinaryinc.roo-cline/settings/mcp_settings.json",
        "server_key": "mcpServers",
        "format": "standard",
    },
    "saoudrizwan.claude-dev": {
        "name": "Cline",
        "config_relpath": "User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json",
        "server_key": "mcpServers",
        "format": "standard",
    },
    "continue.continue": {
        "name": "Continue",
        "config_relpath": "User/globalStorage/continue.continue/config.json",
        "server_key": "mcpServers",
        "format": "standard",
    },
}


## ── Hermes config parsing ─────────────────────────────────────────────

def read_hermes_servers():
    """Parse mcp_servers from Hermes config.yaml. Returns dict {name: config}."""
    if not HERMES_CONFIG.exists():
        print(f"ERROR: Hermes config not found: {HERMES_CONFIG}", file=sys.stderr)
        sys.exit(1)
    with open(HERMES_CONFIG) as f:
        cfg = yaml.safe_load(f)
    servers = cfg.get("mcp_servers", {})
    if not servers:
        print("WARNING: No mcp_servers found in Hermes config.")
    return servers


## ── Format converters ─────────────────────────────────────────────────

def to_copilot_format(hermes_servers):
    """Copilot format: {"servers": {"name": {"type":"stdio", "command":..., ...}}}"""
    result = {}
    for name, srv in hermes_servers.items():
        entry = {"type": "stdio", "command": srv.get("command", "")}
        if srv.get("args"):
            entry["args"] = srv["args"]
        if srv.get("env"):
            entry["env"] = srv["env"]
        if "timeout" in srv:
            entry["timeout"] = srv["timeout"]
        result[name] = entry
    return result


def to_standard_format(hermes_servers):
    """Standard format: {"name": {"command":..., "args":[...]}} (no "type" field)"""
    result = {}
    for name, srv in hermes_servers.items():
        entry = {"command": srv.get("command", "")}
        if srv.get("args"):
            entry["args"] = srv["args"]
        if srv.get("env"):
            entry["env"] = srv["env"]
        if "timeout" in srv:
            entry["timeout"] = srv["timeout"]
        result[name] = entry
    return result


FORMAT_CONVERTERS = {
    "copilot": to_copilot_format,
    "standard": to_standard_format,
}


## ── Extension detection ───────────────────────────────────────────────

def detect_installed(extensions_dir):
    """Scan an extensions directory for known MCP-capable extensions.
    Returns list of (ext_id, info_dict)."""
    found = []
    if not extensions_dir.exists():
        return found
    for ext_id, info in MCP_EXTENSIONS.items():
        ## Extension dirs are named like "publisher.name-1.2.3"
        for d in extensions_dir.iterdir():
            if d.is_dir() and d.name.startswith(ext_id + "-"):
                found.append((ext_id, info))
                break
    return found


def detect_cursor():
    """Check if Cursor is installed (standalone app, not a VS Code extension)."""
    if sys.platform == "darwin":
        app = Path("/Applications/Cursor.app")
    else:
        app = Path("/usr/share/cursor/cursor")
    return app.exists()


## ── Config file I/O ───────────────────────────────────────────────────

def read_json(path):
    """Read JSON file, return dict or empty dict."""
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {}


def merge_servers(existing, new_servers, server_key):
    """Merge new servers into existing, preserving non-Hermes entries."""
    if not existing:
        return {server_key: new_servers}
    merged = dict(existing.get(server_key, {}))
    for name, cfg in new_servers.items():
        if name in merged:
            merged[name] = {**merged[name], **cfg}
        else:
            merged[name] = cfg
    result = dict(existing)
    result[server_key] = merged
    return result


def backup_file(path):
    """Create timestamped backup. Returns backup path or None."""
    if path.exists():
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = path.with_suffix(f".json.bak.{ts}")
        shutil.copy2(path, backup)
        return backup
    return None


## ── Target discovery ──────────────────────────────────────────────────

def discover_targets(mode, target_filter):
    """Discover sync targets based on installed extensions.
    Returns list of (target_id, config_path, server_key, format_name, label)."""
    data_dir = REMOTE_DATA if mode == "remote" else LOCAL_DATA
    ext_dir = REMOTE_EXT if mode == "remote" else LOCAL_EXT

    installed = detect_installed(ext_dir)

    targets = []
    for ext_id, info in installed:
        tid = ext_id.split(".")[-1]  ## "copilot-chat", "roo-cline", etc.
        if target_filter and target_filter not in (tid, ext_id):
            continue
        config_path = data_dir / info["config_relpath"]
        targets.append((
            tid,
            config_path,
            info["server_key"],
            info["format"],
            info["name"],
        ))

    ## Cursor (standalone app)
    if detect_cursor():
        tid = "cursor"
        if not target_filter or target_filter in (tid,):
            cursor_config = CURSOR_DATA / "User/mcp.json"
            targets.append((
                tid,
                cursor_config,
                "mcpServers",
                "standard",
                "Cursor",
            ))

    return targets


## ── Main ──────────────────────────────────────────────────────────────

def sync_target(tid, config_path, server_key, fmt_name, label,
                hermes_servers, write=False):
    """Sync one target."""
    converter = FORMAT_CONVERTERS[fmt_name]
    new_servers = converter(hermes_servers)

    existing = read_json(config_path)
    merged = merge_servers(existing, new_servers, server_key)

    existing_names = list(existing.get(server_key, {}).keys())
    total = len(merged.get(server_key, {}))

    print(f"\n── {label} ({tid}) ──")
    print(f"  Config: {config_path}")
    print(f"  Before: {existing_names}")
    print(f"  Syncing: {list(new_servers.keys())}")
    print(f"  After: {total} servers")

    if write:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        backup_file(config_path)
        with open(config_path, "w") as f:
            json.dump(merged, f, indent=2)
            f.write("\n")
        print(f"  \u2713 Written")
    else:
        print(f"  Preview (use --write to apply):")
        print(json.dumps(merged, indent=2))


def main():
    parser = argparse.ArgumentParser(
        description="Sync Hermes MCP servers to VS Code / Cursor MCP configs"
    )
    parser.add_argument("--write", action="store_true",
                        help="Actually write files (default: dry-run)")
    parser.add_argument("--target",
                        help="Filter to specific target (e.g. 'copilot-chat', 'roo-cline', 'cursor')")
    parser.add_argument("--remote", action="store_true",
                        help="Use VS Code Server remote paths")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview only (default when --write not set)")
    parser.add_argument("--list", action="store_true",
                        help="List detected targets without syncing")
    args = parser.parse_args()

    mode = "remote" if args.remote else "local"
    write = args.write and not args.dry_run

    targets = discover_targets(mode, args.target)

    if args.list:
        print(f"Mode: {mode}")
        if not targets:
            print("No MCP-capable targets detected.")
        else:
            for tid, path, skey, fmt, label in targets:
                print(f"  {label:20s} ({tid:15s}) → {path}")
        return

    print(f"Mode: {mode} | {'WRITE' if write else 'DRY-RUN'}")
    print(f"Hermes config: {HERMES_CONFIG}")

    hermes_servers = read_hermes_servers()
    if not hermes_servers:
        return
    print(f"Hermes servers: {list(hermes_servers.keys())}")

    if not targets:
        print(f"\nNo MCP-capable targets detected in {mode} mode.")
        print("Install one of: " + ", ".join(info["name"] for info in MCP_EXTENSIONS.values()))
        return

    print(f"Detected {len(targets)} target(s):")
    for tid, path, skey, fmt, label in targets:
        print(f"  - {label} ({tid})")

    for tid, path, skey, fmt, label in targets:
        sync_target(tid, path, skey, fmt, label, hermes_servers, write=write)

    if not write:
        print("\n\U0001f4a1 Dry-run complete. Use --write to apply changes.")


if __name__ == "__main__":
    main()
