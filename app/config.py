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
) -> tuple[str, list[ModelSpec], PunctuationSpec | None]:
    """Return (default_model_id, specs, punctuation_spec). Raises on bad config."""
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

    punctuation = None
    raw_punct = data.get("punctuation")
    if raw_punct:
        resolved = _resolve_paths(path.parent, raw_punct)
        ct_transformer = resolved.get("ct_transformer")
        if not ct_transformer:
            raise ValueError("punctuation 配置缺少 ct_transformer 字段")
        try:
            num_threads = int(resolved.get("num_threads", 1))
        except (TypeError, ValueError):
            raise ValueError("punctuation.num_threads 必须是整数")
        punctuation = PunctuationSpec(ct_transformer=ct_transformer, num_threads=num_threads)
    return default_id, specs, punctuation
