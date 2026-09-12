# Paper artifact

Everything behind *Architectural Health Scoring with Inferred Module Contracts
and Explicit Non-Measurement Reporting*: the two scan drivers, the raw results
they emitted, the figure scripts, and the script that typesets the paper from
those results.

The point of keeping it here rather than in a scratch directory is that the
numbers in the paper are not transcribed. `build_paper.py` reads
`results/*.json` and formats the tables and the prose figures from them, so a
re-run of the corpus propagates into the document and a stale number cannot
survive a rebuild.

```
build_paper.py        Typesets the DOCX from results/ + figures/ + template.docx
render_pdf.py         DOCX -> PDF via Word, and reports the page count
run_corpus.py         First-scan condition: clone and analyse ten repositories
rescan_corpus.py      Repeat-scan condition: re-analyse the same working copies
figures.py            Figs. 2 and 3 (health, runtime) from results/
figure_pipeline.py    Fig. 1 (pipeline schematic)
results/              Raw JSON and console logs of both runs -- the evidence
figures/              Generated PNGs at 600 dpi
EVIDENCE.md           Every claim in the paper mapped to what supports it
```

## Rebuilding the document

The IEEE A4 conference template is not redistributed here. Download
"Conference Template A4" from
<https://www.ieee.org/conferences/publishing/templates.html>, save it as
`paper/template.docx` (or pass `--template`), then:

```bash
python paper/build_paper.py
python paper/render_pdf.py      # Windows + Word; also reports the page count
```

`render_pdf.py` uses Word rather than LibreOffice because the heading, table
and reference numbers are produced by the template's own list definitions, and
only Word resolves those fields the way a reviewer opening the DOCX will see
them. Its page count is therefore the authoritative one against a page limit.

The paper is **6 pages**. Three levers move that, in order of preference:
`COL_W` in the figure scripts (figures are authored at printed size, so changing
it means regenerating them), then prose, then the template's own margins --
which should not be touched.

## Regenerating the figures

```bash
python paper/figures.py
python paper/figure_pipeline.py
```

Both read `results/` and write `figures/`. They are greyscale with hatching
rather than colour, so the bars survive both a monochrome printer and a reader
with colour vision deficiency.

Figures are authored at `COL_W = 2.45` inches, the width they occupy in the
paper. That matters: authoring at full column width and letting Word scale the
image down shrinks the type with it, and a 7.5 pt axis label becomes 5.4 pt on
the page.

## Re-running the measurements

This overwrites `results/` and therefore changes the paper. Needs the machine
learning extras installed, and takes roughly twenty minutes.

```bash
export AG_CORPUS_DIR=/some/scratch/dir    # same value for both runs
python paper/run_corpus.py                # -> results/first_scan.json
python paper/rescan_corpus.py             # -> results/repeat_scan.json
python paper/figures.py && python paper/figure_pipeline.py
python paper/build_paper.py && python paper/render_pdf.py
```

`rescan_corpus.py` must see the *same working copies* `run_corpus.py` left
behind, not fresh clones: the repeat-scan condition is defined by the contract,
cached embeddings and stored centroid that the first scan wrote. Cloning again
would measure the first-scan condition twice.

Absolute durations will differ from the published ones -- they are wall-clock
measurements on one workstation, and the paper says so. The layer activation
results should not differ; if they do, that is a finding worth chasing rather
than a flaky run.
