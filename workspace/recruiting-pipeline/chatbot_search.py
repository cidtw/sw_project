#!/usr/bin/env python3
import argparse
import json
import re
import sys

from common import FINAL_DASHBOARD_PATH, SCORE_OUTPUT_PATH, VERIFY_OUTPUT_PATH, read_json, write_json
from scorer import (
    clean_text,
    dedupe_preserve_order,
    keyword_in_text,
    load_user_profile,
    normalize_schema_payload,
)


def parse_amount_manwon(value):
    text = clean_text(value)
    if not text or "회사내규" in text:
        return 0
    match = re.search(r"(\d{1,3}(?:,\d{3})+|\d{3,5})\s*(?:만원|만\s*원)?", text)
    if not match:
        return 0
    return int(match.group(1).replace(",", ""))


def as_list(value):
    if isinstance(value, list):
        return [clean_text(item) for item in value if clean_text(item)]
    if value:
        return [clean_text(value)]
    return []


def merge_profile(base_profile, request_profile):
    merged = dict(base_profile or {})
    request_profile = request_profile if isinstance(request_profile, dict) else {}
    for key, value in request_profile.items():
        if value not in (None, "", []):
            merged[key] = value
    return merged


def profile_values(profile, *keys):
    values = []
    for key in keys:
        values.extend(as_list(profile.get(key)))
    return dedupe_preserve_order(values)


def load_candidates():
    for path in [FINAL_DASHBOARD_PATH, VERIFY_OUTPUT_PATH, SCORE_OUTPUT_PATH]:
        data = read_json(path, [])
        if isinstance(data, dict):
            data = [data]
        data = [item for item in data if isinstance(item, dict)]
        if data:
            return data
    return []


def build_search_text(item):
    return clean_text(
        " ".join(
            [
                item.get("company", ""),
                item.get("title", ""),
                item.get("employment_type", ""),
                item.get("location", ""),
                item.get("salary", ""),
                item.get("requirements", ""),
                item.get("preferences", ""),
                item.get("jd_summary", ""),
                " ".join(as_list(item.get("job_keywords"))),
            ]
        )
    )


def tokenize_query(query):
    return [
        token
        for token in re.split(r"[\s,/#]+", clean_text(query))
        if len(token) >= 2 and token not in {"채용", "공고", "추천", "검색"}
    ]


def career_signal(value):
    text = clean_text(value)
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return ""
    if "신입" in compact:
        return "new"
    if "1년~3년" in compact or "1년-3년" in compact:
        return "career_1_3"
    if "3년~5년" in compact or "3년-5년" in compact:
        return "career_3_5"
    if "5년이상" in compact or "5년+" in compact:
        return "career_5_plus"
    return "career" if "경력" in compact else ""


def job_career_signal(text):
    compact = re.sub(r"\s+", "", clean_text(text))
    if "경력무관" in compact or "신입/경력" in compact or "신입·경력" in compact:
        return "any"
    if "신입" in compact and "경력" not in compact:
        return "new"
    if re.search(r"경력\s*5\s*년|5\s*년\s*이상", text):
        return "career_5_plus"
    if re.search(r"경력\s*[3-4]\s*년|3\s*년\s*(?:~|-)", text):
        return "career_3_5"
    if re.search(r"경력\s*[1-2]\s*년|1\s*년\s*(?:~|-)", text):
        return "career_1_3"
    if "경력" in compact or "경력직" in compact:
        return "career"
    return ""


def career_match_delta(user_signal, job_signal):
    if not user_signal or not job_signal or job_signal == "any":
        return 0
    if user_signal == job_signal:
        return 35
    if user_signal == "new" and job_signal.startswith("career"):
        return -60
    if user_signal.startswith("career") and job_signal == "new":
        return -45
    if user_signal.startswith("career") and job_signal == "career":
        return 15
    career_order = {"career_1_3": 1, "career_3_5": 2, "career_5_plus": 3}
    if user_signal in career_order and job_signal in career_order:
        return 20 if career_order[user_signal] >= career_order[job_signal] else -35
    return 0


def hard_match_score(item, profile, query=""):
    text = build_search_text(item)
    score = int(item.get("fit_score", 50) or 50)

    user_career = career_signal(profile.get("career_level") or profile.get("경력구분"))
    score += career_match_delta(user_career, job_career_signal(text))

    preferred_locations = profile_values(profile, "location_pref", "preferred_locations", "work_location", "근무희망지역")
    if preferred_locations:
        if any(location and location in item.get("location", "") for location in preferred_locations):
            score += 30
        else:
            score -= 35

    desired_salary = parse_amount_manwon(
        profile.get("desired_salary")
        or profile.get("희망연봉")
        or profile.get("salary_pref")
        or profile.get("preferred_salary")
    )
    job_salary = parse_amount_manwon(item.get("salary", ""))
    if desired_salary and job_salary:
        score += 25 if job_salary >= desired_salary else -45
    elif desired_salary and not job_salary:
        score -= 5

    certifications = profile_values(profile, "certifications", "licenses", "certs", "보유자격", "자격증")
    for cert in certifications:
        if cert and cert in text:
            score += 12

    for skill in profile_values(profile, "skills", "보유기술"):
        if keyword_in_text(skill, text):
            score += 8

    for token in tokenize_query(profile.get("experience_summary") or profile.get("경험요약")):
        if keyword_in_text(token, text):
            score += 6

    for language in profile_values(profile, "language_scores", "어학성적"):
        for token in tokenize_query(language):
            if keyword_in_text(token, text):
                score += 10

    for token in tokenize_query(query):
        if keyword_in_text(token, text):
            score += 15

    return score


def parse_request(args):
    data = {}
    if args.request_file:
        data = read_json(args.request_file, {})
    elif args.request_json:
        data = json.loads(args.request_json)
    data = data if isinstance(data, dict) else {}
    if args.slack_user_id:
        data["slack_user_id"] = args.slack_user_id
    if args.query:
        data["query"] = args.query
    return data


def empty_result(slack_user_id, query):
    return normalize_schema_payload(
        {
            "company": "검색 결과 없음",
            "title": clean_text(query) or "조건에 맞는 채용공고 없음",
            "employment_type": "확인 필요",
            "location": "근무지역 확인 필요",
            "salary": "회사내규에 따름",
            "requirements": "조건에 맞는 공고를 찾지 못했습니다",
            "preferences": "검색 조건을 완화해 다시 요청해 주세요",
            "jd_summary": "저장된 채용공고 목록에서 매칭 결과가 없습니다",
            "job_keywords": ["#검색조건", "#채용매칭", "#조건완화"],
            "detail_url": "",
            "company_career_url": "",
            "image_url": "",
        },
        "SEARCH",
        slack_user_id,
    )


def run_search(request):
    slack_user_id = clean_text(request.get("slack_user_id") or request.get("user_id") or request.get("user"))
    query = clean_text(request.get("query") or request.get("text") or request.get("keyword"))
    profile = merge_profile(load_user_profile(), request.get("profile", {}))
    candidates = load_candidates()
    if not candidates:
        return empty_result(slack_user_id, query)

    ranked = sorted(candidates, key=lambda item: hard_match_score(item, profile, query), reverse=True)
    return normalize_schema_payload(ranked[0], "SEARCH", slack_user_id)


def main():
    parser = argparse.ArgumentParser(description="Slack chatbot pull-mode recruiting search")
    parser.add_argument("--request-file")
    parser.add_argument("--request-json")
    parser.add_argument("--slack-user-id")
    parser.add_argument("--query")
    parser.add_argument("--output")
    args = parser.parse_args()

    try:
        result = run_search(parse_request(args))
    except Exception as exc:
        print(f"chatbot search failed: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
