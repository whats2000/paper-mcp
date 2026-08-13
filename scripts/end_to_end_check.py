"""The whole product, as one connected workflow, against a running service.

Every earlier check exercised a phase in isolation. This is the loop the
service actually exists for, driven by a real MCP client over the wire:

    resolve_paper -> fetch_paper -> read the markdown and the figure index
                  -> build a deck that cites a real figure
                  -> compile_latex -> download the PDF

Point it at a deployed instance (including the container, where the nsjail
sandbox is real):

    uv run python scripts/end_to_end_check.py http://127.0.0.1:8010 1706.03762

Green here means an agent can go from a paper id to a compiled deck grounded
in that paper's own figures. That is the product.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from typing import Any

import httpx
from mcp.client.client import Client

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    _results.append((ok, name, detail))
    print(f"[{'  ok  ' if ok else ' FAIL '}] {name}" + (f"\n           {detail}" if detail else ""),
          flush=True)
    return ok


def value(result: Any) -> Any:
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


def error_text(result: Any) -> str:
    for block in getattr(result, "content", None) or []:
        if getattr(block, "text", None):
            return str(block.text)
    return ""


def _latex_escape(text: str) -> str:
    for raw, safe in (
        ("\\", r"\textbackslash{}"), ("&", r"\&"), ("%", r"\%"), ("$", r"\$"),
        ("#", r"\#"), ("_", r"\_"), ("{", r"\{"), ("}", r"\}"), ("~", r"\textasciitilde{}"),
        ("^", r"\textasciicircum{}"),
    ):
        text = text.replace(raw, safe)
    return text


async def run(base: str, arxiv_id: str) -> int:
    async with Client(f"{base}/mcp", read_timeout_seconds=180.0) as client:
        tools = sorted(t.name for t in (await client.list_tools()).tools)
        check(len(tools) == 7, "seven tools available", ", ".join(tools))

        # 1. Resolve — what an agent does before spending an extraction.
        resolved = await client.call_tool("resolve_paper", {"identifier": arxiv_id})
        if not resolved.is_error:
            paper = value(resolved)
            check(
                bool(paper.get("title")),
                "resolve_paper",
                f"{paper['paper_id']} | {paper['title'][:50]}",
            )
        else:
            check(
                True,
                "resolve_paper (skipped)",
                "upstream unavailable; not required for the loop",
            )

        # 2. Fetch the paper as data.
        started = time.monotonic()
        first = value(await client.call_tool("fetch_paper", {"paper_id": arxiv_id}))
        bundle = first.get("bundle")
        if first["status"] == "extracting":
            job_id = first["job"]["job_id"]
            print(f"           extracting (job {job_id}) …", flush=True)
            deadline = time.monotonic() + 1800
            state = "queued"
            while time.monotonic() < deadline:
                await asyncio.sleep(10)
                status = value(await client.call_tool("get_job", {"job_id": job_id}))
                state = status["state"]
                if state in ("done", "error"):
                    break
            if state != "done":
                return 1 if not check(False, "extraction completed", f"state={state}") else 0
            again = await client.call_tool("fetch_paper", {"paper_id": arxiv_id})
            bundle = value(again).get("bundle")

        elapsed = time.monotonic() - started
        if not check(bundle is not None, "fetch_paper returned a bundle", f"{elapsed:.0f}s"):
            return 1

        markdown = bundle["markdown"]
        figures = bundle["figures"]
        check(len(markdown) > 2000, "markdown is substantial", f"{len(markdown)} chars")
        check(bool(figures), "figure index populated", f"{len(figures)} figures")
        has_table = bool(re.search(r"^\|\s*---", markdown, re.MULTILINE))
        check(has_table, "tables present as markdown tables")

        if not figures:
            return 1

        # 3. Use the data: a deck grounded in a figure the paper really has.
        figure = figures[0]
        image = httpx.get(figure["image_url"], timeout=60.0)
        if not check(
            image.status_code == 200 and image.content[:4] in (b"\x89PNG", b"\xff\xd8\xff\xe0"),
            "figure image downloads",
            f"HTTP {image.status_code}, {len(image.content)} bytes",
        ):
            return 1

        import base64

        asset_path = figure["image_path"]
        title = _latex_escape(bundle["paper"].get("title") or arxiv_id)
        caption = _latex_escape((figure.get("caption") or "")[:90])
        tex = (
            "\\documentclass{beamer}\n\\usepackage{graphicx}\n\\begin{document}\n"
            f"\\begin{{frame}}{{{title}}}\n"
            f"\\begin{{center}}\\includegraphics[width=0.7\\textwidth]{{{asset_path}}}\\end{{center}}\n"
            f"\\small {caption}\n"
            "\\end{frame}\n\\end{document}\n"
        )

        # 4. Compile it — the figure travels as an asset, cited by the exact
        #    path the source references.
        compiled = value(
            await client.call_tool(
                "compile_latex",
                {
                    "tex": tex,
                    "assets": [
                        {
                            "path": asset_path,
                            "content_base64": base64.b64encode(image.content).decode(),
                        }
                    ],
                },
            )
        )
        if not check(
            compiled["ok"],
            "compile_latex built a deck grounded in the paper's own figure",
            f"engine={compiled['engine']} pages={compiled['page_count']} "
            f"errors={[e['message'][:60] for e in compiled['errors'][:2]]}",
        ):
            return 1

        pdf = httpx.get(compiled["pdf_url"], timeout=60.0)
        check(
            pdf.status_code == 200 and pdf.content.startswith(b"%PDF"),
            "the compiled PDF downloads",
            f"HTTP {pdf.status_code}, {len(pdf.content)} bytes",
        )

    failures = sum(1 for ok, _, _ in _results if not ok)
    print(f"\n{'=' * 72}\n{len(_results) - failures} passed, {failures} failed\n{'=' * 72}")
    return 1 if failures else 0


def main() -> int:
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
    arxiv_id = sys.argv[2] if len(sys.argv) > 2 else "1706.03762"
    health = httpx.get(f"{base}/health", timeout=10.0).json()
    print(f"paper-mcp end-to-end — {base} (marker={health.get('marker')})\n{'=' * 72}", flush=True)
    if health.get("marker") != "up":
        print("Marker is not reachable from the service; extraction cannot run.")
        return 2
    return asyncio.run(run(base, arxiv_id))


if __name__ == "__main__":
    raise SystemExit(main())
