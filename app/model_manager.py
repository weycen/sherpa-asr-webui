"""Lazy-loaded, thread-safe registry of sherpa-onnx recognizers."""

import threading

import sherpa_onnx

from .config import ModelSpec

_FACTORIES = {
    "sense_voice": sherpa_onnx.OfflineRecognizer.from_sense_voice,
    "paraformer": sherpa_onnx.OfflineRecognizer.from_paraformer,
    "whisper": sherpa_onnx.OfflineRecognizer.from_whisper,
    "transducer": sherpa_onnx.OfflineRecognizer.from_transducer,
}

SUPPORTED_TYPES = tuple(sorted(_FACTORIES))


class ModelNotFoundError(KeyError):
    pass


class ModelLoadError(RuntimeError):
    pass


class ModelRuntime:
    """One loaded recognizer plus the lock serializing its inference."""

    def __init__(self, spec: ModelSpec, recognizer):
        self.spec = spec
        self.recognizer = recognizer
        self.lock = threading.Lock()


class ModelManager:
    def __init__(self, specs: list[ModelSpec], default_model_id: str):
        self._specs = {spec.id: spec for spec in specs}
        self._default_model_id = default_model_id
        self._runtimes: dict[str, ModelRuntime] = {}
        self._loading_locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    @property
    def default_model_id(self) -> str:
        return self._default_model_id

    def list(self) -> list[dict]:
        with self._guard:
            loaded = set(self._runtimes)
        return [
            {
                "id": spec.id,
                "label": spec.label,
                "description": spec.description,
                "type": spec.type,
                "ready": spec.id in loaded,
            }
            for spec in self._specs.values()
        ]

    def get(self, model_id: str) -> ModelRuntime:
        """Return the cached runtime, loading it once on first use."""
        if model_id not in self._specs:
            raise ModelNotFoundError(model_id)

        with self._guard:
            runtime = self._runtimes.get(model_id)
            if runtime is None:
                load_lock = self._loading_locks.setdefault(model_id, threading.Lock())
        if runtime is not None:
            return runtime

        # One loader per model id so concurrent first requests do not load twice.
        with load_lock:
            with self._guard:
                runtime = self._runtimes.get(model_id)
            if runtime is None:
                runtime = self._load(self._specs[model_id])
                with self._guard:
                    self._runtimes[model_id] = runtime
        return runtime

    @staticmethod
    def _load(spec: ModelSpec) -> ModelRuntime:
        factory = _FACTORIES.get(spec.type)
        if factory is None:
            raise ModelLoadError(
                f"模型 '{spec.id}' 类型 '{spec.type}' 不受支持，可选: {', '.join(SUPPORTED_TYPES)}"
            )
        if not spec.config:
            raise ModelLoadError(f"模型 '{spec.id}' 缺少 config 参数")
        try:
            recognizer = factory(**spec.config)
        except Exception as exc:
            raise ModelLoadError(f"模型 '{spec.id}' 加载失败: {exc}") from exc
        return ModelRuntime(spec, recognizer)
