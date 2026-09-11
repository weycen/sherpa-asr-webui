"""Thread-safe wrapper around sherpa-onnx punctuation restoration."""

import os
import sys
import threading

import sherpa_onnx

from .config import PunctuationSpec


class PunctuationNotFoundError(KeyError):
    pass


class Punctuator:
    """Lazy-loaded OfflinePunctuation. Never raises: degrades to raw text."""

    def __init__(self, spec: PunctuationSpec):
        self._spec = spec
        self._lock = threading.Lock()
        self._model = None
        self._warned_missing = False
        self._warned_failure = False

    def available(self) -> bool:
        return os.path.isfile(self._spec.ct_transformer)

    def add(self, text: str) -> str:
        """Add punctuation to text, or return it unchanged if unavailable."""
        if not text:
            return text
        with self._lock:
            if self._model is None:
                path = self._spec.ct_transformer
                if not os.path.isfile(path):
                    if not self._warned_missing:
                        print(
                            f"[警告] 标点模型不存在，已跳过标点恢复: {path}",
                            file=sys.stderr,
                        )
                        self._warned_missing = True
                    return text
                try:
                    self._model = sherpa_onnx.OfflinePunctuation(
                        sherpa_onnx.OfflinePunctuationConfig(
                            model=sherpa_onnx.OfflinePunctuationModelConfig(
                                ct_transformer=path,
                                num_threads=self._spec.num_threads,
                            )
                        )
                    )
                except Exception as exc:
                    self._warned_failure = True
                    print(f"[警告] 标点模型加载失败，已跳过标点恢复: {exc}", file=sys.stderr)
                    return text

            try:
                return self._model.add_punctuation(text)
            except Exception as exc:
                if not self._warned_failure:
                    self._warned_failure = True
                    print(f"[警告] 标点恢复执行失败，已跳过: {exc}", file=sys.stderr)
                return text


class PunctuationManager:
    """Registry of punctuation models; one lazily-loaded Punctuator per id."""

    def __init__(self, specs: list[PunctuationSpec], default_id: str | None = None):
        self._specs = {spec.id: spec for spec in specs}
        self._default_id = default_id if default_id in self._specs else next(iter(self._specs), None)
        self._instances: dict[str, Punctuator] = {}
        self._lock = threading.Lock()

    @property
    def default_id(self) -> str | None:
        return self._default_id

    def list(self) -> list[dict]:
        return [
            {
                "id": spec.id,
                "label": spec.label,
                "available": os.path.isfile(spec.ct_transformer),
            }
            for spec in self._specs.values()
        ]

    def get(self, punct_id: str) -> Punctuator:
        spec = self._specs.get(punct_id)
        if spec is None:
            raise PunctuationNotFoundError(punct_id)
        with self._lock:
            instance = self._instances.get(punct_id)
            if instance is None:
                instance = Punctuator(spec)
                self._instances[punct_id] = instance
        return instance
