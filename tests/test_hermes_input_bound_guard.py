"""Pinned Hermes normalizers, synthetic contexts only; no provider/network/session store.

Normalizer excerpts: NousResearch/hermes-agent commit
5fc308a70719a83cccdbba4c0e39c23f5a8239d5, gateway/platforms/api_server.py.
Keep them exact: production checks their source hashes before changing the bound.
"""

import asyncio
import importlib.util
import json
import runpy
import sys
import types
from pathlib import Path

import httpx
import pytest
from agent_orchestrator.hermes_client import HermesClient, HermesEndpoint

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "ops/hermes/run_guarded_gateway.py"

PINNED_NORMALIZERS = (
    'def _normalize_chat_content(\n'
    '    content: Any, *, _max_depth: int = 10, _depth: int = 0,\n'
    ') -> str:\n'
    '    """Normalize OpenAI chat message content into a plain text st'
    'ring.\n'
    '\n'
    '    Some clients (Open WebUI, LobeChat, etc.) send content as an '
    'array of\n'
    '    typed parts instead of a plain string::\n'
    '\n'
    '        [{"type": "text", "text": "hello"}, {"type": "input_text"'
    ', "text": "..."}]\n'
    '\n'
    '    This function flattens those into a single string so the agen'
    't pipeline\n'
    "    (which expects strings) doesn't choke.\n"
    '\n'
    '    Defensive limits prevent abuse: recursion depth, list size, a'
    'nd output\n'
    '    length are all bounded.\n'
    '    """\n'
    '    if _depth > _max_depth:\n'
    '        return ""\n'
    '    if content is None:\n'
    '        return ""\n'
    '    if isinstance(content, str):\n'
    '        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(conten'
    't) > MAX_NORMALIZED_TEXT_LENGTH else content\n'
    '\n'
    '    if isinstance(content, list):\n'
    '        parts: List[str] = []\n'
    '        total_len = 0\n'
    '        items = content[:MAX_CONTENT_LIST_SIZE] if len(content) >'
    ' MAX_CONTENT_LIST_SIZE else content\n'
    '        for item in items:\n'
    '            if isinstance(item, str):\n'
    '                if item:\n'
    '                    part = item[:MAX_NORMALIZED_TEXT_LENGTH]\n'
    '                    parts.append(part)\n'
    '                    total_len += len(part)\n'
    '            elif isinstance(item, dict):\n'
    '                item_type = str(item.get("type") or "").strip().l'
    'ower()\n'
    '                if item_type in {"text", "input_text", "output_te'
    'xt"}:\n'
    '                    text = item.get("text", "")\n'
    '                    if text:\n'
    '                        try:\n'
    '                            part = str(text)[:MAX_NORMALIZED_TEXT'
    '_LENGTH]\n'
    '                            parts.append(part)\n'
    '                            total_len += len(part)\n'
    '                        except Exception:\n'
    '                            pass\n'
    '                # Silently skip image_url / other non-text parts\n'
    '            elif isinstance(item, list):\n'
    '                nested = _normalize_chat_content(item, _max_depth'
    '=_max_depth, _depth=_depth + 1)\n'
    '                if nested:\n'
    '                    parts.append(nested)\n'
    '                    total_len += len(nested)\n'
    '            # Check accumulated size\n'
    '            if total_len >= MAX_NORMALIZED_TEXT_LENGTH:\n'
    '                break\n'
    '        result = "\\n".join(parts)\n'
    '        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result)'
    ' > MAX_NORMALIZED_TEXT_LENGTH else result\n'
    '\n'
    '    # Fallback for unexpected types (int, float, bool, etc.)\n'
    '    try:\n'
    '        result = str(content)\n'
    '        return result[:MAX_NORMALIZED_TEXT_LENGTH] if len(result)'
    ' > MAX_NORMALIZED_TEXT_LENGTH else result\n'
    '    except Exception:\n'
    '        return ""\n'
    'def _normalize_multimodal_content(content: Any) -> Any:\n'
    '    """Validate and normalize multimodal content for the API serv'
    'er.\n'
    '\n'
    '    Returns a plain string when the content is text-only, or a li'
    'st of\n'
    '    ``{"type": "text"|"image_url", ...}`` parts when images are p'
    'resent.\n'
    '    The output shape is the native OpenAI Chat Completions vision'
    ' format,\n'
    '    which the agent pipeline accepts verbatim (OpenAI-wire provid'
    'ers) or\n'
    '    converts (``_preprocess_anthropic_content`` for Anthropic).\n'
    '\n'
    '    Raises ``ValueError`` with an OpenAI-style code on invalid in'
    'put:\n'
    '      * ``unsupported_content_type`` — file/input_file/file_id pa'
    'rts, or\n'
    '        non-image ``data:`` URLs.\n'
    '      * ``invalid_image_url`` — missing URL or unsupported scheme'
    '.\n'
    '      * ``invalid_content_part`` — malformed text/image objects.\n'
    '\n'
    '    Callers translate the ValueError into a 400 response.\n'
    '    """\n'
    '    # Scalar passthrough mirrors ``_normalize_chat_content``.\n'
    '    if content is None:\n'
    '        return ""\n'
    '    if isinstance(content, str):\n'
    '        return content[:MAX_NORMALIZED_TEXT_LENGTH] if len(conten'
    't) > MAX_NORMALIZED_TEXT_LENGTH else content\n'
    '    if not isinstance(content, list):\n'
    "        # Mirror the legacy text-normalizer's fallback so callers"
    ' that\n'
    '        # pre-existed image support still get a string back.\n'
    '        return _normalize_chat_content(content)\n'
    '\n'
    '    items = content[:MAX_CONTENT_LIST_SIZE] if len(content) > MAX'
    '_CONTENT_LIST_SIZE else content\n'
    '    normalized_parts: List[Dict[str, Any]] = []\n'
    '    text_accum_len = 0\n'
    '\n'
    '    for part in items:\n'
    '        if isinstance(part, str):\n'
    '            if part:\n'
    '                trimmed = part[:MAX_NORMALIZED_TEXT_LENGTH]\n'
    '                normalized_parts.append({"type": "text", "text": '
    'trimmed})\n'
    '                text_accum_len += len(trimmed)\n'
    '            continue\n'
    '\n'
    '        if not isinstance(part, dict):\n'
    '            # Ignore unknown scalars for forward compatibility wi'
    'th future\n'
    '            # Responses API additions (e.g. ``refusal``).  The sa'
    'me policy\n'
    '            # the text normalizer applies.\n'
    '            continue\n'
    '\n'
    '        raw_type = part.get("type")\n'
    '        part_type = str(raw_type or "").strip().lower()\n'
    '\n'
    '        if part_type in _TEXT_PART_TYPES:\n'
    '            text = part.get("text")\n'
    '            if text is None:\n'
    '                continue\n'
    '            if not isinstance(text, str):\n'
    '                text = str(text)\n'
    '            if text:\n'
    '                trimmed = text[:MAX_NORMALIZED_TEXT_LENGTH]\n'
    '                normalized_parts.append({"type": "text", "text": '
    'trimmed})\n'
    '                text_accum_len += len(trimmed)\n'
    '            continue\n'
    '\n'
    '        if part_type in _IMAGE_PART_TYPES:\n'
    '            detail = part.get("detail")\n'
    '            image_ref = part.get("image_url")\n'
    '            # OpenAI Responses sends ``input_image`` with a top-l'
    'evel\n'
    '            # ``image_url`` string; Chat Completions sends ``imag'
    'e_url`` as\n'
    '            # ``{"url": "...", "detail": "..."}``.  Support both.'
    '\n'
    '            if isinstance(image_ref, dict):\n'
    '                url_value = image_ref.get("url")\n'
    '                detail = image_ref.get("detail", detail)\n'
    '            else:\n'
    '                url_value = image_ref\n'
    '            if not isinstance(url_value, str) or not url_value.st'
    'rip():\n'
    '                raise ValueError("invalid_image_url:Image parts m'
    'ust include a non-empty image URL.")\n'
    '            url_value = url_value.strip()\n'
    '            lowered = url_value.lower()\n'
    '            if lowered.startswith("data:"):\n'
    '                if not lowered.startswith("data:image/") or "," n'
    'ot in url_value:\n'
    '                    raise ValueError(\n'
    '                        "unsupported_content_type:Only image data'
    ' URLs are supported. "\n'
    '                        "Non-image data payloads are not supporte'
    'd."\n'
    '                    )\n'
    '            elif not (lowered.startswith("http://") or lowered.st'
    'artswith("https://")):\n'
    '                raise ValueError(\n'
    '                    "invalid_image_url:Image inputs must use http'
    '(s) URLs or data:image/... URLs."\n'
    '                )\n'
    '            image_part: Dict[str, Any] = {"type": "image_url", "i'
    'mage_url": {"url": url_value}}\n'
    '            if detail is not None:\n'
    '                if not isinstance(detail, str) or not detail.stri'
    'p():\n'
    '                    raise ValueError("invalid_content_part:Image '
    'detail must be a non-empty string when provided.")\n'
    '                image_part["image_url"]["detail"] = detail.strip('
    ')\n'
    '            normalized_parts.append(image_part)\n'
    '            continue\n'
    '\n'
    '        if part_type in _FILE_PART_TYPES:\n'
    '            raise ValueError(\n'
    '                "unsupported_content_type:Inline image inputs are'
    ' supported, "\n'
    '                "but uploaded files and document inputs are not s'
    'upported on this endpoint."\n'
    '            )\n'
    '\n'
    '        # Unknown part type — reject explicitly so clients get a '
    'clear error\n'
    '        # instead of a silently dropped turn.\n'
    '        raise ValueError(\n'
    '            f"unsupported_content_type:Unsupported content part t'
    'ype {raw_type!r}. "\n'
    '            "Only text and image_url/input_image parts are suppor'
    'ted."\n'
    '        )\n'
    '\n'
    '    if not normalized_parts:\n'
    '        return ""\n'
    '\n'
    '    # Text-only: collapse to a plain string so downstream logging'
    '/trajectory\n'
    '    # code sees the native shape and prompt caching on text-only '
    'turns is\n'
    '    # unaffected.\n'
    '    if all(p.get("type") == "text" for p in normalized_parts):\n'
    '        return "\\n".join(p["text"] for p in normalized_parts if p'
    '.get("text"))\n'
    '\n'
    '    return normalized_parts\n'
)


@pytest.fixture
def api_module(tmp_path, monkeypatch):
    path = tmp_path / "pinned_api_server.py"
    path.write_text(
        "from typing import Any, Dict, List\n"
        "MAX_NORMALIZED_TEXT_LENGTH = 65536\nMAX_CONTENT_LIST_SIZE = 1000\n"
        "_TEXT_PART_TYPES = frozenset({'text', 'input_text', 'output_text'})\n"
        "_IMAGE_PART_TYPES = frozenset({'image_url', 'input_image'})\n"
        "_FILE_PART_TYPES = frozenset({'file', 'input_file'})\n"
        + PINNED_NORMALIZERS
    )
    name = "gateway.platforms.api_server"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    gateway = types.ModuleType("gateway")
    platforms = types.ModuleType("gateway.platforms")
    gateway.platforms = platforms
    platforms.api_server = module
    for key, value in (("gateway", gateway), ("gateway.platforms", platforms), (name, module)):
        monkeypatch.setitem(sys.modules, key, value)
    return module


def guard():
    return runpy.run_path(str(GUARD))["install_input_bound_guard"]


@pytest.mark.parametrize("length", [65536, 65537, 81555, 120000])
def test_complete_context_survives_both_pinned_normalizers(api_module, length):
    sentinel = "FACTS_AND_CLAIMS_AT_END"
    payload = "x" * (length - len(sentinel)) + sentinel
    guard()()
    assert api_module._normalize_chat_content(payload) == payload
    assert api_module._normalize_multimodal_content(payload) == payload


def test_old_pinned_normalizer_actually_loses_the_tail_before_guard(api_module):
    payload = json.dumps({"evidence": "x" * 66000, "facts": {"amount": 10000}})
    assert api_module._normalize_multimodal_content(payload) == payload[:65536]
    with pytest.raises(json.JSONDecodeError):
        json.loads(api_module._normalize_multimodal_content(payload))
    guard()()
    decoded = json.loads(api_module._normalize_multimodal_content(payload))
    assert decoded["facts"] == {"amount": 10000}


@pytest.mark.parametrize("drift", ["constant", "chat-source", "multimodal-source"])
def test_unreviewed_normalizer_drift_refuses_start_before_mutation(api_module, monkeypatch, drift):
    install = guard()
    if drift == "constant":
        api_module.MAX_NORMALIZED_TEXT_LENGTH = 65535
    else:
        target = ("_normalize_chat_content" if drift == "chat-source"
                  else "_normalize_multimodal_content")
        original = getattr(api_module, target)
        real = install.__globals__["inspect"].getsource
        monkeypatch.setattr(install.__globals__["inspect"], "getsource", lambda value:
                            real(value) + "# SYNTHETIC_PRIVATE_DETAIL\n"
                            if value is original else real(value))
    before = api_module.MAX_NORMALIZED_TEXT_LENGTH
    with pytest.raises(SystemExit) as caught:
        install()
    assert str(caught.value) == "Hermes input-bound guard mismatch; refusing to start"
    assert before == api_module.MAX_NORMALIZED_TEXT_LENGTH
    assert caught.value.__suppress_context__


def test_guard_only_changes_the_shared_bound_in_memory(api_module):
    chat = api_module._normalize_chat_content
    multimodal = api_module._normalize_multimodal_content
    original_file = Path(api_module.__file__).read_bytes()
    guard()()
    assert api_module.MAX_NORMALIZED_TEXT_LENGTH == 120000
    assert api_module._normalize_chat_content is chat
    assert api_module._normalize_multimodal_content is multimodal
    assert Path(api_module.__file__).read_bytes() == original_file
    assert chat("x" * 120001) == "x" * 120000
    assert multimodal("x" * 120001) == "x" * 120000


def test_application_rejects_over_limit_context_before_transport():
    calls = []

    async def scenario():
        def handler(request):
            calls.append(request)
            return httpx.Response(200, json={})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            hermes = HermesClient(
                HermesEndpoint("http://synthetic.invalid", "synthetic"), client=client,
            )
            with pytest.raises(ValueError, match="bounded context"):
                await hermes.complete_json(system="synthetic", user="x" * 120001)
    asyncio.run(scenario())
    assert calls == []


def test_cli_runs_only_after_the_real_input_guard_is_installed(api_module, monkeypatch):
    namespace = runpy.run_path(str(GUARD))
    globals_ = namespace["main"].__globals__
    called = []
    monkeypatch.setitem(globals_, "install_completion_guard", lambda: called.append("completion"))

    def run_cli(path, *, run_name):
        assert api_module.MAX_NORMALIZED_TEXT_LENGTH == 120000
        assert called == ["completion"]
        called.append("cli")
    monkeypatch.setattr(globals_["runpy"], "run_path", run_cli)
    monkeypatch.setattr(sys, "argv", [str(GUARD)])
    namespace["main"]()
    assert called == ["completion", "cli"]


def test_input_drift_prevents_cli_and_other_runtime_patch(api_module, monkeypatch):
    namespace = runpy.run_path(str(GUARD))
    globals_ = namespace["main"].__globals__
    api_module.MAX_NORMALIZED_TEXT_LENGTH = 10
    monkeypatch.setitem(globals_, "install_completion_guard", lambda: pytest.fail("must not patch"))
    monkeypatch.setattr(globals_["runpy"], "run_path", lambda *a, **kw: pytest.fail("must not run"))
    monkeypatch.setattr(sys, "argv", [str(GUARD)])
    with pytest.raises(SystemExit, match="input-bound guard mismatch"):
        namespace["main"]()
