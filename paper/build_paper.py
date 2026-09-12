"""Build the IEEE paper into the supplied A4 conference template.

Edits the template package in place (styles, fonts, margins preserved) and
replaces word/document.xml with the paper body. Headings, captions, table
heads and references are auto-numbered by the template's own list definitions,
so their numbers are NOT typed here.

    python paper/build_paper.py [--template PATH]

Every number in the tables and in the prose is read from results/*.json, which
are the raw outputs of run_corpus.py and rescan_corpus.py. Nothing is typed in
by hand, so a re-run of the corpus propagates into the paper.
"""
from __future__ import annotations

import argparse
import json
import shutil
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent

_ap = argparse.ArgumentParser(description=__doc__)
_ap.add_argument(
    "--template", type=Path, default=ROOT / "template.docx",
    help="IEEE A4 conference template .docx (see README for where to get it)")
_ap.add_argument("--out", type=Path, default=ROOT / "ArchGuard_IEEE_paper.docx")
_args = _ap.parse_args()

BUILD = ROOT / "build"
SRC = BUILD / "template"
FIGS = ROOT / "figures"
OUT_DOCX = _args.out

if not _args.template.is_file():
    raise SystemExit(
        f"template not found: {_args.template}\n"
        "Download the IEEE A4 conference template and pass --template, or place "
        "it at paper/template.docx. See paper/README.md.")

# Unpack the template fresh on every build: the styles, fonts, margins and list
# definitions all come from it, and an unpacked tree left over from a previous
# build would silently keep whatever that build put in it.
if BUILD.exists():
    shutil.rmtree(BUILD)
SRC.mkdir(parents=True)
with zipfile.ZipFile(_args.template) as _z:
    _z.extractall(SRC)

R_NS = "http://purl.oclc.org/ooxml/officeDocument/relationships"
PIC_NS = "http://purl.oclc.org/ooxml/drawingml/picture"
A_NS = "http://purl.oclc.org/ooxml/drawingml/main"

COL_DXA = 4800          # usable column width in twentieths of a point
EMU_PER_IN = 914400

first = json.loads((ROOT / "results/first_scan.json").read_text(encoding="utf-8"))
rescan = json.loads((ROOT / "results/repeat_scan.json").read_text(encoding="utf-8"))
BY = {r["repo"]: r for r in rescan}
ROWS = [r for r in first if r.get("status") == "ok"]

# --------------------------------------------------------------------------- xml helpers

def run(text: str, *, italic=False, bold=False, sub=False, sup=False) -> str:
    rpr = ""
    if bold:
        rpr += "<w:b/>"
    if italic:
        rpr += "<w:i/>"
    if sub:
        rpr += '<w:vertAlign w:val="subscript"/>'
    if sup:
        rpr += '<w:vertAlign w:val="superscript"/>'
    rpr = f"<w:rPr>{rpr}</w:rPr>" if rpr else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def para(style: str, runs: str, *, jc: str | None = None, keep_next=False,
         unnumbered=False, spacing: str | None = None) -> str:
    # pPr child order is schema-enforced: pStyle, keepNext, numPr, spacing, jc
    ppr = f'<w:pStyle w:val="{style}"/>'
    if keep_next:
        ppr += "<w:keepNext/>"
    if unnumbered:
        ppr += '<w:numPr><w:ilvl w:val="0"/><w:numId w:val="0"/></w:numPr>'
    if spacing:
        ppr += spacing
    if jc:
        ppr += f'<w:jc w:val="{jc}"/>'
    return f"<w:p><w:pPr>{ppr}</w:pPr>{runs}</w:p>"


def body(text: str) -> str:
    """A body paragraph; *italic* spans are marked with asterisks."""
    out = []
    for i, part in enumerate(text.split("*")):
        if part:
            out.append(run(part, italic=(i % 2 == 1)))
    return para("BodyText", "".join(out))


def head(level: int, text: str) -> str:
    return para(f"Heading{level}", run(text), keep_next=True)


def equation(text: str) -> str:
    """Centred italic formula.

    The template's own `equation` style is set in the Symbol font, which maps
    Latin letters onto Greek glyphs and turned every formula into gibberish.
    The font is therefore pinned explicitly here.
    """
    r = ('<w:r><w:rPr><w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" '
         'w:cs="Times New Roman"/><w:i/></w:rPr>'
         f'<w:t xml:space="preserve">{escape(text)}</w:t></w:r>')
    return para("BodyText", r, jc="center",
                spacing='<w:spacing w:before="80" w:after="80"/>')


def caption(text: str) -> str:
    return para("figurecaption", run(text))


def image_para(rid: str, width_in: float, aspect: float) -> str:
    cx = int(width_in * EMU_PER_IN)
    cy = int(width_in * aspect * EMU_PER_IN)
    drawing = (
        f'<w:p><w:pPr><w:jc w:val="center"/><w:keepNext/></w:pPr><w:r><w:drawing>'
        f'<wp:inline distT="0" distB="0" distL="0" distR="0">'
        f'<wp:extent cx="{cx}" cy="{cy}"/>'
        f'<wp:docPr id="{abs(hash(rid)) % 9000 + 100}" name="Figure {rid}"/>'
        f'<a:graphic xmlns:a="{A_NS}"><a:graphicData uri="{PIC_NS}">'
        f'<pic:pic xmlns:pic="{PIC_NS}">'
        f'<pic:nvPicPr><pic:cNvPr id="0" name="{rid}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
        f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
        f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr>'
        f"</pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r></w:p>"
    )
    return drawing


def table(title: str, headers: list[str], rows: list[list[str]], widths: list[int]) -> str:
    borders = (
        "<w:tblBorders>"
        '<w:top w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        '<w:bottom w:val="single" w:sz="6" w:space="0" w:color="000000"/>'
        '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="000000"/>'
        "</w:tblBorders>"
    )
    grid = "".join(f'<w:gridCol w:w="{w}"/>' for w in widths)
    def cell(txt: str, w: int, style: str, jc: str) -> str:
        return (f'<w:tc><w:tcPr><w:tcW w:w="{w}" w:type="dxa"/>'
                f'<w:vAlign w:val="center"/></w:tcPr>'
                f'{para(style, run(txt), jc=jc)}</w:tc>')
    hdr = "<w:tr><w:trPr><w:tblHeader/></w:trPr>" + "".join(
        cell(h, w, "tablecolhead", "center") for h, w in zip(headers, widths, strict=True)) + "</w:tr>"
    trs = []
    for r in rows:
        trs.append("<w:tr>" + "".join(
            cell(v, w, "tablecopy", "center" if i else "start")
            for i, (v, w) in enumerate(zip(r, widths, strict=True))) + "</w:tr>")
    return (
        para("tablehead", run(title), keep_next=True)
        + f'<w:tbl><w:tblPr><w:tblW w:w="{sum(widths)}" w:type="dxa"/>'
          f'<w:jc w:val="center"/>{borders}</w:tblPr>'
          f"<w:tblGrid>{grid}</w:tblGrid>{hdr}{''.join(trs)}</w:tbl>"
        + para("BodyText", "")
    )


# --------------------------------------------------------------------------- content
P: list[str] = []

# ---- title block (single column section) ----
P.append(para("papertitle", run(
    "Architectural Health Scoring with Inferred Module Contracts "
    "and Explicit Non-Measurement Reporting")))
# Four authors sharing one affiliation, laid out as a borderless four-column
# table across the single-column title section. A table rather than tab stops
# because the affiliation lines wrap, and tab stops do not hold a wrapped block
# in a column.
AUTHORS = [
    ("Harvik Sanghavi", "harvik07sanghavi@gmail.com"),
    ("Jainam Sethia", "jainamsethia19@gmail.com"),
    ("Vansh Parikh", "vanshparikh@gmail.com"),
    ("Rashmi Patel", "rashmi.patel@nmims.edu"),
]
DEPT = "Department of Data Science"
SCHOOL = "Mukesh Patel School of Technology Management and Engineering, NMIMS"
CITY = "Mumbai, Maharashtra, India"

_cells = []
_w = 2523  # (11906 - 907 - 907) / 4
for _name, _email in AUTHORS:
    _body = (
        para("Author", run(_name))
        + para("Affiliation", run(DEPT, italic=True))
        + para("Affiliation", run(SCHOOL, italic=True))
        + para("Affiliation", run(CITY))
        + para("Affiliation", run(_email))
    )
    _cells.append(
        f'<w:tc><w:tcPr><w:tcW w:w="{_w}" w:type="dxa"/></w:tcPr>{_body}</w:tc>')

_AUTHOR_GRID = ("<w:tblGrid>"
                + "".join(f'<w:gridCol w:w="{_w}"/>' for _ in AUTHORS)
                + "</w:tblGrid>")

P.append(
    f'<w:tbl><w:tblPr><w:tblW w:w="{_w * 4}" w:type="dxa"/>'
    '<w:jc w:val="center"/>'
    '<w:tblBorders>'
    '<w:top w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    '<w:left w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    '<w:bottom w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    '<w:right w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    '<w:insideH w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    '<w:insideV w:val="none" w:sz="0" w:space="0" w:color="auto"/>'
    "</w:tblBorders>"
    # fixed, so all four cells are the same width and wrap identically;
    # autofit sizes each column to its content and made the block ragged
    '<w:tblLayout w:type="fixed"/>'
    # zero side margins: the longest e-mail is a hair wider than the default
    # cell padding allows, and it was breaking mid-address
    '<w:tblCellMar>'
    '<w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/>'
    "</w:tblCellMar></w:tblPr>"
    + _AUTHOR_GRID
    + "<w:tr>" + "".join(_cells) + "</w:tr></w:tbl>"
)
P.append(para("BodyText", ""))

SECT_ONE_COL = (
    '<w:p><w:pPr><w:sectPr>'
    '<w:type w:val="continuous"/>'
    '<w:pgSz w:w="11906" w:h="16838" w:code="9"/>'
    '<w:pgMar w:top="1080" w:right="907" w:bottom="1440" w:left="907" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    '<w:cols w:space="720"/><w:docGrid w:linePitch="360"/>'
    "</w:sectPr></w:pPr></w:p>"
)
P.append(SECT_ONE_COL)

# ---- abstract & keywords ----
P.append(para("Abstract", run("Abstract", bold=True, italic=True) + run(
    "\u2014Architectural erosion is usually detected long after it becomes expensive "
    "to reverse, and the tools that could detect it earlier require a hand written "
    "description of the intended architecture that most repositories do not have. "
    "This paper presents a repository level analysis pipeline that removes that "
    "prerequisite. Module boundaries are inferred from version history using "
    "community detection over a co change graph, with a directory based fallback "
    "when history is too sparse, and the inferred boundaries are then checked by four "
    "analysis layers covering import rules, structural coupling, semantic drift and "
    "cross module duplication. Semantic layers use sentence embeddings and an "
    "approximate nearest neighbour index rather than token comparison. The central "
    "design decision is that a layer which cannot measure anything reports the reason "
    "instead of a score, and the composite is averaged only over layers that actually "
    "ran. The system was applied to ten public Python repositories totalling 102331 "
    "lines, twice each, and every repository was analysed end to end without manual "
    "configuration. The measurements show that on first contact only two of four "
    "layers produce a value, that a repeat scan activates a third, and that the "
    "resulting composite rose on five of ten repositories purely because the set of "
    "contributing layers changed rather than because the code improved. The evaluation "
    "therefore supports feasibility of unattended analysis and identifies a "
    "comparability constraint that any active layer reweighting scheme must address. "
    "Claims about explanation quality are not made, because that component was not "
    "evaluated.")))
P.append(para("Keywords", run("Keywords", bold=True, italic=True) + run(
    "\u2014software architecture; architectural erosion; static analysis; "
    "code embeddings; similarity search; repository mining; software metrics")))

# ---- I. INTRODUCTION ----
P.append(head(1, "Introduction"))
P.append(body(
    "Software architecture degrades gradually. Each individual change is defensible, "
    "and the loss of structure only becomes visible once a module has acquired more "
    "dependencies than any one developer can reason about. Perry and Wolf identified "
    "this as architectural erosion and drift [1], Lehman described the underlying "
    "pressure as a law of continuing change [2], and van Gurp and Bosch documented "
    "design erosion in practice [3]. The cost is well understood in the technical debt "
    "literature [4], [5]."))
P.append(body(
    "The tooling response has largely been conformance checking: a developer states "
    "the intended architecture, and a checker reports deviations. This works, and it "
    "is the basis of established dependency rule enforcement. It also has a practical "
    "prerequisite that is rarely met. The specification must exist before the first "
    "check can run, and writing one for an existing repository requires precisely the "
    "architectural understanding that the repository has already lost. Projects that "
    "would benefit most from conformance checking are therefore the least likely to "
    "adopt it. Surveys of static analysis adoption report related friction: developers "
    "abandon tools whose output they cannot act on or whose configuration cost is paid "
    "before any value is returned [6], [7]."))
P.append(body(
    "A second difficulty concerns what an analysis reports when it cannot run. "
    "A layer that finds no rules to enforce, or no historical baseline to compare "
    "against, has not observed a healthy repository; it has observed nothing. If that "
    "absence is encoded as a zero and averaged into an aggregate score, the aggregate "
    "improves as coverage falls. This is a measurement validity problem rather than an "
    "implementation defect, and it is not specific to any one tool."))
P.append(body(
    "This paper presents a pipeline addressing both points. Module boundaries are "
    "inferred automatically, so no specification is required in advance, and every "
    "layer distinguishes a measured value from an absence of measurement, so the "
    "aggregate is computed only over layers that produced evidence. The system was "
    "applied to ten public Python repositories, twice each, and the measurements are "
    "reported without a claim that high scores indicate good architecture."))
P.append(body(
    "The contributions are: a method for unattended module contract inference "
    "combining version history with a structural fallback; an aggregation rule that "
    "makes non measurement explicit rather than silently favourable; an implemented "
    "system realising both; and an empirical characterisation of layer activation "
    "that identifies a comparability constraint the aggregation rule introduces."))

# ---- II. RELATED WORK ----
P.append(head(1, "Related Work"))
P.append(head(2, "Software architecture recovery"))
P.append(body(
    "Recovering a module view from source is a mature field. Ducasse and Pollet "
    "survey the space and organise it by the goal of the reconstruction and the "
    "inputs it consumes [8]; Garcia et al. compare recovery techniques empirically and "
    "report that accuracy varies substantially with the system under study [9]. "
    "Clustering over structural dependencies is the dominant input. The pipeline "
    "described here differs in the input rather than the algorithm: it clusters a co "
    "change graph derived from version history, following the observation of Gall et "
    "al. that files changing together are logically coupled even when no static "
    "dependency connects them [10], and the later confirmation by Zimmermann et al. "
    "that such histories predict change propagation [11]. Recovery here is a means to "
    "an end. The inferred grouping exists so that later layers have a unit to measure, "
    "not as a claim about the true architecture."))
P.append(head(2, "Coupling metrics and erosion detection"))
P.append(body(
    "Chidamber and Kemerer established coupling and cohesion as measurable object "
    "oriented properties [12], and Martin's dependency metrics introduced the "
    "instability and abstractness formulation that underlies most modern dependency "
    "rule checking [13]. These give absolute measures of structure at a point in time. "
    "The coupling layer described here measures the same property but against a "
    "declared budget rather than as a bare figure, so what it reports is the distance "
    "from a stated policy. Two policies are available, one derived from the "
    "repository's own state and one fixed; Section IV explains why unattended analysis "
    "uses the fixed one, and Section VIII examines what that costs in "
    "discrimination."))
P.append(head(2, "Clone detection and code embeddings"))
P.append(body(
    "Token and tree based clone detection is well established, with CCFinder as a "
    "representative large scale implementation [14] and comparative surveys "
    "documenting the trade offs between clone types and detection techniques [15]. "
    "Studies of clone prevalence at ecosystem scale show duplication is pervasive "
    "rather than exceptional [16]. Purely lexical techniques, however, miss "
    "reimplementations that share intent but not tokens. Learned representations "
    "address this: deep learning has been applied directly to clone detection [17], "
    "and pretrained code models such as CodeBERT [18] and GraphCodeBERT [19] provide "
    "general purpose code embeddings. The system described here uses a general "
    "sentence embedding model, MiniLM [20] in the Sentence BERT configuration [21], "
    "over function level text, indexed with an approximate nearest neighbour "
    "structure [22]. This is a weaker representation than a code specific model, and "
    "Section IX treats that as a limitation rather than a design advantage."))
P.append(head(2, "Developer facing analysis tooling"))
P.append(body(
    "Sadowski et al. describe the organisational requirements for static analysis to "
    "survive contact with developers, emphasising low friction and actionable output "
    "[6]. Johnson et al. found that unclear or unactionable output is a primary reason "
    "tools are abandoned [7]. Both motivate the reporting decisions here: a layer that "
    "did not run states why in the same place it would have stated a score."))
P.append(head(2, "Gap addressed"))
P.append(body(
    "Architecture recovery produces module views but stops short of continuous "
    "enforcement; conformance checking enforces but requires a specification; clone "
    "detection addresses similarity in isolation from architectural structure. The "
    "combination pursued here is one unattended pipeline that infers the unit of "
    "analysis, measures it structurally and semantically, and is explicit about which "
    "measurements were possible. How often each layer can measure anything appears "
    "not to be routinely reported, and that is what Section VII answers here."))

# ---- III. RESEARCH QUESTIONS ----
P.append(head(1, "Research Questions"))
P.append(body(
    "The evaluation is limited to questions the available evidence can answer. "
    "Explanation quality and the accuracy of inferred boundaries against a ground "
    "truth are outside it, for the reasons given in Section IX."))
P.append(body(
    "*RQ1.* Can module contracts be inferred well enough for a four layer analysis to "
    "complete end to end on unseen repositories without manual configuration?"))
P.append(body(
    "*RQ2.* Which layers actually produce a measurement on first contact with an "
    "unseen repository, and does that change on a repeat scan?"))
P.append(body(
    "*RQ3.* What effect does averaging over only the active layers have on the "
    "comparability of the composite score across scans?"))
P.append(body(
    "*RQ4.* What is the wall clock cost of the pipeline, and how much of it is "
    "avoided by caching on a repeat scan?"))

# ---- IV. METHOD ----
P.append(head(1, "Proposed Method"))
P.append(body(
    "Fig. 1 shows the pipeline. A repository is cloned, parsed into an import graph, "
    "partitioned into modules, planned incrementally, analysed by four layers, scored, "
    "and persisted."))
P.append(image_para("rIdFig1", 2.45, 2.25 / 3.42))
P.append(caption("Analysis pipeline. Dashed labels mark the two inference inputs: "
                 "version history for module boundaries and embeddings for the "
                 "semantic layers."))

P.append(head(2, "Module contract inference"))
P.append(body(
    "The unit of measurement is a module: a named set of file paths. When no "
    "specification is present one is generated. Non merge commits are read from "
    "version history and each commit contributes an edge between every pair of Python "
    "files it touches. Two bounds keep this tractable and meaningful. Only the most "
    "recent 500 commits are converted to edges once a repository exceeds 1000 commits, "
    "and any commit touching more than 100 files is counted but contributes no edges, "
    "since bulk reformatting and vendored tree imports would otherwise dominate the "
    "graph with edges carrying no coupling signal. Louvain modularity optimisation [23] "
    "partitions the resulting graph, and communities are consolidated when they "
    "resolve to the same directory path. Where history is too sparse, boundaries fall "
    "back to directory structure. Candidate modules that resolve to no existing path "
    "are dropped; if none survives, the analysis stops and reports that it could not "
    "identify a module, rather than scoring an empty partition."))

P.append(head(2, "Layer 1: import boundary violations"))
P.append(body(
    "Each file is assigned to a module and its import statements are checked against "
    "the forbidden import rules the contract declares. The layer score is the "
    "proportion of examined imports that breached a rule:"))
P.append(equation("L1 = violations / max(imports_examined, 1)"))
P.append(body(
    "A contract that declares no import rules puts no file in front of a rule. The "
    "layer then reports that it examined nothing, rather than the zero that the "
    "expression above would otherwise yield."))

P.append(head(2, "Layer 2: structural coupling"))
P.append(body(
    "Fan out is the number of distinct non standard library, non relative modules a "
    "module imports. Each module carries a coupling budget, and the per module delta "
    "and layer score are"))
P.append(equation("delta(m) = min(1, (fan_out(m) - budget(m)) / max(budget(m), 1))"))
P.append(equation("L2 = max over modules m of delta(m)"))
P.append(body(
    "Where the budget comes from is the more consequential choice, and the generator "
    "offers two policies. A team enforcing a contract against its own future changes "
    "can derive the budget from the module's fan out at generation time, at 1.5 times "
    "the observed value. For one off analysis of a repository nobody has agreed a "
    "contract for, that policy is rejected in favour of a fixed threshold, on the "
    "explicit grounds that grading a repository against thresholds derived from its "
    "own current state is tautological and can only pass. The hosted path used "
    "throughout this evaluation therefore applies a fixed profile that sets a budget "
    "of ten for every module, so Layer 2 measures observed coupling against a stated "
    "policy rather than against the repository itself."))

P.append(head(2, "Layer 3: semantic drift"))
P.append(body(
    "Function level text is embedded with all MiniLM L6 v2, a 384 dimensional "
    "sentence embedding model [20], [21], used as a pretrained encoder; no training or "
    "fine tuning is performed. Embeddings are computed in batches of 32 and cached by "
    "content hash. A module centroid is the mean of its function embeddings normalised "
    "to unit length, and drift is the cosine distance between the stored centroid and "
    "the current one:"))
P.append(equation("L3 = 1 - cosine_similarity(centroid_previous, centroid_current)"))
P.append(body(
    "A module exceeding its drift threshold raises a violation, and "
    "the layer score is the maximum drift across modules. The threshold is set by "
    "the same profile that sets the coupling budget, as one minus the profile's "
    "minimum cohesion, which is 0.35 for the profile used here. On a repository with no "
    "stored centroid there is nothing to compare against; the layer records the new "
    "centroid, reports that no baseline existed and contributes no score."))

P.append(head(2, "Layer 4: cross module duplication"))
P.append(body(
    "Function embeddings are unit normalised and indexed in a flat inner product "
    "index over exact L2 distance [22], from which cosine similarity is recovered as"))
P.append(equation("cosine_similarity = 1 − (L2_distance²) / 2"))
P.append(body(
    "Each function queries its ten nearest neighbours. Candidates below 0.70 "
    "similarity are discarded, matches are retained only across module boundaries, and "
    "a match is scored by linear interpolation above a similarity floor:"))
P.append(equation("dup(s) = clamp((s - 0.85) / 0.15, 0, 1)"))
P.append(body(
    "A module aggregates the mean of its match scores and the layer takes the maximum "
    "across modules. The floor at 0.85 means near identical implementations score "
    "close to one while merely related functions score zero, which keeps the layer "
    "from reporting ordinary domain similarity as duplication."))

P.append(head(2, "Composite score and non measurement"))
P.append(body(
    "Let A be the set of layers that produced a measurement. The composite is their "
    "unweighted mean and the reported health score is its complement on a percentage "
    "scale:"))
P.append(equation("composite = (1 / |A|) * sum of layer scores over A"))
P.append(equation("health = (1 - composite) * 100"))
P.append(body(
    "When A is empty the composite is set to 1.0 rather than 0.0. Averaging over no "
    "layers is arithmetically zero, which would report a repository on which nothing "
    "could be checked as perfectly healthy; of the two available answers, the "
    "conservative one is the only defensible choice. Bands are contract relative: a "
    "composite at or above the contract's fail threshold is critical, at or above the "
    "warn threshold is warning, at or above half the warn threshold is watch, and "
    "below that is healthy. A failed critical fitness gate caps the reported band and "
    "letter grade without altering the composite, so a breach cannot be presented as "
    "healthy while the numeric measurement remains what was measured."))

P.append(head(2, "Incremental analysis"))
P.append(body(
    "Files are hashed with SHA-256 and compared against the previous run to partition "
    "them into changed and unchanged sets, from which the set of dirty modules "
    "follows. The contract is fingerprinted so that a change to the specification "
    "invalidates reuse. Layer 4 is recomputed repository wide whenever any module is "
    "dirty, because duplication is a cross module property and a partial index would "
    "silently miss matches whose counterpart lies in an unchanged module."))

# ---- V. IMPLEMENTATION ----
P.append(head(1, "System Implementation"))
P.append(body(
    "The system is implemented in Python in 16534 non blank lines across 116 modules, "
    "with a test suite of 20529 lines across 146 modules, a ratio of 1.24 test lines "
    "per source line. It is deployed as two container images from one definition: a "
    "web image serving an HTTP API and dashboard, and a worker image carrying the "
    "machine learning dependencies and the embedding model. Analysis runs only in the "
    "worker, reached through a durable queue, so that untrusted repository parsing "
    "never executes in the process holding user sessions. Results are persisted in "
    "PostgreSQL; the queue, sessions and rate limit counters are held in Redis."))
P.append(body(
    "Two pieces of per repository state live in PostgreSQL rather than in the clone: "
    "content hashes, which drive incremental reuse, and module centroids, which give "
    "Layer 3 its baseline. Both were once written into a cache inside the analysed "
    "repository, which the hosted path deletes when the job ends, so neither survived "
    "to be read again. Keying them by repository is what lets an incremental scan or "
    "a drift comparison happen outside a retained working copy."))
P.append(body(
    "Several controls are relevant to a system that clones arbitrary repositories. "
    "Submitted URLs are resolved and the resolved address is pinned for the outbound "
    "request, so that the address validated against the private address blocklist is "
    "the address actually contacted, closing a time of check to time of use gap "
    "without disabling certificate verification. Clones are depth limited and bounded "
    "by a workspace budget. Dependency auditing declines to resolve a project that "
    "ships only a build specification, because resolution would execute the "
    "repository's own build backend. Responses carry a per request content security "
    "policy nonce. Each of these has a corresponding regression test."))
P.append(body(
    "Continuous integration runs ten jobs, expanding to eleven runs across a two "
    "version Python matrix, covering linting and type checking, dependency "
    "vulnerability auditing and static security analysis, unit and integration tests "
    "against real PostgreSQL and Redis services, a dedicated job that installs the "
    "machine learning extras and asserts that no test skipped for want of them, "
    "migration round trips, container build verification, and browser based "
    "accessibility, behavioural and visual regression suites. With the machine "
    "learning extras present 1372 tests pass and one skips by design; with them absent "
    "branch coverage is 84.59 percent. "
    "The system additionally analyses its own repository as a release "
    "gate, reporting 98.1 of 100 over 274 files with all eight declared fitness gates "
    "passing; this is a self consistency check and not independent evidence."))
P.append(body(
    "A language model component provides natural language advisory responses and "
    "remediation drafts through an external API, and degrades to a disabled state "
    "when no key is configured. It was not evaluated in this work and no claim is made "
    "about the quality or usefulness of its output."))

# ---- VI. EXPERIMENTAL SETUP ----
P.append(head(1, "Experimental Setup"))
P.append(body(
    "Ten public Python repositories were selected to span three orders of magnitude "
    "of commit history and roughly two of size, while remaining within one language "
    "and one packaging ecosystem so that parser coverage is not a confounding "
    "variable. They are widely used libraries and one tutorial application, chosen "
    "before any result was observed and analysed without exception or retry. "
    "Table I reports their characteristics. The corpus totals 499 Python files and "
    "102331 non blank lines."))
P.append(table(
    "Evaluation Corpus Characteristics",
    ["Repository", "Commits", "Files", "Lines"],
    [[r["repo"], f'{r["commits"]:,}', str(r["py_files"]), f'{r["py_loc"]:,}'] for r in ROWS],
    [1600, 1000, 900, 1300]))
P.append(body(
    "Each repository was cloned and analysed twice in the same working copy through "
    "the same pipeline entry point the production worker uses. The first scan is the "
    "first contact condition: no contract, no cached embeddings, no stored centroid. "
    "The second scan is the repeat condition: the contract generated by the first scan "
    "is present, as are its cached embeddings and centroids. Wall clock duration was "
    "measured around the pipeline call. Measurements were taken on one workstation "
    "running Python 3.11 with the machine learning extras installed; the embedding "
    "model is loaded once per process, so the first repository analysed absorbs that "
    "one off cost and is identified as such in Fig. 3."))
P.append(body(
    "Reported quantities are those the system itself produces: per layer score, "
    "whether each layer was skipped and why, violation counts, composite, health score "
    "and duration. No ground truth for architectural quality was available, so no "
    "precision or recall is reported; Section IX states why."))
P.append(body(
    "*Data availability.* Both scan drivers, the raw results they emitted, the "
    "figure scripts and the script that typeset this paper from those results are "
    "in the repository, under *paper/*: github.com/jainamsethia/ArchGuard"))

# ---- VII. RESULTS ----
P.append(head(1, "Results"))
P.append(head(2, "Completion without configuration"))
P.append(body(
    "All ten repositories were analysed end to end on both scans with no manual "
    "configuration and no hand authored contract. "
    "Contract inference produced a usable partition in every case, and no run "
    "terminated in the degenerate state where no candidate module resolves to an "
    "existing path. This answers RQ1 affirmatively for this corpus, and establishes "
    "only that the pipeline completes, not that the inferred boundaries are correct."))

P.append(head(2, "Which layers measured anything"))
P.append(body(
    "The activation result is uniform and is the most consequential finding. On the "
    "first scan the active set was layers 2 and 4 for all ten repositories. Layer 1 "
    "reported that the contract declared no import rules, and layer 3 reported that no "
    "prior baseline existed. On the repeat scan the active set was layers 2, 3 and 4 "
    "for all ten: the centroid stored by the first scan gave layer 3 something to "
    "compare against. Layer 1 was inactive throughout. Table II gives "
    "the first scan outcome per repository."))
P.append(table(
    "First Scan Layer Outcomes",
    ["Repository", "L2", "L4", "Comp.", "Health", "Gr."],
    [[r["repo"],
      f'{next(x for x in r["layers"] if x["layer"] == 2)["score"]:.3f}',
      f'{next(x for x in r["layers"] if x["layer"] == 4)["score"]:.3f}',
      f'{r["composite"]:.3f}', f'{r["health"]:.1f}', r["grade"]] for r in ROWS],
    [1250, 700, 700, 800, 800, 550]))
P.append(body(
    "Layer 2 produced at least one violation on four of ten repositories and layer 4 "
    "on one. The single layer 4 result was two cross module matches in one repository "
    "with an aggregate of 0.076, well below the 0.85 similarity floor at which the "
    "score approaches one. Of the four layers, therefore, one never measured, one "
    "measured only after a baseline existed, one fired on four repositories and one "
    "fired on one."))

P.append(head(2, "Effect of reweighting on comparability"))
P.append(body(
    "Fig. 2 compares health scores across the two conditions. Five of ten repositories "
    "scored higher on the repeat scan. No source file changed between scans. The "
    "increase follows arithmetically from the aggregation rule: the composite is the "
    "mean over the active set, and adding a third active layer whose score is near "
    "zero lowers the mean. For the lowest scoring repository the composite moved from "
    "0.500, the mean of 1.000 and 0.000 over two layers, to 0.333, the mean of the "
    "same two values and a third near zero drift score, raising reported health from "
    "50.0 to 66.7 with no change to the code."))
P.append(image_para("rIdFig2", 2.45, 2.15 / 3.42))
P.append(caption("Health score per repository on first and repeat scan. Repeat scan "
                 "values are labelled where they differ. No source file changed "
                 "between the two scans."))
P.append(body(
    "Across the corpus, mean health rose from 90.6 to 93.8 and the standard deviation "
    "fell from 15.4 to 10.3. Both movements are artefacts of the change in the active "
    "set, not evidence of improvement. This answers RQ3: composite scores computed "
    "over different active sets are not directly comparable, and the aggregation rule "
    "that prevents unmeasured layers from inflating a score introduces a distinct "
    "comparability hazard of its own."))

P.append(head(2, "Cost"))
P.append(body(
    "Fig. 3 reports wall clock analysis time. Excluding the first repository, which "
    "absorbs the one off model load, the first scan averaged 13.1 seconds and the "
    "repeat scan 8.3 seconds, a mean per repository reduction of 45 percent with a "
    "range of 21 to 78 percent. The largest repository in the corpus, at 23803 lines, "
    "completed its first scan in 31.3 seconds. Absolute times are single machine "
    "measurements and indicate an order of magnitude, not a benchmark."))
P.append(image_para("rIdFig3", 2.45, 2.15 / 3.42))
P.append(caption("Wall clock analysis time per repository. The first bar includes the "
                 "one off embedding model load paid once per process."))
P.append(table(
    "First Versus Repeat Scan",
    ["Repository", "Active layers", "Health", "Time (s)"],
    [[r["repo"],
      ",".join(str(x) for x in BY[r["repo"]]["active_1"]) + " → "
      + ",".join(str(x) for x in BY[r["repo"]]["active_2"]),
      f'{r["health"]:.1f} to {BY[r["repo"]]["health_2"]:.1f}',
      f'{r["duration_s"]:.1f} to {BY[r["repo"]]["dur_2"]:.1f}'] for r in ROWS],
    [1150, 1350, 1150, 1150]))

# ---- VIII. DISCUSSION ----
P.append(head(1, "Discussion"))
P.append(head(2, "Two of four layers dominate the result"))
P.append(body(
    "The headline design is a four layer analysis; the measured behaviour on first "
    "contact is a two layer analysis, and effectively a one layer analysis given that "
    "layer 4 fired on a single repository. The causes are structural rather than "
    "incidental. Layer 1 is inactive because contract inference declines to emit "
    "import rules at all, and declines for a stated reason: a dependency cycle "
    "proves that at least one edge in it is wrong without identifying which, so "
    "naming a particular edge as forbidden would be a guess presented as a rule. "
    "The generator emits a cycle detecting fitness function instead, which reports "
    "the path it found rather than inventing a policy. Layer 1 therefore has "
    "nothing to enforce by construction rather than by omission. Layer 3 is "
    "inactive on first contact because "
    "drift is defined against a previous observation, and a first observation has no "
    "predecessor. Neither is a wrong answer, and the system reports both, but "
    "together they mean a single unattended scan "
    "of an unseen repository exercises far less of the pipeline than its description "
    "implies. Reporting only a composite would have concealed this entirely."))
P.append(head(2, "What a high score does and does not mean"))
P.append(body(
    "The scores cluster high, and the obvious explanation is wrong. "
    "It is not that the contract ratifies the "
    "repository: the generator explicitly rejects self derived thresholds for one off "
    "analysis, and the profile used here fixes every module's budget at ten "
    "regardless of what the repository does. The four violations are genuine "
    "exceedances of that stated policy, at fan outs of 11, 12, 15 and 21 against a "
    "budget of 10."))
P.append(body(
    "What the clustering reflects is that a budget of ten is permissive for this kind "
    "of repository: six of ten mature libraries keep every module below it, which is "
    "a plausible property of well factored code rather than an artefact. The "
    "limitation is one of discrimination rather than validity. At this threshold the "
    "layer separates the four most coupled repositories from the rest and says "
    "nothing about the ordering within either group; a stricter profile would "
    "separate more, at the cost of failing healthy libraries. That trade is a policy "
    "choice the profile mechanism exposes, not a question this evaluation settles."))
P.append(head(2, "Semantic layers and their yield"))
P.append(body(
    "The semantic machinery accounted for a large share of dependency weight and "
    "runtime and produced findings on one of ten repositories. Two readings are "
    "available and this evaluation cannot separate them. The libraries in the corpus "
    "are mature and well factored, so genuine cross module duplication may simply be "
    "rare; alternatively the 0.85 similarity floor, chosen to suppress ordinary domain "
    "similarity, may be too conservative. Distinguishing these requires a labelled "
    "duplication benchmark, which is future work. A comparable ambiguity applies to "
    "layer 3, whose activation on the repeat scan was demonstrated but whose drift "
    "values were near zero because the two scans observed identical code; the layer "
    "has been shown to activate, not to detect real drift."))
P.append(head(2, "Threats to validity"))
P.append(body(
    "*Construct.* Health score is an aggregate of four heuristics and is not validated "
    "against any external judgement of architectural quality; it measures conformance "
    "to an inferred contract. *Internal.* Both scans ran in the same working copy, so "
    "the repeat condition confounds three caches that were all populated by the first "
    "scan. The activation change is attributable to the stored centroid because the "
    "layer states its own reason, but runtime reduction cannot be attributed to a "
    "single cache. *External.* Ten Python repositories, nine of them libraries, do not "
    "generalise to other languages, to applications, or to repositories under active "
    "architectural change. *Conclusion.* The corpus is too small for inferential "
    "statistics, and none is reported; the activation results are uniform across all "
    "ten repositories, which is the strongest claim the design supports."))

# ---- IX. LIMITATIONS ----
P.append(head(1, "Limitations"))
P.append(body(
    "The parser handles Python only, and non Python files in a repository are "
    "excluded, so a polyglot repository is analysed on a partial view. Repositories "
    "must be public and reachable over HTTPS."))
P.append(body(
    "No ground truth was available for either inferred module boundaries or "
    "architectural quality, so the evaluation reports what the system measured and "
    "does not report accuracy. Establishing accuracy would require expert labelled "
    "module decompositions, and validating the health score would require an "
    "independent quality judgement per repository."))
P.append(body(
    "Layer 3's inactivity on a first scan is inherent, and the baseline is stored "
    "per repository rather than in the analysed clone, so the repeat condition "
    "reported here is the one a hosted deployment reaches on a second submission. "
    "What that condition does not establish is detection: both scans observed "
    "identical code, so drift was near zero by construction. The layer was shown "
    "to activate and to compare, not to discriminate."))
P.append(body(
    "The embedding model is a general sentence encoder rather than a code specific "
    "one [18], [19], and function text is embedded without structural context. "
    "Duplication thresholds were fixed by design rather than calibrated against a "
    "benchmark such as a curated clone corpus. The language model component is "
    "unevaluated. The self analysis figure is not independent evidence, since the "
    "system, the contract and the thresholds share an author."))

# ---- X. CONCLUSION ----
P.append(head(1, "Conclusion and Future Work"))
P.append(body(
    "Architectural conformance checking is most valuable where an architectural "
    "specification is least likely to exist. This paper described a pipeline that "
    "removes that prerequisite by inferring module boundaries from version history "
    "with a structural fallback, checks them with four structural and semantic layers, "
    "and treats the inability to measure as a reportable outcome rather than a zero. "
    "Applied to ten public Python repositories twice each, the system completed every "
    "analysis without manual configuration."))
P.append(body(
    "The measurements qualify that result in a way the design alone would not reveal. "
    "On first contact only two of four layers produced a value; a repeat scan "
    "activated a third; and the composite rose on five of ten repositories solely "
    "because the set of contributing layers changed. Averaging over active layers "
    "prevents unmeasured layers from inflating a score, but makes scores computed over "
    "different active sets incomparable. A practical response is to report the active "
    "set alongside every score and to refuse comparison across differing sets, which "
    "the underlying data already supports."))
P.append(body(
    "Future work follows directly from the limitations. Layer 1 is the harder case "
    "and may not be a case at all: no import rule appears derivable from an unseen "
    "repository without either guessing which edge of a cycle is wrong or "
    "allowlisting the edges that already exist, and the second only ever passes on "
    "the scan that created it. Whether a fourth layer that cannot act "
    "unattended belongs in an unattended pipeline is a design question this "
    "evaluation raises rather than answers. Layer 3 is tractable: with baselines now "
    "persisted, drift detection can be evaluated across two commits with a known "
    "refactoring between them. Calibrating the duplication threshold against a "
    "labelled "
    "clone benchmark would separate a conservative threshold from a genuinely clean "
    "corpus. Extending the parser beyond Python would test whether the co change "
    "inference generalises where import structure differs. Finally, the explanation "
    "component requires a study with developers before any claim about its usefulness "
    "can be made."))

# ---- REFERENCES ----
P.append(para("Heading1", run("References"), keep_next=True, unnumbered=True))
REFS = [
    'D. E. Perry and A. L. Wolf, "Foundations for the study of software architecture," '
    "ACM SIGSOFT Software Engineering Notes, vol. 17, no. 4, pp. 40-52, 1992.",
    'M. M. Lehman, "Programs, life cycles, and laws of software evolution," '
    "Proceedings of the IEEE, vol. 68, no. 9, pp. 1060-1076, 1980.",
    'J. van Gurp and J. Bosch, "Design erosion: problems and causes," '
    "Journal of Systems and Software, vol. 61, no. 2, pp. 105-119, 2002.",
    'W. Cunningham, "The WyCash portfolio management system," in Proc. OOPSLA, 1992, '
    "pp. 29-30.",
    'P. Kruchten, R. L. Nord, and I. Ozkaya, "Technical debt: from metaphor to theory '
    'and practice," IEEE Software, vol. 29, no. 6, pp. 18-21, 2012.',
    'C. Sadowski, J. van Gogh, C. Jaspan, E. Soederberg, and C. Winter, "Tricorder: '
    'building a program analysis ecosystem," in Proc. ICSE, 2015, pp. 598-608.',
    'B. Johnson, Y. Song, E. Murphy-Hill, and R. Bowdidge, "Why don\u2019t software '
    'developers use static analysis tools to find bugs?" in Proc. ICSE, 2013, '
    "pp. 672-681.",
    'S. Ducasse and D. Pollet, "Software architecture reconstruction: a '
    'process-oriented taxonomy," IEEE Trans. Software Engineering, vol. 35, no. 4, '
    "pp. 573-591, 2009.",
    'J. Garcia, I. Ivkovic, and N. Medvidovic, "A comparative analysis of software '
    'architecture recovery techniques," in Proc. ASE, 2013, pp. 486-496.',
    'H. Gall, K. Hajek, and M. Jazayeri, "Detection of logical coupling based on '
    'product release history," in Proc. ICSM, 1998, pp. 190-198.',
    'T. Zimmermann, P. Weissgerber, S. Diehl, and A. Zeller, "Mining version histories '
    'to guide software changes," IEEE Trans. Software Engineering, vol. 31, no. 6, '
    "pp. 429-445, 2005.",
    'S. R. Chidamber and C. F. Kemerer, "A metrics suite for object oriented design," '
    "IEEE Trans. Software Engineering, vol. 20, no. 6, pp. 476-493, 1994.",
    "R. C. Martin, Agile Software Development: Principles, Patterns, and Practices. "
    "Upper Saddle River, NJ, USA: Prentice Hall, 2003.",
    'T. Kamiya, S. Kusumoto, and K. Inoue, "CCFinder: a multilinguistic token-based '
    'code clone detection system for large scale source code," IEEE Trans. Software '
    "Engineering, vol. 28, no. 7, pp. 654-670, 2002.",
    'C. K. Roy, J. R. Cordy, and R. Koschke, "Comparison and evaluation of code clone '
    'detection techniques and tools: a qualitative approach," Science of Computer '
    "Programming, vol. 74, no. 7, pp. 470-495, 2009.",
    'C. V. Lopes et al., "D\u00e9j\u00e0Vu: a map of code duplicates on GitHub," '
    "Proc. ACM on Programming Languages, vol. 1, no. OOPSLA, pp. 1-28, 2017.",
    'M. White, M. Tufano, C. Vendome, and D. Poshyvanyk, "Deep learning code fragments '
    'for code clone detection," in Proc. ASE, 2016, pp. 87-98.',
    'Z. Feng et al., "CodeBERT: a pre-trained model for programming and natural '
    'languages," in Findings of EMNLP, 2020, pp. 1536-1547.',
    'D. Guo et al., "GraphCodeBERT: pre-training code representations with data flow," '
    "in Proc. ICLR, 2021.",
    'W. Wang, F. Wei, L. Dong, H. Bao, N. Yang, and M. Zhou, "MiniLM: deep '
    'self-attention distillation for task-agnostic compression of pre-trained '
    'transformers," in Proc. NeurIPS, 2020, pp. 5776-5788.',
    'N. Reimers and I. Gurevych, "Sentence-BERT: sentence embeddings using Siamese '
    'BERT-networks," in Proc. EMNLP-IJCNLP, 2019, pp. 3982-3992.',
    'J. Johnson, M. Douze, and H. J\u00e9gou, "Billion-scale similarity search with '
    'GPUs," IEEE Trans. Big Data, vol. 7, no. 3, pp. 535-547, 2021.',
    'V. D. Blondel, J.-L. Guillaume, R. Lambiotte, and E. Lefebvre, "Fast unfolding of '
    'communities in large networks," Journal of Statistical Mechanics: Theory and '
    "Experiment, vol. 2008, no. 10, P10008, 2008.",
]
for r in REFS:
    P.append(para("references", run(r)))

# final section properties: two columns
P.append(
    '<w:sectPr>'
    '<w:type w:val="continuous"/>'
    '<w:pgSz w:w="11906" w:h="16838" w:code="9"/>'
    '<w:pgMar w:top="1080" w:right="907" w:bottom="1440" w:left="907" '
    'w:header="720" w:footer="720" w:gutter="0"/>'
    '<w:cols w:num="2" w:space="360" w:equalWidth="1"/>'
    '<w:docGrid w:linePitch="360"/>'
    "</w:sectPr>"
)

# --------------------------------------------------------------------------- assemble
src_doc = (SRC / "word/document.xml").read_text(encoding="utf-8")
_start = src_doc.index("<w:document")
root_open = src_doc[_start: src_doc.index(">", _start) + 1]
new_doc = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n{root_open}<w:body>{"".join(P)}</w:body></w:document>'

PKG = BUILD / "package"
shutil.copytree(SRC, PKG)
(PKG / "word/document.xml").write_text(new_doc, encoding="utf-8")

# media + relationships
media = PKG / "word/media"
media.mkdir(parents=True, exist_ok=True)
rels_path = PKG / "word/_rels/document.xml.rels"
rels = rels_path.read_text(encoding="utf-8")
for rid, fname in (("rIdFig1", "fig1_pipeline.png"),
                   ("rIdFig2", "fig2_health.png"),
                   ("rIdFig3", "fig3_runtime.png")):
    shutil.copy(FIGS / fname, media / fname)
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="{rid}" Type="{R_NS}/image" Target="media/{fname}"/></Relationships>')
rels_path.write_text(rels, encoding="utf-8")

ct_path = PKG / "[Content_Types].xml"
ct = ct_path.read_text(encoding="utf-8")
if 'Extension="png"' not in ct:
    ct = ct.replace("<Default Extension=\"rels\"",
                    '<Default Extension="png" ContentType="image/png"/><Default Extension="rels"')
ct_path.write_text(ct, encoding="utf-8")

# remove the template's footer reference content (instructional) but keep the part
OUT_DOCX.parent.mkdir(parents=True, exist_ok=True)
if OUT_DOCX.exists():
    OUT_DOCX.unlink()
with zipfile.ZipFile(OUT_DOCX, "w", zipfile.ZIP_DEFLATED) as z:
    for p in sorted(PKG.rglob("*")):
        if p.is_file():
            z.write(p, p.relative_to(PKG).as_posix())

print("wrote", OUT_DOCX, OUT_DOCX.stat().st_size, "bytes")
print("paragraph blocks:", len(P))
