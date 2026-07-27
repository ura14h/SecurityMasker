"""固定したHugging Face token-classification modelによる日本語NER。

標準構成では固定済みmodelをONにする。辞書と決定論的recognizerを信頼の基礎としつつ、
未登録の氏名・法人名・地名を補う。NERの空振りだけを「安全」の根拠にはしない
（不変条件9）。

Safety properties that are not negotiable here, because a NER backend fails in
ways that are silent:

- **Label schema is validated at load.** Models disagree wildly: this one emits
  ``PER``/``ORG``/``LOC``, another emits ``人名``/``法人名``/``地名``. During the
  過去の比較では、未知labelを持つmodelが検出ゼロを正常終了に見せた。
  detections and looked like a clean run — under-detection that reads as success
  is exactly how a leak hides. So the mapping is checked against the model's own
  ``id2label`` at construction, and a model with no mappable label is refused.
- **Unknown labels are surfaced, not ignored.** Labels we cannot map are recorded
  and reported once, rather than silently dropped per-token.
- **Pinned.** The model id, its commit revision, and the expected file digests are
  configuration, and the runtime loads ``local_files_only`` so a request never
  triggers a download and user text never reaches the Hub.
- **No remote code.** ``trust_remote_code`` is never set; only safetensors
  weights are accepted.
- **Off the event loop, on a bounded pool.** Inference is synchronous and
  CPU-bound. A timeout does NOT stop it — nothing can interrupt CPU-bound Python —
  so what protects us is the admission limit in ``detectors.inference``: an
  abandoned inference keeps occupying its slot until it finishes, and further
  requests are refused rather than queued behind it.
"""

from __future__ import annotations

from typing import Any

from securitymasker.detectors.base import DetectionContext
from securitymasker.detectors.context import has_context
from securitymasker.detectors.inference import InferenceOverloaded, shared_runner
from securitymasker.errors import ConfigError, DetectionError
from securitymasker.models import DetectionResult, EntityType, ReplacementProfile, RestorePolicy

# 対応schema間の粗いlabel mapping。fuzzy matchingにせず明示する。
# matched: guessing what an unknown label means is how a model silently mislabels
# a person as a product.
_LABEL_MAP: dict[str, str] = {
    # English／ISO形式（tsmatz/xlm-roberta-ner-japaneseと多くのCoNLL model）
    "PER": EntityType.PERSON.value,
    "PERSON": EntityType.PERSON.value,
    "ORG": EntityType.ORGANIZATION.value,
    "ORG-P": EntityType.ORGANIZATION.value,      # political organisation
    "ORG-O": EntityType.ORGANIZATION.value,      # other organisation
    "ORGANIZATION": EntityType.ORGANIZATION.value,
    "INS": EntityType.ORGANIZATION.value,        # institution
    "LOC": EntityType.LOCATION.value,
    "LOCATION": EntityType.LOCATION.value,
    "GPE": EntityType.LOCATION.value,
    # 日本語label集合（LUKE形式の拡張固有表現）
    "人名": EntityType.PERSON.value,
    "法人名": EntityType.ORGANIZATION.value,
    "政治的組織名": EntityType.ORGANIZATION.value,
    "その他の組織名": EntityType.ORGANIZATION.value,
    "施設名": EntityType.LOCATION.value,
    "地名": EntityType.LOCATION.value,
}

# event、product、その他の分類など、意図的に個人情報として扱わないlabel。
# generic "outside" tag. Listed so they count as *known* and do not trip the
# "unmappable schema" guard.
_IGNORED_LABELS = frozenset({"O", "EVT", "PRD", "イベント名", "製品名", "その他"})

_PERSON_CONTEXT = (
    "氏名", "名前", "契約者", "申込者", "担当者", "代表者", "連絡先", "お客様", "患者", "従業員", "さん", "様",
)


def _coarse(label: str) -> str:
    """BIO prefixを除き、lookup用に大文字小文字を正規化する。"""
    text = label.strip()
    if len(text) > 2 and text[1] == "-" and text[0] in "BILUES":
        text = text[2:]
    return text if any(ord(c) > 127 for c in text) else text.upper()


class UnsupportedLabelSchemaError(ConfigError):
    """model labelをmappingできず、出力が無意味になることを示す。"""


class JapaneseNerDetector:
    name = "jp_ner"
    # model-backedなのでengineはdeterministic detectorと異なるscheduleを行う。
    # detectors (one bounded request-wide pass, not one call per span). This is an
    # explicit marker rather than an inference from `skip_code_contexts`, which is
    # user-configurable and means something else.
    fuzzy = True

    def __init__(
        self,
        *,
        model: str | None = None,
        revision: str | None = None,
        min_score: float = 0.85,
        required: bool = False,
        skip_code_contexts: bool = True,
        local_files_only: bool = True,
        allow_unverified_model: bool = False,
        inference_timeout: float | None = None,
    ) -> None:
        self._inference_timeout = inference_timeout
        # special tokenとoverlapの余裕を残して512-token上限未満にする。
        # the pipeline adds; the overlap is generous enough to contain any Japanese
        # personal or organisation name that lands on a window boundary.
        self._window_tokens = 448
        self._window_overlap = 64
        # offset対応tokenizerがない場合だけ使う（`_windows`参照）。
        self._window_chars = 400
        self._window_overlap_chars = 64
        # fuzzy NERはcode-like spanを対象外にする。
        # deterministic detectors keep running there.
        self.skip_code_contexts = skip_code_contexts
        self._min_score = min_score
        self._pipeline: Any = None
        self.available = False
        self.unmapped_labels: tuple[str, ...] = ()
        if not model:
            return  # not configured -> disabled (a model is never hardcoded)

        try:
            from transformers import pipeline
        except ImportError as exc:
            if required:
                raise ConfigError(
                    f"ner.model={model!r} is configured but 'transformers' is not "
                    "installed. Run the repository's scripts/setup first."
                ) from exc
            return

        # fetch後のcache改変を検出するためload前にartifactを再検証する。
        # verification says what was downloaded once; a cache can be altered
        # afterwards, so the gate has to be here too.
        #
        # `source` is what we hand to transformers. In offline mode it is the
        # model IDではなく検証済みsnapshot directoryを渡し、再解決を防ぐ。
        # transformers 4.57 contact the Hub even with local_files_only=True
        # (measured: one outbound connection per load), which breaks an air-gapped
        # deployment and quietly reintroduces a network dependency into the
        # security boundary. Loading from the path is what the flag was supposed
        # to mean, and it measured zero outbound connections.
        source = model
        if local_files_only:
            from securitymasker.models_fetch import cache_directory, require_verified

            directory = cache_directory(model, revision or "")
            if directory is None:
                if required:
                    raise ConfigError(
                        f"ner.model={model!r}@{revision} is not in the local cache. "
                        "Run 'securitymasker model-load' first."
                    )
                return
            try:
                require_verified(model, revision, directory,
                                 allow_unverified=allow_unverified_model)
            except ConfigError:
                if required:
                    raise      # message already names the artifacts, not any secret
                return
            source = str(directory)

        try:
            # pipelineに暗黙選択させずweightとtokenizerを明示loadする。
            # `pipeline()` resolve them: this is where `revision` pins the exact
            # commit and `local_files_only` guarantees no network access at request
            # time. `pipeline()` itself does not accept local_files_only.
            load_kwargs: dict[str, Any] = {
                "local_files_only": local_files_only,
                # model提供codeは絶対に実行しない（supply chain）。
                "trust_remote_code": False,
            }
            if source == model:
                # online modeだけでrevisionを渡し、local pathでは使わない。
                # meaningless to transformers; offline the pin is enforced more
                # strongly than a kwarg could — cache_directory() resolves the
                # snapshot for exactly this revision and require_verified() has
                # already checked every artifact's bytes.
                load_kwargs["revision"] = revision
            from securitymasker.models_fetch import ADOPTED_MODEL, ADOPTED_REVISION

            if model == ADOPTED_MODEL and revision == ADOPTED_REVISION:
                # 固定architectureを直接loadする。Auto factoryはTransformersの
                # 全model registryを動的探索し、PyInstallerでは無関係なmodel
                # moduleの欠落で停止する。直接指定は供給網の固定も強くする。
                from transformers.models.xlm_roberta.modeling_xlm_roberta import (
                    XLMRobertaForTokenClassification,
                )
                from transformers.models.xlm_roberta.tokenization_xlm_roberta_fast import (
                    XLMRobertaTokenizerFast,
                )

                tokenizer = XLMRobertaTokenizerFast.from_pretrained(
                    source, **load_kwargs
                )
                weights = XLMRobertaForTokenClassification.from_pretrained(
                    source, use_safetensors=True, **load_kwargs
                )
            else:
                # legacy/source互換経路。v2標準設定は上の固定model以外を拒否する。
                from transformers import AutoModelForTokenClassification, AutoTokenizer

                tokenizer = AutoTokenizer.from_pretrained(source, **load_kwargs)  # type: ignore[no-untyped-call]
                # use_safetensors=True is REQUIRED, not preferred: without it
                # transformers silently falls back to a pickle .bin, which executes
                # arbitrary code on load.
                weights = AutoModelForTokenClassification.from_pretrained(
                    source, use_safetensors=True, **load_kwargs
                )
            self._pipeline = pipeline(
                "token-classification",
                model=weights,
                tokenizer=tokenizer,
                aggregation_strategy="simple",
                device=-1,
            )
        except Exception as exc:  # noqa: BLE001 - model missing/corrupt/incompatible
            self._pipeline = None
            if required:
                detail = type(exc).__name__
                # module名は利用者データではなくpackaging診断に必要な公開識別子。
                if isinstance(exc, ModuleNotFoundError) and exc.name:
                    detail = f"{detail}: {exc.name}"
                raise ConfigError(
                    f"ner.model={model!r} (revision={revision or 'unpinned'}) could not "
                    f"be loaded ({detail}). Fetch it first with "
                    "'securitymasker model-load', or clear ner.model."
                ) from exc
            return

        self._validate_label_schema(model, required)
        self._validate_offsets(model, required)
        self.available = self._pipeline is not None

    def _validate_label_schema(self, model: str, required: bool) -> None:
        """解釈不能なlabelを持つmodelを拒否する。

        A model with an unknown schema returns detections we drop on the floor,
        which looks exactly like a clean run. For a masking proxy that is a leak
        wearing a green tick, so it fails at startup instead.
        """
        labels = {
            _coarse(label)
            for label in getattr(self._pipeline.model.config, "id2label", {}).values()
        }
        mappable = {label for label in labels if label in _LABEL_MAP}
        unknown = labels - mappable - _IGNORED_LABELS
        self.unmapped_labels = tuple(sorted(unknown))
        if not mappable:
            self._pipeline = None
            message = (
                f"ner.model={model!r} exposes no label this build can map "
                f"(labels: {sorted(labels)[:8]}). Its output would be discarded "
                "silently, so it is refused."
            )
            if required:
                raise UnsupportedLabelSchemaError(message)

    def _validate_offsets(self, model: str, required: bool) -> None:
        """tokenizerがcharacter offsetを報告できないmodelを拒否する。

        Masking needs a span, not just a label: without ``start``/``end`` we cannot
        say WHICH characters to replace. Some tokenizers (LUKE's, for one) return
        ``None`` offsets, which produced a crash mid-request rather than a clean
        refusal. A one-off synthetic probe at load turns that into a startup
        failure. The probe text contains no real data.
        """
        if self._pipeline is None:
            return
        try:
            probe = self._pipeline("担当者は佐々木健一です。")
        except Exception:  # noqa: BLE001 - treated the same as unusable output
            probe = []
        usable = any(
            ent.get("start") is not None and ent.get("end") is not None for ent in probe
        )
        if usable:
            return
        self._pipeline = None
        message = (
            f"ner.model={model!r} does not report character offsets, so its "
            "detections cannot be mapped to text spans and nothing could be "
            "masked from them. Use a model with a fast/offset-capable tokenizer."
        )
        if required:
            raise UnsupportedLabelSchemaError(message)

    def _windows(self, text: str) -> list[tuple[int, str]]:
        """``text``をmodelが実際に読める``(offset, chunk)`` pairへ分割する。

        A transformer has a hard input length (512 tokens here). Handing it more
        does NOT raise — the tokenizer warns to stderr and the pipeline silently
        classifies only the prefix, so every entity past the limit comes back as
        "nothing found". For a masking proxy that is the worst possible failure:
        indistinguishable from a clean scan, and it hides exactly the long pasted
        documents most likely to contain names.

        So the text is cut into bounded windows, with an overlap, and each window
        is inferred separately. The overlap exists because a name split across a
        cut would be missed by both halves; entities found twice are de-duplicated
        afterwards. Offsets are in ``text`` coordinates — the caller shifts by
        ``offset`` and nothing else changes.
        """
        offsets = self._token_offsets(text)
        if offsets is None:
            # 利用可能tokenizerがなければcharacter windowへfallbackする。
            # back to character windows: cruder, but it still cannot truncate,
            # and truncation is the failure that matters. Japanese runs close to
            # one token per character, so the character budget stays under the
            # token limit.
            return _slice(text, len(text), self._window_chars, self._window_overlap_chars,
                          start_of=lambda i: i, end_of=lambda i: i + 1)
        return _slice(text, len(offsets), self._window_tokens, self._window_overlap,
                      start_of=lambda i: offsets[i][0], end_of=lambda i: offsets[i][1])

    def _token_offsets(self, text: str) -> list[tuple[int, int]] | None:
        """tokenごとのcharacter offset。tokenizerが報告不能ならNone。"""
        tokenizer = getattr(self._pipeline, "tokenizer", None)
        if tokenizer is None:
            return None
        try:
            # verbose=False: the tokenizer warns on stderr whenever the input
            # exceeds model_max_length. Here that is expected and handled —
            # windowing is the whole point of this call — so the warning would
            # only teach operators to ignore a message that matters elsewhere.
            encoded = tokenizer(text, add_special_tokens=False,
                                return_offsets_mapping=True, verbose=False)
            mapping = encoded["offset_mapping"]
        except Exception:  # noqa: BLE001 - any tokenizer shortfall -> char windows
            return None
        if not mapping or any(o is None or o[1] is None for o in mapping):
            return None
        return [(int(a), int(b)) for a, b in mapping]

    async def _infer(self, text: str) -> list[dict[str, Any]]:
        try:
            # 同期CPU-bound inferenceをbounded poolで実行する。
            # stop the worker (nothing can interrupt CPU-bound Python), so the
            # protection is the admission limit: abandoned work keeps its slot
            # until it finishes, and further requests are refused rather than
            # queued behind it.
            return list(await shared_runner().run(
                self._pipeline, text, timeout=self._inference_timeout
            ))
        except (InferenceOverloaded, TimeoutError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise DetectionError(
                f"jp_ner pipeline failed at runtime: {type(exc).__name__}"
            ) from exc

    async def detect(self, context: DetectionContext) -> list[DetectionResult]:
        if not self.available or self._pipeline is None:
            return []
        if self.skip_code_contexts and _is_code_like(context.context_kind):
            return []
        text = context.norm.normalized
        try:
            windows = self._windows(text)
        except Exception as exc:  # noqa: BLE001 - tokenizer failure is a detection failure
            raise DetectionError(
                f"jp_ner could not window the input: {type(exc).__name__}"
            ) from exc

        entities: list[dict[str, Any]] = []
        for offset, chunk in windows:
            entities.extend(
                {**ent, "start": int(ent["start"]) + offset, "end": int(ent["end"]) + offset}
                for ent in await self._infer(chunk)
            )
        entities = _dedupe(entities)

        results: list[DetectionResult] = []
        for ent in entities:
            label = _coarse(str(ent.get("entity_group") or ent.get("entity") or ""))
            etype = _LABEL_MAP.get(label)
            score = float(ent.get("score", 0.0))
            if etype is None or score < self._min_score:
                continue
            start, end = int(ent["start"]), int(ent["end"])
            # 曖昧なgiven name（さくら／葵／ひかり）は信頼に足るcontextを要求する。
            if etype == EntityType.PERSON.value and not has_context(
                text, start, end, _PERSON_CONTEXT
            ):
                score *= 0.8
                if score < self._min_score:
                    continue
            o_start, o_end = context.norm.to_original_span(start, end)
            results.append(
                DetectionResult(
                    entity_type=etype,
                    start=o_start,
                    end=o_end,
                    score=score,
                    detector=self.name,
                    context_kind=context.context_kind,
                    replacement_profile=ReplacementProfile.PROSE_IDENTIFIER.value,
                    restore_policy=RestorePolicy.LITERAL.value,
                    original_value=context.norm.original[o_start:o_end],
                    normalized_value=text[start:end],
                    # 最低信頼signalとし、dictionary／deterministic detectorを優先する。
                    # deterministic detectorがすべてのoverlapで優先される。
                    metadata={"priority": 90, "ner_label": label},
                )
            )
        return results


def _slice(
    text: str,
    total: int,
    size: int,
    overlap: int,
    *,
    start_of: Any,
    end_of: Any,
) -> list[tuple[int, str]]:
    """最大``size`` unit、``overlap``分重複するwindowを作る。

    ``start_of(i)`` is the first character of unit ``i``; ``end_of(i)`` is the
    exclusive last character of unit ``i``. The same walk then serves both the
    token-based and the character-based cut.
    """
    if total <= size:
        return [(0, text)]
    windows: list[tuple[int, str]] = []
    step = max(1, size - overlap)
    for begin in range(0, total, step):
        last = min(begin + size, total) - 1
        if last < begin:
            break
        windows.append((start_of(begin), text[start_of(begin):end_of(last)]))
        if begin + size >= total:
            break
    return windows


def _dedupe(entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """window間overlapで二重報告されたentityを除く。

    Two windows share ``_window_overlap`` tokens, so anything inside that region
    is found by both. Same span and same label is the same finding; the higher
    score wins, because the window that saw more of the surrounding sentence is
    the one that was more confident.
    """
    best: dict[tuple[int, int, str], dict[str, Any]] = {}
    for ent in entities:
        key = (int(ent["start"]), int(ent["end"]),
               str(ent.get("entity_group") or ent.get("entity") or ""))
        current = best.get(key)
        if current is None or float(ent.get("score", 0.0)) > float(current.get("score", 0.0)):
            best[key] = ent
    return sorted(best.values(), key=lambda e: (int(e["start"]), int(e["end"])))


def _is_code_like(kind: str) -> bool:
    # package levelの循環参照を防ぐためlazy importする。
    from securitymasker.context import is_code_like

    return is_code_like(kind)
