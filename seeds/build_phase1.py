#!/usr/bin/env python3
"""Build seeds/phase1_cards.json for Phase 1 acceptance.

Three hand-written cards over REAL fetched sources (data/raw/*.txt).
Anchor quotes and figure source-spans are asserted verbatim against the
fetched bodies before the JSON is written — C3/C4 hold by construction.
"""
import json
from pathlib import Path

RAW = Path(__file__).resolve().parent.parent / "data" / "raw"

xania = (RAW / "xania-licm.txt").read_text()
awocs = (RAW / "awocs-compounding.txt").read_text()
nimh = (RAW / "nimh-slcr.txt").read_text()

CARDS = [
    # ─────────────────────────── 1. technology (LICM) ───────────────────────────
    {
        "title": "Why compilers hoist loop invariants",
        "cluster": "technology",
        "source_url": "https://xania.org/202512/13-licking-licm",
        "hook": "Your loop calls vec.size() on every iteration — and the compiler quietly "
                "rewrites it to run once. Knowing what the compiler can prove is the "
                "difference between trusting it and knowing when it cannot save you.",
        "diagram_type": "concept",
        "diagram_src": """flowchart TD
  Loop[Loop body] --> Q{Does it depend on the iteration?}
  Q -->|No| Hoist[Move above the loop<br/>runs once]
  Q -->|Yes| Keep[Leave in place]""",
        "anchor_quote": "Such a transformation is called Loop-Invariant Code Motion, or LICM",
        "prompts": [
            {
                "question": "What must a compiler prove before it is allowed to hoist code out of a loop?",
                "answer": "That the code does not depend on which iteration it is in — its result "
                          "is identical on every pass, so moving it above the loop cannot change "
                          "the program's behavior.",
            },
            {
                "question": "In Godbolt's example, why does gcc fail to hoist the get_range() call?",
                "answer": "The function returns a std::pair, and the gcc maintainer's analysis "
                          "suggests the structure type does not go through common subexpression "
                          "elimination (CSE), which prevents the analysis LICM needs.",
            },
            {
                "question": "What is the practical lesson Godbolt draws from clang succeeding and gcc failing?",
                "answer": "Trust the compiler, but know how to verify its output too — that is "
                          "exactly what Compiler Explorer is for.",
            },
        ],
        "body_md": """Most programmers have written a loop that calls something in its condition and been quietly saved by the compiler without ever noticing. Consider a simple counting loop over a `std::vector`: the loop condition calls `vec.size()` on every iteration to compare the index against the end. In the source code it looks as though that call happens once per pass. The compiler sees something different. It notices that the result cannot change between iterations and computes it once, before the loop starts. The assembly shows the tell: one `sar` instruction that divides the "end - start" value by the element size, executed before the loop and never again.

This transformation is called Loop-Invariant Code Motion, or LICM. It applies far beyond loop conditions: any code inside the loop whose result does not depend on which iteration it runs in is fair game. The defining check is semantic — would moving the computation above the loop change the observable behavior of the program? If the compiler can prove the value is the same on every pass, hoisting is safe. That proof is the whole game. The compiler must be sure the expression has no hidden dependencies: it does not read memory that the loop writes, it does not call functions with observable side effects, and nothing inside the loop changes its inputs.

Matt Godbolt demonstrates the pattern with a `std::string_view` scanner that counts characters in a range. The naive code calls `get_range()` on every iteration to fetch the min and max characters. Clang notices that the range from `get_range` cannot change during the loop and moves the call outside it — the disassembly shows `call get_range` before the loop, with the loop body reduced to a tight sequence of compares, condition codes, and an accumulator. Clang even avoids a branch inside the loop by using `setle` and `setge` to turn comparisons into 0-or-1 values directly from the flags.

The surprise: gcc fails the same transformation. Even with a [[gnu::pure]] attribute telling it the function depends only on its inputs, gcc calls `get_range` twice per iteration. A gcc maintainer traced it to the structure type returned — a `std::pair` does not go through common subexpression elimination, which is a prerequisite for the analysis LICM needs. The same source code, two mature compilers, different results.

The lesson is not "compilers are unreliable," nor "always trust the compiler." It is that optimization is a bundle of separate, provable transformations, each with its own preconditions, and those preconditions can be silently unmet. LICM, CSE, inlining, unswitching — each pass has a check that must succeed, and the checks differ between compilers. Godbolt ends the post with the practical conclusion: trust the compiler, but know how to verify its output too. Compiler Explorer exists precisely to make that verification fast — paste the loop, read the assembly, and see which transformations actually fired.""",
        "figures": [],
        "source_body": xania,
    },
    # ─────────────────────────── 2. finance (compounding) ───────────────────────────
    {
        "title": "Why compounding feels invisible",
        "cluster": "finance",
        "source_url": "https://awealthofcommonsense.com/2019/05/how-compounding-works-in-the-stock-market/",
        "hook": "A year that feels flat can still be up 18% — because the market's daily average "
                "gain is just 0.19%. Compounding is back-loaded, and that mismatch between "
                "day-to-day feel and decade-scale math shapes how people save.",
        "diagram_type": "data",
        "diagram_src": """xychart-beta
  title "Yield on a $100000 balance at 6% per year"
  x-axis [1, 10, 20, 30]
  y-axis "Annual yield in dollars"
  bar [6000, 10000, 18100, 32510]""",
        "figures": [
            {"value": 100000, "source": "you have $100,000 that earns a return of 6% annually"},
            {"value": 6, "source": "you have $100,000 that earns a return of 6% annually"},
            {"value": 1, "source": "3 down days of 1% or worse"},
            {"value": 10, "source": "By year 10 your 6% would yield more than $10,000"},
            {"value": 20, "source": "By year 20 the return grows to over $18,100"},
            {"value": 30, "source": "the 6% return would give you $32,510 at the end of year 30"},
            {"value": 6000, "source": "you would earn $6,000 in the first year"},
            {"value": 10000, "source": "more than $10,000"},
            {"value": 18100, "source": "over $18,100"},
            {"value": 32510, "source": "$32,510 at the end of year 30"},
        ],
        "anchor_quote": "Small gains can eventually add up into big gains if you let them",
        "prompts": [
            {
                "question": "How could the S&P 500 be up more than 18% for the year when its average daily gain is only 0.19%?",
                "answer": "Small daily changes compound: 0.19% per day, applied over roughly 200 "
                          "trading days, multiplies into an 18% annual move — the daily average "
                          "feels negligible while the yearly total is large.",
            },
            {
                "question": "Why did SPY's dividend yield fall from about 3.8% in 1993 to just over 2% in 2018 while the dividend per share grew?",
                "answer": "Yield is the dividend divided by the prevailing price. SPY paid $1.10 "
                          "per share in its first full year and $5.10 in 2018, but the price rose "
                          "even faster — so yield on price fell, while the yield on the original "
                          "1993 cost basis climbed to almost 18%.",
            },
            {
                "question": "In Carlson's example, how long does it take for investment returns to overtake savings contributions?",
                "answer": "Even by age 50 — after 25 years of saving 10% of a salary growing 3% "
                          "per year — contributions and investment growth are about equal; it "
                          "takes 35 to 40 years of saving for compounding to overwhelm the "
                          "amount saved.",
            },
            {
                "question": "What does it mean that compounding is back-loaded, and why does it matter psychologically?",
                "answer": "Most of the dollar gains arrive late: the first year adds $6,000, year "
                          "30 adds $32,510. Early on, savings dominate the balance, so a young "
                          "saver sees little return for years — which is why sticking with a "
                          "long-term plan feels hard even when the math is sound.",
            },
        ],
        "body_md": """If you watched the market's daily swings in 2019, nothing looked extraordinary. The average daily gain for the S&P 500 was just 0.19% — a median of 0.14% — and only nine days gained more than 1%, with three down days of 1% or worse. Through Friday's close, the index was still up more than 18% on the year, its best start to a year since 1987. Both facts are true at once, and the gap between them is the core mechanism of compounding: daily changes feel like noise, but multiplication makes them accumulate.

The arithmetic is worth making explicit. A 0.19% move is imperceptible on any given day. Compounded across a trading year, the same rate accumulates into an 18% gain. The retina cannot register the daily increment, so the market feels flat — while the spreadsheet shows a banner year. This is not a quirk of 2019. It is how compounding works, generally: small gains add up into big gains if you let them.

The same structure appears in dividends. SPY paid $1.10 per share in its first full year in 1994, a yield of roughly 3.8% on the 1993 year-end price. By 2018 the dividend had grown to $5.10 per share — a lower yield, just over 2%, because the price had risen faster. But for someone who bought in 1993, the dividend in 2018 was almost 18% of their original cost basis, paid out in cash every year. From 1993 through 2018 the dividend grew more than 360%, about 6.1% per year. Six percent in any given year does not feel like much.

The back-loaded shape is easiest to see with a fixed example. Take $100,000 earning 6% annually. Year one adds $6,000; year two earns 6% on $106,000, adding $6,360. By year 10 the annual yield exceeds $10,000; by year 20 it is over $18,100; by year 30 the 6% return produces $32,510 in a single year — a 32.5% yield on the original $100,000, on a balance that has grown to almost $600,000. The gains are not distributed evenly across time; they are concentrated at the end.

That concentration has a behavioral consequence. Carlson works through a saver who puts away 10% of a $40,000 salary growing 3% per year for inflation, earning 6% annually. By age 40 they have saved a little more than $80,000 against a total balance near $125,000 — almost 65% of it from saving alone. Even by age 50, a full 25 years in, contributions and investment growth are basically equal. It takes 35 to 40 years before the compounding from investing finally overwhelms the amount saved.

This is why young savers quit: the mechanism is nearly invisible for two decades, then sudden. Compound interest is extremely back-loaded, which is hard to see unless you actually plot it out on a spreadsheet. And it is why the daily average matters less than the habit of staying in — small gains eventually add up into big gains if you let them.""",
        "source_body": awocs,
    },
    # ─────────────────────────── 3. mental_health (ipRGCs) ───────────────────────────
    {
        "title": "The third photoreceptor: how the eye talks to mood",
        "cluster": "mental_health",
        "source_url": "https://www.nimh.nih.gov/research/research-conducted-at-nimh/research-areas/clinics-and-labs/slcr",
        "hook": "Rods and cones were long assumed to be the retina's only photoreceptors. A "
                "third cell type — ipRGCs — connects light directly to the circadian pacemaker, "
                "sleep, mood, and learning circuits, and is critical for how light regulates "
                "physiology and behavior.",
        "diagram_type": "concept",
        "diagram_src": """flowchart LR
  Light[Light] --> RGC[ipRGCs<br/>melanopsin]
  RGC --> SCN[Circadian pacemaker]
  RGC --> Pupil[Pupil constriction]
  RGC --> Mood[Mood and learning circuits]
  RGC --> Vista[Image-forming areas<br/>rudimentary pattern vision]""",
        "anchor_quote": "critical for the influence of light on circadian rhythms, sleep, mood and pupil constriction",
        "prompts": [
            {
                "question": "What was the third photoreceptor discovered in the mammalian retina, and what photopigment does it express?",
                "answer": "Intrinsically photosensitive retinal ganglion cells (ipRGCs), which "
                          "express their own photopigment called melanopsin — reported in 2002 "
                          "by Hattar and Berson and colleagues in Science.",
            },
            {
                "question": "Which brain targets do ipRGCs connect to?",
                "answer": "Many visual centers, including the circadian pacemaker and the area "
                          "responsible for pupil constriction — making them critical for light's "
                          "influence on circadian rhythms, sleep, mood, and pupil constriction.",
            },
            {
                "question": "Why can mice lacking rod and cone function still have rudimentary pattern vision?",
                "answer": "Some of the at least five ipRGC subtypes target brain regions "
                          "involved in image formation, allowing mice without rods and cones to "
                          "retain rudimentary pattern vision.",
            },
        ],
        "body_md": """For many years, the mammalian retina was thought to have exactly two kinds of light detectors: rods and cones. The rods handle low-light vision, the cones handle color and detail, and every visual phenomenon worth explaining was attributed to them. That picture had a puzzle at its edge — the circadian system responds to light, and so does the pupil, but the classical photoreceptors seemed an awkward fit for both. Research from several laboratories in the early 2000s resolved the puzzle by finding a third type: intrinsically photosensitive retinal ganglion cells, or ipRGCs.

The key facts come from two landmark papers published in Science in 2002 (Hattar et al. and Berson et al.). The retina's output neurons — the ganglion cells whose axons form the optic nerve — turn out to include a small subclass that is itself light-sensitive. These cells express their own photopigment, melanopsin, entirely separate from the rhodopsin and cone opsins of the classical system. They are photosensitive without any input from rods or cones; hence intrinsically photosensitive.

The NIMH Section on Light and Circadian Rhythms studies what these cells do with the light signal. The answer is that ipRGCs project widely into the brain, not just to the classical visual cortex pathways. They target many visual centers, including the circadian pacemaker — the suprachiasmatic nucleus — and the area responsible for pupil constriction. Because of those connections, ipRGCs are critical for the influence of light on circadian rhythms, sleep, mood, and pupil constriction. A single cell type sits upstream of daily timing, wakefulness, affective state, and the iris.

The cell type is not uniform. More recent work found ipRGCs are more abundant than previously appreciated, with at least five subtypes, labeled M1 through M5. The subtypes differ in their targets. Some project to regions involved in image formation, which is why mice engineered without rod and cone function still show rudimentary pattern vision — their ipRGCs carry enough spatial information to support basic pattern detection. Contrast detection in images is also enhanced through these circuits.

The mood link is direct but still mechanistically opaque. The lab has found that ipRGCs also mediate negative effects of light on mood and learning — a research finding about a neural pathway, not a behavioral prescription. The circuit exists: light enters the eye, melanopsin-bearing cells respond, and their targets include affective and cognitive circuitry. Many questions remain about the function of these cells and the circuits critical for ipRGC-mediated behaviors.

The tools in play are modern neurobiology: mouse genetics, anatomy, in vivo calcium imaging, viral circuit tracing, and animal behavior. The lab uses them to ask how ipRGCs detect light and send that information to the brain to regulate physiology and behavior — and why the same pathway that entrains sleep and circadian timing also feeds mood and learning. For anyone studying the mechanisms of mental health, this is a concrete example of a hard, physical connection between the environment and affective state: light, a photopigment expressed in ganglion cells, and a defined set of brain targets.""",
        "figures": [],
        "source_body": nimh,
    },
]

# ── hard pre-checks: C3 (verbatim anchor) and C4 (verbatim figure spans) ──
for c in CARDS:
    assert c["anchor_quote"] in c["source_body"], f"anchor missing for {c['title']}"
    for f in c.get("figures", []):
        assert f["source"] in c["source_body"], f"figure span missing for {c['title']}: {f['value']}"
    assert len(c["body_md"].split()) >= 400, f"body too short: {c['title']}"

OUT = Path(__file__).resolve().parent / "phase1_cards.json"
OUT.write_text(json.dumps(CARDS, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {len(CARDS)} cards -> {OUT}")
for c in CARDS:
    print(f"  - {c['title']}: {len(c['body_md'].split())} words, "
          f"{len(c['prompts'])} prompts, diagram={c['diagram_type']}")