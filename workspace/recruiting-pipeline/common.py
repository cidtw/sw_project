#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
WORKSPACE_DIR = BASE_DIR / "_workspace"

STATE_FILE = DATA_DIR / "pipeline_state.json"
DB_PATH = DATA_DIR / "recruitment.db"
USER_PROFILE_PATH = DATA_DIR / "user_profile.json"
FINAL_DASHBOARD_PATH = DATA_DIR / "final_recruit_dashboard.json"

FETCH_OUTPUT_PATH = WORKSPACE_DIR / "fetch_output.json"
ENRICH_OUTPUT_PATH = WORKSPACE_DIR / "enrich_output.json"
SCORE_OUTPUT_PATH = WORKSPACE_DIR / "score_output.json"
VERIFY_OUTPUT_PATH = WORKSPACE_DIR / "verify_output.json"


def read_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def calculate_file_hash(path):
    path = Path(path)
    if not path.exists():
        return ""
    hasher = hashlib.md5()
    try:
        with path.open("rb") as f:
            hasher.update(f.read())
        return hasher.hexdigest()
    except OSError:
        return ""


def init_openai_client(component_name):
    if not os.environ.get("OPENAI_API_KEY"):
        return None

    try:
        from openai import OpenAI
    except ImportError as exc:
        print(f"OpenAI package unavailable for {component_name}; using fallback data: {exc}", file=sys.stderr)
        return None

    try:
        return OpenAI()
    except Exception as exc:
        print(f"OpenAI client init failed for {component_name}: {exc}", file=sys.stderr)
        return None


def normalize_company_name(company):
    text = str(company or "").strip()
    for pattern in (r"\(주\)", "㈜", "주식회사", r"\(유\)", "유한회사", r"\(재\)", "재단법인"):
        text = re.sub(pattern, "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s\(\)\[\]{}]", "", text)
    return text.lower()


def normalize_title(title):
    text = str(title or "").strip()
    text = re.sub(r"D[-_]?\d+\s*스크랩", "", text, flags=re.IGNORECASE)
    text = re.sub(r"채용$", "", text)
    text = re.sub(r"[\s\(\)\[\]\-_,.!?&@:;|'\"/\\]", "", text)
    return text.lower()


def normalized_job_key(company, title):
    return f"{normalize_company_name(company)}_{normalize_title(title)}"


def dedupe_preserve_order(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def post_json(url, payload, timeout=15):
    try:
        import requests
    except ImportError as exc:
        return False, 0, f"requests package unavailable: {exc}"

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        return 200 <= response.status_code < 300, response.status_code, response.text
    except Exception as exc:
        return False, 0, str(exc)
