"""The command line: serve, list, call, configure, doctor.

Borrowed in shape from Commonwealth-MCP and NEPA-MCP (`configure <client>`,
`doctor`, `tools call`), because the first thing anyone does with a local MCP
server is point a client at it and the second is wonder why it answered
`not_cached`. Everything here is stdlib and read-only except `configure`, which
writes one server entry into a client's own config file, idempotently, and
shows the diff first when asked.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import shutil
import sys
from pathlib import Path

from . import SCHEMA_VERSION, __version__, config
from .core.errors import Sept11Error
from .core.evidence import envelope_schema
from .mcp.server import PROMPTS, PROTOCOL_VERSION, RESOURCE_TEMPLATES, Server
from .mcp.tools import TOOLS

SERVER_KEY = "sept11"

# Only clients whose config is JSON at a stable path are written. Codex is TOML, which the
# standard library reads but does not write, so it gets a block to paste.
CLIENTS = {
    "claude-code": {"key": "mcpServers", "scope": "project", "path": ".mcp.json",
                    "note": "Project-scoped: shared with anyone who clones the repository."},
    "cursor": {"key": "mcpServers", "scope": "project", "path": ".cursor/mcp.json", "note": ""},
    "vscode": {"key": "servers", "scope": "project", "path": ".vscode/mcp.json",
               "note": "VS Code names the block `servers`, not `mcpServers`."},
    "claude": {"key": "mcpServers", "scope": "user", "path": "",
               "note": "Claude Desktop; its file lives in a different place on each platform."},
}
TOML_CLIENTS = ("codex",)


def desktop_config_path() -> Path:
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def config_path(client: str, override: str | None = None, cwd: Path | None = None) -> Path:
    if override:
        return Path(override).expanduser().resolve()
    spec = CLIENTS[client]
    if client == "claude":
        return desktop_config_path()
    base = Path.home() if spec["scope"] == "user" else (cwd or Path.cwd())
    return (base / spec["path"]).resolve()


def launch_command(root: Path) -> dict:
    """How a client starts the server: the console script when installed, else this interpreter.

    SEPT11_ROOT names the checkout that holds the evidence, so an installed package and a
    client started from any directory read the same captures.
    """
    script = shutil.which("sept11-mcp")
    env = {"SEPT11_ROOT": str(root)}
    if script:
        return {"command": script, "args": ["serve"], "env": env}
    return {"command": sys.executable, "args": ["-m", "sept11.cli", "serve"],
            "env": {**env, "PYTHONPATH": str(root)}}


def merged(existing: dict, client: str, root: Path) -> dict:
    spec = CLIENTS[client]
    out = dict(existing)
    servers = dict(out.get(spec["key"]) or {})
    servers[SERVER_KEY] = launch_command(root)
    out[spec["key"]] = servers
    return out


def read_config(path: Path) -> tuple[dict, str]:
    """(parsed, raw). Missing is empty; unparseable stops the command rather than clobbering it."""
    if not path.exists():
        return {}, ""
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        return {}, raw
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path} is not valid JSON ({exc}); fix or move it. Refusing to overwrite "
                         "a file that may hold configuration.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{path} holds a {type(parsed).__name__}, not an object; refusing to overwrite it.")
    block = parsed.get(CLIENTS.get(path.name, {}).get("key", ""))
    if block is not None and not isinstance(block, dict):
        raise ValueError(f"{path} has a {type(block).__name__} where an object of servers belongs")
    return parsed, raw


def render_toml_block(root: Path) -> str:
    launch = launch_command(root)
    lines = [f"[mcp_servers.{SERVER_KEY}]", f'command = {json.dumps(launch["command"])}',
             f'args = {json.dumps(launch["args"])}', "", f"[mcp_servers.{SERVER_KEY}.env]"]
    lines += [f'{k} = {json.dumps(v)}' for k, v in launch["env"].items()]
    return "\n".join(lines) + "\n"


def cmd_configure(args, root: Path, out=print) -> int:
    if args.client in TOML_CLIENTS:
        out(f"# Add this to ~/.codex/config.toml (Codex reads TOML, which this tool does not write):\n"
            + render_toml_block(root))
        return 0
    spec = CLIENTS[args.client]
    path = config_path(args.client, args.path)
    existing, raw = read_config(path)
    block = existing.get(spec["key"])
    if block is not None and not isinstance(block, dict):
        raise ValueError(f"{path} has a {type(block).__name__} under {spec['key']!r}; expected an object")
    updated = merged(existing, args.client, root)
    text = json.dumps(updated, indent=2) + "\n"
    if text == raw:
        out(f"{path}: already configured; nothing to write")
        return 0
    diff = difflib.unified_diff(raw.splitlines(keepends=True), text.splitlines(keepends=True),
                                fromfile=str(path), tofile=str(path))
    out("".join(diff) or f"+ {path} (new file)")
    if args.dry_run:
        out("dry run: nothing written")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    out(f"wrote {path}" + (f"  ({spec['note']})" if spec["note"] else ""))
    return 0


def doctor_report(cfg: config.Config) -> list[dict]:
    """Each row: {check, status: ok|warn|fail, detail}. Nothing here touches the network."""
    rows = []

    def row(check, status, detail):
        rows.append({"check": check, "status": status, "detail": detail})

    version = sys.version_info
    row("python", "ok" if version >= (3, 9) else "fail", f"{sys.version.split()[0]} at {sys.executable}")
    row("root", "ok" if (cfg.root / "review" / "publication.json").is_file() else "fail",
        f"{cfg.root} (set SEPT11_ROOT to point an installed package at a checkout)")
    try:
        from .storage.publication import Publication
        publication = Publication.load(cfg.root)
        problems = publication.problems()
        row("publication allowlist", "ok" if not problems else "fail",
            f"{len(publication.artifacts)} files listed; {len(problems)} problems"
            + (": " + problems[0] if problems else ""))
    except Sept11Error as exc:
        row("publication allowlist", "fail", f"{exc.code}: {exc}")
    try:
        from .adapters import portal
        from .storage.snapshots import SnapshotStore
        latest = SnapshotStore(cfg.snapshot_dir).latest_accepted()
        if latest is None:
            row("catalog snapshot", "warn", "none accepted; run `python3 scripts/watchdog.py run` "
                "(one export) or `watchdog.py import <catalog.csv> --captured-at <ISO-8601>`")
        else:
            row("catalog snapshot", "ok", f"{latest.snapshot_id}: {latest.manifest['documents']} documents, "
                f"captured {latest.captured_at}")
    except Sept11Error as exc:
        row("catalog snapshot", "fail", f"{exc.code}: {exc}")
    from .storage.cache import EvidenceStore
    captured = EvidenceStore(cfg.page_text_dir).documents()
    row("captured page text", "ok" if captured else "warn",
        f"{len(captured)} documents under {cfg.page_text_dir}"
        + ("" if captured else "; portal_get_page_text will answer not_cached"))
    schema_path = cfg.root / "schemas" / "envelope.schema.json"
    try:
        drift = json.loads(schema_path.read_text()) != envelope_schema()
        row("wire schema", "fail" if drift else "ok",
            "schemas/envelope.schema.json " + ("differs from the code; regenerate it" if drift else "matches the code"))
    except (OSError, ValueError) as exc:
        row("wire schema", "fail", f"cannot read {schema_path}: {exc}")
    row("evals", "ok" if cfg.evals_file.is_file() else "warn",
        f"{cfg.evals_file}" + ("" if cfg.evals_file.is_file() else " missing; run `scripts/mcp_evals.py --seed`"))
    row("live search", "ok", "enabled: queries reach the City" if cfg.allow_live_search
        else "disabled (SEPT11_ALLOW_LIVE=0): catalog_search answers locally")
    try:
        cfg.cache_dir.mkdir(parents=True, exist_ok=True)
        writable = os.access(cfg.cache_dir, os.W_OK)
    except OSError:
        writable = False
    row("cache directory", "ok" if writable else "warn", f"{cfg.cache_dir} ({'writable' if writable else 'not writable'})")
    row("server", "ok", f"sept11-mcp {__version__}, protocol {PROTOCOL_VERSION}, schema {SCHEMA_VERSION}, "
        f"{len(TOOLS)} tools, {len(PROMPTS)} prompts, {len(RESOURCE_TEMPLATES)} resource templates")
    return rows


def cmd_doctor(args, cfg: config.Config, out=print) -> int:
    rows = doctor_report(cfg)
    width = max(len(r["check"]) for r in rows)
    for r in rows:
        out(f"{r['status']:<4} {r['check']:<{width}}  {r['detail']}")
    failed = [r for r in rows if r["status"] == "fail"]
    out(f"examined {len(rows)} checks: {len(failed)} failed, "
        f"{sum(1 for r in rows if r['status'] == 'warn')} warnings")
    return 1 if failed else 0


def cmd_call(args, out=print) -> int:
    try:
        arguments = json.loads(args.args) if args.args else {}
    except json.JSONDecodeError as exc:
        print(f"--args must be JSON: {exc}", file=sys.stderr)
        return 2
    server = Server()
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                              "params": {"name": args.name, "arguments": arguments}})
    if "error" in response:
        out(json.dumps(response["error"], indent=2))
        return 1
    result = response["result"]
    out(json.dumps(result["structuredContent"], indent=2, ensure_ascii=False))
    return 1 if result.get("isError") else 0


def cmd_tools(args, out=print) -> int:
    definitions = [t.definition() for t in TOOLS.values()]
    if args.json:
        out(json.dumps(definitions, indent=2))
        return 0
    width = max(len(d["name"]) for d in definitions)
    for d in definitions:
        args_list = ", ".join(d["inputSchema"]["properties"]) or "(no arguments)"
        out(f"{d['name']:<{width}}  {d['title']}  [{args_list}]")
    out(f"{len(definitions)} tools; {len(PROMPTS)} prompts; {len(RESOURCE_TEMPLATES)} resource templates")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv == ["--tools"]:  # the v0 flag, kept working
        argv = ["tools", "--json"]
    ap = argparse.ArgumentParser(prog="sept11-mcp", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the read-only MCP server on stdio (the default)")
    tools = sub.add_parser("tools", help="list the tools")
    tools.add_argument("--json", action="store_true", help="print the full definitions")
    call = sub.add_parser("call", help="call one tool from the shell, no MCP client needed")
    call.add_argument("name")
    call.add_argument("--args", default="", help='JSON object of arguments, e.g. \'{"text": "John Street"}\'')
    conf = sub.add_parser("configure", help="write this server into a client's MCP config")
    conf.add_argument("client", choices=sorted(CLIENTS) + list(TOML_CLIENTS))
    conf.add_argument("--dry-run", action="store_true", help="show the change without writing")
    conf.add_argument("--path", default=None, help="config file to write instead of the default")
    sub.add_parser("doctor", help="check the local evidence and the server without touching the network")
    args = ap.parse_args(argv)
    cfg = config.load()
    try:
        if args.cmd in (None, "serve"):
            return Server().serve()
        if args.cmd == "tools":
            return cmd_tools(args)
        if args.cmd == "call":
            return cmd_call(args)
        if args.cmd == "configure":
            return cmd_configure(args, cfg.root)
        if args.cmd == "doctor":
            return cmd_doctor(args, cfg)
    except (ValueError, Sept11Error) as exc:
        print(f"sept11-mcp: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    sys.exit(main())
