# paper-mcp — the extraction service.
#
# Deliberately plain. Until v1.0 this image carried an nsjail builder stage and
# five TeX Live package sets, because the service compiled caller-supplied
# LaTeX and had to isolate it. With compilation out of scope there is nothing
# here that executes untrusted input: the service parses arguments, talks to
# Marker over the compose network, and writes derived files to its artifact
# cache. So the jail, the TeX distribution, and the `seccomp=unconfined` that
# nsjail's namespace creation required are all gone.
#
# The untrusted input is a caller's PDF, and it is handled in the Marker
# container, which is where the containment now lives (SRS NFR-02).
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PAPER_MCP_HOST=0.0.0.0 \
    PAPER_MCP_PORT=8000 \
    PAPER_MCP_ARTIFACT_ROOT=/app/artifacts

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
# Skills are served as MCP prompts, so they ship with the image.
COPY skills ./skills
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system .

# The artifact cache. Mounted as a volume in compose so bundles survive a
# container replacement — re-extracting a paper costs GPU minutes.
RUN mkdir -p /app/artifacts

EXPOSE 8000
CMD ["paper-mcp"]
