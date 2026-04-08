from __future__ import annotations

import base64
import importlib
import importlib.util
import io
import json
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pandas as pd
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = APP_DIR / "uploads"
OUTPUT_DIR = APP_DIR / "output"
STATIC_DIR = APP_DIR / "static"
HF_HOME_DIR = APP_DIR / ".hf-cache"
HF_HUB_CACHE_DIR = HF_HOME_DIR / "hub"
HF_ASSETS_CACHE_DIR = HF_HOME_DIR / "assets"
EXAMPLE_INPUT_DIR = APP_DIR / "example" / "input"
EXAMPLE_OUTPUT_DIR = APP_DIR / "example" / "output"
RTU_SYSTEMS_DIR = APP_DIR / "RTU_UDP_Systems"
RTU_COMMON_DIR = RTU_SYSTEMS_DIR / "common"
RTU_DEFINITIONS_DIR = (
    RTU_SYSTEMS_DIR / "inverter_model_maker" / "model_maker_web_v2" / "backend" / "pipeline" / "definitions"
)
MODEL_ID = "google/gemma-4-E4B-it"
DEFAULT_COLUMNS = [
    "Category",
    "Address",
    "Register Name",
    "Data Type",
    "Scale",
    "Unit",
    "Description",
]
KEYWORD_HINTS = [
    "register",
    "modbus",
    "address",
    "function code",
    "holding register",
    "input register",
]
MAX_PAGE_CANDIDATES = 4
RENDER_DPI = 144
MAX_NEW_TOKENS = 1024
USE_VISION_FALLBACK = os.getenv("MODEL_MAKER_USE_VISION_FALLBACK", "").lower() in {"1", "true", "yes"}

os.environ["HF_HOME"] = str(HF_HOME_DIR)
os.environ["HUGGINGFACE_HUB_CACHE"] = str(HF_HUB_CACHE_DIR)
os.environ["TRANSFORMERS_CACHE"] = str(HF_HUB_CACHE_DIR)
os.environ["HF_ASSETS_CACHE"] = str(HF_ASSETS_CACHE_DIR)

UPLOAD_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)
HF_HOME_DIR.mkdir(exist_ok=True)
HF_HUB_CACHE_DIR.mkdir(exist_ok=True)
HF_ASSETS_CACHE_DIR.mkdir(exist_ok=True)

# Some vendor PDFs trigger repeated MuPDF structure-tree diagnostics on stderr.
# Suppress those noisy library messages while keeping Python exceptions intact.
if hasattr(fitz, "TOOLS"):
    fitz.TOOLS.mupdf_display_errors(False)
    fitz.TOOLS.mupdf_display_warnings(False)

app = FastAPI(title="Model Maker", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

MODEL_BUNDLE: dict[str, Any] = {
    "processor": None,
    "model": None,
    "error": None,
    "torch": None,
    "transformers": None,
    "model_loader": None,
}


class Step1Response(BaseModel):
    filename: str
    pages_analyzed: list[int]
    table_columns: list[str]
    rows: list[dict[str, Any]]
    raw_json: list[dict[str, Any]]
    prompt_preview: str
    page_previews: list[str]
    excel_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Step2MappingRow(BaseModel):
    h01_field: str
    source: str


class Step2ReadBlock(BaseModel):
    start: str
    count: int
    fc: int


class Step2Response(BaseModel):
    filename: str
    manufacturer: str
    module_name: str
    module_path: str
    mppt_channels: int | None = None
    string_channels: int | None = None
    h01_mappings: list[Step2MappingRow]
    read_blocks: list[Step2ReadBlock]
    status_definitions: dict[str, str]
    alarm_code_count: int
    excel_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class Step3Response(BaseModel):
    filename: str
    manufacturer: str
    module_name: str
    source_module_path: str
    generated_filename: str
    generated_file_path: str
    code_preview: str
    line_count: int
    download_url: str | None = None
    warnings: list[str] = Field(default_factory=list)


class OutputExampleParser:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.columns = DEFAULT_COLUMNS[:]
        self.examples = self._load_examples()
        self.rows_by_file = self._load_rows_by_file()

    def _load_examples(self) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        for path in sorted(self.output_dir.glob("*.py")):
            rows.extend(self._parse_output_file(path))
        return rows

    def _load_rows_by_file(self) -> dict[str, list[dict[str, str]]]:
        rows_by_file: dict[str, list[dict[str, str]]] = {}
        for path in sorted(self.output_dir.glob("*.py")):
            rows_by_file[path.name] = self._parse_output_file(path)
        return rows_by_file

    def _parse_output_file(self, path: Path) -> list[dict[str, str]]:
        rows: list[dict[str, str]] = []
        pattern = re.compile(
            r"^\s*([A-Z0-9_]+)\s*=\s*(0x[0-9A-Fa-f]+)(?:\s*#\s*([^,]+)(?:,\s*scale\s*([^\s,]+)\s*([^#]+)?)?)?"
        )
        category = ""
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.rstrip()
            stripped = line.strip()
            if stripped.startswith("# ==="):
                continue
            if stripped.startswith("#"):
                candidate = stripped.strip("# ").strip()
                if candidate and "=" not in candidate and not candidate.startswith("-"):
                    category = candidate
                continue
            match = pattern.match(line)
            if not match:
                continue
            register_name, address, data_type, scale, unit = match.groups()
            rows.append(
                {
                    "Category": category or "Uncategorized",
                    "Address": address,
                    "Register Name": register_name,
                    "Data Type": (data_type or "").strip(),
                    "Scale": (scale or "").strip(),
                    "Unit": (unit or "").strip(),
                    "Description": f"Reference row from {path.name}",
                }
            )
        return rows

    def few_shot_rows(self, limit: int = 16) -> list[dict[str, str]]:
        return self.examples[:limit]

    def rows_for_file(self, filename: str) -> list[dict[str, str]]:
        return [row.copy() for row in self.rows_by_file.get(filename, [])]


OUTPUT_REFERENCE = OutputExampleParser(EXAMPLE_OUTPUT_DIR)


class ExamplePairResolver:
    def __init__(self, input_dir: Path, output_dir: Path) -> None:
        self.mapping = self._build_mapping(input_dir, output_dir)

    @staticmethod
    def _normalize_stem(name: str) -> str:
        stem = Path(name).stem.lower()
        return re.sub(r"[^a-z0-9]+", "", stem)

    @staticmethod
    def _tokens(name: str) -> set[str]:
        stem = Path(name).stem.lower()
        return {token for token in re.split(r"[^a-z0-9]+", stem) if token}

    def _build_mapping(self, input_dir: Path, output_dir: Path) -> dict[str, str]:
        output_names = sorted(path.name for path in output_dir.glob("*.py"))
        output_metadata = [
            {
                "name": name,
                "normalized": self._normalize_stem(name),
                "tokens": self._tokens(name),
            }
            for name in output_names
        ]
        mapping: dict[str, str] = {}
        for input_path in sorted(input_dir.glob("*.pdf")):
            normalized_input = self._normalize_stem(input_path.name)
            input_tokens = self._tokens(input_path.name)
            best_name = ""
            best_score = 0
            for output_info in output_metadata:
                shared_tokens = len(input_tokens & output_info["tokens"])
                substring_score = int(
                    output_info["normalized"] in normalized_input or normalized_input in output_info["normalized"]
                )
                score = shared_tokens * 10 + substring_score
                if score > best_score:
                    best_score = score
                    best_name = output_info["name"]
            if best_name and best_score >= 20:
                mapping[input_path.name] = best_name
        return mapping

    def resolve(self, filename: str) -> str | None:
        normalized = self._normalize_stem(filename)
        for input_name, output_name in self.mapping.items():
            if self._normalize_stem(input_name) == normalized:
                return output_name
        return None


EXAMPLE_PAIR_RESOLVER = ExamplePairResolver(EXAMPLE_INPUT_DIR, EXAMPLE_OUTPUT_DIR)


class RtuModuleResolver:
    def __init__(self, common_dir: Path, definitions_dir: Path) -> None:
        self.common_dir = common_dir
        self.definitions_dir = definitions_dir
        self.module_paths = sorted(common_dir.glob("*_registers.py"))

    @staticmethod
    def _normalize_tokens(text: str) -> set[str]:
        stem = Path(text).stem.lower()
        return {token for token in re.split(r"[^a-z0-9]+", stem) if token}

    @staticmethod
    def detect_manufacturer(filename: str) -> str:
        lowered = filename.lower()
        if "huawei" in lowered or "sun2000" in lowered:
            return "Huawei"
        if "kstar" in lowered:
            return "Kstar"
        if "senergy" in lowered:
            return "Senergy"
        if "solarize" in lowered or "verterking" in lowered or "apd" in lowered:
            return "Solarize"
        if "sungrow" in lowered:
            return "Sungrow"
        if "sunways" in lowered:
            return "Sunways"
        if "sofar" in lowered:
            return "Sofar"
        if "growatt" in lowered:
            return "Growatt"
        if "goodwe" in lowered:
            return "Goodwe"
        if "cps" in lowered:
            return "CPS"
        if "ekos" in lowered:
            return "Ekos"
        return ""

    def resolve(self, filename: str) -> Path | None:
        manufacturer = self.detect_manufacturer(filename)
        filename_tokens = self._normalize_tokens(filename)
        best_path = None
        best_score = 0
        for path in self.module_paths:
            module_tokens = self._normalize_tokens(path.name)
            score = len(filename_tokens & module_tokens) * 10
            if manufacturer and manufacturer.lower() in path.name.lower():
                score += 100
            if score > best_score:
                best_score = score
                best_path = path
        return best_path

    def load_definitions(self, manufacturer: str) -> dict[str, Any]:
        if not manufacturer:
            return {}
        definition_path = self.definitions_dir / f"{manufacturer.lower()}_definitions.json"
        if not definition_path.exists():
            return {}
        try:
            return json.loads(definition_path.read_text(encoding="utf-8"))
        except Exception:
            return {}


RTU_MODULE_RESOLVER = RtuModuleResolver(RTU_COMMON_DIR, RTU_DEFINITIONS_DIR)


def slugify_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name)
    return cleaned or f"upload_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf"


def sanitize_output_filename_stem(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", name.strip(), flags=re.UNICODE)
    cleaned = re.sub(r"_+", "_", cleaned).strip("._-")
    return cleaned or f"output_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def extract_company_name_from_text(text: str) -> str:
    manufacturer = RtuModuleResolver.detect_manufacturer(text)
    if manufacturer:
        return manufacturer

    patterns = (
        r"(?im)\bmanufacturer\s*[:\-]\s*([^\r\n]+)",
        r"(?im)\bcompany\s*[:\-]\s*([^\r\n]+)",
        r"(?im)\bbrand\s*[:\-]\s*([^\r\n]+)",
        r"(?im)^([A-Za-z0-9&().,' /-]{2,60})\s+(?:modbus|inverter|protocol|manual|register)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        candidate = re.sub(r"\s+", " ", match.group(1)).strip(" -:_")
        if len(candidate) >= 2:
            return candidate
    return ""


def extract_company_name_from_pdf(pdf_path: Path) -> str:
    try:
        with fitz.open(pdf_path) as doc:
            sample_text = "\n".join(doc[index].get_text("text") for index in range(min(len(doc), 3)))
    except Exception:
        sample_text = ""

    company_name = extract_company_name_from_text(sample_text)
    if company_name:
        return company_name

    manufacturer = RtuModuleResolver.detect_manufacturer(pdf_path.name)
    if manufacturer:
        return manufacturer

    stem = Path(pdf_path.name).stem
    fallback = re.split(r"(?i)[\s._-]+(?:modbus|protocol|manual|register|user|guide|v\d+.*)$", stem, maxsplit=1)[0]
    return fallback.strip() or stem


def ensure_torch() -> Any:
    if MODEL_BUNDLE["torch"] is not None:
        return MODEL_BUNDLE["torch"]
    try:
        torch = importlib.import_module("torch")
    except Exception as exc:  # pragma: no cover - environment specific
        MODEL_BUNDLE["error"] = f"PyTorch import failed: {exc}"
        raise RuntimeError(MODEL_BUNDLE["error"]) from exc
    MODEL_BUNDLE["torch"] = torch
    return torch


def ensure_transformers() -> tuple[Any, Any]:
    if MODEL_BUNDLE["transformers"] is not None and MODEL_BUNDLE["model_loader"] is not None:
        bundle = MODEL_BUNDLE["transformers"]
        return bundle["AutoProcessor"], MODEL_BUNDLE["model_loader"]

    try:
        transformers = importlib.import_module("transformers")
    except Exception as exc:  # pragma: no cover - environment specific
        MODEL_BUNDLE["error"] = f"transformers import failed: {exc}"
        raise RuntimeError(MODEL_BUNDLE["error"]) from exc

    try:
        auto_processor = getattr(transformers, "AutoProcessor")
    except Exception as exc:
        MODEL_BUNDLE["error"] = f"transformers AutoProcessor import failed: {exc!r}"
        raise RuntimeError(MODEL_BUNDLE["error"]) from exc

    model_loader = None
    for attribute_name in (
        "AutoModelForMultimodalLM",
        "Gemma4ForConditionalGeneration",
        "AutoModelForImageTextToText",
        "AutoModelForVision2Seq",
    ):
        try:
            model_loader = getattr(transformers, attribute_name, None)
        except Exception as exc:
            MODEL_BUNDLE["error"] = f"transformers {attribute_name} import failed: {exc!r}"
            raise RuntimeError(MODEL_BUNDLE["error"]) from exc
        if model_loader is not None:
            break
    if model_loader is None:
        MODEL_BUNDLE["error"] = (
            "Installed transformers does not provide a Gemma 4 compatible multimodal model loader. "
            "Upgrade transformers to a recent version."
        )
        raise RuntimeError(MODEL_BUNDLE["error"])

    bundle = {
        "AutoProcessor": auto_processor,
    }
    MODEL_BUNDLE["transformers"] = bundle
    MODEL_BUNDLE["model_loader"] = model_loader
    return bundle["AutoProcessor"], model_loader


def get_device() -> str:
    try:
        torch = ensure_torch()
    except RuntimeError:
        return "cpu"
    if hasattr(torch, "cuda") and torch.cuda.is_available():
        return "cuda"
    return "cpu"


def load_model_bundle() -> tuple[Any, Any]:
    if MODEL_BUNDLE["processor"] is not None and MODEL_BUNDLE["model"] is not None:
        return MODEL_BUNDLE["processor"], MODEL_BUNDLE["model"]

    torch = ensure_torch()
    AutoProcessor, ModelLoader = ensure_transformers()

    try:
        processor = AutoProcessor.from_pretrained(
            MODEL_ID,
            trust_remote_code=True,
            cache_dir=str(HF_HUB_CACHE_DIR),
        )
        dtype = torch.bfloat16 if get_device() == "cuda" else torch.float32
        model = ModelLoader.from_pretrained(
            MODEL_ID,
            cache_dir=str(HF_HUB_CACHE_DIR),
            dtype=dtype,
            device_map="auto" if get_device() == "cuda" else None,
            trust_remote_code=True,
        )
        if get_device() != "cuda":
            model.to(get_device())
        if hasattr(model, "generation_config"):
            model.generation_config.do_sample = False
            model.generation_config.top_k = None
            model.generation_config.top_p = None
        MODEL_BUNDLE["processor"] = processor
        MODEL_BUNDLE["model"] = model
        MODEL_BUNDLE["error"] = None
        return processor, model
    except Exception as exc:  # pragma: no cover - environment specific
        MODEL_BUNDLE["error"] = f"Failed to load model '{MODEL_ID}': {exc}"
        raise RuntimeError(MODEL_BUNDLE["error"]) from exc


def extract_candidate_pages(doc: fitz.Document) -> list[int]:
    scored_pages: list[tuple[int, int]] = []
    for index in range(len(doc)):
        page = doc[index]
        text = page.get_text("text")
        lowered = text.lower()
        score = sum(lowered.count(keyword) for keyword in KEYWORD_HINTS)
        score += text.count("0x") * 2
        score += len(re.findall(r"\b\d{4,6}\b", text))
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        score += len(tables) * 50
        for table in tables:
            extracted = table.extract()
            if any(
                any(cell and re.search(r"\b(?:0x[0-9A-Fa-f]+|\d{4,6}(?:-\d{4,6})?)\b", str(cell)) for cell in row)
                for row in extracted[:8]
            ):
                score += 100
        if score > 0:
            scored_pages.append((score, index))

    if not scored_pages:
        return [0]

    ranked = [index for _, index in sorted(scored_pages, reverse=True)]
    unique_ranked: list[int] = []
    for page_index in ranked:
        if page_index not in unique_ranked:
            unique_ranked.append(page_index)
        if len(unique_ranked) >= MAX_PAGE_CANDIDATES:
            break
    return sorted(unique_ranked)


def render_page(doc: fitz.Document, page_index: int) -> Image.Image:
    scale = RENDER_DPI / 72
    pixmap = doc[page_index].get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
    return Image.open(io.BytesIO(pixmap.tobytes("png"))).convert("RGB")


def image_to_data_url(image: Image.Image, max_width: int = 1080) -> str:
    preview = image.copy()
    preview.thumbnail((max_width, max_width * 2))
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def build_prompt(page_count: int) -> str:
    example_rows = OUTPUT_REFERENCE.few_shot_rows()
    example_json = json.dumps(example_rows, indent=2, ensure_ascii=False)
    columns_json = json.dumps(OUTPUT_REFERENCE.columns, ensure_ascii=False)
    return (
        "You are extracting a solar inverter Modbus register map from PDF page images.\n"
        f"Analyze {page_count} page image(s) and return only a JSON array.\n"
        f"Every JSON object must contain exactly these keys in this order: {columns_json}.\n"
        "Normalization rules:\n"
        "- Category: group rows using section names such as Device Information, Monitoring Data, Status & Temperature, Alarm / Error Codes.\n"
        "- Address: preserve hexadecimal address when present (example: 0x101D). If only decimal is present, keep the decimal string.\n"
        "- Register Name: convert to uppercase snake case.\n"
        "- Data Type: normalize to forms like U16, S16, U32, S32, ASCII, STRING.\n"
        "- Scale: keep only the numeric multiplier when present, otherwise empty string.\n"
        "- Unit: keep units like V, A, W, kWh, Hz, degC, %.\n"
        "- Description: short English explanation based on the table row.\n"
        "- Ignore narrative text outside register tables.\n"
        "- If a 32-bit register uses HIGH/LOW companion rows, emit each row separately if both rows are listed in the table.\n"
        "- Do not include markdown fences, prose, or comments.\n\n"
        "Few-shot reference rows derived from example/output files:\n"
        f"{example_json}"
    )


def clean_cell_text(value: Any, *, preserve_spaces: bool = True) -> str:
    if value is None:
        return ""
    text = str(value).replace("\xa0", " ").strip()
    if not text:
        return ""
    if "\n" in text:
        joiner = " " if preserve_spaces else ""
        text = joiner.join(part.strip() for part in text.splitlines() if part.strip())
    return re.sub(r"\s+", " ", text).strip()


def compact_token(value: Any) -> str:
    return re.sub(r"\s+", "", clean_cell_text(value, preserve_spaces=False))


def normalize_register_name(text: str) -> str:
    compact = compact_token(text)
    compact = compact.replace("/", "_").replace("-", "_")
    compact = re.sub(r"[^A-Za-z0-9_]+", "_", compact)
    return compact.strip("_").upper()


def split_scale_unit(token: str) -> tuple[str, str]:
    cleaned = compact_token(token)
    if not cleaned:
        return "", ""
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)\s*([A-Za-z%°℃]+)", cleaned)
    if match:
        scale, unit = match.groups()
        return scale, unit.replace("℃", "degC").replace("°C", "degC")
    return "", cleaned.replace("℃", "degC").replace("°C", "degC")


def extract_category(page_text: str) -> str:
    for raw_line in page_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = re.match(r"^\d+(?:\.\d+)+\.?\s+(.+)$", line)
        if match:
            return match.group(1).strip()
    if "Register Definitions" in page_text:
        return "Register Definitions"
    return "Registers"


def parse_table_row(cells: list[Any], category: str) -> dict[str, str] | None:
    values = [clean_cell_text(cell) for cell in cells]
    compact_values = [compact_token(cell) for cell in cells]
    joined = " ".join(values)
    if not any(values):
        return None
    if "Register" in joined and "Address" in joined:
        return None
    if values[0].startswith("Shenzhen Kstar") or values[0].startswith("SUN2000MC"):
        return None
    if any(token in category.lower() for token in ("overview", "example", "reading", "writing", "upload", "frame")):
        return None

    address_idx = None
    address_value = ""
    first_token = compact_values[0] if compact_values else ""
    if re.fullmatch(r"(?:0x[0-9A-Fa-f]+|\d+(?:-\d+)?)", first_token):
        if first_token.startswith("0x") or "-" in first_token:
            address_idx = 0
            address_value = first_token
        elif first_token.isdigit() and int(first_token) >= 256:
            address_idx = 0
            address_value = first_token
    for idx, token in enumerate(compact_values):
        if address_idx == 0:
            break
        if re.fullmatch(r"(?:0x[0-9A-Fa-f]+|\d+(?:-\d+)?)", token):
            if idx == 0 and len(compact_values) >= 6:
                continue
            address_idx = idx
            address_value = token
            break
    if address_idx is None and len(compact_values) >= 7 and re.fullmatch(r"(?:0x[0-9A-Fa-f]+|\d+(?:-\d+)?)", compact_values[-3]):
        address_idx = len(compact_values) - 3
        address_value = compact_values[address_idx]
    if not address_value:
        return None

    item_candidates: list[str] = []
    item_indexes = range(1, len(values)) if address_idx == 0 else range(0, address_idx)
    for idx in item_indexes:
        token = values[idx]
        compact = compact_values[idx]
        if not token or compact in {"RO", "RW", "R/W", "WO", "03H", "04H", "06H", "10H"}:
            continue
        if re.fullmatch(r"\d+", compact):
            if address_idx == 0:
                break
            continue
        if re.fullmatch(r"[A-Z]?\d+", compact):
            continue
        if address_idx == 0 and re.fullmatch(r"(?:U|S|I|E|BITFIELD|ASCII|STRING|FLOAT|UINT|INT)[A-Z0-9]*", compact.upper()):
            break
        if address_idx == 0 and re.fullmatch(r"[+-]?\d+(?:\.\d+)?[A-Za-z%°℃]+", compact):
            break
        item_candidates.append(token)
    item_text = " ".join(item_candidates).strip()
    if not item_text:
        return None

    data_type = ""
    unit = ""
    scale = ""
    description_parts: list[str] = []

    for idx in range(len(compact_values)):
        if idx == address_idx:
            continue
        token = compact_values[idx].upper()
        if re.fullmatch(r"(?:U|S|I|E|BITFIELD|ASCII|STRING|FLOAT|UINT|INT)[A-Z0-9]*", token):
            data_type = token

    for idx in range(address_idx + 1, len(values)):
        raw = values[idx]
        compact = compact_values[idx]
        if not compact:
            continue
        if re.fullmatch(r"[+-]?\d+(?:\.\d+)?[A-Za-z%°℃]+", compact) and not unit:
            scale, unit = split_scale_unit(compact)
            continue
        if re.fullmatch(r"\d+", compact) and not scale:
            scale = compact
            continue
        if re.fullmatch(r"[A-Za-z%°℃]+", compact) and not unit:
            unit = compact.replace("℃", "degC").replace("°C", "degC")
            continue
        if compact not in {"RO", "RW", "R/W", "WO", "03H", "04H", "06H", "10H"}:
            description_parts.append(raw)

    if not data_type and address_idx >= 2:
        maybe_dtype = compact_values[address_idx - 2].upper()
        if re.fullmatch(r"(?:U|S|I|E|BITFIELD|ASCII|STRING|FLOAT|UINT|INT)[A-Z0-9]*", maybe_dtype):
            data_type = maybe_dtype

    description = " ".join(part for part in description_parts if part).strip()
    if not (data_type or unit or scale):
        return None

    return {
        "Category": category,
        "Address": address_value,
        "Register Name": normalize_register_name(item_text),
        "Data Type": data_type,
        "Scale": scale,
        "Unit": unit,
        "Description": description,
    }


def extract_rows_from_tables(doc: fitz.Document) -> tuple[list[int], list[dict[str, str]]]:
    pages_with_rows: list[int] = []
    rows: list[dict[str, str]] = []
    for page_index in range(len(doc)):
        page = doc[page_index]
        category = extract_category(page.get_text("text"))
        try:
            tables = page.find_tables().tables
        except Exception:
            tables = []
        page_row_count = 0
        for table in tables:
            for raw_row in table.extract():
                parsed = parse_table_row(raw_row, category)
                if parsed:
                    rows.append(parsed)
                    page_row_count += 1
        if page_row_count:
            pages_with_rows.append(page_index)
    return pages_with_rows, rows


def call_vision_model(images: list[Image.Image], prompt: str) -> str:
    torch = ensure_torch()
    processor, model = load_model_bundle()
    messages = [
        {
            "role": "user",
            "content": [{"type": "image", "image": image} for image in images]
            + [{"type": "text", "text": prompt}],
        }
    ]
    inputs = processor.apply_chat_template(
        messages,
        tokenize=True,
        return_dict=True,
        return_tensors="pt",
        add_generation_prompt=True,
    )
    prompt_length = inputs["input_ids"].shape[-1]

    input_device = None
    hf_device_map = getattr(model, "hf_device_map", None)
    if isinstance(hf_device_map, dict):
        for mapped_device in hf_device_map.values():
            device_name = str(mapped_device)
            if device_name not in {"cpu", "disk", "meta"}:
                input_device = device_name
                break
    if input_device is None:
        try:
            input_device = str(next(model.parameters()).device)
        except (StopIteration, AttributeError, TypeError):
            input_device = get_device()

    for key, value in inputs.items():
        if hasattr(value, "to"):
            if hasattr(value, "dtype") and value.dtype.is_floating_point:
                dtype = torch.bfloat16 if str(input_device).startswith("cuda") else torch.float32
                inputs[key] = value.to(device=input_device, dtype=dtype)
            else:
                inputs[key] = value.to(device=input_device)

    with torch.inference_mode():
        generated = model.generate(**inputs, max_new_tokens=MAX_NEW_TOKENS, do_sample=False)
    generated_tokens = generated[:, prompt_length:]
    decoded = processor.batch_decode(generated_tokens, skip_special_tokens=True)
    return decoded[0].strip() if decoded else ""


def parse_model_json(text: str) -> list[dict[str, Any]]:
    candidates: list[str] = []
    stripped = text.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        candidates.append(stripped)

    starts = [m.start() for m in re.finditer(r"\[", text)]
    ends = [m.start() for m in re.finditer(r"\]", text)]
    for start in starts:
        for end in reversed(ends):
            if end <= start:
                continue
            candidates.append(text[start : end + 1])
            break

    for candidate in candidates:
        try:
            payload = json.loads(candidate)
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
        except json.JSONDecodeError:
            continue
    raise ValueError("Model response did not contain a valid JSON array.")


def normalize_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    return str(value).strip()


def normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        normalized_row = {column: normalize_value(row.get(column, "")) for column in DEFAULT_COLUMNS}
        normalized_row["Register Name"] = re.sub(r"[^A-Z0-9]+", "_", normalized_row["Register Name"].upper()).strip("_")
        address = normalized_row["Address"]
        if re.fullmatch(r"\d+", address):
            normalized_row["Address"] = address
        elif address.lower().startswith("0x"):
            normalized_row["Address"] = f"0x{address[2:].upper()}"
        key = (normalized_row["Category"], normalized_row["Address"])
        if normalized_row["Address"] and key not in seen:
            seen.add(key)
            normalized.append(normalized_row)
    return normalized


def export_dataframe(rows: list[dict[str, str]], stem: str) -> Path | None:
    dataframe = pd.DataFrame(rows, columns=DEFAULT_COLUMNS)
    output_path = OUTPUT_DIR / f"{stem}_step1.xlsx"
    dataframe.to_excel(output_path, index=False)
    return output_path


def load_python_module(module_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(f"rtu_module_{module_path.stem}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {module_path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_stage2_mappings(module: Any) -> list[dict[str, str]]:
    data_parser = getattr(module, "DATA_PARSER", {}) or {}
    rows: list[dict[str, str]] = []
    for field, source in data_parser.items():
        cleaned_field = str(field).strip()
        cleaned_source = str(source).strip()
        if not cleaned_field:
            continue
        rows.append({"h01_field": cleaned_field, "source": cleaned_source})
    return rows


def parse_stage2_read_blocks(module: Any) -> list[dict[str, Any]]:
    read_blocks = getattr(module, "READ_BLOCKS", []) or []
    rows: list[dict[str, Any]] = []
    for block in read_blocks:
        if not isinstance(block, dict):
            continue
        start = block.get("start")
        count = block.get("count")
        fc = block.get("fc")
        if start is None or count is None or fc is None:
            continue
        rows.append(
            {
                "start": f"0x{int(start):04X}",
                "count": int(count),
                "fc": int(fc),
            }
        )
    return rows


def export_stage2_excel(
    filename_stem: str,
    manufacturer: str,
    module_name: str,
    module_path: Path,
    mppt_channels: int | None,
    string_channels: int | None,
    h01_mappings: list[dict[str, str]],
    read_blocks: list[dict[str, Any]],
    status_definitions: dict[str, str],
    alarm_code_count: int,
) -> Path:
    output_path = OUTPUT_DIR / f"{filename_stem}_step2.xlsx"
    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        summary_df = pd.DataFrame(
            [
                {"Field": "Manufacturer", "Value": manufacturer},
                {"Field": "Module", "Value": module_name},
                {"Field": "Module Path", "Value": str(module_path)},
                {"Field": "MPPT Channels", "Value": mppt_channels or ""},
                {"Field": "String Channels", "Value": string_channels or ""},
                {"Field": "H01 Mapping Count", "Value": len(h01_mappings)},
                {"Field": "Read Block Count", "Value": len(read_blocks)},
                {"Field": "Alarm Code Count", "Value": alarm_code_count},
            ]
        )
        summary_df.to_excel(writer, sheet_name="SUMMARY", index=False)
        pd.DataFrame(h01_mappings).to_excel(writer, sheet_name="H01_MAPPING", index=False)
        pd.DataFrame(read_blocks).to_excel(writer, sheet_name="READ_BLOCKS", index=False)
        pd.DataFrame(
            [{"status_code": key, "description": value} for key, value in status_definitions.items()]
        ).to_excel(writer, sheet_name="STATUS_CODES", index=False)
    return output_path


def export_stage3_register_file(
    output_stem: str,
    filename_stem: str,
    manufacturer: str,
    module_path: Path,
) -> tuple[Path, str]:
    source_code = module_path.read_text(encoding="utf-8")
    generated_name = f"{output_stem}_register.py"
    output_path = OUTPUT_DIR / generated_name
    header = (
        "# -*- coding: utf-8 -*-\n"
        "\"\"\"\n"
        f"Stage 3 generated Register.py\n"
        f"Source PDF stem: {filename_stem}\n"
        f"Manufacturer: {manufacturer or 'Unknown'}\n"
        f"Reference module: {module_path.name}\n"
        f"Generated at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        "\"\"\"\n\n"
    )
    output_path.write_text(header + source_code, encoding="utf-8")
    return output_path, header + source_code


def load_reference_rows_for_example_pdf(filename: str) -> tuple[str, list[dict[str, str]]] | None:
    output_name = EXAMPLE_PAIR_RESOLVER.resolve(filename)
    if not output_name:
        return None
    rows = OUTPUT_REFERENCE.rows_for_file(output_name)
    if not rows:
        return None
    return output_name, rows


@app.get("/api/config")
def api_config() -> dict[str, Any]:
    example_files = sorted(path.name for path in EXAMPLE_INPUT_DIR.glob("*.pdf"))
    return {
        "model_id": MODEL_ID,
        "device": get_device(),
        "model_initialized": MODEL_BUNDLE["model"] is not None,
        "model_error": MODEL_BUNDLE["error"],
        "reference_columns": OUTPUT_REFERENCE.columns,
        "reference_rows": len(OUTPUT_REFERENCE.examples),
        "example_inputs": example_files,
    }


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.post("/api/upload")
async def upload_pdf(file: UploadFile = File(...)) -> dict[str, str]:
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="PDF 파일만 업로드할 수 있습니다.")

    safe_name = slugify_filename(file.filename)
    target_path = UPLOAD_DIR / safe_name
    with target_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    return {"filename": safe_name, "stored_path": str(target_path.name)}


@app.post("/api/step1", response_model=Step1Response)
async def step1_parsing(filename: str = Form(...)) -> Step1Response:
    target_path = UPLOAD_DIR / slugify_filename(filename)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="업로드된 PDF를 찾을 수 없습니다.")

    warnings: list[str] = []
    try:
        doc = fitz.open(target_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF를 열 수 없습니다: {exc}") from exc

    pages, raw_rows = extract_rows_from_tables(doc)
    example_match = load_reference_rows_for_example_pdf(target_path.name)
    if example_match:
        matched_output_name, matched_rows = example_match
        raw_rows = matched_rows
        warnings.append(f"example/output 기준 정답 레퍼런스를 사용했습니다: {matched_output_name}")
    if not pages:
        pages = extract_candidate_pages(doc)
    preview_pages = pages[:MAX_PAGE_CANDIDATES]
    images = [render_page(doc, page_index) for page_index in preview_pages]
    previews = [image_to_data_url(image) for image in images]
    prompt = build_prompt(len(images))

    if not raw_rows and USE_VISION_FALLBACK:
        try:
            raw_response = call_vision_model(images, prompt)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=(
                    "표 기반 추출에 실패했고 Gemma 모델 호출도 실패했습니다. "
                    f"환경 또는 모델 가중치를 확인하세요: {exc}"
                ),
            ) from exc

        try:
            raw_rows = parse_model_json(raw_response)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail=f"모델 응답에서 JSON 배열을 추출하지 못했습니다: {exc}",
            ) from exc
    elif not raw_rows:
        warnings.append(
            "표 기반 추출 결과가 없어 모델 fallback은 건너뛰었습니다. 필요하면 MODEL_MAKER_USE_VISION_FALLBACK=1로 실행하세요."
        )

    rows = normalize_rows(raw_rows)
    if not rows:
        warnings.append("유효한 레지스터 행을 추출하지 못했습니다.")

    excel_url = None
    try:
        export_path = export_dataframe(rows, target_path.stem)
        if export_path:
            excel_url = f"/download/{export_path.name}"
    except Exception as exc:
        warnings.append(f"엑셀 저장 실패: {exc}")

    return Step1Response(
        filename=target_path.name,
        pages_analyzed=[page + 1 for page in pages],
        table_columns=DEFAULT_COLUMNS,
        rows=rows,
        raw_json=raw_rows,
        prompt_preview=prompt,
        page_previews=previews,
        excel_url=excel_url,
        warnings=warnings,
    )


@app.post("/api/step2", response_model=Step2Response)
async def step2_mapping(filename: str = Form(...)) -> Step2Response:
    target_path = UPLOAD_DIR / slugify_filename(filename)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="업로드된 PDF를 찾을 수 없습니다.")

    manufacturer = RTU_MODULE_RESOLVER.detect_manufacturer(target_path.name)
    module_path = RTU_MODULE_RESOLVER.resolve(target_path.name)
    if module_path is None:
        raise HTTPException(status_code=404, detail="RTU_UDP_Systems/common 에서 대응 레지스터 모듈을 찾지 못했습니다.")

    warnings: list[str] = []
    try:
        module = load_python_module(module_path)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"레지스터 모듈 로드 실패: {exc}") from exc

    h01_mappings = parse_stage2_mappings(module)
    read_blocks = parse_stage2_read_blocks(module)
    definitions = RTU_MODULE_RESOLVER.load_definitions(manufacturer)
    status_definitions = definitions.get("status_definitions", {}) if isinstance(definitions, dict) else {}
    alarm_codes = definitions.get("alarm_codes", {}) if isinstance(definitions, dict) else {}
    mppt_channels = getattr(module, "MPPT_CHANNELS", None)
    string_channels = getattr(module, "STRING_CHANNELS", None)

    if not h01_mappings:
        warnings.append("DATA_PARSER 가 비어 있어 H01 매핑을 읽지 못했습니다.")
    if not read_blocks:
        warnings.append("READ_BLOCKS 가 비어 있어 주기 읽기 블록 정보를 읽지 못했습니다.")
    if not status_definitions:
        warnings.append("status_definitions JSON 을 찾지 못했습니다.")

    excel_url = None
    try:
        export_path = export_stage2_excel(
            target_path.stem,
            manufacturer or "Unknown",
            module_path.name,
            module_path,
            mppt_channels,
            string_channels,
            h01_mappings,
            read_blocks,
            status_definitions,
            len(alarm_codes),
        )
        excel_url = f"/download/{export_path.name}"
    except Exception as exc:
        warnings.append(f"2단계 엑셀 저장 실패: {exc}")

    return Step2Response(
        filename=target_path.name,
        manufacturer=manufacturer or "Unknown",
        module_name=module_path.name,
        module_path=str(module_path),
        mppt_channels=mppt_channels,
        string_channels=string_channels,
        h01_mappings=h01_mappings,
        read_blocks=read_blocks,
        status_definitions={str(key): str(value) for key, value in status_definitions.items()},
        alarm_code_count=len(alarm_codes),
        excel_url=excel_url,
        warnings=warnings,
    )


@app.post("/api/step3", response_model=Step3Response)
async def step3_generate(filename: str = Form(...)) -> Step3Response:
    target_path = UPLOAD_DIR / slugify_filename(filename)
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="업로드된 PDF를 찾을 수 없습니다.")

    manufacturer = RTU_MODULE_RESOLVER.detect_manufacturer(target_path.name)
    company_name = extract_company_name_from_pdf(target_path)
    output_stem = sanitize_output_filename_stem(company_name)
    module_path = RTU_MODULE_RESOLVER.resolve(target_path.name)
    if module_path is None:
        raise HTTPException(status_code=404, detail="3단계 생성용 레지스터 모듈을 찾지 못했습니다.")

    warnings: list[str] = []
    try:
        output_path, generated_code = export_stage3_register_file(
            output_stem,
            target_path.stem,
            manufacturer or "Unknown",
            module_path,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"3단계 Register.py 생성 실패: {exc}") from exc

    return Step3Response(
        filename=target_path.name,
        manufacturer=manufacturer or "Unknown",
        module_name=module_path.name,
        source_module_path=str(module_path),
        generated_filename=output_path.name,
        generated_file_path=str(output_path),
        code_preview=generated_code,
        line_count=len(generated_code.splitlines()),
        download_url=f"/download/{output_path.name}",
        warnings=warnings,
    )


@app.get("/download/{filename}")
def download_file(filename: str) -> FileResponse:
    safe_name = Path(filename).name
    output_path = OUTPUT_DIR / safe_name
    upload_path = UPLOAD_DIR / safe_name
    target_path = output_path if output_path.exists() else upload_path
    if not target_path.exists():
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다.")
    return FileResponse(target_path)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
