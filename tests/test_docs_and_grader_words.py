"""
Offline tests for two word-level checkers: the ASD-STE100 docs check (scripts/ste_check.py) and the
negation handling of the geometry-claim verifier (packs/designreview/checks.py).    Markers: none

Author(s): ____________    Written by hand: [ ] yes

Background: docs/CHANGES.md entries of 2026-10-14 ("The docs in ASD-STE100", "geometry disclaimers");
tests/MANUAL_TEST_LOG.md row A-LIVE-07 (a live t10 run failed on a true disclaimer). Both exist today.

Use cases to cover:
  STE check (scripts/ste_check.py: check_file(path) -> (sentence count, [Finding]); main(argv) -> exit code):
  [x] a sentence of 26 words -> one "sentence length" finding; 25 words -> none
  [ ] TODO: a list item that starts "Run ..." with 21 words -> a finding (instructions: 20 words or fewer)
  [ ] TODO: a paragraph of 7 short sentences -> one "paragraph" finding
  [x] "The file is removed by the gateway." -> "passive voice"; "The gateway removes the file." -> none
  [x] "It doesn't stop." -> "contraction"; "It does not stop." -> none
  [ ] TODO: "e.g." and "allows" -> "unapproved word" findings that name "for example" and "lets"
  [ ] TODO: the same passive or contraction inside "double quotes" -> no finding (quotations keep their wording)
  [ ] TODO: text in a ``` code fence or in `inline code` -> no finding
  [ ] TODO: "**Bold lead-in.** Then a second sentence." counts as two sentences
  [x] main([<clean file>]) returns 0; main([<file with a finding>]) returns 1; a missing file returns 2
  [ ] TODO: the shipped docs pass: main([]) returns 0 (README.md, docs/ARCHITECTURE.md, docs/EXPLAINERS.md,
      docs/explainers/*.md)

  Geometry disclaimers (packs.designreview.checks.geometry_claims, says_ready):
  [x] "Nothing in this answer comes from a geometry diff, 3D comparison or CAD analysis." -> no claim
  [x] "Neither a CAD comparison nor a 3D overlay was run." and "None of this is from a geometry comparison."
      -> no claim
  [x] "The geometry comparison shows the wall is 2.5 mm thicker." -> a claim
  [x] "The geometry comparison shows nonetheless a thicker wall." -> a claim ("nonetheless" denies nothing)
  [x] says_ready("Nothing blocks it; it is ready for release.") is still True (the new words apply only to
      the geometry check)

More use cases (added 2026-10-16, to write by hand):
  [ ] TODO: a Markdown table cell is checked on its own: a 26-word cell -> one "sentence length" finding,
      and a list item that starts "Run" inside a table cell keeps the 25-word limit
  [ ] TODO: "is open", "is given", "is broken" are not passive voice (NOT_PARTICIPLE); "is removed" is
  [ ] TODO: a link "[the guide](docs/a_very_long_path.md)" counts the link text only, not the target
Hint: scripts/ is not a package. Load the script with
importlib.util.spec_from_file_location("ste_check", <repo>/scripts/ste_check.py), then module_from_spec and
spec.loader.exec_module. Put the module in sys.modules["ste_check"] BEFORE exec_module: the script has
a @dataclass, which looks its module up there. Write sample Markdown files to tmp_path.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

from packs.designreview.checks import geometry_claims, says_ready

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ste_check", ROOT / "scripts" / "ste_check.py")
ste = importlib.util.module_from_spec(spec)
sys.modules["ste_check"] = ste  # its @dataclass looks the module up here
spec.loader.exec_module(ste)


def _rules(tmp_path, text):
    p = tmp_path / "doc.md"
    p.write_text(text, encoding="utf-8")
    return [f.rule for f in ste.check_file(p)[1]]


def test_sentence_of_26_words_is_too_long_25_is_not(tmp_path):
    assert _rules(tmp_path, " ".join(["word"] * 26) + ".\n") == ["sentence length"]
    assert _rules(tmp_path, " ".join(["word"] * 25) + ".\n") == []


@pytest.mark.parametrize("bad, good, rule", [
    ("The file is removed by the gateway.", "The gateway removes the file.", "passive voice"),
    ("It doesn't stop.", "It does not stop.", "contraction"),
])
def test_passive_voice_and_contractions_are_found(tmp_path, bad, good, rule):
    assert _rules(tmp_path, bad + "\n") == [rule]
    assert _rules(tmp_path, good + "\n") == []


def test_main_exit_codes_clean_finding_and_missing(tmp_path, capsys):
    clean = tmp_path / "clean.md"
    clean.write_text("The gateway removes the file.\n", encoding="utf-8")
    dirty = tmp_path / "dirty.md"
    dirty.write_text("It doesn't stop.\n", encoding="utf-8")
    assert ste.main([str(clean)]) == 0
    assert ste.main([str(dirty)]) == 1
    assert ste.main([str(tmp_path / "missing.md")]) == 2


def test_geometry_disclaimers_are_not_claims_but_real_claims_are():
    for disclaimer in (
        "Nothing in this answer comes from a geometry diff, 3D comparison or CAD analysis.",
        "Neither a CAD comparison nor a 3D overlay was run.",
        "None of this is from a geometry comparison.",
    ):
        assert geometry_claims(disclaimer) == [], disclaimer
    assert geometry_claims("The geometry comparison shows the wall is 2.5 mm thicker.")
    assert geometry_claims("The geometry comparison shows nonetheless a thicker wall.")
    assert says_ready("Nothing blocks it; it is ready for release.") is True

