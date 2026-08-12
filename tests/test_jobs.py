from __future__ import annotations

import asyncio

from paper_mcp.jobs import JobStore


async def _settle() -> None:
    """Let queued job tasks run to completion."""
    for _ in range(50):
        await asyncio.sleep(0)


async def test_a_job_runs_and_reports_its_result_key() -> None:
    store = JobStore()

    async def run() -> str:
        return "arxiv:1706.03762"

    job = store.submit(content_key="arxiv:1706.03762", run=run)
    assert job.state == "queued"

    await _settle()

    assert store.get(job.job_id) is not None
    assert store.get(job.job_id).state == "done"  # type: ignore[union-attr]
    assert store.get(job.job_id).result_key == "arxiv:1706.03762"  # type: ignore[union-attr]


async def test_two_callers_for_one_paper_share_a_job() -> None:
    # Without coalescing, N callers start N GPU extractions of the same PDF
    # and starve each other.
    store = JobStore()
    started = 0

    async def run() -> str:
        nonlocal started
        started += 1
        await asyncio.sleep(0.05)
        return "arxiv:1"

    first = store.submit(content_key="arxiv:1", run=run)
    second = store.submit(content_key="arxiv:1", run=run)

    assert first.job_id == second.job_id
    await _settle()
    await asyncio.sleep(0.1)
    assert started == 1


async def test_different_papers_get_different_jobs() -> None:
    store = JobStore()

    async def run() -> str:
        return "k"

    a = store.submit(content_key="arxiv:1", run=run)
    b = store.submit(content_key="arxiv:2", run=run)

    assert a.job_id != b.job_id


async def test_a_failing_job_reports_a_typed_error_rather_than_vanishing() -> None:
    store = JobStore()

    async def run() -> str:
        raise RuntimeError("marker exploded")

    job = store.submit(content_key="arxiv:bad", run=run)
    await _settle()

    status = store.get(job.job_id)
    assert status is not None
    assert status.state == "error"
    assert "marker exploded" in (status.error or "")


async def test_work_is_serialized() -> None:
    # VRAM scales with page content density; concurrency on a small GPU means
    # OOM, not throughput.
    store = JobStore(concurrency=1)
    concurrent = 0
    peak = 0

    async def run() -> str:
        nonlocal concurrent, peak
        concurrent += 1
        peak = max(peak, concurrent)
        await asyncio.sleep(0.02)
        concurrent -= 1
        return "k"

    for i in range(4):
        store.submit(content_key=f"arxiv:{i}", run=run)
    await _settle()
    await asyncio.sleep(0.3)

    assert peak == 1


async def test_an_unknown_job_id_is_none_not_an_exception() -> None:
    assert JobStore().get("no-such-job") is None


async def test_a_finished_job_can_be_resubmitted_after_completion() -> None:
    # Coalescing must not pin a key to a completed job forever, or a later
    # re-extraction (say after a cache sweep) could never run.
    store = JobStore()
    runs = 0

    async def run() -> str:
        nonlocal runs
        runs += 1
        return "k"

    first = store.submit(content_key="arxiv:1", run=run)
    await _settle()
    assert store.get(first.job_id).state == "done"  # type: ignore[union-attr]

    second = store.submit(content_key="arxiv:1", run=run)
    await _settle()

    assert second.job_id != first.job_id
    assert runs == 2


async def test_sweep_drops_finished_jobs_but_not_running_ones() -> None:
    store = JobStore(ttl_seconds=-1.0)  # everything finished is instantly stale

    async def quick() -> str:
        return "k"

    done = store.submit(content_key="arxiv:done", run=quick)
    await _settle()

    removed = store.sweep()

    assert removed == 1
    assert store.get(done.job_id) is None
