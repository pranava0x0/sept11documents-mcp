"""Stdio MCP server: JSON-RPC 2.0 frames in, evidence out.

Framework-free on purpose (spec 10). The dependency surface of this process is
the Python standard library, so there is no SDK transport to misconfigure and
nothing to enable by accident: no HTTP listener, no WebSocket, no experimental
tasks, no session store, no OAuth discovery.

Transport rules that matter:
  * stdout carries protocol frames only — `sys.stdout` is redirected to stderr
    for the whole process, so a stray print cannot corrupt the stream;
  * one request is handled at a time, so concurrency is bounded by construction;
  * every log line goes to stderr and names no query text and no source body.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

from .. import SCHEMA_VERSION, __version__
from ..config import TOOL_DEADLINE_SECONDS
from ..core.errors import InputError, IntegrityError, NotCachedError, Sept11Error
from .context import Context, Deadline
from .tools import CITE_RULE, CURATED_RULE, LEDGER_RULE, RESULTS_RULE, TOOLS, UNTRUSTED, resolve_name

PROTOCOL_VERSION = "2025-11-25"
SERVER_INFO = {"name": "sept11", "title": "September 11th Documents", "version": __version__}

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS = -32700, -32600, -32601, -32602
RESOURCE_NOT_FOUND = -32002

RESEARCH_PROMPT = (
    "You are assisting research in a mass-casualty archive. Search, then read pages verbatim before "
    "answering. Cite every fact as `NYC-WTC_… p.N`. If a date is not printed on the page, say so. Do not "
    "speculate about individuals. Keep a clinical tone. " + UNTRUSTED + " " + CITE_RULE)

VERIFY_PROMPT = (
    "Before you state a number, a date, a reading or a quotation from this archive or from a registered "
    "source, run citations_verify with the exact words you intend to use and the locator (Bates number "
    "and one-based page, or the registered source id). Publish only what comes back `found`, and say "
    "that `found` means the text was located and that the claim has not been reviewed. Anything `not-found` or "
    "`unverifiable` is stated as unverified or left out. " + CITE_RULE)


def _changes_prompt(arguments: dict) -> str:
    since = str(arguments.get("since") or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T[0-9:+.Z-]+)?", since):
        raise InputError("`since` must be an ISO-8601 date such as 2026-09-09")
    return (f"Call portal_changes_since with since={since!r} and report the interval it actually compared, "
            "the counts of documents observed added and observed absent, and the boxes they fall in. "
            "Absence is an observation only; removal, redaction and withholding are separate findings the capture cannot make. Then call "
            "doi_milestones and list which dated obligations fall inside the window and what was observed "
            "for each. Make no compliance determination and give no percentage score. " + UNTRUSTED)


def _argument(arguments: dict, name: str, limit: int, required: bool = True) -> str:
    """A prompt argument is the caller's own words: one line, bounded, no control characters."""
    value = str(arguments.get(name) or "").strip()
    if required and not value:
        raise InputError(f"`{name}` is required")
    if len(value) > limit or re.search(r"[\x00-\x1f\x7f]", value):
        raise InputError(f"`{name}` must be one line of at most {limit} characters")
    return value


def _plan_prompt(arguments: dict) -> str:
    question = _argument(arguments, "question", 300)
    return (f"Research question: {question!r}. Before answering, write a short plan that names the tools you "
            "will call and why, in this order where they apply: portal_query_draft or catalog_search to find "
            "candidate documents; building_lookup for an address; portal_get_document for a record; "
            "portal_get_page_text to read the page; timeline_lookup and readings_lookup for statements, "
            "records and sampling results already located; budget_lookup, doi_milestones and upcoming_dates "
            "for money and deadlines. Then run the plan. Answer only from returned text, cite each fact as "
            "`NYC-WTC_… p.N` or the official source, run citations_verify on every quote you take from "
            "portal_get_page_text, and say plainly what the archive does not show. Do not speculate about "
            "individuals. " + UNTRUSTED + " " + RESULTS_RULE)


def _building_prompt(arguments: dict) -> str:
    address = _argument(arguments, "address", 80)
    return (f"Find what the City's archive files under {address!r}. Call building_lookup with that address and "
            "report the folders it returns with their box, document counts and first Bates number, and the "
            "BIN, block and lot the labels print. For the largest folder, run the catalog_search call in its "
            "next_calls entry as given, which lists that folder's documents alone, then portal_get_document on "
            "one of them. Read a page with "
            "portal_get_page_text only when it is captured, and cite it with citations_format. State that a "
            "label match shows where paper was filed, that a document can concern the building without a "
            "label naming it, and that the tool decides no exposure-zone question; quote each program's zone "
            "wording from zone_definitions instead, naming the group when a zone has `who` set. " + UNTRUSTED + " "
            + CITE_RULE)


def _presence_prompt(arguments: dict) -> str:
    return ("Help someone see which documents the World Trade Center Health Program and the Victim "
            "Compensation Fund accept as proof of presence. Ask only two things: which program, and whether "
            "they were a responder or a survivor (lived, worked or went to school in the area). Do not ask for "
            "or record a name, address, date of birth, employer or any identifier. Call presence_evidence with "
            "those two answers and present the official rules exactly as quoted, each with its source link, "
            "followed by the New York City offices that hold records. Say that this is not legal advice and "
            "does not decide eligibility, and give the helplines from the result. " + UNTRUSTED)


def _statements_prompt(arguments: dict) -> str:
    topic = _argument(arguments, "topic", 20, required=False) or "all"
    if topic not in ("air", "schools", "liability", "cleanup", "oversight", "all"):
        raise InputError("`topic` must be air, schools, liability, cleanup, oversight or all")
    return (f"Call timeline_lookup with topic={topic!r} and present the entries in date order in two columns: "
            "what officials said in public, and what the City's records show, with the later reviews after "
            "them. Quote each entry exactly as returned with its citation and the basis for its date. If the "
            "topic is air or schools, call readings_lookup with include_unreviewed=true and list the sampling "
            "results with their units as printed, labelled unreviewed. Draw no conclusion about what any "
            "person knew or intended: placement side by side is not a finding, and Council Resolution 560-A "
            "assigns that analysis to the Department of Investigation. " + UNTRUSTED + " " + CURATED_RULE)


def _money_prompt(arguments: dict) -> str:
    topic = _argument(arguments, "topic", 20, required=False) or "all"
    if topic not in ("portal", "doi", "education", "all"):
        raise InputError("`topic` must be portal, doi, education or all")
    return (f"Call budget_lookup with topic={topic!r}. For each commitment, give the announced amount with its "
            "source and the state of each stage: announcement, adopted line, contract, payment, delivery. Say "
            "`not linked` where a stage has no evidence; an unknown amount is not zero. Never add the amounts "
            "together: their fiscal periods differ and none of them is spending. Then call upcoming_dates and "
            "doi_milestones and list the dated obligations ahead that bear on these commitments, with what "
            "was last observed. Make no compliance determination. " + LEDGER_RULE)


# Prompts are fixed text with validated arguments; nothing from a document reaches them.
PROMPTS = {
    "research-assistant": {
        "title": "Archive research rules",
        "description": "Cite-or-abstain rules for reading this archive.",
        "arguments": [],
        "render": lambda arguments: RESEARCH_PROMPT,
    },
    "verify-before-publishing": {
        "title": "Verify every quote before publishing",
        "description": "Run citations_verify on each number, date or quote and publish only located text.",
        "arguments": [],
        "render": lambda arguments: VERIFY_PROMPT,
    },
    "what-changed": {
        "title": "What the catalog shows since a date",
        "description": "Compare captures since a date and place the dated obligations beside the result.",
        "arguments": [{"name": "since", "description": "ISO-8601 date, e.g. 2026-09-09", "required": True}],
        "render": _changes_prompt,
    },
    "research-plan": {
        "title": "Plan the tool calls for a question",
        "description": "Name the tools a question needs, run them, and answer only from cited text.",
        "arguments": [{"name": "question", "description": "The research question, one line", "required": True}],
        "render": _plan_prompt,
    },
    "building-records": {
        "title": "Records filed under an address",
        "description": "Folders whose labels name an address, their documents, and a cited page.",
        "arguments": [{"name": "address", "description": "e.g. 15 John Street, or a seven-digit BIN",
                       "required": True}],
        "render": _building_prompt,
    },
    "proof-of-presence": {
        "title": "Proof-of-presence documents, without personal details",
        "description": "Walk through the programs' quoted rules after asking only program and role.",
        "arguments": [],
        "render": _presence_prompt,
    },
    "statements-and-records": {
        "title": "Public statements beside City records",
        "description": "The timeline in two columns, with sampling results, and no finding drawn.",
        "arguments": [{"name": "topic", "description": "air, schools, liability, cleanup, oversight or all",
                       "required": False}],
        "render": _statements_prompt,
    },
    "follow-the-money": {
        "title": "Announced funding and its evidence",
        "description": "Each commitment's stages and the dated obligations ahead, never summed.",
        "arguments": [{"name": "topic", "description": "portal, doi, education or all", "required": False}],
        "render": _money_prompt,
    },
}

# Resource templates: a document or a page addressed as a resource, served by the same
# handlers as the tools (same bounds, same PII stop rule, no fetching).
RESOURCE_TEMPLATES = [
    {"uriTemplate": "sept11://document/{bates}", "name": "document",
     "title": "Catalog record for one document", "mimeType": "application/json",
     "description": "portal_get_document as a resource: metadata, capture status and folder neighbours."},
    {"uriTemplate": "sept11://page/{bates}/{page}", "name": "page-text",
     "title": "Verbatim text of one page", "mimeType": "application/json",
     "description": "portal_get_page_text as a resource: captured text only, withheld when the PII screen fires."},
]
_DOCUMENT_URI = re.compile(r"^sept11://document/(NYC-WTC_[0-9]+)$")
_PAGE_URI = re.compile(r"^sept11://page/(NYC-WTC_[0-9]+)/([0-9]{1,5})$")


def _result(request_id, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class Server:
    def __init__(self, ctx: Context | None = None, deadline_seconds: float = TOOL_DEADLINE_SECONDS):
        self.ctx = ctx or Context()
        self.deadline_seconds = deadline_seconds
        self.initialized = False

    # -- resources and prompts ---------------------------------------------
    def _resources(self) -> list[dict]:
        try:
            artifacts = self.ctx.publication().artifacts.values()
        except Sept11Error as exc:
            self.ctx.log(f"sept11-mcp: no publication allowlist ({exc.code}); serving no resources")
            return []
        return [{"uri": a.resource, "name": Path(a.path).stem, "mimeType": "application/json",
                 "description": f"{a.notes} (review status: {a.review_status})"}
                for a in artifacts if a.resource]

    def _read_template(self, uri: str) -> dict | None:
        """A templated URI answered by a tool handler, or None when the URI is not templated."""
        if not isinstance(uri, str):
            return None
        if _DOCUMENT_URI.match(uri):
            name, arguments = "portal_get_document", {"bates": _DOCUMENT_URI.match(uri).group(1)}
        elif _PAGE_URI.match(uri):
            match = _PAGE_URI.match(uri)
            name, arguments = "portal_get_page_text", {"bates": match.group(1), "page": int(match.group(2))}
        else:
            return None
        envelope = TOOLS[name].handler(self.ctx, arguments, Deadline(self.deadline_seconds))
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(envelope.as_dict(), indent=2, ensure_ascii=False)}]}

    def _read_resource(self, uri: str) -> dict:
        templated = self._read_template(uri)
        if templated is not None:
            return templated
        payload, artifact = self.ctx.publication().read_json(uri)
        body = {"schema_version": SCHEMA_VERSION, "review_status": artifact.review_status,
                "reviewed_by": artifact.reviewed_by, "freshness_policy": artifact.freshness_policy,
                "sha256": artifact.sha256, "data": payload}
        return {"contents": [{"uri": uri, "mimeType": "application/json",
                              "text": json.dumps(body, indent=2)}]}

    # -- tool calls ---------------------------------------------------------
    def _call_tool(self, params: dict) -> dict:
        name = resolve_name(params.get("name"))
        tool = TOOLS.get(name)
        if tool is None:
            raise Sept11Error(f"unknown tool {name!r}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise Sept11Error("arguments must be an object")
        unknown = sorted(set(arguments) - set(tool.schema["properties"]))
        if unknown:
            raise Sept11Error(f"unknown argument(s) for {name}: {', '.join(unknown)}")
        missing = [key for key in tool.schema["required"] if key not in arguments]
        if missing:
            raise Sept11Error(f"{name} requires {', '.join(missing)}")
        started = time.monotonic()
        envelope = tool.handler(self.ctx, arguments, Deadline(self.deadline_seconds))
        payload = envelope.as_dict()
        self.ctx.log(f"sept11-mcp: {name} ok in {time.monotonic() - started:.2f}s "
                     f"({len(envelope.warnings)} warnings)")
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
                "structuredContent": payload, "isError": False}

    def _tool_failure(self, error: Sept11Error) -> dict:
        payload = {"error": error.payload()}
        self.ctx.log(f"sept11-mcp: tool failed ({error.code})")
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}],
                "structuredContent": payload, "isError": True}

    # -- dispatch -----------------------------------------------------------
    def handle(self, message: dict) -> dict | None:
        if message.get("jsonrpc") != "2.0" or "method" not in message:
            return _error(message.get("id"), INVALID_REQUEST, "not a JSON-RPC 2.0 request")
        method, request_id = message["method"], message.get("id")
        params = message.get("params") or {}
        is_notification = "id" not in message

        if method == "initialize":
            self.initialized = True
            return _result(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False},
                                 "prompts": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": RESEARCH_PROMPT,
            })
        if method in ("notifications/initialized", "notifications/cancelled"):
            if method == "notifications/cancelled":
                # Requests are handled one at a time, so a cancellation arrives between calls.
                self.ctx.log("sept11-mcp: cancellation received")
            return None
        if method == "ping":
            return _result(request_id, {})
        if method == "tools/list":
            return _result(request_id, {"tools": [t.definition() for t in TOOLS.values()]})
        if method == "tools/call":
            try:
                return _result(request_id, self._call_tool(params))
            except Sept11Error as exc:
                return _result(request_id, self._tool_failure(exc))
            except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as exc:
                # Model-supplied identifiers that fail validation are the caller's error.
                return _result(request_id, self._tool_failure(InputError(str(exc))))
        if method == "resources/list":
            return _result(request_id, {"resources": self._resources()})
        if method == "resources/templates/list":
            return _result(request_id, {"resourceTemplates": list(RESOURCE_TEMPLATES)})
        if method == "resources/read":
            uri = params.get("uri")
            try:
                return _result(request_id, self._read_resource(uri))
            except (NotCachedError, IntegrityError) as exc:
                # A templated URI that names a document this machine does not hold.
                return _error(request_id, RESOURCE_NOT_FOUND, f"{exc.code}: {exc}")
            except Sept11Error as exc:
                return _error(request_id, INVALID_PARAMS, f"{exc.code}: {exc}")
            except (AttributeError, TypeError, ValueError, KeyError, OverflowError) as exc:
                return _error(request_id, INVALID_PARAMS, f"invalid_input: {exc}")
        if method == "prompts/list":
            return _result(request_id, {"prompts": [
                {"name": name, "title": spec["title"], "description": spec["description"],
                 "arguments": list(spec["arguments"])} for name, spec in PROMPTS.items()]})
        if method == "prompts/get":
            spec = PROMPTS.get(params.get("name"))
            if spec is None:
                return _error(request_id, INVALID_PARAMS, f"unknown prompt {params.get('name')!r}")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                return _error(request_id, INVALID_PARAMS, "prompt arguments must be an object")
            try:
                text = spec["render"](arguments)
            except Sept11Error as exc:
                return _error(request_id, INVALID_PARAMS, f"{exc.code}: {exc}")
            return _result(request_id, {
                "description": spec["description"],
                "messages": [{"role": "user", "content": {"type": "text", "text": text}}]})
        if is_notification:
            return None
        return _error(request_id, METHOD_NOT_FOUND, f"unknown method {method!r}")

    # -- transport ----------------------------------------------------------
    def serve(self, stdin=None, stdout=None) -> int:
        stdin = stdin or sys.stdin
        frames = stdout or sys.stdout
        # Anything that prints from here on lands on stderr, never in the frame stream.
        sys.stdout = sys.stderr
        self.ctx.log(f"sept11-mcp {__version__} on stdio; protocol {PROTOCOL_VERSION}; "
                     f"{len(TOOLS)} tools; live search "
                     f"{'enabled' if self.ctx.cfg.allow_live_search else 'disabled'}")
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                self._write(frames, _error(None, PARSE_ERROR, "invalid JSON"))
                continue
            if not isinstance(message, dict):
                self._write(frames, _error(None, INVALID_REQUEST, "batch requests are not supported"))
                continue
            response = self.handle(message)
            if response is not None:
                self._write(frames, response)
        return 0

    @staticmethod
    def _write(stream, message: dict) -> None:
        stream.write(json.dumps(message, ensure_ascii=False) + "\n")
        stream.flush()
