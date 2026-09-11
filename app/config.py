"""Load and validate the model registry file."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_MODELS_FILE = ROOT_DIR / "config" / "models.json"

# Keys whose string values are file/dir paths, resolved against models.json.
_PATH_KEYS = {
    "model",
    "tokens",
    "encoder",
    "decoder",
    "joiner",
    "lm",
    "ct_transformer",
    "rule_fsts",
    "rule_fars",
    "hotwords_file",
    "bpe_vocab",
    "hr_dict_dir",
    "hr_rule_fsts",
    "hr_lexicon",
    "lodr_fst",
}


@dataclass
class ModelSpec:
    id: str
    label: str
    description: str
    type: str
    config: dict = field(default_factory=dict)


@dataclass
class PunctuationSpec:
    """Companion punctuation-restoration model (sherpa-onnx OfflinePunctuation)."""

    ct_transformer: str
    num_threads: int = 1
    id: str = "default"
    label: str = "标点"


@dataclass
class VadSpec:
    """Global voice-activity-detection settings for long-audio splitting."""

    model: str
    sample_rate: int = 16000
    threshold: float = 0.5
    min_speech_duration: float = 0.25
    min_silence_duration: float = 0.5
    max_speech_duration: float = 25.0


def _resolve_paths(base_dir: Path, config: dict) -> dict:
    resolved = {}
    for key, value in config.items():
        if key in _PATH_KEYS and isinstance(value, str):
            path = Path(value)
            resolved[key] = str(path if path.is_absolute() else (base_dir / path).resolve())
        else:
            resolved[key] = value
    return resolved


def load_models(
    models_file: str | os.PathLike | None = None,
) -> tuple[str, list[ModelSpec], PunctuationSpec | None, VadSpec | None]:
    """Return (default_model_id, specs, punct_specs, default_punct_id, vad_spec)."""
    path = Path(models_file or os.getenv("ASR_MODELS_FILE") or DEFAULT_MODELS_FILE)
    if not path.is_file():
        raise FileNotFoundError(f"模型配置文件不存在: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    specs = []
    seen = set()
    for item in data.get("models", []):
        model_id = str(item.get("id", "")).strip()
        if not model_id:
            raise ValueError("models 列表中存在缺少 id 的条目")
        if model_id in seen:
            raise ValueError(f"模型 id 重复: {model_id}")
        seen.add(model_id)
        specs.append(
            ModelSpec(
                id=model_id,
                label=str(item.get("label") or model_id),
                description=str(item.get("description") or ""),
                type=str(item.get("type", "")).strip(),
                config=_resolve_paths(path.parent, item.get("config") or {}),
            )
        )

    if not specs:
        raise ValueError(f"模型配置文件里没有任何模型: {path}")

    default_id = str(data.get("default_model") or specs[0].id)
    if default_id not in seen:
        raise ValueError(f"default_model '{default_id}' 不在 models 列表中")

    # punctuations: 标点模型列表；兼容旧的单个 "punctuation" 配置。
    punct_specs: list[PunctuationSpec] = []
    raw_puncts = data.get("punctuations")
    if raw_puncts is None and data.get("punctuation"):
        raw_puncts = [data["punctuation"]]
    for idx, item in enumerate(raw_puncts or []):
        resolved = _resolve_paths(path.parent, item)
        ct_transformer = resolved.get("ct_transformer")
        if not ct_transformer:
            raise ValueError("punctuations 配置缺少 ct_transformer 字段")
        try:
            num_threads = int(resolved.get("num_threads", 1))
        except (TypeError, ValueError):
            raise ValueError("punctuations.num_threads 必须是整数")
        punct_specs.append(
            PunctuationSpec(
                ct_transformer=ct_transformer,
                num_threads=num_threads,
                id=str(resolved.get("id") or f"punct{idx}"),
                label=str(resolved.get("label") or f"标点模型 {idx + 1}"),
            )
        )

    default_punct = None
    if punct_specs:
        punct_ids = {spec.id for spec in punct_specs}
        default_punct = str(data.get("default_punctuation") or punct_specs[0].id)
        if default_punct not in punct_ids:
            raise ValueError(f"default_punctuation '{default_punct}' 不在 punctuations 列表中")

    vad = None
    raw_vad = data.get("vad")
    if raw_vad:
        resolved = _resolve_paths(path.parent, raw_vad)
        model = resolved.get("model")
        if not model:
            raise ValueError("vad 配置缺少 model 字段")

        def _num(key, default):
            try:
                return type(default)(resolved.get(key, default))
            except (TypeError, ValueError):
                raise ValueError(f"vad.{key} 必须是数字")

        vad = VadSpec(
            model=model,
            sample_rate=_num("sample_rate", 16000),
            threshold=_num("threshold", 0.5),
            min_speech_duration=_num("min_speech_duration", 0.25),
            min_silence_duration=_num("min_silence_duration", 0.5),
            max_speech_duration=_num("max_speech_duration", 25.0),
        )
    return default_id, specs, punct_specs, default_punct, vad
