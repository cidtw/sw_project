#!/usr/bin/env python3
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus

import requests
from fastapi import FastAPI, Form

from chatbot_search import hard_match_score, load_candidates, run_search, profile_to_search_profile
from common import BASE_DIR, DATA_DIR, DB_PATH, normalized_job_key
from scorer import clean_text, normalize_schema_payload

pipeline_lock = threading.Lock()


SLACK_API_BASE = "https://slack.com/api"
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
PROFILE_DB_PATH = DATA_DIR / "slack_user_profiles.db"
PIPELINE_SCRIPT = BASE_DIR / "pipeline.py"
LAUNCHER_BLOCKS_PATH = BASE_DIR / "slack-launcher-blocks.json"
SUPPORTED_SITES = ["JobKorea", "Saramin", "Incruit"]
DEFAULT_EMPLOYMENT_TYPES = "정규직,계약직,인턴,기타"
DEFAULT_CAREER_TYPES = "신입,경력 무관,경력직"
CAREER_PERIODS = ["3년 미만", "3년 ~ 5년", "5년 ~ 8년", "8년 ~ 11년", "11년 이상"]
SEARCH_PAGE_SIZE = 10
MAX_SEARCH_RESULTS = 200
VALID_SEARCH_TARGETS = {"all", "both", "title", "company"}
SCRAP_SOURCE_LABELS = {
    "crawled": "크롤된 공고",
    "recommended": "맞춤 공고",
    "search": "검색한 공고",
}
SCRAP_SELECTION_LIMIT = 10

app = FastAPI(title="Recruiting Slack Interactive App")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def init_profile_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_job_selection_cache (
                user_id TEXT,
                source TEXT,
                job_key TEXT,
                rank INTEGER,
                payload_json TEXT,
                created_at TEXT,
                PRIMARY KEY (user_id, source, job_key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_job_scraps (
                user_id TEXT,
                job_key TEXT,
                source TEXT,
                platform TEXT,
                company TEXT,
                title TEXT,
                employment_type TEXT,
                location TEXT,
                salary TEXT,
                deadline TEXT,
                detail_url TEXT,
                image_url TEXT,
                payload_json TEXT,
                created_at TEXT,
                PRIMARY KEY (user_id, job_key)
            )
            """
        )
        conn.commit()


def get_user_profile(user_id):
    init_profile_db()
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
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


def build_job_search_modal(channel_id, initial_query=""):
    search_target_options = [
        plain_option("전체 텍스트", "all"),
        plain_option("제목 + 회사명", "both"),
        plain_option("공고 제목만", "title"),
        plain_option("회사명만", "company"),
    ]
    query_element = {
        "type": "plain_text_input",
        "action_id": "input_search_query",
        "placeholder": {"type": "plain_text", "text": "예시) 삼성, AI 개발자, 백엔드"},
        "multiline": False,
        "min_length": 1,
        "max_length": 80,
    }
    initial_query = clean_text(initial_query)
    if initial_query:
        query_element["initial_value"] = initial_query[:80]

    blocks = [
        {
            "type": "input",
            "block_id": "blk_search_query",
            "label": {"type": "plain_text", "text": "검색어"},
            "element": query_element,
        },
        {
            "type": "input",
            "block_id": "blk_search_target",
            "label": {"type": "plain_text", "text": "검색 기준"},
            "element": {
                "type": "radio_buttons",
                "action_id": "input_search_target",
                "initial_option": search_target_options[0],
                "options": search_target_options,
            },
        },
        {
            "type": "input",
            "optional": True,
            "block_id": "blk_search_include_closed",
            "label": {"type": "plain_text", "text": "마감 공고 포함"},
            "element": {
                "type": "checkboxes",
                "action_id": "input_search_include_closed",
                "options": [
                    plain_option("마감된 공고도 포함하여 검색", "include_closed"),
                ],
            },
        },
    ]
    return {
        "type": "modal",
        "callback_id": "modal_job_search",
        "title": {"type": "plain_text", "text": "공고 검색"},
        "submit": {"type": "plain_text", "text": "검색하기"},
        "close": {"type": "plain_text", "text": "취소"},
        "private_metadata": channel_id,
        "blocks": blocks,
    }


def tokenize_search_query(query):
    return [
        token.lower()
        for token in re.split(r"[\s,/#]+", clean_text(query))
        if token.strip()
    ]


def escape_like_term(term):
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def job_row_to_candidate(row):
    deep_data = parse_json_field(row["deep_scraped_json"])
    extracted = parse_json_field(row["extracted_info_json"])
    return {
        "dispatch_type": "SEARCH",
        "platform": clean_text(row["platform"]),
        "company": clean_text(row["company"]),
        "title": clean_text(row["title"]),
        "employment_type": deep_data.get("employment_type") or extracted.get("employment_type") or "확인 필요",
        "location": deep_data.get("location") or extracted.get("location") or "확인 필요",
        "salary": deep_data.get("salary") or extracted.get("salary") or "확인 필요",
        "requirements": extracted.get("requirements") or deep_data.get("requirements") or "",
        "preferences": extracted.get("preferences") or deep_data.get("preferences") or "",
        "jd_summary": extracted.get("jd_summary") or deep_data.get("jd_summary") or "",
        "job_keywords": extracted.get("job_keywords") or [],
        "detail_url": clean_text(row["detail_url"]),
        "company_career_url": extracted.get("company_career_url") or "",
        "image_url": clean_text(row["image_url"]),
        "deadline": clean_text(row["deadline"]),
        "scraped_at": row["scraped_at"],
    }


def job_search_text(item, target="all"):
    keywords = item.get("job_keywords", []) or []
    if not isinstance(keywords, list):
        keywords = [keywords]
    if target == "title":
        values = [item.get("title", "")]
    elif target == "company":
        values = [item.get("company", "")]
    elif target == "both":
        values = [item.get("title", ""), item.get("company", "")]
    else:
        values = [
            item.get("company", ""),
            item.get("title", ""),
            item.get("platform", ""),
            item.get("employment_type", ""),
            item.get("location", ""),
            item.get("salary", ""),
            item.get("requirements", ""),
            item.get("preferences", ""),
            item.get("jd_summary", ""),
            " ".join(clean_text(keyword) for keyword in keywords),
        ]
    return clean_text(" ".join(str(value or "") for value in values)).lower()


def search_relevance_score(item, terms):
    title = clean_text(item.get("title", "")).lower()
    company = clean_text(item.get("company", "")).lower()
    full_text = job_search_text(item, "all")
    score = 0
    for term in terms:
        if term in title:
            score += 40
        if term in company:
            score += 35
        if term in full_text:
            score += 10
    return score


def google_job_search_url(query):
    text = clean_text(query)
    google_query = (
        f"{text} 채용 "
        "(site:jobkorea.co.kr OR site:saramin.co.kr OR site:incruit.com)"
    )
    return f"https://www.google.com/search?q={quote_plus(google_query)}"


def job_key_for_payload(payload):
    detail_url = clean_text(payload.get("detail_url", ""))
    if detail_url:
        raw_key = detail_url
    else:
        raw_key = normalized_job_key(payload.get("company", ""), payload.get("title", ""))
    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:24]


def normalize_job_for_storage(item, user_id=""):
    payload = normalize_schema_payload(dict(item), "SEARCH", user_id)
    payload["platform"] = detect_platform(item)
    payload["deadline"] = clean_text(item.get("deadline", payload.get("deadline", "")))
    payload["scraped_at"] = clean_text(item.get("scraped_at", payload.get("scraped_at", "")))
    return payload


def cache_selection_jobs(user_id, source, jobs, limit=None):
    init_profile_db()
    now = now_utc()
    selected_jobs = jobs[:limit] if limit else jobs[:MAX_SEARCH_RESULTS]
    normalized_jobs = [normalize_job_for_storage(job, user_id) for job in selected_jobs]
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
        conn.execute("DELETE FROM user_job_selection_cache WHERE user_id = ? AND source = ?", (user_id, source))
        for rank, payload in enumerate(normalized_jobs, start=1):
            job_key = job_key_for_payload(payload)
            payload["_job_key"] = job_key
            conn.execute(
                """
                INSERT OR REPLACE INTO user_job_selection_cache (
                    user_id, source, job_key, rank, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, source, job_key, rank, json.dumps(payload, ensure_ascii=False), now),
            )
        conn.commit()
    return normalized_jobs


def scrap_button_value(source, payload):
    return json.dumps(
        {"s": source, "k": payload.get("_job_key") or job_key_for_payload(payload)},
        ensure_ascii=False,
    )


def build_single_scrap_button(payload, source):
    return {
        "type": "button",
        "text": {"type": "plain_text", "text": "스크랩"},
        "action_id": "btn_scrap_single",
        "value": scrap_button_value(source, payload),
    }


def load_selection_cache(user_id, source):
    init_profile_db()
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT job_key, payload_json
            FROM user_job_selection_cache
            WHERE user_id = ? AND source = ?
            ORDER BY rank ASC
            """,
            (user_id, source),
        ).fetchall()
    jobs = []
    for row in rows:
        payload = parse_json_field(row["payload_json"])
        if payload:
            payload["_job_key"] = row["job_key"]
            jobs.append(payload)
    return jobs


def save_scrap_jobs(user_id, source, job_keys):
    init_profile_db()
    selected_keys = set(job_keys)
    if not selected_keys:
        return []
    cached_jobs = load_selection_cache(user_id, source)
    saved_jobs = []
    now = now_utc()
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
        for payload in cached_jobs:
            job_key = payload.get("_job_key") or job_key_for_payload(payload)
            if job_key not in selected_keys:
                continue
            payload = dict(payload)
            payload.pop("_job_key", None)
            conn.execute(
                """
                INSERT OR REPLACE INTO user_job_scraps (
                    user_id, job_key, source, platform, company, title, employment_type,
                    location, salary, deadline, detail_url, image_url, payload_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    job_key,
                    source,
                    payload.get("platform", ""),
                    payload.get("company", ""),
                    payload.get("title", ""),
                    payload.get("employment_type", ""),
                    payload.get("location", ""),
                    payload.get("salary", ""),
                    payload.get("deadline", ""),
                    payload.get("detail_url", ""),
                    payload.get("image_url", ""),
                    json.dumps(payload, ensure_ascii=False),
                    now,
                ),
            )
            saved_jobs.append(payload)
        conn.commit()
    return saved_jobs


def load_scrapped_jobs(user_id, limit=30):
    init_profile_db()
    with sqlite3.connect(PROFILE_DB_PATH, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT source, payload_json, created_at
            FROM user_job_scraps
            WHERE user_id = ?
            ORDER BY datetime(created_at) DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    jobs = []
    for row in rows:
        payload = parse_json_field(row["payload_json"])
        if payload:
            payload["_scrap_source"] = row["source"]
            payload["_scrapped_at"] = row["created_at"]
            jobs.append(payload)
    return jobs


def search_jobs_in_db(query, target="all", include_closed=False):
    target = target if target in VALID_SEARCH_TARGETS else "all"
    query_terms = tokenize_search_query(query)
    if not query_terms:
        return []
    if not DB_PATH.exists():
        return []

    where_parts = []
    params = []
    for term in query_terms:
        like_value = f"%{escape_like_term(term)}%"
        if target == "title":
            where_parts.append("LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\'")
            params.append(like_value)
        elif target == "company":
            where_parts.append("LOWER(COALESCE(company, '')) LIKE ? ESCAPE '\\'")
            params.append(like_value)
        elif target == "both":
            where_parts.append(
                "(LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\' OR LOWER(COALESCE(company, '')) LIKE ? ESCAPE '\\')"
            )
            params.extend([like_value, like_value])
        else:
            where_parts.append(
                """
                (
                    LOWER(COALESCE(title, '')) LIKE ? ESCAPE '\\'
                    OR LOWER(COALESCE(company, '')) LIKE ? ESCAPE '\\'
                    OR LOWER(COALESCE(platform, '')) LIKE ? ESCAPE '\\'
                    OR LOWER(COALESCE(deadline, '')) LIKE ? ESCAPE '\\'
                    OR LOWER(COALESCE(deep_scraped_json, '')) LIKE ? ESCAPE '\\'
                    OR LOWER(COALESCE(extracted_info_json, '')) LIKE ? ESCAPE '\\'
                )
                """
            )
            params.extend([like_value] * 6)
    where_sql = " AND ".join(where_parts)

    with sqlite3.connect(DB_PATH, timeout=15.0) as conn:
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT platform, company, title, detail_url, deadline, image_url,
                       deep_scraped_json, extracted_info_json, scraped_at
                FROM jobs
                WHERE {where_sql}
                ORDER BY datetime(scraped_at) DESC, rowid DESC
                LIMIT ?
                """,
                (*params, MAX_SEARCH_RESULTS * 2),
            ).fetchall()
        except sqlite3.Error:
            return []

    filtered = []
    for row in rows:
        item = job_row_to_candidate(row)
        if not include_closed and is_closed_deadline(item.get("deadline", "")):
            continue
        searchable_text = job_search_text(item, target)
        if not all(term in searchable_text for term in query_terms):
            continue
        item["_search_score"] = search_relevance_score(item, query_terms)
        filtered.append(item)

    filtered.sort(key=lambda item: (item.get("_search_score", 0), clean_text(item.get("scraped_at", ""))), reverse=True)
    return filtered[:MAX_SEARCH_RESULTS]


def build_search_result_blocks(query, target, include_closed, candidates, page, channel_id):
    target = target if target in VALID_SEARCH_TARGETS else "all"
    page = max(0, int(page or 0))
    limit = SEARCH_PAGE_SIZE
    total_items = len(candidates)
    total_pages = (total_items + limit - 1) // limit if total_items > 0 else 0
    if total_pages:
        page = min(page, total_pages - 1)
    
    headline = f"🔍 공고 검색 결과 ({page + 1}/{total_pages} 페이지)" if total_pages > 0 else "🔍 공고 검색 결과"
    target_labels = {
        "all": "전체 텍스트",
        "both": "제목+회사명",
        "title": "공고 제목",
        "company": "회사명",
    }
    target_str = target_labels.get(target, "전체 텍스트")
    closed_str = "포함" if include_closed else "제외"
    
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": headline[:150]},
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"검색어: *'{clean_text(query)}'* (기준: *{target_str}* | 마감: *{closed_str}*) · 총 *{total_items}*건",
                }
            ],
        },
        {"type": "divider"},
    ]
    
    if not candidates:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*'{clean_text(query)}'* 에 해당하는 검색 결과가 없습니다. 검색 조건을 변경하거나 다시 검색해 주세요."},
            }
        )
    else:
        start_idx = page * limit
        end_idx = min(start_idx + limit, total_items)
        
        for idx, item in enumerate(candidates[start_idx:end_idx], start=start_idx + 1):
            payload = normalize_schema_payload(item, "SEARCH", "")
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
                "accessory": build_single_scrap_button(item, "search"),
            }
        )
            
    blocks.append({"type": "divider"})
    
    action_elements = []
    if total_pages > 1:
        if page > 0:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "◀ 이전"},
                "action_id": "btn_search_page_prev",
                "value": json.dumps({"q": query, "t": target, "ic": include_closed, "p": page - 1, "c": channel_id}, ensure_ascii=False)
            })
        if page < total_pages - 1:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "다음 ▶"},
                "action_id": "btn_search_page_next",
                "value": json.dumps({"q": query, "t": target, "ic": include_closed, "p": page + 1, "c": channel_id}, ensure_ascii=False)
            })
            
    action_elements.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "🔎 다시 검색"},
        "action_id": "btn_search_jobs_again",
        "value": channel_id
    })
    action_elements.append({
        "type": "button",
        "text": {"type": "plain_text", "text": "Google에서 검색"},
        "url": google_job_search_url(query),
        "action_id": "btn_google_job_search",
    })
    
    blocks.append({
        "type": "actions",
        "elements": action_elements
    })
    
    return blocks


def short_option_text(payload, index):
    company = clean_text(payload.get("company", "")) or "회사명 확인 필요"
    title = clean_text(payload.get("title", "")) or "공고명 확인 필요"
    text = f"{index}. {company} - {title}"
    return text[:75]


def build_scrap_menu_blocks(user_id=""):
    mention = f"<@{user_id}>님, " if user_id else ""
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "공고 스크랩"},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"{mention}스크랩할 공고 소스를 선택하거나 저장된 스크랩을 확인하세요.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "크롤된 공고 중에서 선택"},
                    "action_id": "btn_scrap_source_crawled",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "맞춤 공고 중에서 선택"},
                    "action_id": "btn_scrap_source_recommended",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "검색한 공고 중에서 선택"},
                    "action_id": "btn_scrap_source_search",
                },
            ],
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "스크랩된 공고 보기"},
                    "action_id": "btn_scrap_view",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "초기 메뉴"},
                    "action_id": "btn_show_launcher",
                },
            ],
        },
    ]


def build_scrap_select_modal(source, jobs, channel_id=""):
    source_label = SCRAP_SOURCE_LABELS.get(source, "공고")
    if not jobs:
        return {
            "type": "modal",
            "callback_id": "modal_scrap_empty",
            "title": {"type": "plain_text", "text": "공고 스크랩"},
            "close": {"type": "plain_text", "text": "닫기"},
            "blocks": [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{source_label}* 후보가 없습니다. 먼저 공고를 검색하거나 최신 업데이트를 실행해 주세요.",
                    },
                }
            ],
        }

    options = [
        {
            "text": {"type": "plain_text", "text": short_option_text(payload, index)},
            "value": payload.get("_job_key") or job_key_for_payload(payload),
        }
        for index, payload in enumerate(jobs[:SCRAP_SELECTION_LIMIT], start=1)
    ]
    return {
        "type": "modal",
        "callback_id": "modal_scrap_select",
        "title": {"type": "plain_text", "text": "공고 스크랩"},
        "submit": {"type": "plain_text", "text": "스크랩 저장"},
        "close": {"type": "plain_text", "text": "취소"},
        "private_metadata": json.dumps({"source": source, "channel_id": channel_id}, ensure_ascii=False),
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"*{source_label}* 중 저장할 공고를 선택하세요."},
            },
            {
                "type": "input",
                "block_id": "blk_scrap_jobs",
                "label": {"type": "plain_text", "text": "스크랩할 공고"},
                "element": {
                    "type": "checkboxes",
                    "action_id": "input_scrap_jobs",
                    "options": options,
                },
            },
        ],
    }


def build_scrap_saved_blocks(saved_jobs):
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "스크랩 저장 완료"},
        }
    ]
    if not saved_jobs:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "선택된 공고가 없습니다."}})
    else:
        for idx, payload in enumerate(saved_jobs[:10], start=1):
            title_text = payload.get("title", "")
            if payload.get("detail_url"):
                title_text = f"<{payload['detail_url']}|{payload.get('title', '공고 보기')}>"
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{idx}. {payload.get('company', '')}*\n{title_text}\n지역: {payload.get('location', '')} | 마감: {payload.get('deadline', '')}",
                    },
                }
            )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "style": "primary",
                    "text": {"type": "plain_text", "text": "스크랩된 공고 보기"},
                    "action_id": "btn_scrap_view",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "초기 메뉴"},
                    "action_id": "btn_show_launcher",
                },
            ],
        }
    )
    return blocks


def build_scrapped_jobs_blocks(user_id):
    jobs = load_scrapped_jobs(user_id)
    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "스크랩된 공고"},
        },
        {"type": "divider"},
    ]
    if not jobs:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "아직 스크랩한 공고가 없습니다."}})
    else:
        for idx, payload in enumerate(jobs[:30], start=1):
            source = SCRAP_SOURCE_LABELS.get(payload.get("_scrap_source", ""), payload.get("_scrap_source", ""))
            title_text = payload.get("title", "")
            if payload.get("detail_url"):
                title_text = f"<{payload['detail_url']}|{payload.get('title', '공고 보기')}>"
            blocks.append(
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{idx}. {payload.get('company', '')}* · `{source}`\n"
                            f"{title_text}\n"
                            f"지역: {payload.get('location', '')} | 마감: {payload.get('deadline', '')}"
                        ),
                    },
                }
            )
    blocks.append(
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "스크랩 메뉴로 돌아가기"},
                    "action_id": "btn_scrap_jobs",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "초기 메뉴"},
                    "action_id": "btn_show_launcher",
                },
            ],
        }
    )
    return blocks


def call_slack_api(method, payload):
    if not SLACK_BOT_TOKEN:
        return False, "SLACK_BOT_TOKEN is not set"

    try:
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
        if not body.get("ok"):
            print(f"[SLACK API] {method} failed: status={response.status_code}, error={body.get('error')}")
        return bool(body.get("ok")), json.dumps(body, ensure_ascii=False)
    except Exception as e:
        print(f"[SLACK API] {method} request failed: {e}")
        return False, str(e)


def open_slack_modal(trigger_id, existing_data=None):
    return call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_user_profile_modal(existing_data)})


def open_search_preferences_modal(trigger_id, existing_data=None):
    return call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_search_preferences_modal(existing_data)})


def update_slack_modal(view_id, view):
    return call_slack_api("views.update", {"view_id": view_id, "view": view})


def post_response(response_url, payload):
    if not response_url:
        return
    try:
        response = requests.post(response_url, json=payload, timeout=10)
        if response.status_code >= 400:
            print(f"[SLACK RESPONSE_URL] failed: status={response.status_code}, body={response.text[:200]}")
    except Exception as e:
        print(f"[SLACK RESPONSE_URL] request failed: {e}")


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
        f"`{command} 공고검색` : 일반 채용공고 키워드 검색\n"
        f"`{command} 스크랩` : 스크랩된 공고 보기\n"
        f"`{command} 프로필` : 개인정보 입력/수정\n"
        f"`{command} 설정` : 검색/알림 환경설정"
    )


def command_matches(text, *keywords):
    normalized = clean_text(text).lower()
    return any(keyword in normalized for keyword in keywords)


def extract_job_search_query(text):
    cleaned = clean_text(text)
    for keyword in ["공고검색", "공고 검색", "찾기", "find", "search_jobs"]:
        if cleaned.lower().startswith(keyword.lower()):
            return clean_text(cleaned[len(keyword):])
    return ""


def build_job_blocks(
    payload,
    headline="맞춤 채용공고 추천",
    prev_index=None,
    next_index=None,
    user_id="",
    scrap_source="recommended",
):
    payload = normalize_schema_payload(payload, payload.get("dispatch_type", "SEARCH"), payload.get("slack_user_id", ""))
    if user_id:
        cached = cache_selection_jobs(user_id, scrap_source, [payload], 1)
        if cached:
            payload = cached[0]
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
    if user_id:
        action_elements.append(build_single_scrap_button(payload, scrap_source))
    if prev_index is not None:
        action_elements.append(
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "이전 추천"},
                "action_id": "btn_prev_recommendation",
                "value": str(prev_index),
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
    with sqlite3.connect(DB_PATH, timeout=15.0) as conn:
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

    candidates = [job_row_to_candidate(row) for row in rows]
    filtered = filter_candidates_by_preferences(candidates, preferences)
    return filtered[:limit]


def cell(value, width):
    text = clean_text(value).replace("|", "/")
    if len(text) > width:
        text = text[: max(0, width - 1)] + "…"
    return text.ljust(width)


def build_job_table_blocks(candidates, user_id="", title="채용공고 업데이트", limit=10):
    candidates = candidates[:limit]
    if user_id and candidates:
        candidates = cache_selection_jobs(user_id, "crawled", candidates, limit)
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

    for idx, item in enumerate(candidates, start=1):
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
                **({"accessory": build_single_scrap_button(item, "crawled")} if user_id else {}),
            }
            )
    return blocks


def post_ephemeral(response_url, payload):
    payload = payload.copy()
    payload.setdefault("response_type", "ephemeral")
    payload.setdefault("replace_original", False)
    post_response(response_url, payload)


def is_ephemeral_interaction(data):
    container = data.get("container", {}) or {}
    message = data.get("message", {}) or {}
    return bool(container.get("is_ephemeral") or message.get("is_ephemeral"))


def post_ephemeral_navigation(response_url, payload, data):
    payload = payload.copy()
    payload.setdefault("response_type", "ephemeral")
    payload.setdefault("replace_original", is_ephemeral_interaction(data))
    post_response(response_url, payload)


def build_scrap_ack_blocks(data, saved_jobs):
    blocks = data.get("message", {}).get("blocks") or []
    if not blocks:
        return []
    blocks = [block for block in blocks if not str(block.get("block_id", "")).startswith("scrap_ack_")]
    first_job = saved_jobs[0] if saved_jobs else {}
    company = clean_text(first_job.get("company", ""))
    title = clean_text(first_job.get("title", ""))
    label = f"{company} · {title}".strip(" ·") or "선택한 공고"
    blocks.append(
        {
            "type": "context",
            "block_id": f"scrap_ack_{now_utc().replace(':', '').replace('-', '').replace('.', '')[:30]}",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"저장 완료: *{label[:80]}*",
                }
            ],
        }
    )
    return blocks


def run_pipeline_and_post_table(response_url, user_id):
    if not pipeline_lock.acquire(blocking=False):
        post_ephemeral(
            response_url,
            {
                "text": "현재 다른 사용자의 요청 또는 백그라운드에서 채용 파이프라인 수집/분석 작업이 실행 중입니다. 완료될 때까지 기다려 주세요."
            }
        )
        return
    try:
        log_file = DATA_DIR / "pipeline_run.log"
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(f"\n--- Pipeline Run Started at {datetime.now()} ---\n")
            f.flush()
            subprocess.run(
                [sys.executable, "-u", "-X", "utf8", str(PIPELINE_SCRIPT)],
                cwd=str(BASE_DIR),
                stdout=f,
                stderr=subprocess.STDOUT,
                check=False
            )
    except Exception as e:
        print(f"[ERROR] Failed to run pipeline: {e}")
    finally:
        pipeline_lock.release()
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
    ranked = get_recommended_jobs(user_id, profile, limit=50)
    if ranked:
        selected_index = start_index % len(ranked)
        best = normalize_schema_payload(ranked[selected_index], "SEARCH", user_id)
        prev_index = (selected_index - 1) % len(ranked) if len(ranked) > 1 else None
        next_index = (selected_index + 1) % len(ranked) if len(ranked) > 1 else None
        headline = f"AI 맞춤 채용공고 추천 ({selected_index + 1}/{len(ranked)})"
    else:
        request = {
            "slack_user_id": user_id,
            "query": " ".join(profile_to_search_profile(profile).get("근무희망지역", [])),
            "profile": profile_to_search_profile(profile),
        }
        best = run_search(request)
        prev_index = None
        next_index = None
        headline = "AI 맞춤 채용공고 추천"
    return build_job_blocks(
        best,
        headline,
        prev_index=prev_index,
        next_index=next_index,
        user_id=user_id,
        scrap_source="recommended",
    )


def get_recommended_jobs(user_id, profile, limit=10):
    preferences = get_user_preferences(user_id)
    request = {
        "slack_user_id": user_id,
        "query": " ".join(profile_to_search_profile(profile).get("근무희망지역", [])),
        "profile": profile_to_search_profile(profile),
    }
    candidates = []
    seen = set()
    for item in filter_candidates_by_preferences(load_candidates(), preferences):
        item = normalize_job_for_storage(item, user_id)
        key = job_key_for_payload(item)
        if key and key not in seen:
            seen.add(key)
            candidates.append(item)
    for item in load_recent_open_jobs(max(limit * 3, 50), preferences):
        item = normalize_job_for_storage(item, user_id)
        key = job_key_for_payload(item)
        if key and key not in seen:
            seen.add(key)
            candidates.append(item)
    if not candidates:
        return []
    return sorted(candidates, key=lambda item: hard_match_score(item, request["profile"], request["query"]), reverse=True)[:limit]


def get_scrap_source_jobs(user_id, source):
    if source == "crawled":
        return cache_selection_jobs(user_id, source, load_recent_open_jobs(SCRAP_SELECTION_LIMIT, get_user_preferences(user_id)), SCRAP_SELECTION_LIMIT)
    if source == "recommended":
        profile = get_user_profile(user_id)
        if not profile:
            return []
        return cache_selection_jobs(user_id, source, get_recommended_jobs(user_id, profile, SCRAP_SELECTION_LIMIT), SCRAP_SELECTION_LIMIT)
    if source == "search":
        return load_selection_cache(user_id, source)
    return []


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
    if not pipeline_lock.acquire(blocking=False):
        print("[WARNING] Pipeline launch skipped because it is already running.")
        return

    def target():
        try:
            subprocess.run(
                [sys.executable, "-X", "utf8", str(PIPELINE_SCRIPT)],
                cwd=str(BASE_DIR),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            pipeline_lock.release()

    threading.Thread(target=target, daemon=True).start()


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

    if interaction_type == "view_submission" and data.get("view", {}).get("callback_id") == "modal_job_search":
        values = data.get("view", {}).get("state", {}).get("values", {})
        query = clean_text(values.get("blk_search_query", {}).get("input_search_query", {}).get("value", ""))
        
        target_selected = values.get("blk_search_target", {}).get("input_search_target", {}).get("selected_option", {})
        target = target_selected.get("value", "all") if target_selected else "all"
        
        include_closed_selected = values.get("blk_search_include_closed", {}).get("input_search_include_closed", {}).get("selected_options", [])
        include_closed = any(opt.get("value") == "include_closed" for opt in include_closed_selected)
        
        channel_id = data.get("view", {}).get("private_metadata", "") or user_id
        
        results = search_jobs_in_db(query, target, include_closed)
        cache_selection_jobs(user_id, "search", results)
        blocks = build_search_result_blocks(query, target, include_closed, results, 0, channel_id)
        
        call_slack_api("chat.postEphemeral", {
            "channel": channel_id,
            "user": user_id,
            "blocks": blocks,
            "text": "공고 검색 결과"
        })
        return {"response_action": "clear"}

    if interaction_type == "view_submission" and data.get("view", {}).get("callback_id") == "modal_scrap_select":
        values = data.get("view", {}).get("state", {}).get("values", {})
        selected = values.get("blk_scrap_jobs", {}).get("input_scrap_jobs", {}).get("selected_options", [])
        job_keys = [option.get("value", "") for option in selected if option.get("value")]
        try:
            metadata = json.loads(data.get("view", {}).get("private_metadata", "{}"))
        except json.JSONDecodeError:
            metadata = {}
        source = metadata.get("source", "")
        saved_jobs = save_scrap_jobs(user_id, source, job_keys)
        channel_id = metadata.get("channel_id") or user_id
        call_slack_api(
            "chat.postEphemeral",
            {
                "channel": channel_id,
                "user": user_id,
                "blocks": build_scrap_saved_blocks(saved_jobs),
                "text": "스크랩 저장 완료",
            },
        )
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
            post_ephemeral_navigation(
                response_url,
                {
                    "blocks": build_custom_job_blocks(user_id, user_profile),
                },
                data,
            )

    elif action_id in ("btn_next_recommendation", "btn_prev_recommendation"):
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

    elif action_id == "btn_scrap_single":
        try:
            value = json.loads(actions[0].get("value", "{}"))
        except json.JSONDecodeError:
            value = {}
        source = value.get("s", "")
        job_key = value.get("k", "")
        saved_jobs = save_scrap_jobs(user_id, source, [job_key])
        if saved_jobs:
            ack_blocks = build_scrap_ack_blocks(data, saved_jobs)
            if ack_blocks and is_ephemeral_interaction(data):
                post_response(
                    response_url,
                    {
                        "replace_original": True,
                        "text": "스크랩 저장 완료",
                        "blocks": ack_blocks,
                    },
                )
        else:
            post_ephemeral(
                response_url,
                {
                    "text": "스크랩 후보 정보를 찾을 수 없습니다. 결과를 다시 조회한 뒤 스크랩해 주세요.",
                },
            )

    elif action_id in ("btn_scrap_jobs", "btn_scrap_menu"):
        post_ephemeral_navigation(
            response_url,
            {
                "text": "공고 스크랩",
                "blocks": build_scrap_menu_blocks(user_id),
            },
            data,
        )

    elif action_id == "btn_show_launcher":
        post_ephemeral_navigation(
            response_url,
            {
                "text": "스마트 AI 맞춤형 채용 비서 서비스",
                "blocks": build_launcher_blocks(user_id),
            },
            data,
        )

    elif action_id in ("btn_scrap_source_crawled", "btn_scrap_source_recommended", "btn_scrap_source_search"):
        source = {
            "btn_scrap_source_crawled": "crawled",
            "btn_scrap_source_recommended": "recommended",
            "btn_scrap_source_search": "search",
        }[action_id]
        channel_id = data.get("channel", {}).get("id") or data.get("container", {}).get("channel_id", "") or user_id
        jobs = get_scrap_source_jobs(user_id, source)
        ok, message = call_slack_api(
            "views.open",
            {"trigger_id": trigger_id, "view": build_scrap_select_modal(source, jobs, channel_id)},
        )
        if not ok:
            post_ephemeral(response_url, {"text": f"스크랩 선택 모달을 열 수 없습니다. ({message})"})

    elif action_id == "btn_scrap_view":
        post_ephemeral_navigation(
            response_url,
            {
                "text": "스크랩된 공고",
                "blocks": build_scrapped_jobs_blocks(user_id),
            },
            data,
        )

    elif action_id == "btn_search_jobs":
        channel_id = data.get("channel", {}).get("id") or data.get("container", {}).get("channel_id", "") or user_id
        ok, message = call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_job_search_modal(channel_id)})
        if not ok:
            post_ephemeral(response_url, {"text": f"공고 검색 모달을 열 수 없습니다. ({message})"})

    elif action_id in ("btn_search_page_next", "btn_search_page_prev"):
        try:
            btn_val = json.loads(actions[0].get("value", "{}"))
            query = btn_val.get("q", "")
            target = btn_val.get("t", "all")
            include_closed = btn_val.get("ic", False)
            page = btn_val.get("p", 0)
            channel_id = btn_val.get("c", "") or user_id
            
            results = search_jobs_in_db(query, target, include_closed)
            blocks = build_search_result_blocks(query, target, include_closed, results, page, channel_id)
            post_response(response_url, {
                "replace_original": True,
                "blocks": blocks,
                "text": "공고 검색 결과"
            })
        except Exception as e:
            print(f"[ERROR] Failed to handle page navigation: {e}")

    elif action_id == "btn_search_jobs_again":
        channel_id = actions[0].get("value", "") or user_id
        ok, message = call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_job_search_modal(channel_id)})
        if not ok:
            post_ephemeral(response_url, {"text": f"공고 검색 모달을 열 수 없습니다. ({message})"})

    return {"ok": True}


@app.post("/slack/command")
async def handle_slack_command(
    command: str = Form(""),
    text: str = Form(""),
    user_id: str = Form(""),
    user_name: str = Form(""),
    trigger_id: str = Form(""),
    response_url: str = Form(""),
    channel_id: str = Form(""),
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

    if command_matches(normalized_text, "공고검색", "찾기", "find", "search_jobs"):
        search_query = extract_job_search_query(text)
        if search_query:
            results = search_jobs_in_db(search_query, "all", False)
            cache_selection_jobs(user_id, "search", results)
            return {
                "response_type": "ephemeral",
                "text": "공고 검색 결과",
                "blocks": build_search_result_blocks(search_query, "all", False, results, 0, channel_id or user_id),
            }
        ok, message = call_slack_api("views.open", {"trigger_id": trigger_id, "view": build_job_search_modal(channel_id or user_id)})
        return {
            "response_type": "ephemeral",
            "text": "공고 검색 모달을 열었습니다." if ok else f"공고 검색 모달을 열 수 없습니다. ({message})",
        }

    if command_matches(normalized_text, "스크랩", "scrap", "bookmark"):
        return {
            "response_type": "ephemeral",
            "text": "스크랩된 공고",
            "blocks": build_scrapped_jobs_blocks(user_id),
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
