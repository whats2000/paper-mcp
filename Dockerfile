# paper-mcp — the service, TeX Live, and nsjail.
#
# nsjail is the layer that makes compiling untrusted LaTeX defensible, and it
# is Linux-only, so this image is where the sandbox actually exists. On a host
# without it the service refuses to compile rather than running a stranger's
# program unisolated (see paper_mcp.sandbox).

# ── nsjail ────────────────────────────────────────────────────────────────
# Built from source: it is not in Debian stable's archive, and vendoring a
# random binary for a security boundary would be worse than compiling it.
FROM debian:bookworm-slim AS nsjail-build
RUN apt-get update && apt-get install -y --no-install-recommends \
        autoconf bison flex gcc g++ git libprotobuf-dev libnl-route-3-dev \
        libtool make pkg-config protobuf-compiler ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch 3.4 https://github.com/google/nsjail.git /nsjail \
    && make -C /nsjail -j"$(nproc)"

# ── runtime ───────────────────────────────────────────────────────────────
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    UV_SYSTEM_PYTHON=1 \
    PAPER_MCP_HOST=0.0.0.0 \
    PAPER_MCP_PORT=8000 \
    PAPER_MCP_ARTIFACT_ROOT=/app/artifacts

# TeX Live: enough for real Beamer decks, not the full 5GB distribution.
#   latex-recommended  — core
#   latex-extra        — beamer, metropolis
#   fonts-recommended  — the fonts those themes assume
#   xetex + lang-cjk   — CJK decks, which silently lose every glyph under pdflatex
#   fonts-firacode     — metropolis's expected font
# libnl/libprotobuf are nsjail's runtime deps, carried over from the builder.
RUN apt-get update && apt-get install -y --no-install-recommends \
        texlive-latex-recommended \
        texlive-latex-extra \
        texlive-fonts-recommended \
        texlive-xetex \
        texlive-lang-cjk \
        fonts-firacode \
        fonts-noto-cjk \
        lmodern \
        libnl-route-3-200 \
        libprotobuf32 \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=nsjail-build /nsjail/nsjail /usr/local/bin/nsjail
RUN nsjail --help > /dev/null 2>&1 || (echo "nsjail is not runnable" && exit 1)

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
