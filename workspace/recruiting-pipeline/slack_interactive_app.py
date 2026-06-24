#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from fastapi import FastAPI, Form

from chatbot_search import hard_match_score, load_candidates, run_search
from common import BASE_DIR, DATA_DIR, DB_PATH
from scorer import clean_text, normalize_schema_payload

SLACK_API_BASE = "https://slack.com/api"
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
PROFILE_DB_PATH = DATA_DIR / "slack_user_profiles.db"
PIPELINE_SCRIPT = BASE_DIR / "pipeline.py"
LAUNCHER_BLOCKS_PATH = BASE_DIR / "slack-launcher-blocks.json"
SUPPORTED_SITES = ["JobKorea", "Saramin", "Incruit"]
DEFAULT_EMPLOYMENT_TYPES = "정규직,계약직,인턴,기타"
DEFAULT_CAREER_TYPES = "신입,경력 무관,경력직"
CAREER_PERIODS = ["3년 미만", "3년 ~ 5년", "5년 ~ 8년", "8년 ~ 11년", "11년 이상"]

app = FastAPI(title="Recruiting Slack Interactive App")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def init_profile_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                age_gender TEXT,
                education TEXT,
                career_level TEXT,
                location_pref TEXT,
                desired_salary TEXT,
                certificates TEXT,
                skills TEXT,
                experience_summary TEXT,
                language_scores TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        user_columns = {row[1] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        user_profile_columns = {
            "career_level": "TEXT",
            "skills": "TEXT",
            "experience_summary": "TEXT",
            "language_scores": "TEXT",
        }
        for column_name, column_type in user_profile_columns.items():
            if column_name not in user_columns:
                conn.execute(f"ALTER TABLE users ADD COLUMN {column_name} {column_type}")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id TEXT PRIMARY KEY,
                excluded_sites TEXT,
                excluded_keywords TEXT,
                employment_types TEXT,
                include_imminent_deadlines TEXT,
                career_types TEXT,
                career_periods TEXT,
                push_enabled TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """
        )
        existing_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(user_preferences)").fetchall()
        }
        preference_columns = {
            "employment_types": "TEXT",
            "include_imminent_deadlines": "TEXT",
            "career_types": "TEXT",
            "career_periods": "TEXT",
            "push_enabled": "TEXT",
        }
        for column_name, column_type in preference_columns.items():
            if column_name not in existing_columns:
                conn.execute(f"ALTER TABLE user_preferences ADD COLUMN {column_name} {column_type}")
        conn.commit()


def get_user_profile(user_id):
    init_profile_db()
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def save_user_profile(user_id, profile):
    init_profile_db()
    existing = get_user_profile(user_id)
    created_at = existing.get("created_at") if existing else now_utc()
    values = {
        "user_id": user_id,
        "age_gender": clean_text(profile.get("age_gender")),
        "education": clean_text(profile.get("education")),
        "career_level": clean_text(profile.get("career_level")),
        "location_pref": clean_text(profile.get("location_pref")),
        "desired_salary": clean_text(profile.get("desired_salary")),
        "certificates": clean_text(profile.get("certificates")),
        "skills": clean_text(profile.get("skills")),
        "experience_summary": clean_text(profile.get("experience_summary")),
        "language_scores": clean_text(profile.get("language_scores")),
        "created_at": created_at,
        "updated_at": now_utc(),
    }
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO users (
                user_id, age_gender, education, career_level, location_pref, desired_salary,
                certificates, skills, experience_summary, language_scores, created_at, updated_at
            )
            VALUES (
                :user_id, :age_gender, :education, :career_level, :location_pref,
                :desired_salary, :certificates, :skills, :experience_summary,
                :language_scores, :created_at, :updated_at
            )
            ON CONFLICT(user_id) DO UPDATE SET
                age_gender = excluded.age_gender,
                education = excluded.education,
                career_level = excluded.career_level,
                location_pref = excluded.location_pref,
                desired_salary = excluded.desired_salary,
                certificates = excluded.certificates,
                skills = excluded.skills,
                experience_summary = excluded.experience_summary,
                language_scores = excluded.language_scores,
                updated_at = excluded.updated_at
            """,
            values,
        )
        conn.commit()
    return values


def delete_user_profile(user_id):
    init_profile_db()
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        conn.commit()


def get_user_preferences(user_id):
    init_profile_db()
    defaults = {
        "user_id": user_id,
        "excluded_sites": "",
        "excluded_keywords": "",
        "employment_types": DEFAULT_EMPLOYMENT_TYPES,
        "include_imminent_deadlines": "true",
        "career_types": DEFAULT_CAREER_TYPES,
        "career_periods": "",
        "push_enabled": "true",
    }
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM user_preferences WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return defaults
    saved = dict(row)
    defaults.update({key: value for key, value in saved.items() if value is not None})
    return defaults


def save_user_preferences(user_id, preferences):
    init_profile_db()
    existing = get_user_preferences(user_id)
    created_at = existing.get("created_at") or now_utc()
    values = {
        "user_id": user_id,
        "excluded_sites": ",".join(split_csv(preferences.get("excluded_sites", ""))),
        "excluded_keywords": clean_text(preferences.get("excluded_keywords", "")),
        "employment_types": ",".join(split_csv(preferences.get("employment_types", DEFAULT_EMPLOYMENT_TYPES))),
        "include_imminent_deadlines": clean_text(preferences.get("include_imminent_deadlines", "true")) or "false",
        "career_types": ",".join(split_csv(preferences.get("career_types", DEFAULT_CAREER_TYPES))),
        "career_periods": ",".join(split_csv(preferences.get("career_periods", ""))),
        "push_enabled": clean_text(preferences.get("push_enabled", "true")) or "false",
        "created_at": created_at,
        "updated_at": now_utc(),
    }
    with sqlite3.connect(PROFILE_DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO user_preferences (
                user_id, excluded_sites, excluded_keywords, employment_types,
                include_imminent_deadlines, career_types, career_periods,
                push_enabled, created_at, updated_at
            )
            VALUES (
                :user_id, :excluded_sites, :excluded_keywords, :employment_types,
                :include_imminent_deadlines, :career_types, :career_periods,
                :push_enabled, :created_at, :updated_at
            )
            ON CONFLICT(user_id) DO UPDATE SET
                excluded_sites = excluded.excluded_sites,
                excluded_keywords = excluded.excluded_keywords,
                employment_types = excluded.employment_types,
                include_imminent_deadlines = excluded.include_imminent_deadlines,
                career_types = excluded.career_types,
                career_periods = excluded.career_periods,
                push_enabled = excluded.push_enabled,
                updated_at = excluded.updated_at
            """,
            values,
        )
        conn.commit()
    return values


def split_csv(value):
    return [part.strip() for part in clean_text(value).split(",") if part.strip()]


def profile_to_search_profile(profile):
    profile = profile or {}
    return {
        "age_gender": profile.get("age_gender", ""),
        "education": profile.get("education", ""),
        "career_level": profile.get("career_level", ""),
        "경력구분": profile.get("career_level", ""),
        "희망연봉": profile.get("desired_salary", ""),
        "근무희망지역": split_csv(profile.get("location_pref", "")),
        "보유자격": split_csv(profile.get("certificates", "")),
        "skills": split_csv(profile.get("skills", "")),
        "보유기술": split_csv(profile.get("skills", "")),
        "experience_summary": profile.get("experience_summary", ""),
        "경험요약": profile.get("experience_summary", ""),
        "language_scores": profile.get("language_scores", ""),
        "어학성적": profile.get("language_scores", ""),
    }


def profile_input(block_id, action_id, label, placeholder, initial_value="", optional=False):
    element = {
        "type": "plain_text_input",
        "action_id": action_id,
        "placeholder": {"type": "plain_text", "text": placeholder},
    }
    if initial_value:
        element["initial_value"] = initial_value
    return {
        "type": "input",
        "optional": optional,
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def profile_select(block_id, action_id, label, options, initial_value=""):
    select_options = [plain_option(option) for option in options]
    element = {
        "type": "static_select",
        "action_id": action_id,
        "placeholder": {"type": "plain_text", "text": "선택해 주세요"},
        "options": select_options,
    }
    if initial_value:
        for option in select_options:
            if option["value"] == initial_value:
                element["initial_option"] = option
                break
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def build_user_profile_modal(existing_data=None):
    existing_data = existing_data or {}
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "회원님의 정보를 토대로 AI가 맞춤형 채용 공고를 매칭하고 적합도 점수(`fit_score`)를 계산합니다. 정확하게 입력해 주세요.",
            },
        },
        {"type": "divider"},
        profile_input(
            "blk_demographics",
            "input_age_gender",
            "연령 및 성별",
            "예시) 27세 / 남성",
            existing_data.get("age_gender", ""),
        ),
        profile_input(
            "blk_education",
            "input_education",
            "최종 학력 및 전공",
            "예시) 대졸(4년) / 컴퓨터공학 전공",
            existing_data.get("education", ""),
        ),
        profile_select(
            "blk_career_level",
            "input_career_level",
            "경력 구분 및 총 경력",
            ["신입", "경력(1년~3년 미만)", "경력(3년~5년 미만)", "경력(5년 이상)"],
            existing_data.get("career_level", ""),
        ),
        profile_input(
            "blk_location",
            "input_location",
            "근무 희망 지역",
            "예시) 서울 전체, 경기 성남시",
            existing_data.get("location_pref", ""),
        ),
        profile_input(
            "blk_salary",
            "input_salary",
            "최소 희망 연봉 (숫자만 입력)",
            "예시) 4000",
            existing_data.get("desired_salary", ""),
        ),
        profile_input(
            "blk_certificates",
            "input_certificates",
            "보유 자격증 (쉼표로 구분)",
            "예시) 빅데이터분석기사, 정보처리기사, SQLD",
            existing_data.get("certificates", ""),
        ),
        profile_input(
            "blk_skills",
            "input_skills",
            "핵심 보유 기술 스택",
            "예시) Python, FastAPI, SQLite, Docker, Slack API",
            existing_data.get("skills", ""),
        ),
        profile_input(
            "blk_experience_summary",
            "input_experience_summary",
            "주요 경력/인턴십/프로젝트 한 줄 요약",
            "예시) 공공기관 데이터 검증 계약직, 자산 관리 자동화 프로젝트",
            existing_data.get("experience_summary", ""),
        ),
        profile_input(
            "blk_language_scores",
            "input_language_scores",
            "어학 성적 및 보유 점수 (옵션)",
            "예시) 오픽 IH, 토익 850점",
            existing_data.get("language_scores", ""),
            optional=True,
        ),
        {
            "type": "actions",
            "block_id": "blk_profile_actions",
            "elements": [
                {
                    "type": "button",
                    "style": "danger",
                    "text": {"type": "plain_text", "text": "입력 정보 전체 삭제", "emoji": True},
                    "action_id": "btn_delete_profile",
                    "confirm": {
                        "title": {"type": "plain_text", "text": "개인정보 삭제"},
                        "text": {"type": "mrkdwn", "text": "저장된 개인정보를 모두 삭제할까요?"},
                        "confirm": {"type": "plain_text", "text": "삭제"},
                        "deny": {"type": "plain_text", "text": "취소"},
                    },
                }
            ],
        },
    ]
    return {
        "type": "modal",
        "callback_id": "modal_user_profile",
        "title": {"type": "plain_text", "text": "개인정보 설정"},
        "submit": {"type": "plain_text", "text": "저장하기"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": blocks,
    }


def build_profile_deleted_modal():
    return {
        "type": "modal",
        "callback_id": "modal_user_profile_deleted",
        "title": {"type": "plain_text", "text": "삭제 완료"},
        "close": {"type": "plain_text", "text": "닫기"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "저장된 개인정보가 삭제되었습니다. 다시 등록하려면 `개인정보 확인/수정` 버튼이나 `/recruit 프로필` 명령어를 사용해 주세요.",
                },
            }
        ],
    }


def site_option(site):
    labels = {"JobKorea": "잡코리아", "Saramin": "사람인", "Incruit": "인크루트"}
    return {"text": {"type": "plain_text", "text": labels.get(site, site)}, "value": site}


def plain_option(text, value=None):
    return {"text": {"type": "plain_text", "text": text}, "value": value or text}


def initial_options(options, selected_values):
    selected_values = set(selected_values)
    return [option for option in options if option["value"] in selected_values]


def checkbox_input(block_id, action_id, label, options, selected_values=None, optional=True):
    selected = initial_options(options, selected_values or [])
    element = {
        "type": "checkboxes",
        "action_id": action_id,
        "options": options,
    }
    if selected:
        element["initial_options"] = selected
    return {
        "type": "input",
        "optional": optional,
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": element,
    }


def build_search_preferences_modal(existing_data=None):
    existing_data = existing_data or {}
    excluded_sites = set(split_csv(existing_data.get("excluded_sites", "")))
    site_options = [site_option(site) for site in SUPPORTED_SITES]
    employment_options = [plain_option(value) for value in ["정규직", "계약직", "인턴", "기타"]]
    career_options = [plain_option(value) for value in ["신입", "경력 무관", "경력직"]]
    career_period_options = [plain_option(value) for value in CAREER_PERIODS]
    notification_options = [
        plain_option("실시간 업데이트 푸시 알림 받기", "push_enabled"),
        plain_option("마감 임박 공고 포함 (D-3 이내)", "include_imminent_deadlines"),
    ]
    selected_notifications = []
    if clean_text(existing_data.get("push_enabled", "true")).lower() == "true":
        selected_notifications.append("push_enabled")
    if clean_text(existing_data.get("include_imminent_deadlines", "true")).lower() == "true":
        selected_notifications.append("include_imminent_deadlines")
    keyword_element = {
        "type": "plain_text_input",
        "action_id": "input_excluded_keywords",
        "multiline": False,
        "placeholder": {"type": "plain_text", "text": "예시) 영업, 계약직, 파견"},
    }
    if existing_data.get("excluded_keywords"):
        keyword_element["initial_value"] = existing_data["excluded_keywords"]
    return {
        "type": "modal",
        "callback_id": "modal_search_preferences",
        "title": {"type": "plain_text", "text": "검색 환경설정"},
        "submit": {"type": "plain_text", "text": "저장하기"},
        "close": {"type": "plain_text", "text": "취소"},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "맞춤 공고 검색과 실시간 푸시 알림 조건을 설정합니다."},
            },
            checkbox_input(
                "blk_notifications",
                "input_notifications",
                "알림 및 검색 주기/피드백",
                notification_options,
                selected_notifications,
            ),
            checkbox_input(
                "blk_excluded_sites",
                "input_excluded_sites",
                "검색결과에서 제외할 채용사이트",
                site_options,
                excluded_sites,
            ),
            {
                "type": "input",
                "optional": True,
                "block_id": "blk_excluded_keywords",
                "label": {"type": "plain_text", "text": "제외 키워드 (쉼표로 구분)"},
                "element": keyword_element,
            },
            checkbox_input(
                "blk_employment_types",
                "input_employment_types",
                "고용 형태 필터",
                employment_options,
                split_csv(existing_data.get("employment_types", DEFAULT_EMPLOYMENT_TYPES)),
            ),
            checkbox_input(
                "blk_career_types",
                "input_career_types",
                "신입/경력 여부 필터",
                career_options,
                split_csv(existing_data.get("career_types", DEFAULT_CAREER_TYPES)),
            ),
            checkbox_input(
                "blk_career_periods",
                "input_career_periods",
                "경력직 선택 시 경력 기간 필터",
                career_period_options,
                split_csv(existing_data.get("career_periods", "")),
            ),
        ],
    }


def call_slack_api(method, payload):
    if not SLACK_BOT_TOKEN:
        return False, "SLACK_BOT_TOKEN is not set"

    response = requests.post(
        f"{SLACK_API_BASE}/{method}",
        headers={
            "Authorization": f"Bearer {SLACK_BOT_TOKEN}",
            "Content-Type": "application/json; charset=utf-8",
        },
        json=payload,
        timeout=10,
    )
    body = response.json()
    return bool(body.get("ok")), json.dumps(body, ensure_ascii=False)


def open_slack_modal(trigger_id, existing_data=None):
    return call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_user_profile_modal(existing_data)})


def open_search_preferences_modal(trigger_id, existing_data=None):
    return call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_search_preferences_modal(existing_data)})


def update_slack_modal(view_id, view):
    return call_slack_api("views.update", {"view_id": view_id, "view": view})


def post_response(response_url, payload):
    if not response_url:
        return
    requests.post(response_url, json=payload, timeout=10)


def build_launcher_blocks(user_id="", user_name="회원님"):
    with LAUNCHER_BLOCKS_PATH.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    display_name = f"<@{user_id}>" if user_id else f"*{clean_text(user_name) or '회원님'}*"
    try:
        text = payload["blocks"][0]["text"]["text"]
        payload["blocks"][0]["text"]["text"] = text.replace("*회원님*", display_name)
    except (KeyError, IndexError, TypeError):
        pass
    return payload.get("blocks", [])


def slash_help_text(command):
    command = clean_text(command) or "/recruit"
    return (
        f"`{command}` : 메뉴 열기\n"
        f"`{command} 업데이트` : 최신 채용공고 수집\n"
        f"`{command} 검색` : 내 프로필 기준 맞춤 공고 추천\n"
        f"`{command} 프로필` : 개인정보 입력/수정\n"
        f"`{command} 설정` : 검색/알림 환경설정"
    )


def command_matches(text, *keywords):
    normalized = clean_text(text).lower()
    return any(keyword in normalized for keyword in keywords)


def build_job_blocks(payload, headline="맞춤 채용공고 추천", next_index=None):
    payload = normalize_schema_payload(payload, payload.get("dispatch_type", "SEARCH"), payload.get("slack_user_id", ""))
    keywords = " ".join(payload.get("job_keywords", []))
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": headline[:150]},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{payload['company']}*\n{payload['title']}\n*지역* {payload['location']} | *연봉* {payload['salary']} | *형태* {payload['employment_type']}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*자격요건*\n{payload['requirements']}"},
                {"type": "mrkdwn", "text": f"*우대요건*\n{payload['preferences']}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*직무기술서*\n{payload['jd_summary']}"},
        },
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": keywords or "#채용매칭"}],
        },
    ]

    action_elements = []
    if payload["detail_url"]:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "공고 보기"},
                "url": payload["detail_url"],
                "action_id": "btn_open_detail",
            }
        )
    if next_index is not None:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "다른 추천 채용"},
                "action_id": "btn_next_recommendation",
                "value": str(next_index),
            }
        )
    if action_elements:
        blocks.append(
            {
                "type": "actions",
                "elements": action_elements,
            }
        )
    return blocks


def detect_platform(item):
    platform = clean_text(item.get("platform", ""))
    if platform:
        return platform
    url = clean_text(item.get("detail_url", "")).lower()
    if "jobkorea" in url:
        return "JobKorea"
    if "saramin" in url:
        return "Saramin"
    if "incruit" in url:
        return "Incruit"
    return "Unknown"


def truthy(value):
    return clean_text(value).lower() in {"1", "true", "yes", "y", "on"}


def parse_json_field(value):
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        return {}


def deadline_date(deadline):
    text = clean_text(deadline)
    match = re.search(r"(\d{4})[./-](\d{1,2})[./-](\d{1,2})", text)
    if match:
        try:
            return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).date()
        except ValueError:
            return None
    match = re.search(r"(?<!\d)(\d{1,2})[./-](\d{1,2})(?!\d)", text)
    if match:
        today = datetime.now().date()
        try:
            parsed = datetime(today.year, int(match.group(1)), int(match.group(2))).date()
        except ValueError:
            return None
        if parsed < today - timedelta(days=31):
            parsed = datetime(today.year + 1, parsed.month, parsed.day).date()
        return parsed
    return None


def deadline_dday(deadline):
    text = clean_text(deadline)
    if re.search(r"D[-_ ]?(?:day|0)", text, re.IGNORECASE) or "오늘" in text:
        return 0
    if "내일" in text:
        return 1
    match = re.search(r"D[-_ ]?(\d+)", text, re.IGNORECASE)
    return int(match.group(1)) if match else None


def is_closed_deadline(deadline):
    text = clean_text(deadline)
    if not text:
        return False
    if re.search(r"(마감완료|접수마감|채용마감|closed|expired)", text, re.IGNORECASE):
        return True
    if re.search(r"(상시|채용시|수시|접수중)", text, re.IGNORECASE):
        return False
    parsed = deadline_date(text)
    return parsed is not None and parsed < datetime.now().date()


def is_imminent_deadline(deadline):
    dday = deadline_dday(deadline)
    if dday is not None:
        return 0 <= dday <= 3
    parsed = deadline_date(deadline)
    if not parsed:
        return False
    days_left = (parsed - datetime.now().date()).days
    return 0 <= days_left <= 3


def normalize_employment_type(value):
    text = clean_text(value)
    if "정규" in text:
        return "정규직"
    if "계약" in text:
        return "계약직"
    if "인턴" in text:
        return "인턴"
    return "기타"


def candidate_text(item):
    return " ".join(
        clean_text(item.get(key, ""))
        for key in ["company", "title", "employment_type", "requirements", "preferences", "jd_summary"]
    )


def detect_career_types(text):
    matches = set()
    compact = re.sub(r"\s+", "", clean_text(text))
    if "신입" in compact:
        matches.add("신입")
    if "경력무관" in compact or "무관" in compact:
        matches.add("경력 무관")
    if re.search(r"경력\s*\d|경력직|(?:\d+)\s*년\s*(?:이상|이하|~|-)", text):
        matches.add("경력직")
    return matches


def career_period_bucket(text):
    year_matches = []
    for pattern in (r"경력\s*(\d+)", r"(\d+)\s*년\s*(?:이상|이하|~|-)"):
        year_matches.extend(int(match) for match in re.findall(pattern, text))
    if not year_matches:
        return ""
    year = min(year_matches)
    if year < 3:
        return "3년 미만"
    if year < 5:
        return "3년 ~ 5년"
    if year < 8:
        return "5년 ~ 8년"
    if year < 11:
        return "8년 ~ 11년"
    return "11년 이상"


def filter_candidates_by_preferences(candidates, preferences):
    preferences = preferences or {}
    excluded_sites = set(split_csv(preferences.get("excluded_sites", "")))
    excluded_keywords = split_csv(preferences.get("excluded_keywords", ""))
    selected_employment_types = set(split_csv(preferences.get("employment_types", DEFAULT_EMPLOYMENT_TYPES)))
    include_imminent = truthy(preferences.get("include_imminent_deadlines", "true"))
    selected_career_types = set(split_csv(preferences.get("career_types", DEFAULT_CAREER_TYPES)))
    selected_career_periods = set(split_csv(preferences.get("career_periods", "")))
    filtered = []
    for item in candidates:
        platform = detect_platform(item)
        if platform in excluded_sites:
            continue
        if is_closed_deadline(item.get("deadline", "")):
            continue
        if not include_imminent and is_imminent_deadline(item.get("deadline", "")):
            continue
        if selected_employment_types:
            employment_type = normalize_employment_type(item.get("employment_type", ""))
            if employment_type not in selected_employment_types:
                continue
        text = candidate_text(item)
        if any(keyword and keyword in text for keyword in excluded_keywords):
            continue
        career_types = detect_career_types(text)
        if selected_career_types and career_types and career_types.isdisjoint(selected_career_types):
            continue
        period_bucket = career_period_bucket(text)
        if selected_career_periods and period_bucket and period_bucket not in selected_career_periods:
            continue
        filtered.append(item)
    return filtered


def load_recent_open_jobs(limit=10, preferences=None):
    if not DB_PATH.exists():
        return filter_candidates_by_preferences(load_candidates(), preferences)[:limit]
    rows = []
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                """
                SELECT platform, company, title, detail_url, deadline, image_url,
                       deep_scraped_json, extracted_info_json, scraped_at
                FROM jobs
                ORDER BY datetime(scraped_at) DESC, rowid DESC
                LIMIT 80
                """
            ).fetchall()
        except sqlite3.Error:
            rows = []

    candidates = []
    for row in rows:
        deep_data = parse_json_field(row["deep_scraped_json"])
        extracted = parse_json_field(row["extracted_info_json"])
        location = deep_data.get("location") or extracted.get("location") or "확인 필요"
        employment_type = deep_data.get("employment_type") or extracted.get("employment_type") or "확인 필요"
        candidates.append(
            {
                "dispatch_type": "SEARCH",
                "platform": row["platform"],
                "company": row["company"],
                "title": row["title"],
                "employment_type": employment_type,
                "location": location,
                "salary": deep_data.get("salary") or extracted.get("salary") or "확인 필요",
                "requirements": extracted.get("requirements") or deep_data.get("requirements") or "",
                "preferences": extracted.get("preferences") or deep_data.get("preferences") or "",
                "jd_summary": extracted.get("jd_summary") or deep_data.get("jd_summary") or "",
                "job_keywords": extracted.get("job_keywords") or [],
                "detail_url": row["detail_url"],
                "company_career_url": extracted.get("company_career_url") or "",
                "image_url": row["image_url"],
                "deadline": row["deadline"],
                "scraped_at": row["scraped_at"],
            }
        )
    filtered = filter_candidates_by_preferences(candidates, preferences)
    return filtered[:limit]


def cell(value, width):
    text = clean_text(value).replace("|", "/")
    if len(text) > width:
        text = text[: max(0, width - 1)] + "…"
    return text.ljust(width)


def build_job_table_blocks(candidates, user_id="", title="채용공고 업데이트", limit=10):
    mention = f"<@{user_id}>님, " if user_id else ""
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": title[:150]},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"{mention}업로드 최신순 상위 {limit}개입니다. 마감 공고는 제외했습니다.",
                }
            ],
        },
        {"type": "divider"},
    ]
    if not candidates:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "조건에 맞는 채용 중 공고가 없습니다."},
            }
        )
        return blocks

    for idx, item in enumerate(candidates[:limit], start=1):
        payload = normalize_schema_payload(item, "SEARCH", user_id)
        platform = detect_platform(item)
        deadline = clean_text(item.get("deadline", "")) or "마감일 확인 필요"
        title_text = payload["title"]
        if payload["detail_url"]:
            title_text = f"<{payload['detail_url']}|{payload['title']}>"
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{idx}. {payload['company']}*  ·  `{platform}`\n"
                        f"{title_text}\n"
                        f"지역: {payload['location']}  |  마감: {deadline}"
                    ),
                },
            }
        )
    return blocks


def post_ephemeral(response_url, payload):
    payload = payload.copy()
    payload.setdefault("response_type", "ephemeral")
    payload.setdefault("replace_original", False)
    post_response(response_url, payload)


def run_pipeline_and_post_table(response_url, user_id):
    try:
        subprocess.run([sys.executable, "-X", "utf8", str(PIPELINE_SCRIPT)], cwd=str(BASE_DIR), check=False)
    finally:
        preferences = get_user_preferences(user_id)
        candidates = load_recent_open_jobs(10, preferences)
        post_ephemeral(
            response_url,
            {
                "text": "채용공고 업데이트 결과",
                "blocks": build_job_table_blocks(candidates, user_id, "최신 채용공고 업데이트 결과"),
            },
        )


def build_custom_job_blocks(user_id, profile, start_index=0):
    preferences = get_user_preferences(user_id)
    request = {
        "slack_user_id": user_id,
        "query": " ".join(profile_to_search_profile(profile).get("근무희망지역", [])),
        "profile": profile_to_search_profile(profile),
    }
    candidates = filter_candidates_by_preferences(load_candidates(), preferences)
    if candidates:
        ranked = sorted(candidates, key=lambda item: hard_match_score(item, request["profile"], request["query"]), reverse=True)
        selected_index = start_index % len(ranked)
        best = normalize_schema_payload(ranked[selected_index], "SEARCH", user_id)
        next_index = (selected_index + 1) % len(ranked) if len(ranked) > 1 else None
        headline = f"AI 맞춤 채용공고 추천 ({selected_index + 1}/{len(ranked)})"
    else:
        ranked = []
        best = run_search(request)
        next_index = None
        headline = "AI 맞춤 채용공고 추천"
    return build_job_blocks(best, headline, next_index=next_index)


def parse_modal_submission(data):
    values = data.get("view", {}).get("state", {}).get("values", {})

    def get_value(block_id, action_id):
        return clean_text(values.get(block_id, {}).get(action_id, {}).get("value", ""))

    def get_selected_value(block_id, action_id):
        return clean_text(
            values.get(block_id, {}).get(action_id, {}).get("selected_option", {}).get("value", "")
        )

    return {
        "age_gender": get_value("blk_demographics", "input_age_gender"),
        "education": get_value("blk_education", "input_education"),
        "career_level": get_selected_value("blk_career_level", "input_career_level"),
        "location_pref": get_value("blk_location", "input_location"),
        "desired_salary": get_value("blk_salary", "input_salary"),
        "certificates": get_value("blk_certificates", "input_certificates"),
        "skills": get_value("blk_skills", "input_skills"),
        "experience_summary": get_value("blk_experience_summary", "input_experience_summary"),
        "language_scores": get_value("blk_language_scores", "input_language_scores"),
    }


def parse_preferences_submission(data):
    values = data.get("view", {}).get("state", {}).get("values", {})

    def selected_values(block_id, action_id):
        selected = values.get(block_id, {}).get(action_id, {}).get("selected_options", [])
        return [option.get("value", "") for option in selected if option.get("value")]

    excluded_sites = ",".join(selected_values("blk_excluded_sites", "input_excluded_sites"))
    excluded_keywords = clean_text(
        values.get("blk_excluded_keywords", {}).get("input_excluded_keywords", {}).get("value", "")
    )
    notifications = set(selected_values("blk_notifications", "input_notifications"))
    return {
        "excluded_sites": excluded_sites,
        "excluded_keywords": excluded_keywords,
        "employment_types": ",".join(selected_values("blk_employment_types", "input_employment_types")),
        "include_imminent_deadlines": "true" if "include_imminent_deadlines" in notifications else "false",
        "career_types": ",".join(selected_values("blk_career_types", "input_career_types")),
        "career_periods": ",".join(selected_values("blk_career_periods", "input_career_periods")),
        "push_enabled": "true" if "push_enabled" in notifications else "false",
    }


def launch_pipeline():
    subprocess.Popen(
        [sys.executable, "-X", "utf8", str(PIPELINE_SCRIPT)],
        cwd=str(BASE_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.post("/slack/interactive")
async def handle_slack_interactive(payload: str = Form(...)):
    data = json.loads(payload)
    interaction_type = data.get("type", "")
    user_id = data.get("user", {}).get("id", "")

    if interaction_type == "view_submission" and data.get("view", {}).get("callback_id") == "modal_user_profile":
        save_user_profile(user_id, parse_modal_submission(data))
        return {"response_action": "clear"}

    if interaction_type == "view_submission" and data.get("view", {}).get("callback_id") == "modal_search_preferences":
        save_user_preferences(user_id, parse_preferences_submission(data))
        return {"response_action": "clear"}

    actions = data.get("actions", [])
    if not actions:
        return {"ok": True}

    action_id = actions[0].get("action_id", "")
    trigger_id = data.get("trigger_id", "")
    response_url = data.get("response_url", "")

    if action_id == "btn_live_update":
        post_ephemeral(
            response_url,
            {
                "text": f"<@{user_id}>님, 최신 채용공고를 수집하고 분석하는 중입니다. 완료되면 최신순 카드 목록으로 나에게만 표시됩니다.",
            },
        )
        if response_url:
            threading.Thread(target=run_pipeline_and_post_table, args=(response_url, user_id), daemon=True).start()
        else:
            launch_pipeline()

    elif action_id == "btn_custom_search":
        user_profile = get_user_profile(user_id)
        if not user_profile:
            ok, message = open_slack_modal(trigger_id)
            if not ok:
                post_response(
                    response_url,
                    {
                        "text": f"개인정보 입력 모달을 열 수 없습니다. 서버 환경변수 SLACK_BOT_TOKEN을 확인해 주세요. ({message})",
                        "replace_original": False,
                    },
                )
        else:
            post_response(
                response_url,
                {
                    "response_type": "ephemeral",
                    "replace_original": False,
                    "blocks": build_custom_job_blocks(user_id, user_profile),
                },
            )

    elif action_id == "btn_next_recommendation":
        user_profile = get_user_profile(user_id)
        if not user_profile:
            ok, message = open_slack_modal(trigger_id)
            if not ok:
                post_ephemeral(response_url, {"text": f"개인정보 입력 모달을 열 수 없습니다. ({message})"})
        else:
            try:
                next_index = int(actions[0].get("value", "0"))
            except ValueError:
                next_index = 0
            post_response(
                response_url,
                {
                    "response_type": "ephemeral",
                    "replace_original": True,
                    "blocks": build_custom_job_blocks(user_id, user_profile, start_index=next_index),
                },
            )

    elif action_id == "btn_user_profile":
        user_profile = get_user_profile(user_id) or {}
        ok, message = open_slack_modal(trigger_id, user_profile)
        if not ok:
            post_response(
                response_url,
                {
                    "text": f"개인정보 설정 모달을 열 수 없습니다. 서버 환경변수 SLACK_BOT_TOKEN을 확인해 주세요. ({message})",
                    "replace_original": False,
                },
            )

    elif action_id == "btn_delete_profile":
        delete_user_profile(user_id)
        view_id = data.get("view", {}).get("id", "")
        if view_id:
            ok, message = update_slack_modal(view_id, build_profile_deleted_modal())
            if not ok:
                post_ephemeral(response_url, {"text": f"개인정보는 삭제되었지만 모달 화면 갱신에 실패했습니다. ({message})"})
        elif response_url:
            post_ephemeral(response_url, {"text": "저장된 개인정보가 삭제되었습니다."})

    elif action_id == "btn_search_preferences":
        ok, message = open_search_preferences_modal(trigger_id, get_user_preferences(user_id))
        if not ok:
            post_ephemeral(
                response_url,
                {
                    "text": f"환경설정 모달을 열 수 없습니다. 서버 환경변수 SLACK_BOT_TOKEN을 확인해 주세요. ({message})",
                },
            )

    return {"ok": True}


@app.post("/slack/command")
async def handle_slack_command(
    command: str = Form(""),
    text: str = Form(""),
    user_id: str = Form(""),
    user_name: str = Form(""),
    trigger_id: str = Form(""),
    response_url: str = Form(""),
):
    normalized_text = clean_text(text).lower()

    if not normalized_text or command_matches(normalized_text, "시작", "메뉴", "help", "도움"):
        return {
            "response_type": "ephemeral",
            "text": "스마트 AI 맞춤형 채용 비서 서비스",
            "blocks": build_launcher_blocks(user_id, user_name),
        }

    if command_matches(normalized_text, "업데이트", "수집", "최신", "update", "live"):
        if response_url:
            threading.Thread(target=run_pipeline_and_post_table, args=(response_url, user_id), daemon=True).start()
        else:
            launch_pipeline()
        return {
            "response_type": "ephemeral",
            "text": f"<@{user_id}>님, 최신 채용공고를 수집하고 분석하는 중입니다. 완료되면 최신순 카드 목록으로 나에게만 표시됩니다.",
        }

    if command_matches(normalized_text, "검색", "추천", "맞춤", "search", "recommend"):
        user_profile = get_user_profile(user_id)
        if not user_profile:
            ok, message = open_slack_modal(trigger_id)
            if ok:
                return {
                    "response_type": "ephemeral",
                    "text": "맞춤 채용공고 추천을 위해 개인정보 입력 모달을 열었습니다.",
                }
            return {
                "response_type": "ephemeral",
                "text": f"개인정보 입력 모달을 열 수 없습니다. 서버 환경변수 SLACK_BOT_TOKEN을 확인해 주세요. ({message})",
            }
        return {
            "response_type": "ephemeral",
            "text": "AI 맞춤 채용공고 추천",
            "blocks": build_custom_job_blocks(user_id, user_profile),
        }

    if command_matches(normalized_text, "프로필", "개인정보", "profile"):
        ok, message = open_slack_modal(trigger_id, get_user_profile(user_id) or {})
        return {
            "response_type": "ephemeral",
            "text": "개인정보 설정 모달을 열었습니다." if ok else f"개인정보 설정 모달을 열 수 없습니다. ({message})",
        }

    if command_matches(normalized_text, "설정", "환경", "알림", "필터", "preference", "setting"):
        ok, message = open_search_preferences_modal(trigger_id, get_user_preferences(user_id))
        return {
            "response_type": "ephemeral",
            "text": "검색 환경설정 모달을 열었습니다." if ok else f"환경설정 모달을 열 수 없습니다. ({message})",
        }

    return {
        "response_type": "ephemeral",
        "text": slash_help_text(command),
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*사용 가능한 명령어*\n{slash_help_text(command)}"},
            }
        ],
    }


@app.get("/slack/launcher-blocks")
async def slack_launcher_blocks():
    return {"blocks": build_launcher_blocks()}


@app.get("/health")
async def health():
    return {"ok": True, "profile_db": str(PROFILE_DB_PATH)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("slack_interactive_app:app", host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), reload=False)
