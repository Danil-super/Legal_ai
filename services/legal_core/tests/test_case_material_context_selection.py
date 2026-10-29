"""Deterministic excerpts represent every opted-in file within the shared budget."""

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from legal_core.case_material_context import (
    CaseMaterialSnapshot, ParsedCaseMaterial, parse_case_material_locally,
    select_case_material_fragments,
)
from legal_core.contracts import FactKey


def parsed(index, text):
    return ParsedCaseMaterial(CaseMaterialSnapshot(
        material_id=uuid4(), display_name=f"Материал {index}", mime_type="text/plain",
        raw_sha256=hashlib.sha256(text.encode()).hexdigest(), raw_size_bytes=len(text.encode()),
        raw_object_key="unused-local-selection", expires_at=datetime.now(UTC) + timedelta(days=1),
    ), text)


def test_all_ten_files_are_represented_before_additional_relevant_excerpts():
    materials = [parsed(i, "Описание " * 230 + "\n\nГарантия: синтетический текст " * 80)
                 for i in range(1, 11)]
    result = select_case_material_fragments(materials, {
        FactKey.EVENT_SUMMARY: "Гарантия на синтетическую услугу",
    })
    assert len(result) == 12
    assert {item.material_id for item in result[:10]} == {
        item.snapshot.material_id for item in materials
    }
    assert all(0 < len(item.text) <= 1200 for item in result)
    assert all(item.extraction_limitations for item in result)
    assert result == select_case_material_fragments(materials, {
        FactKey.EVENT_SUMMARY: "Гарантия на синтетическую услугу",
    })


def test_local_parser_accepts_only_bounded_anonymised_text():
    result = asyncio.run(parse_case_material_locally(
        b"Synthetic anonymised appointment material.", mime_type="text/plain",
    ))
    assert result == "Synthetic anonymised appointment material."
    with pytest.raises(ValueError):
        asyncio.run(parse_case_material_locally(
            b"person@example.test", mime_type="text/plain",
        ))


def test_local_parser_bounds_its_own_output_before_writing_it():
    # The existing 200k-character input cap permits this text, but its escaped
    # JSON exceeds the separate local-worker output cap. No partial text escapes.
    with pytest.raises(ValueError):
        asyncio.run(parse_case_material_locally(b"\x01" * 200000, mime_type="text/plain"))


def test_local_worker_overflow_stops_before_waiting_for_eof(monkeypatch):
    import sys
    from legal_core import case_material_context as module

    actual_spawn = asyncio.create_subprocess_exec
    processes = []

    async def replacement(*args, **kwargs):
        process = await actual_spawn(sys.executable, "-c", (
            "import sys,time; sys.stdout.buffer.write(b'x'*1000001); "
            "sys.stdout.buffer.flush(); time.sleep(60)"
        ), **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", replacement)

    async def scenario():
        async with asyncio.timeout(2):
            with pytest.raises(ValueError, match="output"):
                await module.parse_case_material_locally(
                    b"Synthetic material", mime_type="text/plain",
                )
        assert processes[0].returncode is not None

    asyncio.run(scenario())


def test_cancelled_worker_kills_native_descendant_and_inherits_no_keys(monkeypatch, tmp_path):
    import os
    import sys
    from legal_core import case_material_context as module

    actual_spawn = asyncio.create_subprocess_exec
    processes = []
    pid_path = tmp_path / "synthetic-child.pid"
    monkeypatch.setenv("SYNTHETIC_PROVIDER_SECRET", "must-not-reach-worker")

    async def replacement(*args, **kwargs):
        assert "SYNTHETIC_PROVIDER_SECRET" not in kwargs["env"]
        assert not any("POSTGRES" in key or "API_KEY" in key for key in kwargs["env"])
        script = (
            "import pathlib,subprocess,sys,time; "
            "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            f"pathlib.Path({str(pid_path)!r}).write_text(str(child.pid)); time.sleep(60)"
        )
        process = await actual_spawn(sys.executable, "-c", script, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", replacement)

    async def scenario():
        task = asyncio.create_task(module.parse_case_material_locally(
            b"Synthetic material", mime_type="text/plain",
        ))
        async with asyncio.timeout(3):
            while not pid_path.exists():
                await asyncio.sleep(0.01)
        child_pid = int(pid_path.read_text())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert processes[0].returncode is not None  # direct worker was reaped
        async with asyncio.timeout(3):
            while os.path.exists(f"/proc/{child_pid}/stat"):
                # A killed grandchild can briefly await PID1 reaping; it cannot run.
                try:
                    with open(f"/proc/{child_pid}/stat") as status:
                        state = status.read().split()[2]
                except FileNotFoundError:
                    break  # PID1 reaped it between the existence check and read.
                if state == "Z":
                    break
                await asyncio.sleep(0.01)

    asyncio.run(scenario())
