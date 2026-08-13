---
name: paper-to-deck
description: Turn a paper into a compiled Beamer deck, grounded in figures the paper actually contains.
---

# Paper to deck

You build a slide deck from a paper, and every figure you show must be one the
extractor actually found.

That constraint is the point. A deck citing `\includegraphics{fig-004.png}`
when the bundle listed three figures is a deck that fails in front of an
audience. `fetch_paper`'s `figures` array is the complete list of what exists —
if it is not in that array, it does not exist.

## MANDATORY WORKFLOW

1. **Fetch.** `fetch_paper(paper_id)`. On `status: "extracting"`, poll
   `get_job` every 15s until `done`, then call `fetch_paper` again — it returns
   the bundle from cache. A dense paper takes roughly a minute per page.
2. **Read before planning.** Read `markdown` end to end. It carries real
   markdown tables and equations as LaTeX; the numbers you quote come from
   there, never from memory of the paper.
3. **Inventory the figures.** List every `{id, caption, image_path}` from
   `figures`. This is your entire visual vocabulary.
4. **Plan against that inventory.** Decide the narrative, then assign figures
   to slides *from the list*. If a slide needs a figure that does not exist,
   change the slide — not the citation.
5. **Write.** Beamer, one idea per slide. Default to ~12 slides for a
   15-minute talk unless the user says otherwise; detail belongs in speaker
   notes, not on the slide.
6. **Attach.** For each figure used: download `image_url`, pass it in `assets`
   with `path` set to that figure's exact `image_path`, and reference the same
   path in `\includegraphics`. These three strings must match.
7. **Compile.** `compile_latex(tex, assets)`. It runs once and does not
   self-repair — reading the errors and resubmitting is your job.

## Output

The Beamer source, then the `pdf_url`. Report which figures you used by id.

## Handling compile errors

`errors[]` gives `{kind, file, line, message}`. Fix by kind:

| kind | means | fix |
| --- | --- | --- |
| `missing_file` | a referenced asset was not attached | add it to `assets`, or drop the reference |
| `latex_error` | syntax, at `file:line` | fix that line |
| `timeout` | unbounded expansion | simplify; suspect a runaway macro |
| `sandbox_unavailable` | the deployment cannot compile at all | report it; do **not** retry |

Three attempts. If it still fails, hand back the source and the errors rather
than looping.

## Examples

**Figure exists — use it.**

```
figures: [{id: "fig-001", caption: "The Transformer architecture.",
           image_path: "figures/fig-001.png"}]
→ \includegraphics[width=0.8\textwidth]{figures/fig-001.png}
   assets: [{path: "figures/fig-001.png", content_base64: "..."}]
```

**Figure does not exist — restructure, do not invent.**

```
You want an ablation chart. figures[] has no ablation figure.
→ WRONG: \includegraphics{figures/fig-007.png}
         (does not exist; the compile fails or shows the wrong image)
→ RIGHT: a table slide built from the ablation numbers in `markdown`.
```

Why: the extractor is the only source of truth about what the paper contains.

**Numbers come from the markdown.**

```
markdown: | Model | BLEU |\n| --- | --- |\n| Big | 28.4 |
→ "28.4 BLEU (Table 2)"   — quoted from the bundle
→ NOT "roughly 28 BLEU"   — approximating data you were given exactly
```

**Truncation is not absence.**

```
markdown_truncated: true
→ The tail is in artifact.zip. Say what you could not see, rather than
  concluding the paper omits it.
```
