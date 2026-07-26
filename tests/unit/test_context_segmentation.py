"""context分割と構文安全性を検証する。

Two properties matter and they pull in opposite directions:

- segmentation must be LOSSLESS — the spans tile the input exactly, so masking a
  segmented body cannot lose or reorder a byte of the user's Markdown, shell,
  JSON, YAML or diff;
- the detector policy must differ by span — fuzzy NER stays out of code, while
  the dictionary and the deterministic secret detectors run everywhere, because
  a real key pasted into a fence is still a real key.

Synthetic data only.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from securitymasker.context import (
    MAX_SEGMENTS,
    coalesce_for_detection,
    is_code_like,
    segment,
)
from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.dictionary import DictionaryDetector, DictionaryEntry
from securitymasker.detectors.secret_patterns import build_secret_detector
from securitymasker.engine import MaskingEngine
from securitymasker.models import ContextKind, EntityType, ReplacementProfile, RestorePolicy
from securitymasker.sessions.memory import InMemorySessionStore

PERSON = "山田太郎"
SECRET = "sk-ant-" + "a" * 30


# --- losslessness ----------------------------------------------------------------


SAMPLES = [
    "plain prose only",
    "prose\n\n```python\nx = 1\n```\n\nmore prose",
    "inline `code` here",
    "```\nno language\n```",
    "~~~js\nlet a = 1;\n~~~",
    "```markdown\nnested ``` inside\n```",
    "unclosed ```python\nx = 1",
    "--- a/f.py\n+++ b/f.py\n@@ -1 +1 @@\n-a\n+b\n",
    "```bash\ncurl -X POST 'https://h.example' | jq .\n```",
    "text with `a` and `b` and ```\nblock\n```",
    "",
    "\n\n\n",
    "日本語のprose\n```python\nuser = '山田'\n```\n続き",
]


@pytest.mark.parametrize("text", SAMPLES)
def test_segmentation_is_lossless(text) -> None:
    segments = segment(text)
    assert "".join(s.text for s in segments) == text


@pytest.mark.parametrize("text", SAMPLES)
def test_segments_tile_without_gaps_or_overlap(text) -> None:
    segments = segment(text)
    cursor = 0
    for s in segments:
        assert s.start == cursor, "gap or overlap between segments"
        assert s.text == text[s.start:s.end]
        cursor = s.end
    assert cursor == len(text)


@given(st.text(max_size=400))
def test_segmentation_is_lossless_for_arbitrary_text(text) -> None:
    assert "".join(s.text for s in segment(text)) == text


@given(st.lists(st.sampled_from([
    "prose ", "\n", "```py\nx=1\n```\n", "`inline`", "~~~\nblk\n~~~\n",
    "--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n+b\n",
]), max_size=8))
def test_segmentation_is_lossless_for_composed_fragments(parts) -> None:
    text = "".join(parts)
    assert "".join(s.text for s in segment(text)) == text


# --- classification ---------------------------------------------------------------


def _kinds(text: str) -> list[str]:
    return [s.kind for s in segment(text)]


def test_fenced_python_is_source_code() -> None:
    assert ContextKind.SOURCE_CODE.value in _kinds("a\n```python\nx=1\n```\nb")


def test_fenced_shell_is_shell() -> None:
    assert ContextKind.SHELL.value in _kinds("a\n```bash\nls -la\n```\nb")


def test_fenced_json_and_yaml_are_classified() -> None:
    assert ContextKind.JSON_STRING.value in _kinds('```json\n{"a":1}\n```')
    assert ContextKind.YAML_SCALAR.value in _kinds("```yaml\na: 1\n```")


def test_bare_unified_diff_is_diff() -> None:
    assert ContextKind.DIFF.value in _kinds("--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n")


def test_inline_code_is_classified() -> None:
    assert ContextKind.MARKDOWN_INLINE_CODE.value in _kinds("see `x = 1` above")


def test_unknown_text_stays_prose_the_widest_policy() -> None:
    # Ambiguity must err toward MORE scanning, never less.
    assert _kinds("just some words") == [ContextKind.PROSE.value]


def test_code_like_covers_every_non_prose_code_kind() -> None:
    for kind in (ContextKind.SOURCE_CODE, ContextKind.SHELL, ContextKind.DIFF,
                 ContextKind.JSON_STRING, ContextKind.YAML_SCALAR,
                 ContextKind.MARKDOWN_CODE, ContextKind.MARKDOWN_INLINE_CODE):
        assert is_code_like(kind.value)
    assert not is_code_like(ContextKind.PROSE.value)


# --- detector policy per context ---------------------------------------------------


class _FuzzyStub:
    """HF NERの代役としてcodeを除外し、実行箇所を記録する。"""

    name = "jp_ner"
    skip_code_contexts = True

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def detect(self, context: DetectionContext) -> list:
        self.seen.append(context.context_kind)
        return []


def _engine(*detectors) -> MaskingEngine:
    return MaskingEngine(list(detectors))


@pytest.mark.asyncio
async def test_fuzzy_ner_skips_code_but_runs_in_prose() -> None:
    fuzzy = _FuzzyStub()
    await _engine(fuzzy).detect("prose here\n```python\nx = 1\n```\nmore prose")
    assert ContextKind.PROSE.value in fuzzy.seen
    assert not any(is_code_like(k) for k in fuzzy.seen), fuzzy.seen


@pytest.mark.asyncio
async def test_secret_detector_still_fires_inside_a_code_fence() -> None:
    # Invariant 8: a real secret in code is still a secret.
    engine = _engine(build_secret_detector())
    hits = await engine.detect(f"look:\n```python\nkey = '{SECRET}'\n```\n")
    assert [h for h in hits if h.entity_type == EntityType.API_KEY.value]


@pytest.mark.asyncio
async def test_dictionary_still_fires_inside_a_code_fence() -> None:
    dictionary = DictionaryDetector([DictionaryEntry(
        EntityType.PERSON.value, (PERSON,),
        ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])
    hits = await _engine(dictionary).detect(f"```python\nuser = '{PERSON}'\n```")
    assert len(hits) == 1


@pytest.mark.asyncio
async def test_offsets_stay_absolute_after_segmentation() -> None:
    dictionary = DictionaryDetector([DictionaryEntry(
        EntityType.PERSON.value, (PERSON,),
        ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])
    text = f"intro\n\n```python\nuser = '{PERSON}'\n```\n\noutro"
    hits = await _engine(dictionary).detect(text)
    assert len(hits) == 1
    hit = hits[0]
    # The span must index the ORIGINAL string, not the segment.
    assert text[hit.start:hit.end] == PERSON


# --- syntax preservation after masking ---------------------------------------------


async def _mask(text: str, *detectors) -> str:
    session = await InMemorySessionStore().get_or_create("s")
    return (await _engine(*detectors).mask_text(session, text)).masked_text


def _dictionary():
    return DictionaryDetector([DictionaryEntry(
        EntityType.PERSON.value, (PERSON,),
        ReplacementProfile.PROSE_IDENTIFIER.value, RestorePolicy.LITERAL.value)])


@pytest.mark.asyncio
async def test_markdown_fences_survive_masking() -> None:
    text = f"見て:\n\n```python\nuser = \"{PERSON}\"\n```\n\n以上。"
    masked = await _mask(text, _dictionary())
    assert PERSON not in masked
    assert masked.count("```") == 2
    assert masked.startswith("見て:") and masked.endswith("以上。")


@pytest.mark.asyncio
async def test_inline_backticks_survive_masking() -> None:
    text = f"`{PERSON}` を参照"
    masked = await _mask(text, _dictionary())
    assert PERSON not in masked
    assert masked.startswith("`") and "`" in masked[1:]


@pytest.mark.asyncio
async def test_masked_json_still_parses() -> None:
    payload = json.dumps({"owner": PERSON, "n": 1, "nested": {"who": PERSON}},
                         ensure_ascii=False)
    masked = await _mask(payload, _dictionary())
    parsed = json.loads(masked)              # must still be valid JSON
    assert PERSON not in masked
    assert parsed["n"] == 1 and set(parsed) == {"owner", "n", "nested"}


@pytest.mark.asyncio
async def test_masked_json_with_escapes_still_parses() -> None:
    payload = json.dumps({"note": f'{PERSON}\n"quoted"\ttab\\back'}, ensure_ascii=False)
    masked = await _mask(payload, _dictionary())
    parsed = json.loads(masked)
    assert PERSON not in masked
    assert '"quoted"' in parsed["note"] and "\t" in parsed["note"]


@pytest.mark.asyncio
async def test_masked_yaml_still_parses() -> None:
    import yaml

    doc = f"owner: {PERSON}\nitems:\n  - a\n  - b\nblock: |\n  line1 {PERSON}\n  line2\n"
    masked = await _mask(doc, _dictionary())
    parsed = yaml.safe_load(masked)          # must still be valid YAML
    assert PERSON not in masked
    assert parsed["items"] == ["a", "b"] and "line2" in parsed["block"]


@pytest.mark.asyncio
async def test_masked_shell_keeps_quoting_and_pipes() -> None:
    import shlex

    cmd = f"grep '{PERSON}' file.txt | wc -l > out.txt"
    masked = await _mask(cmd, _dictionary())
    assert PERSON not in masked
    parts = shlex.split(masked)              # must still lex as a shell command
    assert "|" in masked and ">" in masked
    assert parts[0] == "grep"


@pytest.mark.asyncio
async def test_masked_diff_still_applies() -> None:
    """A masked patch must remain a patch: `git apply` has to accept it."""
    patch = (
        "--- a/file.txt\n"
        "+++ b/file.txt\n"
        "@@ -1,3 +1,3 @@\n"
        " line one\n"
        f"-owner: {PERSON}\n"
        f"+owner: {PERSON} (updated)\n"
        " line three\n"
    )
    masked = await _mask(patch, _dictionary())
    assert PERSON not in masked
    # Structure preserved exactly where a patch parser looks: headers, hunk
    # header, and one added/removed line each with their prefixes intact.
    assert masked.startswith("--- a/file.txt\n+++ b/file.txt\n@@ -1,3 +1,3 @@\n")
    body = masked.split("@@ -1,3 +1,3 @@\n", 1)[1].splitlines()
    assert [ln[0] for ln in body] == [" ", "-", "+", " "]

    # `git apply --check` is a blocking subprocess; run it off the event loop.
    def _apply_check(masked_patch: str, body_lines: list[str]) -> tuple[int, str]:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            # Build the target from the masked patch's own context/removed lines,
            # so a successful apply proves the patch is internally consistent.
            content = "".join(
                f"{ln[1:]}\n" for ln in body_lines if ln.startswith((" ", "-"))
            )
            (root / "file.txt").write_text(content, encoding="utf-8")
            patch_file = root / "p.diff"
            patch_file.write_text(masked_patch, encoding="utf-8")
            done = subprocess.run(["git", "apply", "--check", str(patch_file)],
                                  cwd=root, capture_output=True, text=True)
            return done.returncode, done.stderr

    code, stderr = await asyncio.to_thread(_apply_check, masked, body)
    assert code == 0, f"masked patch no longer applies: {stderr}"


@pytest.mark.asyncio
async def test_windows_and_posix_paths_survive_in_code() -> None:
    text = f"```\nC:\\Users\\{PERSON}\\a.txt\n/home/{PERSON}/b.txt\n```"
    masked = await _mask(text, _dictionary())
    assert PERSON not in masked
    assert "C:\\Users\\" in masked and "/home/" in masked
    assert masked.count("```") == 2


@pytest.mark.asyncio
async def test_alias_inside_code_is_not_remasked() -> None:
    session = await InMemorySessionStore().get_or_create("s")
    from securitymasker.detectors.existing_alias import ExistingAliasDetector

    engine = MaskingEngine([ExistingAliasDetector(), _dictionary()])
    once = (await engine.mask_text(session, f"```python\nu = '{PERSON}'\n```")).masked_text
    twice = (await engine.mask_text(session, once)).masked_text
    assert once == twice                       # replay is idempotent inside code
    assert len(session.mappings_by_alias) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("split", range(1, 30))
async def test_masking_is_stable_across_fence_boundary_positions(split) -> None:
    """Whatever the fence lands on, masking never corrupts the surrounding text."""
    body = f"abcdefghij {PERSON} klmnopqrst"
    text = f"{body[:split]}\n```\n{body[split:]}\n```"
    masked = await _mask(text, _dictionary())
    assert PERSON not in masked
    assert masked.count("```") == 2


# --- unfenced block classification ------------------------------------------
# People paste these raw, without a Markdown fence. Before, all of them were prose,
# so fuzzy NER ran over identifiers, commands and patch bodies.


def test_bare_shell_transcript_is_shell() -> None:
    text = "run this:\n$ kubectl get pods -n prod\n$ echo done\nthen check."
    assert ContextKind.SHELL.value in _kinds(text)


def test_bare_json_block_is_json() -> None:
    text = 'config:\n{\n  "service": "api",\n  "port": 8080\n}\nend'
    assert ContextKind.JSON_STRING.value in _kinds(text)


def test_bare_yaml_block_is_yaml() -> None:
    text = "see:\nservice: gateway\nreplicas: 2\nimage: x\ndone"
    assert ContextKind.YAML_SCALAR.value in _kinds(text)


def test_bare_source_code_is_source() -> None:
    text = "look:\ndef handler(request):\n    return None\nthanks"
    assert ContextKind.SOURCE_CODE.value in _kinds(text)


def test_apply_patch_envelope_is_patch() -> None:
    text = ("*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** End Patch\n")
    assert ContextKind.PATCH.value in _kinds(text)


def test_patch_kind_is_actually_produced_somewhere() -> None:
    # The enum member must correspond to something the segmenter emits; an enum
    # value nothing can produce is a documentation claim with no implementation.
    text = "*** Begin Patch\n*** Update File: x\n@@\n-a\n+b\n*** End Patch"
    assert ContextKind.PATCH.value in _kinds(text)


def test_prose_with_a_colon_is_not_mistaken_for_yaml() -> None:
    # One `key: value`-ish line is ordinary prose; YAML needs a run of them.
    assert _kinds("Note: this is a sentence.") == [ContextKind.PROSE.value]


def test_prose_sentence_is_not_mistaken_for_shell() -> None:
    assert _kinds("The cost is $5 per unit.") == [ContextKind.PROSE.value]


@pytest.mark.parametrize("text", [
    "run this:\n$ ls -la\n",
    'cfg:\n{\n  "a": 1\n}\n',
    "a: 1\nb: 2\n",
    "def f():\n    pass\n",
    "*** Begin Patch\n@@\n-a\n+b\n*** End Patch\n",
])
def test_unfenced_classification_stays_lossless(text) -> None:
    assert "".join(s.text for s in segment(text)) == text


# --- DoS bounds --------------------------------------------------------------


def test_segment_count_is_capped() -> None:
    text = "prose `code` " * 400
    assert len(segment(text)) <= MAX_SEGMENTS


def test_detector_invocations_do_not_scale_with_code_span_count() -> None:
    """The regression the audit measured: 8,000 inline spans -> 8,001 NER calls.

    Asserts on the invocation COUNT rather than wall-clock, so it fails on a
    complexity regression regardless of how fast the machine is.
    """
    small = len(coalesce_for_detection(segment("prose `c` " * 50)))
    large = len(coalesce_for_detection(segment("prose `c` " * 400)))
    # 8x the input must not produce 8x the detector work: the cap holds it down,
    # so growth is strictly sublinear and bounded by MAX_SEGMENTS.
    assert large < 8 * small
    assert large <= MAX_SEGMENTS


def test_pathological_input_fails_closed() -> None:
    from securitymasker.context import SegmentationLimitError

    with pytest.raises(SegmentationLimitError):
        segment("prose `c` " * 8000)


@pytest.mark.asyncio
async def test_detector_call_count_is_bounded_end_to_end() -> None:
    """One request must not fan out into thousands of model inferences."""
    calls = {"n": 0}

    class _Counting:
        name = "jp_ner"
        skip_code_contexts = True

        async def detect(self, context):
            calls["n"] += 1
            return []

    await MaskingEngine([_Counting()]).detect("prose `c` " * 400)
    assert calls["n"] <= 512, f"detector invoked {calls['n']} times for one request"


def test_coalescing_preserves_offsets_and_text() -> None:
    from securitymasker.context import coalesce_for_detection

    text = "aaa\n```py\nx=1\n```\nbbb\nccc"
    merged = coalesce_for_detection(segment(text))
    assert "".join(s.text for s in merged) == text
    for s in merged:
        assert text[s.start:s.end] == s.text
