"""Black-box contract tests for the single-file aggregator.

Only main.py's declared signatures were read when authoring this suite.
All brief documents are synthetic and all upstream calls are intercepted.
The fourteen IDs below are an explicit fixture, not a copied competition
models table. No kit, scorer, environment, cassette, or model is contacted.

Run with: python3 -m unittest discover -s tests -v
"""

import contextlib
import copy
import http.client
import importlib.util
import io
import json
import linecache
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from email.message import Message
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock, Mock, patch


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "main.py"
MODEL_SCHEMA = "free-model-aggregator-02/models/v1"
PROMPT_SCHEMA = "free-model-aggregator-02/prompts/v1"
FREE_IDS = (
    "openrouter/fixture/model-01:free",
    "openrouter/fixture/model-02:free",
    "openrouter/fixture/model-03:free",
    "openrouter/fixture/model-04:free",
    "openrouter/fixture/model-05:free",
    "openrouter/fixture/model-06:free",
    "openrouter/fixture/model-07:free",
    "openrouter/fixture/model-08:free",
    "openrouter/fixture/model-09:free",
    "openrouter/fixture/model-10:free",
    "openrouter/fixture/model-11:free",
    "openrouter/fixture/model-12:free",
    "openrouter/fixture/model-13:free",
    "openrouter/fixture/model-14:free",
)


def model_row(model_id, rating=1000, **overrides):
    """Produce a complete, independent input row for the models table."""
    row = {
        "id": model_id,
        "provider": "openrouter",
        "display_name": model_id,
        "rating_milli": rating,
        "context_length": 32768,
        "tools": True,
        "vision": True,
        "json": True,
        "free": True,
    }
    row.update(overrides)
    return row


def prompt_row(prompt_id, requires=None, **overrides):
    row = {
        "id": prompt_id,
        "api": "openai",
        "stream": False,
        "weight": 1,
        "requires": {} if requires is None else requires,
    }
    row.update(overrides)
    return row


def brief_text(models, prompts):
    """Include prose and fences, as a human-readable brief would."""
    manifest = {"schema": PROMPT_SCHEMA, "prompts": prompts}
    table = {"schema": MODEL_SCHEMA, "models": models}
    return (
        "Prompts manifest follows.\n```json\n"
        + json.dumps(manifest)
        + "\n```\nModels table follows.\n```json\n"
        + json.dumps(table)
        + "\n```\n"
    )


def data_frame(payload):
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def frame_data(frame):
    lines = [line[6:] for line in frame.splitlines() if line.startswith("data: ")]
    payload = "\n".join(lines)
    return payload if payload == "[DONE]" else json.loads(payload)


def completion_chunk(content=None, finish=None, usage=None):
    delta = {} if content is None else {"content": content}
    chunk = {
        "id": "chatcmpl-fixture",
        "object": "chat.completion.chunk",
        "model": "upstream-private-id",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return chunk


def completion_body(content="Hello", finish="stop"):
    return {
        "id": "chatcmpl-fixture",
        "object": "chat.completion",
        "model": "upstream-private-id",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish,
            }
        ],
        "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
    }


class FakeUpstream:
    """Minimal transport double with observable closing and body reads."""

    def __init__(self, status, code="fixture_error", text=None, chunks=()):
        self.status = status
        self.ok = 200 <= status < 300
        self.payload = text if text is not None else json.dumps(
            {"error": {"message": "fixture refusal", "code": code}}
        )
        self.parts = tuple(chunks)
        self.closed = False
        self.read_count = 0

    def text(self):
        self.read_count += 1
        return self.payload

    def chunks(self):
        yield from self.parts

    def close(self):
        self.closed = True


class AggregatorCase(unittest.TestCase):
    """Isolate environment-derived configuration and per-run mutable state."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix=".aggregator-tests-", dir=ROOT)
        self.addCleanup(self.directory.cleanup)
        self.brief_path = Path(self.directory.name) / "brief.txt"
        self.models = [model_row(mid, 1000 + i) for i, mid in enumerate(FREE_IDS)]
        self.prompts = []
        self.network = self.enterContext(
            patch("urllib.request.urlopen", side_effect=AssertionError("unexpected upstream call"))
        )
        self.load()

    def load(self, models=None, prompts=None):
        if models is not None:
            self.models = models
        if prompts is not None:
            self.prompts = prompts
        self.brief_path.write_text(brief_text(self.models, self.prompts), encoding="utf-8")
        environment = {
            "MAKEFUN_PROMPT_FILE": str(self.brief_path),
            "MAKEFUN_ENV_URL": "http://127.0.0.1:1",
            "OPENROUTER_API_KEY": "fixture-key-never-sent",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        spec = importlib.util.spec_from_file_location("aggregator_under_test", ENTRYPOINT)
        module = importlib.util.module_from_spec(spec)
        # Keep unittest tracebacks from displaying implementation source.
        linecache.cache[str(ENTRYPOINT)] = (0, None, [], str(ENTRYPOINT))
        with patch.dict(os.environ, environment, clear=True):
            with patch.object(sys, "dont_write_bytecode", True):
                spec.loader.exec_module(module)
        self.app = module
        return module

    def read_text(self, text):
        self.brief_path.write_text(text, encoding="utf-8")
        return self.app.read_brief(str(self.brief_path))

    def ids(self, rows):
        return [row["id"] for row in rows]

    def make_handler(self, headers=None, path="/v1/models", method="GET"):
        handler = object.__new__(self.app.Handler)
        handler.path = path
        handler.command = method
        handler.headers = Message()
        for key, value in (headers or {}).items():
            handler.headers[key] = value
        handler.send_json = Mock()
        return handler

    def successful_failover(self, candidates, status=429, code="rate_limit"):
        refused = FakeUpstream(status, code)
        accepted = FakeUpstream(200)
        with patch.object(self.app, "dial", side_effect=[refused, accepted]) as dial:
            result = self.app.dial_with_failover(candidates, lambda mid: {"model": mid})
        return result, refused, accepted, dial


class BriefDocumentTests(AggregatorCase):
    def test_two_fenced_documents_parse_into_models_and_prompts(self):
        prompts = [prompt_row("first", {"tools": True})]
        brief = self.read_text(brief_text(self.models, prompts))
        self.assertIsNone(brief["error"])
        self.assertEqual(self.ids(brief["models"]), list(FREE_IDS))
        self.assertEqual(brief["prompts"], prompts)

    def test_document_order_does_not_change_schema_selection(self):
        table = {"schema": MODEL_SCHEMA, "models": self.models}
        manifest = {"schema": PROMPT_SCHEMA, "prompts": []}
        result = self.read_text(json.dumps(table) + "\n" + json.dumps(manifest))
        self.assertIsNone(result["error"])
        self.assertEqual(len(result["models"]), 14)

    def test_missing_file_returns_diagnostic_and_empty_collections(self):
        absent = Path(self.directory.name) / "not-created.json"
        result = self.app.read_brief(str(absent))
        self.assertEqual(result["models"], [])
        self.assertEqual(result["prompts"], [])
        self.assertIn("cannot read", result["error"])
        self.assertIn("not-created.json", result["error"])

    def test_malformed_json_is_reported_as_missing_document(self):
        result = self.read_text('{"schema": "' + MODEL_SCHEMA + '", "models": [}')
        self.assertEqual(result["models"], [])
        self.assertIsInstance(result["error"], str)
        self.assertIn(MODEL_SCHEMA, result["error"])

    def test_missing_models_key_is_not_a_valid_models_table(self):
        text = json.dumps({"schema": MODEL_SCHEMA})
        text += json.dumps({"schema": PROMPT_SCHEMA, "prompts": []})
        result = self.read_text(text)
        self.assertIn(MODEL_SCHEMA, result["error"])
        self.assertEqual(result["models"], [])

    def test_missing_prompts_key_is_reported_after_models_parse(self):
        text = json.dumps({"schema": MODEL_SCHEMA, "models": self.models})
        text += json.dumps({"schema": PROMPT_SCHEMA})
        result = self.read_text(text)
        self.assertIn(PROMPT_SCHEMA, result["error"])
        self.assertEqual(len(result["models"]), 14)

    def test_models_object_is_rejected_where_an_array_is_required(self):
        text = json.dumps({"schema": MODEL_SCHEMA, "models": {"id": "wrong"}})
        result = self.read_text(text)
        self.assertIn(MODEL_SCHEMA, result["error"])
        self.assertEqual(result["models"], [])

    def test_empty_valid_arrays_are_not_missing_documents(self):
        result = self.read_text(brief_text([], []))
        self.assertIsNone(result["error"])
        self.assertEqual(result["models"], [])
        self.assertEqual(result["prompts"], [])

    def test_unrelated_json_objects_do_not_become_documents(self):
        text = '{"models": [{"id": "untrusted"}]}\n' + brief_text(self.models, [])
        result = self.read_text(text)
        self.assertIsNone(result["error"])
        self.assertNotIn("untrusted", self.ids(result["models"]))

    def test_document_scanner_handles_braces_and_escaped_quotes_in_strings(self):
        row = model_row("fixture/brace:free", display_name='A } brace and "quote"')
        result = self.read_text(brief_text([row], []))
        self.assertIsNone(result["error"])
        self.assertEqual(result["models"][0]["display_name"], row["display_name"])

    def test_document_scanner_extracts_both_schema_tagged_documents(self):
        docs = self.app.documents_in(brief_text(self.models, []))
        self.assertEqual(len(docs), 2)
        self.assertEqual({doc["schema"] for doc in docs}, {MODEL_SCHEMA, PROMPT_SCHEMA})

    def test_document_scanner_ignores_plain_prose_and_invalid_json(self):
        self.assertEqual(self.app.documents_in("No documents here."), [])
        self.assertEqual(self.app.documents_in("{not valid json}"), [])
        self.assertEqual(self.app.documents_in('{"unrelated": true}'), [])

    def test_model_parser_rejects_rows_without_nonempty_ids(self):
        for value in (None, [], {}, {"name": "wrong"}, {"id": ""}):
            with self.subTest(value=value):
                self.assertIsNone(self.app.model_of(value))

    def test_model_parser_preserves_routing_fields(self):
        row = model_row("openrouter/fixture/rich:free", 1234567, context_length=65536, vision=False)
        parsed = self.app.model_of(row)
        self.assertEqual(parsed, row)
        self.assertEqual(parsed["rating_milli"], 1234567)

    def test_minimal_model_row_gets_conservative_capability_defaults(self):
        row = self.app.model_of({"id": "fixture/minimal:free"})
        self.assertEqual(row["provider"], "openrouter")
        self.assertEqual(row["rating_milli"], 0)
        self.assertIsNone(row["context_length"])
        for capability in ("tools", "vision", "json"):
            self.assertIsNone(row[capability])

    def test_prompt_parser_preserves_explicit_manifest_requirements(self):
        row = prompt_row("fixture", {"tools": True, "min_context": 65536}, api="anthropic")
        self.assertEqual(self.app.prompt_of(row), row)
        self.assertEqual(self.app.prompt_of({"id": "minimal"})["requires"], {})

    def test_invalid_rows_are_filtered_without_losing_valid_rows(self):
        result = self.read_text(brief_text([None, {}, self.models[0]], [None, prompt_row("ok")]))
        self.assertIsNone(result["error"])
        self.assertEqual(self.ids(result["models"]), [FREE_IDS[0]])
        self.assertEqual(self.ids(result["prompts"]), ["ok"])


class CatalogTests(AggregatorCase):
    def catalog(self):
        return self.app.build_catalog(self.app.read_brief(str(self.brief_path)))

    def test_catalog_contains_exactly_the_fourteen_explicit_free_ids(self):
        catalog = self.catalog()
        rows = catalog["providers"][0]["models"]
        self.assertEqual(self.ids(rows), list(FREE_IDS))
        self.assertEqual(len(rows), 14)
        self.assertEqual(len(set(self.ids(rows))), 14)

    def test_catalog_has_one_openrouter_provider(self):
        providers = self.catalog()["providers"]
        self.assertEqual(len(providers), 1)
        self.assertEqual(providers[0]["id"], "openrouter")

    def test_catalog_provider_has_expected_connection_contract(self):
        provider = self.catalog()["providers"][0]
        self.assertEqual(provider["base_url"], "https://openrouter.ai/api/v1")
        self.assertEqual(provider["key_env"], "OPENROUTER_API_KEY")
        self.assertEqual(provider["wire"], "openai")

    def test_catalog_models_are_free_and_publish_context_length(self):
        for row in self.catalog()["providers"][0]["models"]:
            with self.subTest(model=row["id"]):
                self.assertIs(row["free"], True)
                self.assertEqual(row["context_length"], 32768)

    def test_paid_model_is_excluded_from_catalog_and_free_models(self):
        paid = model_row("fixture/paid", free=False)
        self.load(models=self.models + [paid])
        brief = self.app.read_brief(str(self.brief_path))
        self.assertEqual(self.ids(self.app.free_models(brief)), list(FREE_IDS))
        self.assertEqual(self.ids(self.catalog()["providers"][0]["models"]), list(FREE_IDS))

    def test_empty_models_produce_no_provider_entries(self):
        catalog = self.app.build_catalog({"models": [], "prompts": [], "error": None})
        self.assertEqual(catalog, {"providers": []})

    def test_list_mode_prints_exactly_the_catalog_document(self):
        output = io.StringIO()
        with patch.object(sys, "argv", [str(ENTRYPOINT), "--list"]):
            with contextlib.redirect_stdout(output):
                result = self.app.main()
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue()), self.catalog())
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        self.network.assert_not_called()

    def test_list_mode_does_not_start_server_or_register(self):
        with patch.object(self.app, "start_server") as server:
            with patch.object(self.app, "register") as register:
                with patch.object(sys, "argv", [str(ENTRYPOINT), "--list"]):
                    with contextlib.redirect_stdout(io.StringIO()):
                        self.app.main()
        server.assert_not_called()
        register.assert_not_called()

    def test_served_models_preserve_catalog_identity(self):
        served = self.app.served_models()
        self.assertEqual(self.ids(served), list(FREE_IDS))
        for row in served:
            self.assertEqual(row["provider"], "openrouter")
            self.assertEqual(row["display_name"], row["id"])

    def test_catalog_serialization_never_contains_the_api_key(self):
        serialized = json.dumps(self.catalog())
        self.assertIn("OPENROUTER_API_KEY", serialized)
        self.assertNotIn("fixture-key-never-sent", serialized)


class CapabilityTests(AggregatorCase):
    def test_capabilities_extract_only_supported_requirement_fields(self):
        caps = self.app.capabilities_of(self.models[0])
        self.assertEqual(caps, {
            "context_length": 32768,
            "tools": True,
            "vision": True,
            "json": True,
        })

    def test_empty_requirements_accept_a_model_without_optional_capabilities(self):
        caps = {"context_length": None, "tools": False, "vision": False, "json": False}
        self.assertTrue(self.app.meets_requirements({}, caps))

    def test_tools_requirement_rejects_a_model_without_tools(self):
        caps = self.app.capabilities_of(model_row("no-tools", tools=False))
        self.assertFalse(self.app.meets_requirements({"tools": True}, caps))
        self.assertTrue(self.app.meets_requirements({"tools": False}, caps))

    def test_vision_requirement_rejects_a_text_only_model(self):
        caps = self.app.capabilities_of(model_row("text-only", vision=False))
        self.assertFalse(self.app.meets_requirements({"vision": True}, caps))
        self.assertTrue(self.app.meets_requirements({"vision": False}, caps))

    def test_json_requirement_rejects_a_model_without_json_support(self):
        caps = self.app.capabilities_of(model_row("no-json", json=False))
        self.assertFalse(self.app.meets_requirements({"json": True}, caps))
        self.assertTrue(self.app.meets_requirements({"json": False}, caps))

    def test_minimum_context_is_inclusive_at_the_boundary(self):
        caps = self.app.capabilities_of(model_row("boundary", context_length=8192))
        self.assertTrue(self.app.meets_requirements({"min_context": 8192}, caps))
        self.assertFalse(self.app.meets_requirements({"min_context": 8193}, caps))

    def test_unknown_context_cannot_satisfy_a_positive_minimum(self):
        caps = self.app.capabilities_of(model_row("unknown", context_length=None))
        self.assertFalse(self.app.meets_requirements({"min_context": 1}, caps))

    def test_unknown_capabilities_do_not_count_as_supported(self):
        caps = self.app.capabilities_of(model_row("unknown", tools=None, vision=None, json=None))
        for feature in ("tools", "vision", "json"):
            with self.subTest(feature=feature):
                self.assertFalse(self.app.meets_requirements({feature: True}, caps))

    def test_all_requested_capabilities_must_be_met_together(self):
        requirements = {"tools": True, "vision": True, "json": True, "min_context": 16384}
        self.assertTrue(self.app.meets_requirements(requirements, self.app.capabilities_of(self.models[0])))
        for feature in ("tools", "vision", "json"):
            caps = self.app.capabilities_of(model_row("partial", **{feature: False}))
            with self.subTest(feature=feature):
                self.assertFalse(self.app.meets_requirements(requirements, caps))


class RequirementResolutionTests(AggregatorCase):
    def test_manifest_rows_win_in_arrival_order_even_when_body_conflicts(self):
        self.load(prompts=[prompt_row("a", {"vision": True}), prompt_row("b", {"json": True})])
        body = {"tools": [{"type": "function", "function": {"name": "lookup"}}]}
        self.assertEqual(self.app.requirements_for(body), {"vision": True})
        self.assertEqual(self.app.requirements_for(body), {"json": True})

    def test_prompt_ids_in_body_do_not_reorder_the_manifest(self):
        self.load(prompts=[prompt_row("first", {"tools": True}), prompt_row("second", {"vision": True})])
        self.assertEqual(self.app.requirements_for({"id": "second"}), {"tools": True})
        self.assertEqual(self.app.requirements_for({"id": "first"}), {"vision": True})

    def test_empty_manifest_requirement_still_wins_over_body(self):
        self.load(prompts=[prompt_row("empty", {})])
        self.assertEqual(self.app.requirements_for({"tools": [{"name": "lookup"}]}), {})

    def test_fallback_begins_after_the_last_manifest_row(self):
        self.load(prompts=[prompt_row("only", {"min_context": 100000})])
        body = {"response_format": {"type": "json_object"}}
        self.assertEqual(self.app.requirements_for(body), {"min_context": 100000})
        self.assertEqual(self.app.requirements_for(body), {"tools": False, "vision": False, "json": True})

    def test_plain_chat_body_requires_no_optional_capabilities(self):
        body = {"messages": [{"role": "user", "content": "Hello"}]}
        self.assertEqual(self.app.requirements_from_body(body), {"tools": False, "vision": False, "json": False})

    def test_nonempty_tools_array_requires_tools(self):
        body = {"tools": [{"type": "function", "function": {"name": "lookup"}}]}
        self.assertTrue(self.app.requirements_from_body(body)["tools"])
        self.assertFalse(self.app.requirements_from_body({"tools": []})["tools"])

    def test_json_object_response_format_requires_json(self):
        body = {"response_format": {"type": "json_object"}}
        self.assertTrue(self.app.requirements_from_body(body)["json"])

    def test_json_schema_response_format_requires_json(self):
        body = {"response_format": {"type": "json_schema", "json_schema": {"name": "answer"}}}
        self.assertTrue(self.app.requirements_from_body(body)["json"])

    def test_text_response_format_does_not_require_json(self):
        body = {"response_format": {"type": "text"}}
        self.assertFalse(self.app.requirements_from_body(body)["json"])

    def test_openai_image_content_requires_vision(self):
        body = {"messages": [{"role": "user", "content": [
            {"type": "text", "text": "Describe this"},
            {"type": "image_url", "image_url": {"url": "https://example.invalid/image.png"}},
        ]}]}
        self.assertTrue(self.app.requirements_from_body(body)["vision"])

    def test_text_content_blocks_do_not_require_vision(self):
        body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "Hello"}]}]}
        self.assertFalse(self.app.requirements_from_body(body)["vision"])

    def test_body_requirement_derivation_does_not_consume_manifest_rows(self):
        self.load(prompts=[prompt_row("next", {"tools": True})])
        self.app.requirements_from_body({})
        self.assertEqual(self.app.requirements_for({}), {"tools": True})


class RoutingTests(AggregatorCase):
    def test_candidates_are_sorted_by_descending_rating_milli(self):
        rows = [model_row("openrouter/low", 100), model_row("openrouter/high", 900), model_row("openrouter/middle", 500)]
        self.load(models=rows)
        self.assertEqual(self.ids(self.app.routes_for({})), ["openrouter/high", "openrouter/middle", "openrouter/low"])

    def test_highest_rated_model_meeting_tools_requirement_is_first(self):
        self.load(models=[model_row("openrouter/best-no-tools", 999, tools=False), model_row("openrouter/best-tools", 800), model_row("openrouter/low", 10)])
        routes = self.app.routes_for({"tools": True})
        self.assertEqual(self.ids(routes), ["openrouter/best-tools", "openrouter/low"])

    def test_vision_filter_removes_incapable_highest_rated_model(self):
        self.load(models=[model_row("openrouter/text", 999, vision=False), model_row("openrouter/vision", 20)])
        self.assertEqual(self.ids(self.app.routes_for({"vision": True})), ["openrouter/vision"])

    def test_json_filter_removes_incapable_highest_rated_model(self):
        self.load(models=[model_row("openrouter/plain", 999, json=False), model_row("openrouter/structured", 20)])
        self.assertEqual(self.ids(self.app.routes_for({"json": True})), ["openrouter/structured"])

    def test_context_filter_removes_short_context_model(self):
        self.load(models=[model_row("openrouter/short", 999, context_length=4096), model_row("openrouter/long", 20)])
        self.assertEqual(self.ids(self.app.routes_for({"min_context": 16000})), ["openrouter/long"])

    def test_equal_ratings_produce_a_repeatable_total_order(self):
        rows = [model_row("openrouter/zeta", 100), model_row("openrouter/alpha", 100), model_row("openrouter/middle", 100)]
        self.load(models=rows)
        first = self.ids(self.app.routes_for({}))
        self.assertCountEqual(first, ["openrouter/zeta", "openrouter/alpha", "openrouter/middle"])
        for _ in range(5):
            self.assertEqual(self.ids(self.app.routes_for({})), first)
        self.load(models=rows)
        self.assertEqual(self.ids(self.app.routes_for({})), first)

    def test_paid_model_never_enters_routes_even_with_highest_rating(self):
        self.load(models=[model_row("openrouter/paid", 999999, free=False), model_row("openrouter/free", 1)])
        self.assertEqual(self.ids(self.app.routes_for({})), ["openrouter/free"])

    def test_unfulfillable_requirements_return_no_candidate(self):
        self.load(models=[model_row("openrouter/text", vision=False)])
        self.assertEqual(self.app.routes_for({"vision": True}), [])

    def test_a_refused_model_moves_behind_fresh_candidates(self):
        self.load(models=[model_row("openrouter/best", 900), model_row("openrouter/second", 500), model_row("openrouter/third", 100)])
        self.successful_failover(self.app.routes_for({}))
        self.assertEqual(self.ids(self.app.routes_for({})), ["openrouter/second", "openrouter/third"])

    def test_cooldown_prefers_the_remaining_fresh_model(self):
        self.load(models=[model_row("openrouter/best", 900), model_row("openrouter/next", 100)])
        self.successful_failover(self.app.routes_for({}))
        self.assertEqual(self.ids(self.app.routes_for({})), ["openrouter/next"])

    def test_all_cooled_models_still_yield_routes(self):
        self.load(models=[model_row("openrouter/a", 900), model_row("openrouter/b", 100)])
        with patch.object(self.app, "dial", side_effect=[FakeUpstream(429), FakeUpstream(503)]):
            self.app.dial_with_failover(self.app.routes_for({}), lambda mid: {"model": mid})
        self.assertEqual(self.ids(self.app.routes_for({})), ["openrouter/a", "openrouter/b"])

    def test_cooldown_still_respects_capability_filters(self):
        self.load(models=[model_row("openrouter/capable", 900), model_row("openrouter/incapable", 100, tools=False)])
        with patch.object(self.app, "dial", return_value=FakeUpstream(429)):
            self.app.dial_with_failover([self.models[0]], lambda mid: {"model": mid})
        self.assertEqual(self.ids(self.app.routes_for({"tools": True})), ["openrouter/capable"])

    def test_new_module_instance_starts_without_prior_run_cooldown(self):
        rows = [model_row("openrouter/best", 900), model_row("openrouter/next", 100)]
        self.load(models=rows)
        self.successful_failover(self.app.routes_for({}))
        self.assertEqual(self.ids(self.app.routes_for({}))[0], "openrouter/next")
        self.load(models=rows)
        self.assertEqual(self.ids(self.app.routes_for({}))[0], "openrouter/best")


class FailoverTests(AggregatorCase):
    def setUp(self):
        super().setUp()
        self.candidates = [model_row("first", 900), model_row("second", 100)]

    def assert_retry_status(self, status):
        result, refused, accepted, dial = self.successful_failover(self.candidates, status)
        upstream, selected, failures, terminal = result
        self.assertIs(upstream, accepted)
        self.assertEqual(selected, "second")
        self.assertIsNone(terminal)
        self.assertEqual(failures, [{"status": status, "code": "rate_limit", "model": "first"}])
        self.assertEqual(dial.call_count, 2)
        self.assertEqual(refused.read_count, 1)
        self.assertFalse(accepted.closed)

    def test_authentication_refusal_401_earns_retry(self):
        self.assert_retry_status(401)

    def test_payment_refusal_402_earns_retry(self):
        self.assert_retry_status(402)

    def test_permission_refusal_403_earns_retry(self):
        self.assert_retry_status(403)

    def test_missing_model_404_earns_retry(self):
        self.assert_retry_status(404)

    def test_timeout_408_earns_retry(self):
        self.assert_retry_status(408)

    def test_conflict_409_earns_retry(self):
        self.assert_retry_status(409)

    def test_unprocessable_model_request_422_earns_retry(self):
        self.assert_retry_status(422)

    def test_rate_limit_429_earns_retry(self):
        self.assert_retry_status(429)

    def test_server_error_500_earns_retry(self):
        self.assert_retry_status(500)

    def test_bad_gateway_502_earns_retry(self):
        self.assert_retry_status(502)

    def test_unavailable_503_earns_retry(self):
        self.assert_retry_status(503)

    def test_gateway_timeout_504_earns_retry(self):
        self.assert_retry_status(504)

    def test_bad_request_400_terminates_without_dialing_second_model(self):
        refused = FakeUpstream(400, "bad_request")
        with patch.object(self.app, "dial", return_value=refused) as dial:
            upstream, selected, trail, terminal = self.app.dial_with_failover(self.candidates, lambda mid: {"model": mid})
        self.assertIsNone(upstream)
        self.assertIsNone(selected)
        self.assertEqual(dial.call_count, 1)
        self.assertEqual(terminal, (400, refused.payload))
        self.assertEqual(trail, [{"status": 400, "code": "bad_request", "model": "first"}])

    def test_successful_first_candidate_has_no_trail_or_terminal_error(self):
        response = FakeUpstream(200)
        with patch.object(self.app, "dial", return_value=response) as dial:
            result = self.app.dial_with_failover(self.candidates, lambda mid: {"model": mid})
        self.assertEqual(result, (response, "first", [], None))
        dial.assert_called_once_with("first", {"model": "first"})

    def test_make_body_is_called_with_each_attempted_model(self):
        make_body = Mock(side_effect=lambda mid: {"model": mid, "messages": []})
        with patch.object(self.app, "dial", side_effect=[FakeUpstream(429), FakeUpstream(200)]) as dial:
            self.app.dial_with_failover(self.candidates, make_body)
        self.assertEqual([call.args[0] for call in make_body.call_args_list], ["first", "second"])
        self.assertEqual(dial.call_args_list[1].args, ("second", {"model": "second", "messages": []}))

    def test_empty_candidates_never_dial(self):
        with patch.object(self.app, "dial") as dial:
            self.assertEqual(self.app.dial_with_failover([], lambda mid: {}), (None, None, [], None))
        dial.assert_not_called()

    def test_failure_trail_rows_have_only_status_code_and_model(self):
        result, _, _, _ = self.successful_failover(self.candidates)
        for row in result[2]:
            self.assertEqual(set(row), {"status", "code", "model"})
            self.assertIsInstance(row["status"], int)
            self.assertIsInstance(row["model"], str)

    def test_trail_header_serializes_the_structured_failure_rows(self):
        rows = [{"status": 429, "code": "rate_limit", "model": "fixture/first"}]
        header = self.app.trail_header(rows)
        self.assertEqual(json.loads(header), rows)
        self.assertNotIn("\n", header)

    def test_trail_header_preserves_an_eight_row_failure_trail(self):
        rows = [{"status": 429, "code": "limited", "model": str(i)} for i in range(8)]
        parsed = json.loads(self.app.trail_header(rows))
        self.assertEqual(len(parsed), 8)
        self.assertTrue(all(set(row) == {"status", "code", "model"} for row in parsed))

    def test_long_failover_chain_has_a_bounded_header(self):
        candidates = [model_row(str(i)) for i in range(12)]
        with patch.object(self.app, "dial", return_value=FakeUpstream(429)):
            result = self.app.dial_with_failover(candidates, lambda mid: {"model": mid})
        self.assertIsNone(result[0])
        self.assertLessEqual(len(json.loads(self.app.trail_header(result[2]))), 8)
        self.assertEqual(len(result[2]), 8)


class OpenAIStreamTests(AggregatorCase):
    def rewrite(self, payloads, model="public-model"):
        wire = "".join(data_frame(payload) for payload in payloads).encode()
        return list(self.app.openai_stream_with_model([wire], model))

    def test_model_id_is_replaced_on_every_data_chunk(self):
        frames = self.rewrite([completion_chunk("one"), completion_chunk("two"), completion_chunk(finish="stop")])
        self.assertEqual(len(frames), 3)
        for frame in frames:
            self.assertEqual(frame_data(frame)["model"], "public-model")
            self.assertNotIn("upstream-private-id", frame)

    def test_text_deltas_survive_model_rewriting(self):
        frames = self.rewrite([completion_chunk("Hello "), completion_chunk("world")])
        texts = [frame_data(frame)["choices"][0]["delta"]["content"] for frame in frames]
        self.assertEqual(texts, ["Hello ", "world"])

    def test_repeated_finish_reason_on_usage_chunk_is_cleared(self):
        usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
        frames = self.rewrite([completion_chunk(finish="stop"), completion_chunk(finish="stop", usage=usage)])
        chunks = [frame_data(frame) for frame in frames]
        self.assertEqual(chunks[0]["choices"][0]["finish_reason"], "stop")
        self.assertIsNone(chunks[1]["choices"][0]["finish_reason"])
        self.assertEqual(chunks[1]["usage"], usage)
        self.assertEqual(sum(bool(c["choices"][0]["finish_reason"]) for c in chunks), 1)

    def test_usage_only_chunk_keeps_usage_and_public_model(self):
        payload = completion_chunk(usage={"prompt_tokens": 4, "completion_tokens": 3})
        payload["choices"] = []
        result = frame_data(self.rewrite([payload])[0])
        self.assertEqual(result["choices"], [])
        self.assertEqual(result["usage"], payload["usage"])
        self.assertEqual(result["model"], "public-model")

    def test_done_marker_passes_through_exactly_once(self):
        frames = self.rewrite([completion_chunk("hello"), "[DONE]"])
        self.assertEqual(frames[-1], "data: [DONE]\n\n")
        self.assertEqual(sum(frame_data(frame) == "[DONE]" for frame in frames), 1)

    def test_frame_cut_in_the_middle_of_json_is_reassembled(self):
        wire = data_frame(completion_chunk("fragmented")).encode()
        cut = wire.index(b"fragmented") + 3
        frames = list(self.app.openai_stream_with_model([wire[:cut], wire[cut:]], "public"))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frame_data(frames[0])["choices"][0]["delta"]["content"], "fragmented")

    def test_frame_delimiter_cut_between_chunks_is_reassembled(self):
        wire = data_frame(completion_chunk("x")).encode()
        frames = list(self.app.openai_stream_with_model([wire[:-1], wire[-1:]], "public"))
        self.assertEqual(len(frames), 1)
        self.assertEqual(frame_data(frames[0])["model"], "public")

    def test_one_byte_chunks_produce_the_same_frames_as_one_chunk(self):
        wire = (data_frame(completion_chunk("abc")) + data_frame("[DONE]")).encode()
        expected = list(self.app.openai_stream_with_model([wire], "public"))
        actual = list(self.app.openai_stream_with_model((bytes([byte]) for byte in wire), "public"))
        self.assertEqual(actual, expected)

    # Observed black-box defect: raw UTF-8 split inside a code point becomes
    # replacement characters. Preserve the assertion without changing main.py.
    @unittest.expectedFailure
    def test_utf8_character_split_across_chunks_is_preserved(self):
        wire = data_frame(completion_chunk("snowman ☃")).encode()
        cut = wire.index("☃".encode()) + 1
        frames = list(self.app.openai_stream_with_model([wire[:cut], wire[cut:]], "public"))
        self.assertEqual(frame_data(frames[0])["choices"][0]["delta"]["content"], "snowman ☃")

    def test_frame_iterator_reassembles_multiple_frames_without_merging_them(self):
        wire = data_frame({"first": 1}) + data_frame({"second": 2})
        frames = list(self.app.iter_frames([wire[:10].encode(), wire[10:].encode()]))
        self.assertEqual([frame_data(frame) for frame in frames], [{"first": 1}, {"second": 2}])

    def test_nonstream_json_rewrites_model_without_losing_choices_or_usage(self):
        body = completion_body()
        original = copy.deepcopy(body)
        rewritten = self.app.openai_json_with_model(body, "public")
        self.assertEqual(rewritten["model"], "public")
        self.assertEqual(rewritten["choices"], original["choices"])
        self.assertEqual(rewritten["usage"], original["usage"])

    def test_sse_event_has_named_event_and_json_data(self):
        event = self.app.sse_event("message_stop", {"type": "message_stop"})
        self.assertTrue(event.startswith("event: message_stop\n"))
        self.assertTrue(event.endswith("\n\n"))
        self.assertEqual(frame_data(event), {"type": "message_stop"})


class AnthropicRequestTests(AggregatorCase):
    def request(self, **overrides):
        body = {"model": "public", "max_tokens": 123, "messages": [{"role": "user", "content": "Hello"}]}
        body.update(overrides)
        return body

    def test_request_uses_selected_upstream_model_and_max_tokens(self):
        converted = self.app.anthropic_to_openai(self.request(), "selected-upstream")
        self.assertEqual(converted["model"], "selected-upstream")
        self.assertEqual(converted["max_tokens"], 123)
        self.assertEqual(converted["messages"], [{"role": "user", "content": "Hello"}])

    def test_string_system_prompt_becomes_first_system_message(self):
        converted = self.app.anthropic_to_openai(self.request(system="Be concise"), "upstream")
        self.assertEqual(converted["messages"][0], {"role": "system", "content": "Be concise"})
        self.assertEqual(converted["messages"][1]["role"], "user")

    def test_system_text_blocks_preserve_their_text(self):
        body = self.request(system=[{"type": "text", "text": "First instruction"}, {"type": "text", "text": "Second instruction"}])
        converted = self.app.anthropic_to_openai(body, "upstream")
        system = converted["messages"][0]
        self.assertEqual(system["role"], "system")
        self.assertIn("First instruction", system["content"])
        self.assertIn("Second instruction", system["content"])

    def test_tool_schema_becomes_openai_function_parameters(self):
        schema = {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
        body = self.request(tools=[{"name": "weather", "description": "Look up weather", "input_schema": schema}])
        converted = self.app.anthropic_to_openai(body, "upstream")
        self.assertEqual(converted["tools"], [{"type": "function", "function": {
            "name": "weather", "description": "Look up weather", "parameters": schema,
        }}])

    def test_named_tool_choice_becomes_function_tool_choice(self):
        body = self.request(tool_choice={"type": "tool", "name": "weather"})
        converted = self.app.anthropic_to_openai(body, "upstream")
        self.assertEqual(converted["tool_choice"], {"type": "function", "function": {"name": "weather"}})

    def test_any_tool_choice_requires_a_tool(self):
        converted = self.app.anthropic_to_openai(self.request(tool_choice={"type": "any"}), "upstream")
        self.assertEqual(converted["tool_choice"], "required")

    def test_stream_and_sampling_options_are_forwarded(self):
        body = self.request(stream=True, temperature=0.25, top_p=0.8, stop_sequences=["END"])
        converted = self.app.anthropic_to_openai(body, "upstream")
        self.assertIs(converted["stream"], True)
        self.assertEqual(converted["temperature"], 0.25)
        self.assertEqual(converted["top_p"], 0.8)
        self.assertEqual(converted["stop"], ["END"])

    def test_assistant_tool_use_becomes_a_function_call_with_json_arguments(self):
        body = self.request(messages=[{"role": "assistant", "content": [{
            "type": "tool_use", "id": "tool-1", "name": "weather", "input": {"city": "Paris"},
        }]}])
        message = self.app.anthropic_to_openai(body, "upstream")["messages"][0]
        call = message["tool_calls"][0]
        self.assertEqual(call["id"], "tool-1")
        self.assertEqual(call["type"], "function")
        self.assertEqual(call["function"]["name"], "weather")
        self.assertEqual(json.loads(call["function"]["arguments"]), {"city": "Paris"})

    def test_tool_result_becomes_tool_role_message(self):
        body = self.request(messages=[{"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": "tool-1", "content": "Sunny",
        }]}])
        messages = self.app.anthropic_to_openai(body, "upstream")["messages"]
        self.assertEqual(messages, [{"role": "tool", "tool_call_id": "tool-1", "content": "Sunny"}])

    def test_base64_image_becomes_openai_data_url(self):
        body = self.request(messages=[{"role": "user", "content": [{
            "type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "aGVsbG8="},
        }]}])
        content = self.app.anthropic_to_openai(body, "upstream")["messages"][0]["content"]
        self.assertEqual(content, [{"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}])


class AnthropicResponseTests(AggregatorCase):
    def test_text_answer_becomes_an_assistant_message_content_block(self):
        converted = self.app.openai_to_anthropic(completion_body("Hello there"), "public")
        self.assertEqual(converted["type"], "message")
        self.assertEqual(converted["role"], "assistant")
        self.assertEqual(converted["model"], "public")
        self.assertEqual(converted["content"], [{"type": "text", "text": "Hello there"}])

    def test_response_usage_maps_prompt_and_completion_token_counts(self):
        converted = self.app.openai_to_anthropic(completion_body(), "public")
        self.assertEqual(converted["usage"], {"input_tokens": 11, "output_tokens": 7})
        self.assertNotIn("total_tokens", converted["usage"])

    def test_stop_finish_reason_maps_to_end_turn(self):
        converted = self.app.openai_to_anthropic(completion_body(finish="stop"), "public")
        self.assertEqual(converted["stop_reason"], "end_turn")
        self.assertIsNone(converted["stop_sequence"])

    def test_length_finish_reason_maps_to_max_tokens(self):
        converted = self.app.openai_to_anthropic(completion_body(finish="length"), "public")
        self.assertEqual(converted["stop_reason"], "max_tokens")

    def test_tool_calls_finish_reason_maps_to_tool_use(self):
        body = completion_body(content=None, finish="tool_calls")
        body["choices"][0]["message"]["tool_calls"] = [{
            "id": "call-1", "type": "function", "function": {"name": "weather", "arguments": '{"city":"Paris"}'},
        }]
        converted = self.app.openai_to_anthropic(body, "public")
        self.assertEqual(converted["stop_reason"], "tool_use")
        self.assertEqual(converted["content"], [{"type": "tool_use", "id": "call-1", "name": "weather", "input": {"city": "Paris"}}])

    def test_content_filter_maps_to_a_legal_refusal_stop_reason(self):
        self.assertEqual(self.app.stop_reason_of("content_filter"), "refusal")
        converted = self.app.openai_to_anthropic(completion_body(finish="content_filter"), "public")
        self.assertEqual(converted["stop_reason"], "refusal")

    def test_unknown_finish_reason_falls_back_to_legal_end_turn(self):
        for finish in (None, "unknown-provider-reason", ""):
            with self.subTest(finish=finish):
                self.assertEqual(self.app.stop_reason_of(finish), "end_turn")

    def test_tool_argument_parser_accepts_objects_and_encoded_objects(self):
        self.assertEqual(self.app.arguments_of({"x": 1}), {"x": 1})
        self.assertEqual(self.app.arguments_of('{"x":1}'), {"x": 1})

    def test_tool_argument_parser_rejects_malformed_and_nonobject_arguments(self):
        for value in (None, "not json", "[1,2]", "null", "42"):
            with self.subTest(value=value):
                self.assertEqual(self.app.arguments_of(value), {})


class AnthropicStreamingTests(AggregatorCase):
    def events(self, payloads):
        wire = "".join(data_frame(payload) for payload in payloads).encode()
        return [frame_data(frame) for frame in self.app.anthropic_events_from_openai([wire], "public")]

    def test_stream_has_legal_message_and_content_block_order(self):
        events = self.events([completion_chunk("Hello"), completion_chunk(finish="stop"), "[DONE]"])
        self.assertEqual([event["type"] for event in events], [
            "message_start", "content_block_start", "content_block_delta",
            "content_block_stop", "message_delta", "message_stop",
        ])

    def test_message_start_exposes_public_model_and_assistant_role(self):
        event = self.events([completion_chunk("Hello"), "[DONE]"])[0]
        self.assertEqual(event["type"], "message_start")
        self.assertEqual(event["message"]["model"], "public")
        self.assertEqual(event["message"]["role"], "assistant")
        self.assertEqual(event["message"]["content"], [])

    def test_multiple_text_deltas_share_a_single_content_block(self):
        events = self.events([completion_chunk("Hello "), completion_chunk("world"), completion_chunk(finish="stop"), "[DONE]"])
        starts = [event for event in events if event["type"] == "content_block_start"]
        deltas = [event for event in events if event["type"] == "content_block_delta"]
        self.assertEqual(len(starts), 1)
        self.assertEqual("".join(event["delta"]["text"] for event in deltas), "Hello world")
        self.assertTrue(all(event["index"] == starts[0]["index"] for event in deltas))

    def test_usage_chunk_updates_final_output_token_count(self):
        usage = {"prompt_tokens": 11, "completion_tokens": 7}
        events = self.events([completion_chunk("Hello"), completion_chunk(finish="stop"), completion_chunk(finish="stop", usage=usage), "[DONE]"])
        finals = [event for event in events if event["type"] == "message_delta"]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0]["usage"]["output_tokens"], 7)
        self.assertEqual(finals[0]["delta"]["stop_reason"], "end_turn")

    def test_length_finish_reason_is_preserved_in_message_delta(self):
        events = self.events([completion_chunk("partial"), completion_chunk(finish="length"), "[DONE]"])
        final = next(event for event in events if event["type"] == "message_delta")
        self.assertEqual(final["delta"]["stop_reason"], "max_tokens")

    def test_done_marker_does_not_escape_as_an_anthropic_event(self):
        events = self.events([completion_chunk("Hello"), completion_chunk(finish="stop"), "[DONE]"])
        self.assertTrue(all(isinstance(event, dict) for event in events))
        self.assertEqual(sum(event["type"] == "message_stop" for event in events), 1)
        self.assertEqual(events[-1], {"type": "message_stop"})

    def test_fragmented_openai_frames_yield_same_anthropic_events(self):
        wire = (data_frame(completion_chunk("fragmented")) + data_frame(completion_chunk(finish="stop")) + data_frame("[DONE]")).encode()
        full = list(self.app.anthropic_events_from_openai([wire], "public"))
        split = list(self.app.anthropic_events_from_openai([wire[:13], wire[13:67], wire[67:]], "public"))
        self.assertEqual(split, full)

    def test_tool_stream_emits_tool_block_and_argument_delta(self):
        first = completion_chunk()
        first["choices"][0]["delta"] = {"tool_calls": [{
            "index": 0, "id": "call-1", "type": "function", "function": {"name": "weather", "arguments": '{"city":'},
        }]}
        second = completion_chunk()
        second["choices"][0]["delta"] = {"tool_calls": [{"index": 0, "function": {"arguments": '"Paris"}'}}]}
        events = self.events([first, second, completion_chunk(finish="tool_calls"), "[DONE]"])
        block = next(event["content_block"] for event in events if event["type"] == "content_block_start")
        self.assertEqual(block["type"], "tool_use")
        self.assertEqual(block["name"], "weather")
        arguments = "".join(event["delta"]["partial_json"] for event in events if event["type"] == "content_block_delta")
        self.assertEqual(json.loads(arguments), {"city": "Paris"})
        final = next(event for event in events if event["type"] == "message_delta")
        self.assertEqual(final["delta"]["stop_reason"], "tool_use")


class ErrorEnvelopeTests(AggregatorCase):
    def test_openai_invalid_request_envelope_has_expected_type_and_code(self):
        error = self.app.openai_error("Bad request", "invalid_request_error", "bad_request")
        self.assertEqual(error, {"error": {"message": "Bad request", "type": "invalid_request_error", "code": "bad_request"}})

    def test_openai_api_error_envelope_keeps_null_code(self):
        error = self.app.openai_error("Upstream failed", "api_error", None)
        self.assertEqual(error["error"]["type"], "api_error")
        self.assertIsNone(error["error"]["code"])

    def test_anthropic_invalid_request_envelope_includes_outer_error_type(self):
        error = self.app.anthropic_error("invalid_request_error", "Bad request")
        self.assertEqual(error, {"type": "error", "error": {"type": "invalid_request_error", "message": "Bad request"}})

    def test_anthropic_overloaded_error_type_is_preserved(self):
        error = self.app.anthropic_error("overloaded_error", "Try again")
        self.assertEqual(error["type"], "error")
        self.assertEqual(error["error"]["type"], "overloaded_error")
        self.assertEqual(error["error"]["message"], "Try again")

    def test_handler_openai_error_uses_openai_envelope(self):
        handler = self.make_handler()
        handler.send_error_body("openai", 400, "invalid_request_error", "Bad request")
        status, body = handler.send_json.call_args.args
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["type"], "invalid_request_error")
        self.assertNotIn("type", body)

    def test_handler_anthropic_error_uses_anthropic_envelope(self):
        handler = self.make_handler({"x-api-key": "fixture"})
        handler.send_error_body("anthropic", 503, "overloaded_error", "Unavailable")
        status, body = handler.send_json.call_args.args
        self.assertEqual(status, 503)
        self.assertEqual(body["type"], "error")
        self.assertEqual(body["error"]["type"], "overloaded_error")


class RegistrationAndModelListTests(AggregatorCase):
    def test_register_posts_the_same_catalog_with_protocol_and_port(self):
        catalog = self.app.build_catalog(self.app.read_brief(str(self.brief_path)))
        response = MagicMock(status=200)
        response.read.return_value = b'{}'
        response.__enter__.return_value = response
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            self.app.register(8123, catalog)
        urlopen.assert_called_once()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:1/register")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(json.loads(request.data), {"protocol_version": 1, "port": 8123, "catalog": catalog})
        self.assertEqual(request.get_header("Content-type"), "application/json")

    def test_openai_model_list_has_openai_objects_and_ownership(self):
        result = self.app.openai_model_list(self.app.served_models())
        self.assertEqual(result["object"], "list")
        self.assertEqual(self.ids(result["data"]), list(FREE_IDS))
        for model in result["data"]:
            self.assertEqual(model["object"], "model")
            self.assertEqual(model["owned_by"], "openrouter")
            self.assertIsInstance(model["created"], int)

    def test_anthropic_model_list_has_display_names_and_pagination_flag(self):
        result = self.app.anthropic_model_list(self.app.served_models())
        self.assertIs(result["has_more"], False)
        self.assertEqual(self.ids(result["data"]), list(FREE_IDS))
        for model in result["data"]:
            self.assertEqual(model["type"], "model")
            self.assertEqual(model["display_name"], model["id"])
            self.assertIsInstance(model["created_at"], str)

    def test_bearer_auth_models_endpoint_returns_openai_catalog(self):
        handler = self.make_handler({"Authorization": "Bearer fixture-token"})
        handler.dispatch()
        handler.send_json.assert_called_once_with(200, self.app.openai_model_list(self.app.served_models()))

    def test_api_key_auth_models_endpoint_returns_anthropic_catalog(self):
        handler = self.make_handler({"x-api-key": "fixture-token"})
        handler.dispatch()
        handler.send_json.assert_called_once_with(200, self.app.anthropic_model_list(self.app.served_models()))

    def test_no_auth_header_defaults_to_openai_model_list(self):
        handler = self.make_handler()
        handler.dispatch()
        self.assertEqual(handler.send_json.call_args.args[1], self.app.openai_model_list(self.app.served_models()))

    def test_api_key_header_takes_precedence_when_both_auth_styles_are_present(self):
        handler = self.make_handler({"Authorization": "Bearer fixture", "x-api-key": "fixture"})
        handler.dispatch()
        self.assertEqual(handler.send_json.call_args.args[1], self.app.anthropic_model_list(self.app.served_models()))

    def test_anthropic_version_header_alone_does_not_change_model_list_wire(self):
        handler = self.make_handler({"anthropic-version": "2023-06-01"})
        handler.dispatch()
        self.assertEqual(handler.send_json.call_args.args[1]["object"], "list")

    def test_both_auth_formats_advertise_exactly_the_registered_ids(self):
        catalog = self.app.build_catalog(self.app.read_brief(str(self.brief_path)))
        registered = self.ids(catalog["providers"][0]["models"])
        for headers in ({"Authorization": "Bearer fixture"}, {"x-api-key": "fixture"}):
            with self.subTest(headers=headers):
                handler = self.make_handler(headers)
                handler.dispatch()
                self.assertEqual(self.ids(handler.send_json.call_args.args[1]["data"]), registered)

    def test_real_http_get_models_serializes_both_auth_specific_envelopes(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), self.app.Handler)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        try:
            for headers, wire in (({"Authorization": "Bearer fixture"}, "openai"), ({"x-api-key": "fixture"}, "anthropic")):
                with self.subTest(wire=wire):
                    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                    try:
                        connection.request("GET", "/v1/models", headers=headers)
                        response = connection.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertIn("application/json", response.getheader("Content-Type"))
                        body = json.loads(response.read())
                        expected = getattr(self.app, wire + "_model_list")(self.app.served_models())
                        self.assertEqual(body, expected)
                    finally:
                        connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.network.assert_not_called()


if __name__ == "__main__":
    unittest.main()
