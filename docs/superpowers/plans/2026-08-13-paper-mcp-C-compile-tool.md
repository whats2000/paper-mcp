# paper-mcp Plan C — `compile_latex` as a sandboxed tool

**Goal:** `compile_latex(tex, assets)` compiles caller-supplied LaTeX and returns a PDF URL plus **structured errors** — one attempt, no repair loop.

**It is a tool, not a flow (SRS v0.3).** It compiles and reports. It never revises, retries with edits, or authors anything; the calling agent reads the errors and resubmits. That boundary is what keeps this a data/capability service rather than a competing agent.

**The security position:** this executes untrusted code from the internet. TeX is Turing-complete, `\write18` shells out, `\input` reads arbitrary paths, `\openout` writes them. Everything else in this plan is ordinary; this is not.

## Global Constraints

Plan A/B constraints hold. Plus:

- **No unsandboxed compilation on a public endpoint.** If no jail is available and `AUTH_MODE != open`, `compile_latex` refuses with a typed error rather than running. A service that executes strangers' code without isolation is worse than one that declines.
- **Exactly one attempt.** No revise loop, ever.
- **Every failure mode is typed:** `compile_error`, `timeout`, `resource_limit`, `sandbox_unavailable`.

## Defence in depth

Three independent layers, because each defeats a different attack:

| Layer | Stops |
| --- | --- |
| **TeX flags** — `-no-shell-escape`, `openin_any=p`, `openout_any=p`, `-interaction=nonstopmode` | `\write18`, reads/writes outside the job dir |
| **nsjail** — no network, read-only root, tmpfs work dir, unprivileged uid, dropped caps, rlimits | everything the TeX layer misses, including engine bugs |
| **Wall-clock + output caps** | infinite expansion, fork bombs, multi-GB output |

nsjail is Linux-only, so it exists in the container and not on a Windows dev box. That asymmetry is handled explicitly rather than ignored (see the refusal rule above).

## Tasks

### Task 1: Compile core
`src/paper_mcp/pipelines/latex_compile.py` — engine selection ported from PaperHub (`select_engine`: xelatex when the source needs xeCJK/fontspec/ctex, else pdflatex — a CJK deck silently loses every glyph under pdflatex), hardening flags, one run, PDF + log out.

### Task 2: Structured error parsing
Parse `! ...` blocks, `l.<n>` line refs, and missing-file errors into `{file, line, message, kind}`. A raw log dump is not actionable; the caller's model needs the failure located.

### Task 3: Sandbox policy
`src/paper_mcp/sandbox/` — detect nsjail, build its argv, and decide whether compiling is permitted at all. Refuses when unsandboxed and not in dev.

### Task 4: `compile_latex` tool + asset staging
Assets (figures from a bundle) written into the job dir by relative path, traversal-checked exactly like the artifact route.

### Task 5: Adversarial corpus — **release gate**
`\write18{touch pwned}` · `\input{/etc/passwd}` · `\openout` to an absolute path · unbounded expansion · huge output · `\read` from outside. Each must fail closed. **A sandbox change that has not been re-run against this corpus does not ship.**

### Task 6: Container
TeX Live (scheme-medium + beamer, metropolis, Fira, CJK) + nsjail in the image; run the corpus inside it, where the jail actually exists.
