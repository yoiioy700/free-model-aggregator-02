"""Capability requirements, rating order, cooldowns, and gateway failover."""

import json
import os
import threading
import urllib.error
import urllib.request

from .brief import BRIEF, Json, free_models, is_record

GATEWAY_URL = os.environ.get('OPENAI_BASE_URL', '').rstrip('/')
# The trial's dummy key for the entry's gateway lane; the gateway swaps it for the entrant's own key.
GATEWAY_KEY = os.environ.get('OPENAI_API_KEY', '')

# Which prompt row the request now arriving is: the brief's prompts are in the client's arrival order.
manifest_index = 0
# Models that refused in this run, so later prompts skip them (derived from answers only).
COOLDOWN: dict[str, int] = {}
manifest_lock = threading.Lock()


def capabilities_of(model: Json) -> Json:
    return {
        'context_length': model['context_length'],
        'tools': model['tools'],
        'vision': model['vision'],
        'json': model['json'],
    }


def meets_requirements(requires: Json, caps: Json) -> bool:
    """Whether a model's capabilities meet one prompt's requirements. An unknown capability never meets one."""
    if requires.get('tools') is True and caps['tools'] is not True:
        return False
    if requires.get('vision') is True and caps['vision'] is not True:
        return False
    if requires.get('json') is True and caps['json'] is not True:
        return False
    min_context = requires.get('min_context')
    if min_context is not None and (caps['context_length'] is None or caps['context_length'] < min_context):
        return False
    return True


def chat_completions_url() -> str:
    """The gateway's Chat Completions route.

    OPENAI_BASE_URL is the OpenAI-compatible root (`…/v1` on the trial network); a base that does not
    already end in `/v1` gets one, so both spellings reach the route.
    """
    if GATEWAY_URL == '':
        return ''
    return f'{GATEWAY_URL}/chat/completions' if GATEWAY_URL.endswith('/v1') else f'{GATEWAY_URL}/v1/chat/completions'


def routes_for(requires: Json) -> list[Json]:
    """Every free model that meets the request, the best rated first.

    Quality is 60% of the score and reads the model that really answered, so the first candidate is the
    highest rating_milli that satisfies the prompt. Models that already refused this run go last: a 429
    on a free model is per model, and spending the next prompt on it again loses that prompt too.
    """
    candidates = [m for m in free_models(BRIEF) if meets_requirements(requires, capabilities_of(m))]
    candidates.sort(key=lambda m: (-(m.get('rating_milli') or 0), -(m.get('context_length') or 0), m['id']))
    fresh = [m for m in candidates if not COOLDOWN.get(m['id'])]
    return fresh if fresh else candidates


def requirements_for(body: Json) -> Json:
    """The requirements of the prompt now arriving: the brief's row by arrival order, else the body's own."""
    global manifest_index
    with manifest_lock:
        index = manifest_index
        manifest_index += 1
    prompts = BRIEF['prompts']
    if index < len(prompts):
        return prompts[index]['requires']
    # TODO(workload): the brief's prompt list is the declared source of each request's requirements; this
    # fallback is a guess for a brief that holds none.
    return requirements_from_body(body)


def requirements_from_body(body: Json) -> Json:
    """Requirements read off a request body, when the brief has no row for it."""
    messages = body.get('messages') if isinstance(body.get('messages'), list) else []
    vision = False
    for message in messages:
        if not is_record(message) or not isinstance(message.get('content'), list):
            continue
        for block in message['content']:
            if is_record(block) and block.get('type') in ('image', 'image_url'):
                vision = True
    response_format = body.get('response_format')
    response_type = response_format.get('type') if is_record(response_format) else None
    return {'tools': isinstance(body.get('tools'), list) and len(body['tools']) > 0, 'vision': vision, 'json': response_type in ('json_object', 'json_schema')}


class Upstream:
    """One gateway answer: its status and a body that may be read whole or as byte chunks."""

    def __init__(self, status: int, response) -> None:
        self.status = status
        self._response = response

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def chunks(self):
        """The body's bytes as they arrive, never waiting to fill a buffer (SSE must stream)."""
        reader = getattr(self._response, 'read1', None) or self._response.read
        while True:
            chunk = reader(65536)
            if not chunk:
                break
            yield chunk

    def text(self) -> str:
        try:
            return self._response.read().decode('utf-8', 'replace')
        finally:
            self.close()

    def close(self) -> None:
        try:
            self._response.close()
        except Exception:  # a connection already closed is nothing to report
            pass


def dial(model: str, body: Json) -> Upstream:
    """POST one request to the gateway's Chat Completions route, naming the model this entry dialled."""
    url = chat_completions_url()
    if url == '':
        raise RuntimeError('OPENAI_BASE_URL is not set')
    data = json.dumps({**body, 'model': model}, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    headers = {'content-type': 'application/json', 'authorization': f'Bearer {GATEWAY_KEY}'}
    if body.get('stream') is True:
        headers['accept'] = 'text/event-stream'
    request = urllib.request.Request(url, data=data, headers=headers, method='POST')
    try:
        response = urllib.request.urlopen(request)
    except urllib.error.HTTPError as err:
        # A refusal is an answer with a status and a body, exactly as Node's fetch reports it.
        return Upstream(err.code, err)
    return Upstream(response.status, response)


# Statuses worth another model: rate limits, provider faults and gateway timeouts.
FAILOVER_STATUSES = (403, 404, 408, 409, 425, 429, 451, 500, 502, 503, 504, 529)


def dial_with_failover(candidates: list[Json], make_body) -> tuple[Upstream | None, str | None, list[Json], tuple[int, str] | None]:
    """Dial the best candidate, failing over down the rating order on a refusal.

    Returns (upstream, model actually used, the refusal trail, the last refusal). The trail rides back on
    x-makefun-upstream-failures: it is the only way the failover column can see a refusal we recovered from.
    """
    failures: list[Json] = []
    last: tuple[int, str] | None = None
    for model in candidates:
        if len(failures) >= 8:
            break
        try:
            upstream = dial(model['id'], make_body(model['id']))
        except Exception as err:  # an unreachable gateway is this candidate's refusal
            failures.append({'status': 502, 'code': 'unreachable', 'model': model['id']})
            last = (502, str(err))
            COOLDOWN[model['id']] = 1
            continue
        if upstream.ok:
            return upstream, model['id'], failures, None
        text = upstream.text()
        code = ''
        try:
            parsed = json.loads(text)
            if is_record(parsed) and is_record(parsed.get('error')):
                code = str(parsed['error'].get('code') or parsed['error'].get('type') or '')
        except (json.JSONDecodeError, ValueError):
            code = ''
        failures.append({'status': upstream.status, 'code': code[:48] or 'refused', 'model': model['id']})
        last = (upstream.status, text)
        if upstream.status in FAILOVER_STATUSES or upstream.status >= 500:
            # A 403 from a free model is a region gate on that model, not on our request: skip it for
            # the rest of the run and try the next best model.
            COOLDOWN[model['id']] = 1
        elif upstream.status != 400:
            COOLDOWN[model['id']] = 1
        else:
            break  # a 400 aimed at our own body would repeat on every model
    return None, None, failures, last


def trail_header(failures: list[Json]) -> str:
    """The x-makefun-upstream-failures header value."""
    return json.dumps(failures, separators=(',', ':'))
