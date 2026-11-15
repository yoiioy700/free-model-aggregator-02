"""Serve the make.fun free-model-aggregator-02 competition entry.

Purpose
This single-file Python server exposes a catalog of free upstream models.
It accepts client requests on two compatible HTTP API wires.
It routes each request to a model allowed by the competition brief.
The gateway supplies the actual model answers.
The entry translates requests and responses where the client wire requires it.
It does not generate model answers locally.
Its normal lifecycle is coordinated by the competition environment.

Brief input
MAKEFUN_PROMPT_FILE names the local brief supplied to the entry.
The parser extracts JSON documents embedded in that brief.
Documents are identified by the competition schema prefix.
The model document supplies model identifiers and capability metadata.
The prompt document supplies the declared workload and its requirements.
Model rows carry rating_milli, context length, and capability flags.
Prompt rows can require tools, vision, JSON output, or minimum context.
Unknown capabilities cannot satisfy an explicitly required capability.
Missing or unreadable briefs are represented by a diagnostic error string.

Catalog contract
The catalog advertises the free models named by the brief.
Registration sends that catalog to the environment.
GET /v1/models exposes the same intended free-model set to clients.
The competition brief contains exactly fourteen free model identifiers.
The catalog checks require exactly those fourteen identifiers.
Omitting an identifier fails the exact-set check.
Adding a paid model or an invented identifier also fails that check.
The brief is the source of the identifiers; no second hardcoded list is needed.
Catalog construction removes duplicate identifiers and excludes nonfree rows.
The provider's public base URL is stable across live and replay runs.
The gateway's transient address is used for calls, not catalog metadata.
An absent free-model list produces an empty providers array.

OpenAI client wire
POST /v1/chat/completions accepts OpenAI Chat Completions requests.
A nonstreaming request receives an OpenAI-shaped JSON response.
A streaming request receives server-sent events, abbreviated SSE.
The selected model identifier replaces the request's model for the gateway call.
JSON answers report the identifier that the entry actually dialled.
SSE data objects likewise report that dialled model identifier.
Non-JSON SSE content and the terminal DONE marker pass through.
Repeated finish reasons on later usage chunks are cleared.
GET /v1/models without x-api-key uses the OpenAI model-list shape.
Model creation metadata uses a fixed timestamp.

Anthropic client wire
POST /v1/messages accepts Anthropic Messages requests.
The gateway still receives an OpenAI Chat Completions request.
System text becomes a system message on the gateway wire.
Text and supported image blocks become OpenAI content parts.
Tool-use blocks become tool calls, and tool results become tool messages.
Tool definitions and tool choice are translated to the gateway format.
Supported generation parameters are forwarded by the translation helper.
A nonstreaming answer becomes an Anthropic Messages JSON object.
The response contains text or tool-use content blocks and translated usage.
Finish reasons become Anthropic stop reasons.
GET /v1/models with x-api-key uses the Anthropic model-list shape.
Its creation metadata is also fixed rather than read from the clock.

Anthropic streaming
Streaming gateway chunks are converted into named Anthropic SSE events.
The stream begins with message_start.
Text and tool calls produce content_block_start and content_block_delta events.
Completed content blocks receive content_block_stop events.
The stop reason is held while the final usage information arrives.
message_delta carries the resulting stop reason and output token count.
message_stop ends the translated message sequence.
Streaming writes are flushed so the client can consume incremental output.
The HTTP connection closes to delimit the streaming response body.

Routing policy
Declared prompt requirements are consumed in the brief's arrival order.
A lock protects advancement of the prompt manifest index.
Requests beyond the manifest use requirements inferred from their bodies.
Only free models satisfying the requirements are eligible.
The highest rating_milli eligible fresh model is tried first.
Ties are resolved by descending context length and then model identifier.
Models that refused during this run are moved to the back operationally.
The implementation excludes them while any fresh eligible candidates remain.
If no fresh eligible candidates remain, it reuses the full eligible order.
This refusal memory is derived from gateway answers and connection failures.
It is not a timed cooldown and has no clock-based expiry.

Failover evidence
A gateway refusal can cause another eligible model to be tried.
Unreachable candidates are recorded as 502 failures.
Rate limits, provider faults, and other refusals can mark a model as refused.
A 400 response stops failover because the same request may fail everywhere.
A single routing attempt records at most eight candidate failures.
A successful recovery carries the x-makefun-upstream-failures header.
That header contains a compact JSON array describing preceding failures.
Each recorded failure names its status, error code, and model identifier.
The trail makes recovered refusals visible to the environment's checks.
Both JSON and SSE success responses can carry this header.
Exhausted candidates produce an error in the client's own wire format.

Environment lifecycle
MAKEFUN_ENV_URL identifies the environment's control endpoint.
OPENAI_BASE_URL identifies the gateway's OpenAI-compatible root.
OPENAI_API_KEY supplies the gateway credential for this entry's lane.
The HTTP server binds to an available port on all interfaces.
POST /register sends the protocol version, port, and built catalog.
Registration can report how many prompts the trial holds.
A background thread polls GET /work while the server handles requests.
A done response ends the process successfully.
SIGTERM and SIGINT also terminate the process with exit status zero.
Polling sleeps and network timeouts control waiting, not model selection.

Determinism and local checks
Routing choices come from the brief, request data, and gateway answers.
Catalog contents and metadata are derived from stable declared inputs.
Neither a clock nor unseeded randomness selects models or invents answers.
Fixed model timestamps keep catalog replies reproducible.
The normal entrypoint logs brief errors before starting its selected mode.
The --list mode prints the built registration catalog and returns.
The --selftest mode reports the catalog model count when a brief is available.
Without a usable brief, --selftest reports selftest: no brief.
Both local modes return before registration, polling, or serving requests.

Design notes
============
The entry owns request routing and protocol adaptation within one process.
Its inputs are the brief, environment variables, HTTP bodies, and gateway replies.
The brief is parsed during module initialization before a mode is selected.
Parsing searches for JSON objects carrying the competition schema prefix.
Model normalization preserves declared identifiers and capability information.
Prompt normalization retains requirements in their declared arrival order.
A brief error is retained as data so local diagnostics can still run.
The catalog is built once from the normalized model rows.
The normal entrypoint logs any brief diagnostic before selecting a mode.
The list mode prints the registration catalog and then returns.
The selftest mode reports brief availability and, when valid, catalog size.
Neither local mode starts the HTTP server or enters the work loop.

Normal serving first requires environment and gateway endpoint configuration.
The HTTP server binds all interfaces using an operating-system-selected port.
The reserved environment port is rejected if it is unexpectedly selected.
Registration posts the protocol version, chosen port, and catalog together.
The environment may return a request count for the diagnostic log.
A registration failure escapes to the top-level error handler.
After registration, signal handlers and a daemon polling thread are installed.
The server then accepts requests through the common route dispatcher.
GET /v1/models selects its response shape using the x-api-key header.
Both list shapes draw their model rows from the same normalized brief.
Unknown routes receive a JSON error describing the unmatched path and method.
POST /v1/chat/completions consumes an OpenAI-shaped request object.
POST /v1/messages consumes an Anthropic-shaped request object.
Both routes reject a missing or invalid JSON object before choosing models.
The request-body reader checks the declared length against the size limit.

Routing consumes the next manifest row under a lock.
That lock prevents two requests from consuming the same manifest index.
The manifest requirements take precedence over body-based inference.
When the manifest is exhausted, inference checks tools, images, and JSON mode.
Eligibility requires a free model and all requested capabilities.
An unknown capability cannot establish support for a required feature.
A minimum context requirement also excludes models with unknown context length.
Eligible models are sorted by descending rating_milli.
Descending context length breaks equal-rating ties.
The model identifier supplies the final stable tie breaker.
Cooldown is a process-local record of observed unsuccessful attempts.
It has no expiration time and does not predict future provider availability.
While fresh candidates exist, previously refused candidates are omitted.
When all eligible candidates have refused, the full sorted list is reused.
No model is preemptively excluded because of its name or a guessed outage.
Connection exceptions count as observed failures and are recorded as 502.
HTTP refusals retain their observed status and a bounded error-code string.
A 400 stops the attempt because another model may reject the same body.
Other unsuccessful statuses mark the model and allow the loop to continue.
Each attempt records at most eight failures before returning exhaustion.
A successful attempt returns the model identifier actually passed to dial.

The OpenAI route copies the request and replaces its model for each candidate.
The Anthropic route builds a Chat Completions request for each candidate.
System text is collected into a leading system message when present.
String message content retains its user or assistant role mapping.
Assistant tool-use blocks become function calls with JSON argument strings.
Assistant text is collected alongside those calls in the assistant message.
User tool-result blocks become tool messages referring to tool-call identifiers.
Supported user text and image blocks become OpenAI content parts.
Base64 images use data URLs; URL image sources retain their supplied URLs.
Unsupported content blocks are omitted by the existing translation rules.
Numeric output limits, temperature, and top_p are forwarded when present.
Nonempty stop_sequences become the gateway's stop list.
Tool input schemas become function parameters with an object fallback.
Tool choices map auto, any, and named tools into the gateway's vocabulary.
Streaming requests ask the gateway to include usage information.

The upstream wrapper exposes either whole-response text or byte chunks.
HTTP error responses use the same wrapper so their bodies remain readable.
A recovered refusal trail is attached before success headers are committed.
That trail identifies failures that would otherwise disappear behind success.
Exhaustion becomes a 502 error in the requesting client's wire format.
For JSON OpenAI answers, the selected model overwrites the provider model field.
For JSON Anthropic answers, the first choice supplies assistant content.
Tool-call argument strings are decoded into tool-use input objects when valid.
Missing or invalid argument objects become empty objects.
An otherwise empty answer receives an empty text block.
Prompt and completion usage become input and output token counts.
Length, tool, and content-filter finishes map to corresponding stop reasons.
Other finish reasons use the normal end_turn fallback.

Streaming separates upstream byte chunks into blank-line-delimited SSE frames.
An incomplete frame stays buffered until its delimiter or the end of input.
The existing decoder replaces invalid UTF-8 and normalizes CRLF per chunk.
OpenAI JSON object payloads are re-emitted with the dialled model identifier.
Event lines, comments, nonobject payloads, and DONE markers retain their roles.
A per-stream finished marker retains the first nonnull finish reason.
Later repeated finish reasons are cleared while their usage remains available.
This enforces one finish chunk for the supported single-choice stream.
It does not synthesize a finish reason when the upstream never supplies one.
Anthropic streaming instead emits named message and content-block events.
Message start is emitted once, before any translated content delta.
Text and tool blocks receive indexes allocated in their observed arrival order.
Tool argument fragments remain partial JSON strings in input_json_delta events.
A tool call closes an active text block before opening its tool block.
The latest stop reason is held until DONE or the upstream iterator ends.
This delay lets a trailing usage chunk update the final output token count.
Finish events close active blocks, emit message_delta, and emit message_stop.
The completion marker suppresses duplicate terminal Anthropic events.
Each event write is flushed, and connection closure delimits the HTTP body.

The catalog column compares registration and both model-list wire responses.
Its target is the exact set of fourteen free identifiers supplied by the brief.
Missing identifiers and additional identifiers both violate that exact set.
Catalog construction deduplicates identifiers and skips explicitly paid rows.
No usable model rows means an empty providers array, not an invented provider.
The public provider URL stays stable even when the trial gateway address changes.
Model-list creation timestamps are fixed constants for the same reason.
Replay selection depends on declared inputs and observed failures in arrival order.
The manifest lock serializes index allocation, not all concurrent request work.
Reproducibility therefore relies on the replay's corresponding request ordering.
Neither randomized selection nor elapsed-time cooldowns alter the routing order.
Network timeouts and polling sleeps govern waiting rather than model ranking.
The polling thread retries unavailable or malformed work responses.
A work response with done true flushes diagnostics and exits with status zero.
SIGTERM and SIGINT also flush diagnostics and terminate with status zero.
Unexpected top-level exceptions are logged and return a failure exit status.
These notes describe the existing protocol and do not add runtime decisions.
"""

"""Command-line entrypoint for the free model aggregator."""

import json
import math
import os
import signal
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from http.client import HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable, Iterator
from urllib.parse import urlsplit

# With -P, Python omits the usual automatic script-directory path entry.
# The explicit insertion preserves the entrypoint's established import setup.
# Without it, imports that rely on the entry directory can fail in that mode.
# Resolve the directory from this file so a different working directory is harmless.
# This path setup does not read a brief or initiate a gateway request.
# The trial's -P flag keeps the script directory off the import path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

"""Free model aggregator server."""

"""Brief documents, free models, and the registration catalog."""

PROMPT_FILE = os.environ.get('MAKEFUN_PROMPT_FILE', '')

MODELS_SCHEMA = 'free-model-aggregator-02/models/v1'
PROMPTS_SCHEMA = 'free-model-aggregator-02/prompts/v1'
# Every schema this contest's brief writes names this prefix; the brief's JSON is found by it.
SCHEMA_PREFIX = 'free-model-aggregator-02/'

# The provider's public endpoint; requests use the gateway instead.
OPENROUTER_PUBLIC_BASE = 'https://' + 'openrouter' + '.ai/api/v1'
OPENROUTER_KEY_ENV = 'OPENROUTER_API_KEY'

Json = dict[str, object]


def is_record(value: object) -> bool:
    """A JSON object (a Python dict), never an array or another value."""
    return isinstance(value, dict)


def is_number(value: object) -> bool:
    """A JSON number: an int or a float, never a bool (Python's bool is an int)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def string_of(value: object) -> str:
    """Normalize a value used as text.
    Return the string unchanged, or an empty string for other types.
    """
    return value if isinstance(value, str) else ''


def number_or_null(value: object) -> int | float | None:
    """Validate a finite JSON numeric value.
    Return the number, or None for invalid or nonfinite values.
    """
    return value if is_number(value) and math.isfinite(value) else None


def number_or_zero(value: object) -> int | float:
    """Normalize a numeric field such as a token count.
    Return the number unchanged, or zero for other types.
    """
    return value if is_number(value) else 0


def bool_or_null(value: object) -> bool | None:
    """Validate a boolean capability field without coercion.
    Return the boolean unchanged, or None for other types.
    """
    return value if isinstance(value, bool) else None


def object_end(text: str, start: int) -> int:
    """The index of the `}` closing the object that opens at `start`, or -1; string contents skipped."""
    depth = 0
    in_string = False
    i = start
    while i < len(text):
        ch = text[i]
        if in_string:
            if ch == '\\':
                i += 2
                continue
            if ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def documents_in(text: str) -> list[Json]:
    """Every JSON object in `text` that carries a string `schema`, in the order they appear."""
    out: list[Json] = []
    start = text.find('{')
    while start != -1:
        end = object_end(text, start)
        if end != -1:
            try:
                value = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                value = None
            if is_record(value) and isinstance(value.get('schema'), str):
                out.append(value)
        start = text.find('{', start + 1)
    return out


def array_document(docs: list[Json], keys: tuple[str, ...]) -> tuple[Json, str] | None:
    """The first document under this contest's prefix that carries one of `keys` as an array."""
    for doc in docs:
        schema = string_of(doc.get('schema'))
        if SCHEMA_PREFIX not in schema:
            continue
        for key in keys:
            if isinstance(doc.get(key), list):
                return doc, key
    return None


def model_of(value: object) -> Json | None:
    """One model row, as this baseline reads it. A free row is routed as the gateway spells its id."""
    if not is_record(value):
        return None
    raw = string_of(value.get('id'))
    if raw == '':
        raw = string_of(value.get('model'))
    if raw == '':
        return None
    named = string_of(value.get('provider'))
    if named == '':
        named = string_of(value.get('wire'))
    model_id = f'openrouter/{raw}' if named == 'openrouter' and not raw.lower().startswith('openrouter/') else raw
    display_name = string_of(value.get('display_name'))
    rating = number_or_null(value.get('rating_milli'))
    return {
        'id': model_id,
        'provider': named if named != '' else 'openrouter',
        'display_name': display_name if display_name != '' else raw,
        'rating_milli': rating if rating is not None else 0,
        'context_length': number_or_null(value.get('context_length')),
        'tools': bool_or_null(value.get('tools')),
        'vision': bool_or_null(value.get('vision')),
        'json': bool_or_null(value.get('json')),
        'free': value.get('free') is not False,
    }


def prompt_of(value: object) -> Json | None:
    """One prompt row, as this baseline reads it."""
    if not is_record(value):
        return None
    prompt_id = string_of(value.get('id'))
    if prompt_id == '':
        return None
    requires = value.get('requires') if is_record(value.get('requires')) else {}
    return {
        'id': prompt_id,
        'api': 'anthropic' if value.get('api') == 'anthropic' else 'openai',
        'stream': value.get('stream') is True,
        'weight': 2 if value.get('weight') == 2 else 1,
        'requires': {
            **({'tools': True} if requires.get('tools') is True else {}),
            **({'vision': True} if requires.get('vision') is True else {}),
            **({'json': True} if requires.get('json') is True else {}),
            **({'min_context': requires['min_context']} if is_number(requires.get('min_context')) and math.isfinite(requires['min_context']) else {}),
        },
    }


def read_brief(path: str) -> Json:
    """The brief at `path`, its two documents found under this contest's schema prefix.

    Returns {"models": [...], "prompts": [...], "error": <string or None>}. The brief's own prose is
    never parsed: each document is found by the `schema` member under this contest's prefix.
    """
    if path == '':
        return {'models': [], 'prompts': [], 'error': 'MAKEFUN_PROMPT_FILE is not set'}
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError as err:
        return {'models': [], 'prompts': [], 'error': f'cannot read {path}: {err}'}
    docs = documents_in(text)
    models_doc = array_document(docs, ('models', 'ratings'))
    if models_doc is None:
        return {'models': [], 'prompts': [], 'error': f'the brief at {path} holds no {MODELS_SCHEMA} document'}
    models = [m for m in (model_of(v) for v in models_doc[0][models_doc[1]]) if m is not None]
    prompts_doc = array_document(docs, ('prompts', 'manifest'))
    if prompts_doc is None:
        return {'models': models, 'prompts': [], 'error': f'the brief at {path} holds no {PROMPTS_SCHEMA} document'}
    prompts = [p for p in (prompt_of(v) for v in prompts_doc[0][prompts_doc[1]]) if p is not None]
    return {'models': models, 'prompts': prompts, 'error': None}


def free_models(brief: Json) -> list[Json]:
    """The models this entry may serve: the brief's free ones, in the brief's order."""
    return [m for m in brief['models'] if m['free']]


def build_catalog(brief: Json) -> Json:
    """The catalog the brief's free model list makes: one provider, the models it serves.

    The base_url is the provider's public endpoint, never the gateway's trial-local address: the gateway's
    origin differs between a live pass and its replay, and the catalog is part of the state export a
    replay must reproduce byte for byte.

    TODO(catalog): keep it exact — register this set and answer GET /v1/models with it on both auth
    headers; the column checks the register body and both answers against exactly the fourteen ids, so
    listing fewer loses it as surely as listing more. This baseline lists every free model the brief
    names, which is exactly the fourteen ids the column scores, so keep it that way.
    """
    seen: set[str] = set()
    models: list[Json] = []
    for model in brief['models']:
        model_id = model['id']
        if not model['free'] or model_id in seen:
            continue
        seen.add(model_id)
        models.append({'id': model_id, 'free': True, 'context_length': model['context_length']})
    # A missing brief provides no authority to advertise model identifiers.
    # Returning an empty provider list avoids inventing a catalog from defaults.
    # It also keeps --list usable when the brief environment variable is absent.
    # An empty provider with a fabricated model list would fail the exact-set check.
    if not models:
        return {'providers': []}
    return {
        'providers': [
            {
                'id': 'openrouter',
                'base_url': OPENROUTER_PUBLIC_BASE,
                'key_env': OPENROUTER_KEY_ENV,
                'wire': 'openai',
                'models': models,
            }
        ]
    }


BRIEF = read_brief(PROMPT_FILE)
CATALOG = build_catalog(BRIEF)


def served_models() -> list[Json]:
    """The free models the brief lists, as GET /v1/models names them."""
    return [{'id': m['id'], 'provider': m['provider'], 'display_name': m['display_name']} for m in free_models(BRIEF)]


"""Capability requirements, rating order, cooldowns, and gateway failover."""

GATEWAY_URL = os.environ.get('OPENAI_BASE_URL', '').rstrip('/')
# The trial's dummy key for the entry's gateway lane; the gateway swaps it for the entrant's own key.
GATEWAY_KEY = os.environ.get('OPENAI_API_KEY', '')

# Which prompt row the request now arriving is: the brief's prompts are in the client's arrival order.
manifest_index = 0
# Models that refused in this run, so later prompts skip them (derived from answers only).
COOLDOWN: dict[str, int] = {}
manifest_lock = threading.Lock()


def capabilities_of(model: Json) -> Json:
    """Extract the capability fields used to match prompt requirements.
    Return a dictionary containing context length, tools, vision, and JSON support.
    """
    return {
        'context_length': model['context_length'],
        'tools': model['tools'],
        'vision': model['vision'],
        'json': model['json'],
    }


def meets_requirements(requires: Json, caps: Json) -> bool:
    """Whether a model's capabilities meet one prompt's requirements. An unknown capability never meets one."""
    # Required booleans demand explicit support; unknown metadata is insufficient.
    # Optional capabilities do not disqualify an otherwise eligible model.
    if requires.get('tools') is True and caps['tools'] is not True:
        return False
    if requires.get('vision') is True and caps['vision'] is not True:
        return False
    if requires.get('json') is True and caps['json'] is not True:
        return False
    # Context requirements need a known capacity at least as large as requested.
    # Treating an unknown capacity as unlimited would admit an unverified route.
    min_context = requires.get('min_context')
    if min_context is not None and (caps['context_length'] is None or caps['context_length'] < min_context):
        return False
    return True


def chat_completions_url() -> str:
    """The gateway's Chat Completions route.

    OPENAI_BASE_URL is the OpenAI-compatible root (`…/v1` on the trial network); a base that does not
    already end in `/v1` gets one, so both spellings reach the route.
    """
    # An absent gateway stays absent so startup and dial can report configuration errors.
    # The route suffix is appended only after the base has been validated.
    if GATEWAY_URL == '':
        return ''
    return f'{GATEWAY_URL}/chat/completions' if GATEWAY_URL.endswith('/v1') else f'{GATEWAY_URL}/v1/chat/completions'


def routes_for(requires: Json) -> list[Json]:
    """Every free model that meets the request, the best rated first.

    Quality is 60% of the score and reads the model that really answered, so the first candidate is the
    highest rating_milli that satisfies the prompt. Models that already refused this run go last: a 429
    on a free model is per model, and spending the next prompt on it again loses that prompt too.
    """
    # Filter capabilities before ranking so a high rating cannot bypass requirements.
    # Only the normalized free rows participate in this candidate list.
    candidates = [m for m in free_models(BRIEF) if meets_requirements(requires, capabilities_of(m))]
    # The tuple establishes a total, reproducible preference order.
    # Missing numeric metadata contributes zero; identifiers settle remaining ties.
    candidates.sort(key=lambda m: (-(m.get('rating_milli') or 0), -(m.get('context_length') or 0), m['id']))
    # Observed refusals suppress candidates only while a fresh alternative exists.
    # Reusing the full order after exhaustion avoids an irreversible empty route set.
    fresh = [m for m in candidates if not COOLDOWN.get(m['id'])]
    return fresh if fresh else candidates


def requirements_for(body: Json) -> Json:
    """The requirements of the prompt now arriving: the brief's row by arrival order, else the body's own."""
    global manifest_index
    # Reserve one arrival-order index atomically before inspecting its manifest row.
    # The lock protects index advancement rather than the lifetime of the request.
    with manifest_lock:
        index = manifest_index
        manifest_index += 1
    # Declared requirements are authoritative for requests covered by the manifest.
    # Body inference is reached only after those declared rows run out.
    prompts = BRIEF['prompts']
    if index < len(prompts):
        return prompts[index]['requires']
    # TODO(workload): the brief's prompt list is the declared source of each request's requirements; this
    # fallback is a guess for a brief that holds none.
    return requirements_from_body(body)


def requirements_from_body(body: Json) -> Json:
    """Requirements read off a request body, when the brief has no row for it."""
    messages = body.get('messages') if isinstance(body.get('messages'), list) else []
    # Inspect structured content for image blocks without assuming every message is valid.
    # Both client image spellings imply the same routing capability.
    vision = False
    for message in messages:
        if not is_record(message) or not isinstance(message.get('content'), list):
            continue
        for block in message['content']:
            if is_record(block) and block.get('type') in ('image', 'image_url'):
                vision = True
    # A structured response format implies JSON support only for recognized JSON modes.
    # Tools are required only when the supplied tool list is nonempty.
    response_format = body.get('response_format')
    response_type = response_format.get('type') if is_record(response_format) else None
    return {'tools': isinstance(body.get('tools'), list) and len(body['tools']) > 0, 'vision': vision, 'json': response_type in ('json_object', 'json_schema')}


class Upstream:
    """One gateway answer: its status and a body that may be read whole or as byte chunks."""

    def __init__(self: "Upstream", status: int, response: HTTPResponse | urllib.error.HTTPError) -> None:
        """Store a gateway response and its HTTP status for later consumption.
        Return None after initializing the response wrapper.
        """
        self.status = status
        self._response = response

    @property
    def ok(self: "Upstream") -> bool:
        """Check whether the wrapped HTTP response succeeded.
        Return True for a status in the 200 through 299 range.
        """
        return 200 <= self.status < 300

    def chunks(self: "Upstream") -> Iterator[bytes]:
        """The body's bytes as they arrive, never waiting to fill a buffer (SSE must stream)."""
        # Prefer a single buffered read so incremental SSE bytes can reach the client promptly.
        # The ordinary read method remains the fallback for response wrappers without read1.
        reader = getattr(self._response, 'read1', None) or self._response.read
        while True:
            chunk = reader(65536)
            if not chunk:
                break
            yield chunk

    def text(self: "Upstream") -> str:
        """Read the remaining response body and close its connection.
        Return UTF-8 text with replacement characters for invalid bytes.
        """
        try:
            return self._response.read().decode('utf-8', 'replace')
        finally:
            self.close()

    def close(self: "Upstream") -> None:
        """Close the wrapped response, ignoring cleanup errors.
        Return None whether closing succeeds or raises an exception.
        """
        try:
            self._response.close()
        except Exception:  # a connection already closed is nothing to report
            pass


def dial(model: str, body: Json) -> Upstream:
    """POST one request to the gateway's Chat Completions route, naming the model this entry dialled."""
    url = chat_completions_url()
    if url == '':
        raise RuntimeError('OPENAI_BASE_URL is not set')
    # Copy the request while replacing the model with the candidate actually being tried.
    # This ensures upstream routing and downstream model attribution use the same id.
    data = json.dumps({**body, 'model': model}, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    headers = {'content-type': 'application/json', 'authorization': f'Bearer {GATEWAY_KEY}'}
    # Request SSE explicitly when the client asks to stream.
    # Nonstreaming requests keep the normal JSON response negotiation.
    if body.get('stream') is True:
        headers['accept'] = 'text/event-stream'
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        response = urllib.request.urlopen(request)
    except urllib.error.HTTPError as err:
        # A refusal is an answer with a status and a body, exactly as Node's fetch reports it.
        return Upstream(err.code, err)
    return Upstream(response.status, response)


# These refusals can describe availability or access constraints on one model.
# Stopping on the first such response would discard an answer another route can supply.
# 403/404/451 cover access, missing-model, and regional refusal cases.
# 408/409/425/429 cover timeout, conflict, readiness, and rate-limit responses.
# 500/502/503/504/529 cover server, gateway, overload, and timeout failures.
# The loop also handles other server errors and other non-400 refusals.
# A 400 follows its separate stop path to avoid repeating an invalid request body.
# Statuses worth another model: rate limits, provider faults and gateway timeouts.
FAILOVER_STATUSES = (403, 404, 408, 409, 425, 429, 451, 500, 502, 503, 504, 529)


def dial_with_failover(candidates: list[Json], make_body: Callable[[str], Json]) -> tuple[Upstream | None, str | None, list[Json], tuple[int, str] | None]:
    """Dial the best candidate, failing over down the rating order on a refusal.

    Returns (upstream, model actually used, the refusal trail, the last refusal). The trail rides back on
    x-makefun-upstream-failures: it is the only way the failover column can see a refusal we recovered from.
    """
    failures: list[Json] = []
    last: tuple[int, str] | None = None
    for model in candidates:
        # Bound the refusal trail and the number of unsuccessful attempts per request.
        # The bound is independent of timing and of the total catalog size.
        if len(failures) >= 8:
            break
        # Build a fresh wire body for each candidate so model replacement follows failover.
        # A connection exception is evidence against this attempted route.
        try:
            upstream = dial(model['id'], make_body(model['id']))
        except Exception as err:  # an unreachable gateway is this candidate's refusal
            # Record unreachable attempts in the same trail shape as HTTP refusals.
            # Cooldown is updated only after the attempted dial has actually failed.
            failures.append({'status': 502, 'code': 'unreachable', 'model': model['id']})
            last = (502, str(err))
            COOLDOWN[model['id']] = 1
            continue
        # Return immediately on the first successful status and preserve prior failure evidence.
        # The caller needs this selected id even if the provider reports another spelling.
        if upstream.ok:
            return upstream, model['id'], failures, None
        # Consume and close a refused response before moving to another candidate.
        # Its body may carry a provider-specific code useful to the failure trail.
        text = upstream.text()
        code = ''
        try:
            parsed = json.loads(text)
            if is_record(parsed) and is_record(parsed.get('error')):
                code = str(parsed['error'].get('code') or parsed['error'].get('type') or '')
        except (json.JSONDecodeError, ValueError):
            code = ''
        # Keep status, model, and a bounded code rather than copying full error bodies into headers.
        # The complete most recent body remains available for the final client error.
        failures.append({'status': upstream.status, 'code': code[:48] or 'refused', 'model': model['id']})
        last = (upstream.status, text)
        # All non-400 refusal paths below remember the observed failure for later requests.
        # The explicit set documents common recoverable cases without restricting that behavior.
        if upstream.status in FAILOVER_STATUSES or upstream.status >= 500:
            # A 403 from a free model is a region gate on that model, not on our request: skip it for
            # the rest of the run and try the next best model.
            COOLDOWN[model['id']] = 1
        elif upstream.status != 400:
            COOLDOWN[model['id']] = 1
        else:
            break  # a 400 aimed at our own body would repeat on every model
    # No usable response remains after exhaustion, the attempt bound, or a body error.
    # Returning the last refusal lets each client wire construct its own error envelope.
    return None, None, failures, last


def trail_header(failures: list[Json]) -> str:
    """The x-makefun-upstream-failures header value."""
    return json.dumps(failures, separators=(',', ':'))


"""OpenAI and Anthropic translations and server-sent event frames."""

# A fixed epoch second for every listed model's `created`: nothing in the reply reads the clock.
FIXED_OPENAI_CREATED = 1767225600
# A fixed instant for every listed model's `created_at`: nothing in the reply reads the clock.
FIXED_ANTHROPIC_CREATED_AT = '2026-01-01T00:00:00Z'


def openai_model_list(models: list[Json]) -> Json:
    """GET /v1/models on an OpenAI-shaped request: the models this entry serves, in OpenAI's list shape."""
    return {
        'object': 'list',
        'data': [{'id': m['id'], 'object': 'model', 'created': FIXED_OPENAI_CREATED, 'owned_by': m['provider']} for m in models],
    }


def anthropic_model_list(models: list[Json]) -> Json:
    """GET /v1/models on an Anthropic-shaped request (an x-api-key header): Anthropic's model list shape."""
    return {
        'data': [{'type': 'model', 'id': m['id'], 'display_name': m['display_name'], 'created_at': FIXED_ANTHROPIC_CREATED_AT} for m in models],
        'has_more': False,
    }


def anthropic_error(error_type: str, message: str) -> Json:
    """An error in Anthropic's shape (the Messages API's error envelope)."""
    return {'type': 'error', 'error': {'type': error_type, 'message': message}}


def openai_error(message: str, error_type: str, code: str | None) -> Json:
    """An error in OpenAI's shape (Chat Completions)."""
    return {'error': {'message': message, 'type': error_type, 'code': code}}


def text_of(content: object) -> str:
    """The text of a string or of a block array's text blocks; '' when there is none."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    # Collect only recognized text blocks and keep their original order.
    # The newline separator preserves a boundary between adjacent text blocks.
    parts = [b['text'] for b in content if is_record(b) and b.get('type') == 'text' and isinstance(b.get('text'), str)]
    return '\n'.join(parts)


def part_of(block: Json) -> Json | None:
    """One Anthropic content block as an OpenAI content part: text or an inline image; None for the rest."""
    if block.get('type') == 'text' and isinstance(block.get('text'), str):
        return {'type': 'text', 'text': block['text']}
    # Images accept the supported inline and URL source representations.
    # Other block kinds return None so callers can omit unsupported content.
    source = block.get('source')
    if block.get('type') == 'image' and is_record(source):
        if source.get('type') == 'base64' and isinstance(source.get('data'), str):
            # Inline bytes stay base64 text inside a data URL; there is no image fetch here.
            # The existing PNG fallback covers a missing or nonstring media type.
            media = source['media_type'] if isinstance(source.get('media_type'), str) else 'image/png'
            return {'type': 'image_url', 'image_url': {'url': f'data:{media};base64,{source["data"]}'}}
        if source.get('type') == 'url' and isinstance(source.get('url'), str):
            return {'type': 'image_url', 'image_url': {'url': source['url']}}
    return None


def tool_result_text(content: object) -> str:
    """A tool_result block's content as text: its text blocks, or its JSON when it holds something else."""
    if isinstance(content, str):
        return content
    # Prefer readable tool-result text when text blocks exist.
    # Otherwise preserve the structured result as compact JSON text.
    if isinstance(content, list):
        text = text_of(content)
        return text if text != '' else json.dumps(content, separators=(',', ':'), ensure_ascii=False)
    if content is None:
        return ''
    return json.dumps(content, separators=(',', ':'), ensure_ascii=False)


def arguments_of(value: object) -> Json:
    """A tool call's arguments as an object: the gateway's JSON string, or {} when it is not one."""
    if is_record(value):
        return value
    # Tool arguments may already be objects or may arrive as encoded JSON.
    # Only decoded objects are valid tool inputs; other shapes use the empty fallback.
    if isinstance(value, str) and value.strip() != '':
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return {}
        if is_record(parsed):
            return parsed
    return {}


def stop_reason_of(finish: object) -> str:
    """OpenAI's finish_reason as Anthropic's stop_reason."""
    # Translate the gateway finish vocabulary into the Messages stop vocabulary.
    # The default also handles absent or unfamiliar provider finish values.
    if finish == 'length':
        return 'max_tokens'
    if finish == 'tool_calls' or finish == 'function_call':
        return 'tool_use'
    if finish == 'content_filter':
        return 'refusal'
    return 'end_turn'


def anthropic_to_openai(body: Json, model: str) -> Json:
    """An Anthropic Messages request as an OpenAI Chat Completions request that names `model`.

    The system prompt becomes a system message, image blocks become image_url data URLs, tool_use blocks
    become tool_calls, tool_result blocks become tool messages, tools and tool_choice are rewritten, and
    the output cap, temperature, top_p, stop sequences and streaming pass through.
    """
    messages: list[Json] = []
    # Flatten the separate system field into the first gateway message when it has text.
    # An empty system field does not create an otherwise empty message.
    system = text_of(body.get('system'))
    if system != '':
        messages.append({'role': 'system', 'content': system})
    # Validate message containers before translating their content.
    # The existing role rule treats every nonassistant message as user content.
    source = body.get('messages') if isinstance(body.get('messages'), list) else []
    for raw in source:
        if not is_record(raw):
            continue
        assistant = raw.get('role') == 'assistant'
        content = raw.get('content')
        if isinstance(content, str):
            messages.append({'role': 'assistant' if assistant else 'user', 'content': content})
            continue
        if not isinstance(content, list):
            continue
        # Assistant tool-use blocks belong in tool_calls beside any assistant text.
        # Their input objects become JSON strings because Chat Completions expects strings.
        if assistant:
            tool_calls: list[Json] = []
            for block in content:
                if not is_record(block) or block.get('type') != 'tool_use':
                    continue
                tool_calls.append(
                    {
                        'id': block['id'] if isinstance(block.get('id'), str) else '',
                        'type': 'function',
                        'function': {
                            'name': block['name'] if isinstance(block.get('name'), str) else '',
                            'arguments': json.dumps(
                                block['input'] if is_record(block.get('input')) else {},
                                separators=(',', ':'),
                                ensure_ascii=False,
                            ),
                        },
                    }
                )
            text = text_of(content)
            # A tool-only assistant turn uses null content while retaining its function calls.
            # Text and calls are assembled into one gateway assistant message.
            message: Json = {'role': 'assistant', 'content': text if text != '' else None}
            if tool_calls:
                message['tool_calls'] = tool_calls
            messages.append(message)
            continue
        # Tool results become separate tool messages linked by the original call identifier.
        # Other supported blocks accumulate into the user content-parts message.
        parts: list[Json] = []
        for block in content:
            if not is_record(block):
                continue
            if block.get('type') == 'tool_result':
                messages.append(
                    {
                        'role': 'tool',
                        'tool_call_id': block['tool_use_id'] if isinstance(block.get('tool_use_id'), str) else '',
                        'content': tool_result_text(block.get('content')),
                    }
                )
                continue
            part = part_of(block)
            if part is not None:
                parts.append(part)
        if parts:
            messages.append({'role': 'user', 'content': parts})

    # Forward only the supported generation controls using the existing value guards.
    # Stop sequences change field name but retain their declared list contents.
    out: Json = {'model': model, 'messages': messages}
    if is_number(body.get('max_tokens')):
        out['max_tokens'] = body['max_tokens']
    if is_number(body.get('temperature')):
        out['temperature'] = body['temperature']
    if is_number(body.get('top_p')):
        out['top_p'] = body['top_p']
    if isinstance(body.get('stop_sequences'), list) and len(body['stop_sequences']) > 0:
        out['stop'] = body['stop_sequences']
    # Convert named tool definitions into gateway function descriptors.
    # A missing object schema gets the existing empty-object parameter schema.
    if isinstance(body.get('tools'), list):
        tools: list[Json] = []
        for raw in body['tools']:
            if not is_record(raw) or not isinstance(raw.get('name'), str):
                continue
            tools.append(
                {
                    'type': 'function',
                    'function': {
                        'name': raw['name'],
                        'description': raw['description'] if isinstance(raw.get('description'), str) else '',
                        'parameters': raw['input_schema'] if is_record(raw.get('input_schema')) else {'type': 'object', 'properties': {}},
                    },
                }
            )
        if tools:
            out['tools'] = tools
    # Translate the three supported tool-selection modes without inventing a preference.
    # An unrecognized choice is omitted from the gateway request.
    choice = body.get('tool_choice')
    if is_record(choice):
        if choice.get('type') == 'auto':
            out['tool_choice'] = 'auto'
        elif choice.get('type') == 'any':
            out['tool_choice'] = 'required'
        elif choice.get('type') == 'tool' and isinstance(choice.get('name'), str):
            out['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
    # Request trailing usage so the translated message can report final token counts.
    # The downstream event adapter delays its final message delta to consume that usage.
    if body.get('stream') is True:
        out['stream'] = True
        out['stream_options'] = {'include_usage': True}
    return out


def openai_to_anthropic(body: Json, model: str) -> Json:
    """A Chat Completions answer as an Anthropic Messages object. `model` is the id the entry dialled."""
    # The Messages response is built from the first gateway choice.
    # Missing or malformed choice and message containers use existing empty fallbacks.
    choices = body.get('choices') if isinstance(body.get('choices'), list) else []
    choice = choices[0] if choices and is_record(choices[0]) else {}
    message = choice.get('message') if is_record(choice.get('message')) else {}
    content: list[Json] = []
    text = message['content'] if isinstance(message.get('content'), str) else text_of(message.get('content'))
    if text != '':
        content.append({'type': 'text', 'text': text})
    # Translate each structured function call into a Messages tool-use content block.
    # Argument decoding rejects nonobject JSON rather than exposing an invalid input shape.
    calls = message.get('tool_calls') if isinstance(message.get('tool_calls'), list) else []
    for call in calls:
        if not is_record(call) or not is_record(call.get('function')):
            continue
        fn = call['function']
        content.append(
            {
                'type': 'tool_use',
                'id': call['id'] if isinstance(call.get('id'), str) else 'toolu_unknown',
                'name': fn['name'] if isinstance(fn.get('name'), str) else '',
                'input': arguments_of(fn.get('arguments')),
            }
        )
    # An empty text block gives even an empty upstream answer a content representation.
    # Usage fields retain numeric values and default to zero when unavailable.
    if not content:
        content.append({'type': 'text', 'text': ''})
    usage = body.get('usage') if is_record(body.get('usage')) else {}
    return {
        'id': f'msg_{body["id"] if isinstance(body.get("id"), str) else "aggregate"}',
        'type': 'message',
        'role': 'assistant',
        'model': model,
        'content': content,
        'stop_reason': stop_reason_of(choice.get('finish_reason')),
        'stop_sequence': None,
        'usage': {
            'input_tokens': number_or_zero(usage.get('prompt_tokens')),
            'output_tokens': number_or_zero(usage.get('completion_tokens')),
        },
    }


def openai_json_with_model(body: Json, model: str) -> Json:
    """A Chat Completions answer with `model` set to the id the entry dialled: the one truthful claim."""
    return {**body, 'model': model}


def sse_event(event: str | None, data: object) -> str:
    """One server-sent event: `event: <name>` when named, then the JSON data line, then the blank line."""
    # Named events use an event line; OpenAI-style events can omit the name.
    # Compact JSON occupies one data line followed by the SSE blank-line delimiter.
    prefix = '' if event is None else f'event: {event}\n'
    return f'{prefix}data: {json.dumps(data, separators=(",", ":"), ensure_ascii=False)}\n\n'


def frame_with_model(frame: str, model: str) -> str:
    """One SSE frame with every data payload's `model` set to `model`, and its blank line kept.

    Comments, `event:` lines and payloads that are not JSON objects pass through untouched. Chat
    Completions names the model on its chunks; the client sees the id the entry dialled, never the
    provider's spelling.
    """
    # Rewrite data objects without stripping comments or named-event metadata.
    # DONE and non-JSON payloads retain their original line text.
    lines: list[str] = []
    for line in frame.split('\n'):
        trimmed = line.strip()
        if not trimmed.startswith('data:'):
            lines.append(line)
            continue
        payload = trimmed[5:].strip()
        if payload == '' or payload == '[DONE]':
            lines.append(line)
            continue
        try:
            chunk = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            lines.append(line)
            continue
        # Only JSON objects have a model field to overwrite.
        # Arrays and scalar JSON payloads pass through without being reshaped.
        if is_record(chunk):
            lines.append(f'data: {json.dumps({**chunk, "model": model}, separators=(",", ":"), ensure_ascii=False)}')
        else:
            lines.append(line)
    return '\n'.join(lines) + '\n\n'


def iter_frames(chunks: Iterable[bytes]) -> Iterator[str]:
    """SSE frames from an upstream body's byte chunks, split on the blank line that ends each one.

    A frame cut across reads is held until its end arrives, and a body that ends without one is yielded
    as it is. CRLF is normalized to LF as the SSE grammar reads it.
    """
    # Network read boundaries need not coincide with SSE frame boundaries.
    # Retain the unfinished suffix between reads and yield complete frames in order.
    buffer = ''
    for chunk in chunks:
        buffer += chunk.decode('utf-8', 'replace').replace('\r\n', '\n')
        at = buffer.find('\n\n')
        while at != -1:
            frame = buffer[:at]
            buffer = buffer[at + 2 :]
            yield frame
            at = buffer.find('\n\n')
    # Preserve a final nonblank frame even when the provider omits its final delimiter.
    # Whitespace alone does not create a spurious final event.
    if buffer.strip() != '':
        yield buffer


# A client expects one finish chunk for this single-choice stream.
# Some providers repeat finish_reason on a later usage-bearing chunk.
# Forwarding both reasons can make clients terminate twice or reject the stream.
# Clear only the repeated reason so final accounting data is still delivered.
def openai_stream_with_model(chunks: Iterable[bytes], model: str) -> Iterator[str]:
    """A Chat Completions SSE stream as it came, every data payload's `model` the dialled id.

    A provider that repeats its stop reason on the usage chunk would hand the client two finish chunks,
    and the wire allows one: the repeat is cleared and the usage it carried is kept.
    """
    # This marker belongs to one response and remembers whether a finish was already sent.
    # It prevents a provider's repeated stop reason from becoming a second client finish.
    # Keeping it outside the frame loop preserves that memory across network reads.
    # It is not a model cooldown flag and does not affect the next request.
    finished = False
    for frame in iter_frames(chunks):
        frame = frame_with_model(frame, model)
        lines: list[str] = []
        for line in frame.split('\n'):
            trimmed = line.strip()
            if not trimmed.startswith('data:'):
                lines.append(line)
                continue
            payload = trimmed[5:].strip()
            if payload == '' or payload == '[DONE]':
                lines.append(line)
                continue
            try:
                chunk = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                lines.append(line)
                continue
            # Only a nonnull finish reason on the first choice changes the completion marker.
            # Later usage chunks keep all other fields even when their repeated reason is cleared.
            choices = chunk.get('choices') if is_record(chunk) else None
            if isinstance(choices, list) and choices and is_record(choices[0]) and choices[0].get('finish_reason') is not None:
                if finished:
                    choices[0]['finish_reason'] = None
                else:
                    finished = True
                line = 'data: ' + json.dumps(chunk, separators=(',', ':'), ensure_ascii=False)
            lines.append(line)
        yield '\n'.join(lines)


def anthropic_events_from_openai(chunks: Iterable[bytes], model: str) -> Iterator[str]:
    """A Chat Completions SSE stream as the Anthropic event sequence.

    message_start on the first chunk, content_block_start/delta/stop per text block or tool call, then
    message_delta with the stop reason and usage, then message_stop. `model` is the id the entry dialled,
    named back to the client.
    """
    # Track message lifecycle separately from active text and tool blocks.
    # Start and finish guards prevent duplicate terminal events across frames.
    started = False
    finished = False
    # The stop reason a finish_reason named, held until the stream's usage chunk has arrived.
    pending_stop: str | None = None
    text_index = -1
    next_index = 0
    # Map gateway tool indexes to the unified downstream content-block index space.
    # Token totals remain numeric because upstream usage may use either numeric type.
    tool_indexes: dict[object, int] = {}
    input_tokens: int | float = 0
    output_tokens: int | float = 0

    def start_events() -> list[str]:
        """Start the translated message once and update its started flag.
        Return the message_start event list, or an empty list if already started.
        """
        nonlocal started
        # Message start must precede translated content and appear only once.
        # The first available usage values initialize its input-token accounting.
        if started:
            return []
        started = True
        return [
            sse_event(
                'message_start',
                {
                    'type': 'message_start',
                    'message': {
                        'id': 'msg_stream',
                        'type': 'message',
                        'role': 'assistant',
                        'model': model,
                        'content': [],
                        'stop_reason': None,
                        'stop_sequence': None,
                        'usage': {'input_tokens': input_tokens, 'output_tokens': 0},
                    },
                },
            )
        ]

    def close_events() -> list[str]:
        """Close the active text and tool blocks and clear their tracking state.
        Return the corresponding content_block_stop event strings.
        """
        nonlocal text_index
        out: list[str] = []
        # Close only active blocks and clear their bookkeeping after emitting stop events.
        # Sorting tool indexes gives their closure events a stable downstream order.
        if text_index != -1:
            out.append(sse_event('content_block_stop', {'type': 'content_block_stop', 'index': text_index}))
            text_index = -1
        for index in sorted(tool_indexes.values()):
            out.append(sse_event('content_block_stop', {'type': 'content_block_stop', 'index': index}))
        tool_indexes.clear()
        return out

    def finish_events(stop_reason: str) -> list[str]:
        """Finish the message once with block closures, usage, and a stop reason.
        Return the final event strings, or an empty list if already finished.
        """
        nonlocal finished
        # DONE and end-of-input may both reach completion handling.
        # The marker prevents a second message_delta or message_stop sequence.
        if finished:
            return []
        finished = True
        return [
            *close_events(),
            sse_event(
                'message_delta',
                {'type': 'message_delta', 'delta': {'stop_reason': stop_reason, 'stop_sequence': None}, 'usage': {'output_tokens': output_tokens}},
            ),
            sse_event('message_stop', {'type': 'message_stop'}),
        ]

    def events_of_frame(frame: str) -> list[str]:
        """Translate one gateway SSE frame while updating block and usage state.
        Return the Anthropic event strings produced by its data payloads.
        """
        nonlocal input_tokens, output_tokens, text_index, next_index, pending_stop
        out: list[str] = []
        for line in frame.split('\n'):
            trimmed = line.strip()
            if not trimmed.startswith('data:'):
                continue
            payload = trimmed[5:].strip()
            # The terminal marker completes the message after any trailing usage update.
            # An otherwise empty stream still receives its start and finish event envelope.
            if payload == '[DONE]':
                out.extend(start_events())
                out.extend(finish_events(pending_stop if pending_stop is not None else 'end_turn'))
                continue
            try:
                chunk = json.loads(payload)
            except (json.JSONDecodeError, ValueError):
                continue
            if not is_record(chunk):
                continue
            # Usage-only chunks can update totals even when they have no choice delta.
            # Read usage before skipping chunks without a usable first choice.
            if is_record(chunk.get('usage')):
                input_tokens = number_or_zero(chunk['usage'].get('prompt_tokens'))
                output_tokens = number_or_zero(chunk['usage'].get('completion_tokens'))
            choices = chunk.get('choices') if isinstance(chunk.get('choices'), list) else []
            choice = choices[0] if choices and is_record(choices[0]) else None
            if choice is None:
                continue
            delta = choice.get('delta') if is_record(choice.get('delta')) else {}
            out.extend(start_events())
            # Allocate a text block lazily when the first nonempty text fragment arrives.
            # Subsequent fragments reuse that block until a tool transition closes it.
            if isinstance(delta.get('content'), str) and delta['content'] != '':
                if text_index == -1:
                    text_index = next_index
                    next_index += 1
                    out.append(
                        sse_event('content_block_start', {'type': 'content_block_start', 'index': text_index, 'content_block': {'type': 'text', 'text': ''}})
                    )
                out.append(
                    sse_event(
                        'content_block_delta',
                        {'type': 'content_block_delta', 'index': text_index, 'delta': {'type': 'text_delta', 'text': delta['content']}},
                    )
                )
            # Tool-call fragments are grouped by the upstream index across successive chunks.
            # A newly seen call gets its own downstream block and a stable local index.
            calls = delta.get('tool_calls') if isinstance(delta.get('tool_calls'), list) else []
            for call in calls:
                if not is_record(call):
                    continue
                upstream_index = call['index'] if is_number(call.get('index')) else len(tool_indexes)
                index = tool_indexes.get(upstream_index)
                # Close active text before opening a new tool block.
                # Missing provider call identifiers use the existing index-derived fallback.
                if index is None:
                    if text_index != -1:
                        out.append(sse_event('content_block_stop', {'type': 'content_block_stop', 'index': text_index}))
                        text_index = -1
                    index = next_index
                    next_index += 1
                    tool_indexes[upstream_index] = index
                    fn = call.get('function') if is_record(call.get('function')) else {}
                    out.append(
                        sse_event(
                            'content_block_start',
                            {
                                'type': 'content_block_start',
                                'index': index,
                                'content_block': {
                                    'type': 'tool_use',
                                    'id': call['id'] if isinstance(call.get('id'), str) and call['id'] != '' else f'toolu_{index}',
                                    'name': fn['name'] if isinstance(fn.get('name'), str) else '',
                                    'input': {},
                                },
                            },
                        )
                    )
                fn = call.get('function') if is_record(call.get('function')) else {}
                # Forward argument fragments as partial JSON rather than parsing incomplete documents.
                # The client assembles these fragments within the tool block.
                if isinstance(fn.get('arguments'), str) and fn['arguments'] != '':
                    out.append(
                        sse_event(
                            'content_block_delta',
                            {'type': 'content_block_delta', 'index': index, 'delta': {'type': 'input_json_delta', 'partial_json': fn['arguments']}},
                        )
                    )
            # Hold the stop reason until the stream's last chunk (the usage one) and [DONE] have arrived:
            # OpenAI sends the usage after the finish_reason, and Anthropic's message_delta carries the
            # output tokens.
            if choice.get('finish_reason') is not None:
                pending_stop = stop_reason_of(choice.get('finish_reason'))
        return out

    for frame in iter_frames(chunks):
        for event in events_of_frame(frame):
            yield event
    # An upstream EOF without DONE still closes the translated message.
    # Use the held stop reason when available and the normal end-turn fallback otherwise.
    if not finished:
        for event in start_events():
            yield event
        for event in finish_events(pending_stop if pending_stop is not None else 'end_turn'):
            yield event


"""HTTP routes, registration, and the trial work loop."""

# The wire protocol's version, in the register body.
PROTOCOL_VERSION = 1
# How often GET /work is polled, in seconds.
POLL_INTERVAL_SEC = 0.1
# The most bytes of one client request body this baseline reads.
MAX_BODY_BYTES = 8 * 1024 * 1024

ENV_URL = os.environ.get('MAKEFUN_ENV_URL', '').rstrip('/')


def log(message: str) -> None:
    """Write and flush an aggregator-prefixed diagnostic to standard error.
    Return None after the message has been flushed.
    """
    sys.stderr.write(f'aggregator: {message}\n')
    sys.stderr.flush()


class Handler(BaseHTTPRequestHandler):
    """The entry's one server: the models route, the two completion wires, and nothing else."""

    protocol_version = 'HTTP/1.1'
    server_version = 'makefun-aggregator-py/1'
    sys_version = ''

    # The trial's stderr is the entry's own log: no access line per request.
    def log_message(self: "Handler", format: str, *args: object) -> None:  # the base class's name
        """Suppress the HTTP base class's per-request access logging.
        Return None without writing a log entry.
        """
        pass

    def do_GET(self: "Handler") -> None:  # the base class's name
        """Delegate a GET request to the shared route dispatcher.
        Return None after dispatch completes.
        """
        self.dispatch()

    def do_POST(self: "Handler") -> None:  # the base class's name
        """Delegate a POST request to the shared route dispatcher.
        Return None after dispatch completes.
        """
        self.dispatch()

    # -- plumbing -----------------------------------------------------------------------------------

    def dispatch(self: "Handler") -> None:
        """Route the request and handle connection failures or handler exceptions.
        Return None after handling the response or closing a broken connection.
        """
        # Reset response state for each request handled on this connection.
        # Pending failure-trail headers are attached only to this response.
        # The sent flag prevents an exception from starting a second HTTP response.
        self.headers_sent = False
        self.pending_headers: dict[str, str] = {}
        # Dispatch by the URL path so query strings do not alter route matching.
        # Each recognized route handles its own client wire and body validation.
        # Returning after dispatch prevents a successful request from falling into 404 handling.
        path = urlsplit(self.path).path
        try:
            if self.command == 'GET' and path == '/v1/models':
                self.models()
                return
            if self.command == 'POST' and path == '/v1/chat/completions':
                self.chat()
                return
            if self.command == 'POST' and path == '/v1/messages':
                self.messages()
                return
            wire = 'anthropic' if path == '/v1/messages' else 'openai'
            self.send_error_body(wire, 404, 'not_found', f'no route {self.command} {path}')
        except (BrokenPipeError, ConnectionResetError):
            # The client went away mid-answer: nothing left to say, and the connection is done.
            self.close_connection = True
        except Exception:  # one request's failure must not take the server down
            log(traceback.format_exc())
            # A JSON error can replace a response only before headers are committed.
            # Once streaming begins, another status line would corrupt the existing response.
            # An error while writing the fallback is treated as a disconnected client.
            if not self.headers_sent:
                wire = 'anthropic' if urlsplit(self.path).path.startswith('/v1/messages') else 'openai'
                try:
                    self.send_error_body(wire, 500, 'internal_error', 'the server failed to answer')
                except (BrokenPipeError, ConnectionResetError):
                    self.close_connection = True

    def send_json(self: "Handler", status: int, body: object) -> None:
        """Write a JSON response with its length and any pending headers.
        Return None after writing the encoded body to the client.
        """
        # Compute content length from UTF-8 bytes rather than Python character count.
        # Non-ASCII response content can occupy multiple bytes per character.
        # Pending headers must be emitted before the header block is closed.
        text = json.dumps(body, separators=(',', ':'), ensure_ascii=False)
        raw = text.encode('utf-8')
        self.send_response(status)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(raw)))
        for name, value in self.pending_headers.items():
            self.send_header(name, value)
        self.headers_sent = True
        self.end_headers()
        self.wfile.write(raw)

    def send_error_body(self: "Handler", wire: str, status: int, error_type: str, message: str) -> None:
        """Encode and send an error in the requested client wire format.
        Return None after sending the JSON error response.
        """
        # Keep error envelopes consistent with the endpoint the client requested.
        # Both branches carry the same HTTP status and human-readable failure message.
        # Only their JSON error representation differs.
        if wire == 'anthropic':
            self.send_json(status, anthropic_error(error_type, message))
        else:
            self.send_json(status, openai_error(message, error_type, None))

    def stream_headers(self: "Handler") -> None:
        """Send SSE headers and mark the connection to close after the stream.
        Return None after committing the response headers.
        """
        # SSE responses commit success before incremental event bytes are written.
        # Failure-trail headers are available at this point after a successful dial.
        # Connection closure supplies the response boundary because no length is known.
        self.send_response(200)
        self.send_header('content-type', 'text/event-stream')
        self.send_header('cache-control', 'no-cache')
        # Python's http.server does not chunk a response without a length; the close is what ends the
        # stream for the client. Node's http server chunks instead and keeps the connection alive.
        self.send_header('connection', 'close')
        for name, value in self.pending_headers.items():
            self.send_header(name, value)
        self.headers_sent = True
        self.end_headers()
        self.close_connection = True

    def write_event(self: "Handler", text: str) -> None:
        """Encode an event string as UTF-8 and flush it to the client.
        Return None after the stream write has been flushed.
        """
        # Write one already-framed event and flush it promptly.
        # Keeping the blank-line delimiter is necessary for clients to dispatch the event.
        # Encoding is deferred until writing so translation helpers can operate on text.
        self.wfile.write(text.encode('utf-8'))
        self.wfile.flush()

    def read_json(self: "Handler") -> Json | None:
        """One client request body, or None when it is not one JSON object or is too large."""
        # The body reader relies on a finite declared byte length.
        # Missing lengths close the connection instead of waiting indefinitely for a body.
        # Malformed or excessive lengths likewise prevent reuse of an unread connection.
        length = self.headers.get('content-length')
        if length is None:
            self.close_connection = True
            return None
        try:
            size = int(length)
        except ValueError:
            self.close_connection = True
            return None
        if size < 0 or size > MAX_BODY_BYTES:
            self.close_connection = True
            return None
        # Read exactly the accepted body length before decoding UTF-8 and JSON.
        # Empty bodies and nonobject JSON values are not valid completion requests.
        # The caller translates this None result into its own wire-specific 400 response.
        raw = self.rfile.read(size) if size > 0 else b''
        if not raw:
            return None
        try:
            value = json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return None
        return value if is_record(value) else None

    def refused(self: "Handler", wire: str, status: int, text: str) -> None:
        """A refused or broken gateway call as a 502 in the client's wire: the prompt is lost."""
        # Expose only a bounded excerpt of the last upstream refusal to the client.
        # The client receives 502 while the message retains the observed upstream status.
        # The error type follows the requesting wire rather than the provider wire.
        suffix = '' if text == '' else f': {text[:500]}'
        if wire == 'anthropic':
            self.send_error_body(wire, 502, 'api_error', f'the gateway answered HTTP {status}{suffix}')
        else:
            self.send_error_body(wire, 502, 'upstream_error', f'the gateway answered HTTP {status}{suffix}')

    # -- the routes ---------------------------------------------------------------------------------

    def models(self: "Handler") -> None:
        """GET /v1/models, in the shape of the auth header the client sent."""
        # Both model-list formats derive from the same free-model rows.
        # Presence of x-api-key chooses the Messages-compatible list envelope.
        # The fixed metadata in those helpers avoids clock-dependent catalog differences.
        listing = served_models()
        if self.headers.get('x-api-key') is not None:
            self.send_json(200, anthropic_model_list(listing))
        else:
            self.send_json(200, openai_model_list(listing))

    def chat(self: "Handler") -> None:
        """POST /v1/chat/completions: an OpenAI-shaped client, answered by the first free model that meets it."""
        # Validate the client body before consuming its manifest requirement row.
        # A malformed Chat Completions body ends here with an OpenAI error envelope.
        # No gateway attempt or cooldown change occurs on this validation path.
        body = self.read_json()
        if body is None:
            self.send_error_body('openai', 400, 'invalid_request_error', 'the request body is not one JSON object')
            return
        # Select eligible models using one requirement lookup for this client request.
        # An empty route set is a local capability failure before any upstream call.
        # The body remains available for candidate-specific model replacement.
        routes = routes_for(requirements_for(body))
        if not routes:
            self.send_error_body('openai', 503, 'no_free_model', 'no free model in the brief meets this request')
            return
        # Failover returns both the successful response and its actual dialled identifier.
        # If no response succeeds, the last refusal supplies the client-facing diagnostic.
        # A copied request body keeps each candidate model assignment independent.
        upstream, route_id, failures, last = dial_with_failover(routes, lambda mid: {**body, 'model': mid})
        if upstream is None:
            self.refused('openai', last[0] if last else 502, last[1] if last else '')
            return
        # Publish recovered failures before committing either JSON or SSE success headers.
        # For streaming, the generator rewrites model attribution and repeated finishes.
        # The finally clause closes the gateway response even if the client disconnects.
        if failures:
            self.pending_headers['x-makefun-upstream-failures'] = trail_header(failures)
        if body.get('stream') is True:
            self.stream_headers()
            try:
                for frame in openai_stream_with_model(upstream.chunks(), route_id):
                    self.write_event(frame)
            finally:
                upstream.close()
            return
        answer = upstream.text()
        try:
            parsed = json.loads(answer)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        # A nonobject or invalid JSON answer uses the existing empty-object fallback.
        # The selected model field is still supplied by the successful routing decision.
        # Other valid gateway object fields pass through the model-replacement helper.
        self.send_json(200, openai_json_with_model(parsed if is_record(parsed) else {}, route_id))

    def messages(self: "Handler") -> None:
        """POST /v1/messages: an Anthropic-shaped client, rewritten to Chat Completions and answered back."""
        # Messages input uses the same bounded JSON reader as Chat Completions input.
        # Invalid bodies receive a Messages error before any manifest row is consumed.
        # Valid bodies retain their original structure until candidate translation.
        body = self.read_json()
        if body is None:
            self.send_error_body('anthropic', 400, 'invalid_request_error', 'the request body is not one JSON object')
            return
        # Evaluate requirements on the original Messages request before wire conversion.
        # The same eligibility and rating order apply to both public completion endpoints.
        # An empty eligible set gets the Messages overload error representation.
        routes = routes_for(requirements_for(body))
        if not routes:
            self.send_error_body('anthropic', 503, 'overloaded_error', 'no free model in the brief meets this request')
            return
        # Rebuild the gateway request with each candidate model during failover.
        # Translation preserves the caller body while constructing the gateway fields.
        # The eventual response is translated using the identifier returned by failover.
        upstream, route_id, failures, last = dial_with_failover(routes, lambda mid: anthropic_to_openai(body, mid))
        if upstream is None:
            self.refused('anthropic', last[0] if last else 502, last[1] if last else '')
            return
        # Recovered refusal evidence remains visible on Messages success responses too.
        # Streaming converts gateway chunks into named Messages lifecycle events.
        # Always close the upstream response when translation or client output ends.
        if failures:
            self.pending_headers['x-makefun-upstream-failures'] = trail_header(failures)
        if body.get('stream') is True:
            self.stream_headers()
            try:
                for event in anthropic_events_from_openai(upstream.chunks(), route_id):
                    self.write_event(event)
            finally:
                upstream.close()
            return
        answer = upstream.text()
        try:
            parsed = json.loads(answer)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        # Nonstreaming Messages replies use the same successful dialled model attribution.
        # The helper translates content, stop reason, and usage into the client envelope.
        # Invalid upstream JSON follows the existing empty-answer fallback path.
        self.send_json(200, openai_to_anthropic(parsed if is_record(parsed) else {}, route_id))


def register(port: int, report: Json) -> None:
    """POST /register with the port and the one catalog; the env answers with the trial's prompt count."""
    # Registration advertises the bound server port and the already-built catalog.
    # The serialized provider metadata uses the stable public endpoint.
    # The trial-local gateway address is reserved for actual completion calls.
    body = json.dumps({'protocol_version': PROTOCOL_VERSION, 'port': port, 'catalog': report}, separators=(',', ':'), ensure_ascii=False)
    request = urllib.request.Request(
        f'{ENV_URL}/register',
        data=body.encode('utf-8'),
        headers={'content-type': 'application/json'},
        method='POST',
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            status = response.status
            reply_text = response.read().decode('utf-8', 'replace')
    except urllib.error.HTTPError as err:
        raise RuntimeError(f'POST {ENV_URL}/register answered HTTP {err.code}: {err.read().decode("utf-8", "replace")[:500]}') from err
    # Treat every non-success registration response as a startup failure.
    # The bounded reply excerpt gives context without flooding the diagnostic log.
    # A successful response may omit the optional request-count metadata.
    if not (200 <= status < 300):
        raise RuntimeError(f'POST {ENV_URL}/register answered HTTP {status}: {reply_text[:500]}')
    try:
        reply = json.loads(reply_text)
    except (json.JSONDecodeError, ValueError):
        reply = None
    # The returned request count is informational and does not allocate manifest rows.
    # Its numeric guard avoids formatting arbitrary response structures as a count.
    # The declared prompt list remains the source of request requirements.
    if is_record(reply) and is_number(reply.get('requests')):
        log(f'registered: the trial holds {reply["requests"]} prompts')


def poll() -> None:
    """Poll GET /work every 100 ms; exit 0 the moment it says done."""
    while True:
        # Polling is background lifecycle work independent of model selection.
        # The interval limits control-plane traffic while completion requests are served.
        # Transient work endpoint failures are retried on a later polling iteration.
        time.sleep(POLL_INTERVAL_SEC)
        try:
            with urllib.request.urlopen(f'{ENV_URL}/work', timeout=2) as response:
                if response.status != 200:
                    continue
                work = json.loads(response.read().decode('utf-8', 'replace'))
        except Exception:  # the env is not reachable yet: try again on the next tick
            continue
        # Only an explicit boolean completion signal terminates the work loop.
        # Flush diagnostics before the immediate process exit.
        # This exit ends serving threads as well as the daemon poller.
        if is_record(work) and work.get('done') is True:
            sys.stderr.flush()
            os._exit(0)


def stop(*_: object) -> None:
    """SIGTERM and SIGINT end the trial as Node's process.exit(0) does: exit 0, at once."""
    sys.stderr.flush()
    os._exit(0)


def start_server() -> None:
    """Bind and register the server, start work polling, and serve requests.
    Return None if serving stops normally; always close the server on exit.
    """
    if ENV_URL == '':
        raise RuntimeError('MAKEFUN_ENV_URL is not set')
    if chat_completions_url() == '':
        raise RuntimeError('OPENAI_BASE_URL is not set')
    # Port zero asks the operating system to allocate a listening port.
    # The selected port is reported to the environment during registration.
    # Server cleanup remains in finally for normal exceptions and returns.
    server = ThreadingHTTPServer(('0.0.0.0', 0), Handler)
    try:
        port = server.server_address[1]
        # 8080 is the env's on one layout; the OS's ephemeral range never holds it, and the guard says so.
        if port == 8080:
            raise RuntimeError('the OS picked 8080, which the env owns on one layout: restart the trial')
        log(f'listening on 0.0.0.0:{port} with {len(served_models())} free models')
        # Complete registration before starting the background work poller.
        # Install signal handling before entering the long-running serving loop.
        # The daemon thread can observe completion while HTTP handlers are active.
        register(port, CATALOG)
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        threading.Thread(target=poll, name='work-poll', daemon=True).start()
        server.serve_forever()
    finally:
        server.server_close()


def start() -> None:
    """Log brief diagnostics and dispatch the requested command-line mode.
    Return None after a local mode completes or the server returns.
    """
    # Brief diagnostics are visible even in modes that do not start the server.
    # Local mode checks follow immediately and return before normal startup.
    # An absent brief therefore remains inspectable without gateway configuration.
    if BRIEF['error'] is not None:
        log(BRIEF['error'])
    if '--list' in sys.argv[1:]:
        # The same document the register body carries, byte for byte: print the one built object and stop
        # before any server starts. A process whose stdout is not flushed loses the tail, so flush it.
        sys.stdout.write(json.dumps(CATALOG, separators=(',', ':'), ensure_ascii=False) + '\n')
        sys.stdout.flush()
        return
    # This local selftest summarizes the parsed catalog rather than calling models.
    # Without a usable brief it reports that condition and still returns normally.
    # The preceding list branch retains precedence when both flags are present.
    if '--selftest' in sys.argv[1:]:
        if PROMPT_FILE and BRIEF['error'] is None:
            count = sum(len(provider['models']) for provider in CATALOG['providers'])
            print(f'selftest: ok {count} models')
        else:
            print('selftest: no brief')
        return
    start_server()


def main() -> int:
    """Run the entrypoint and log unexpected top-level failures.
    Return zero on success or one on failure, while propagating SystemExit.
    """
    try:
        start()
        return 0
    except SystemExit:
        raise
    except BaseException:  # Node's top-level catch: log the stack and exit 1
        log(traceback.format_exc())
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
