---
name: deep-read
description: Read a paper precisely from a paper-mcp bundle, quoting what it says rather than what you recall.
---

# Deep read

You answer questions about a paper from the bundle in front of you, not from
memory of that paper. Famous papers are exactly where recall is most confident
and most wrong — recalled numbers drift, and the bundle has the real ones.

## MANDATORY WORKFLOW

1. **Fetch.** `fetch_paper(paper_id)`; poll `get_job` if it is extracting.
2. **Orient by headings.** `markdown` carries the paper's own heading
   structure. Locate the relevant section before reading everything.
3. **Quote from the markdown.** Tables arrived as real markdown tables and
   equations as LaTeX, so a number, a row, or a formula can be reproduced
   exactly. Do it.
4. **Check the figure index** when a question is visual. Captions in `figures`
   often answer "what does Figure 3 show?" without opening the image.
5. **Say what is missing.** If the bundle does not contain the answer, say so.

## Output

Answer first, then the evidence — the quoted line, table row, or figure
caption it rests on.

## Examples

**A number that exists.**

```
Q: What BLEU does the big model reach on EN-DE?
markdown: | Model | BLEU | ... | Big | 28.4 |
→ "28.4 BLEU on EN-DE, from the results table."
→ NOT "around 28" — you were given the exact figure.
```

**A question the bundle cannot answer.**

```
Q: What learning rate did they use for the ablations?
markdown: no such detail
→ "The bundle does not state it. `extraction.warnings` is empty, so this is
   the paper being silent rather than the extraction dropping it."
→ NOT a plausible-sounding number.
```

Why: a confident wrong constant is worse than an admission, because the
reader cannot tell it apart from a real one.

**A visual question.**

```
Q: What is in Figure 1?
figures: [{id: "fig-001", caption: "The Transformer - model architecture."}]
→ Answer from the caption, and give `image_url` if they want to see it.
```

**Extraction warnings exist.**

```
extraction.warnings: ["table not rendered as markdown; ..."]
→ Say which content is unreliable before answering from that region.
```
