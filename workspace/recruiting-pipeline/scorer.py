#!/usr/bin/env python3
import json
import re
import sys
import urllib.parse

from common import (
    ENRICH_OUTPUT_PATH,
    SCORE_OUTPUT_PATH,
    USER_PROFILE_PATH,
    VERIFY_ERRORS_PATH,
    dedupe_preserve_order,
    init_openai_client,
    read_json,
    write_json,
)

DEFAULT_USER_PROFILE = {
    "skills": ["AI", "ML", "Python", "Data Pipeline", "데이터"],
    "location_pref": ["서울", "경기"],
    "education": "대졸"
}

client = init_openai_client("scorer")

TEXT_POOR_MARKERS = (
    "참조",
    "분석 생략",
    "분석 실패",
    "정보없음",
    "추출 불가",
    "원본 공고 확인 필요",
    "공고 상세 직무 내용 확인 필요",
    "상세 자격요건은 원본",
    "우대요건은 원본",
)
GENERIC_REQUIREMENTS = {
    "공고 자격요건 및 전공 요건 참조",
    "자격요건 참조",
}
GENERIC_PREFERENCES = {
    "우대 스택 및 동종 업계 경력 우대",
    "우대사항 참조",
}
GENERIC_KEYWORDS = {"#직무역량", "#자소서작성", "#성장가능성", "#성장지향", "#자소서팁"}
OUTPUT_SCHEMA_KEYS = [
    "dispatch_type",
    "slack_user_id",
    "company",
    "title",
    "employment_type",
    "location",
    "salary",
    "requirements",
    "preferences",
    "jd_summary",
    "job_keywords",
    "detail_url",
    "company_career_url",
    "image_url",
]
SECTION_HEADINGS = (
    "주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할",
    "상세요강", "모집부문", "모집분야", "직무기술서",
    "Responsibilities", "Job Description", "Job Details",
    "자격요건", "지원자격", "필수요건", "필수사항", "응시자격", "기본요건", "지원요건",
    "Qualifications", "Required Qualifications", "Basic Qualifications",
    "우대사항", "우대조건", "우대요건", "우대자격", "Preferred Qualifications", "Preferences", "preferred",
    "복리후생", "근무조건", "전형절차"
)
JOB_SKILL_KEYWORDS = [
    "AI", "LLM", "머신러닝", "딥러닝", "Python", "SQL", "데이터분석", "데이터 엔지니어링",
    "데이터 파이프라인", "MLOps", "클라우드", "AWS", "Azure", "GCP", "자동화", "백엔드",
    "프론트엔드", "React", "Node", "Java", "Spring", "PM", "서비스기획", "마케팅",
    "영업", "콘텐츠", "재무", "회계", "인사", "HR", "UX", "UI", "보안", "인프라"
]
TALENT_KEYWORDS = [
    ("협업", "#협업"),
    ("소통", "#소통"),
    ("문제 해결", "#문제해결"),
    ("문제해결", "#문제해결"),
    ("주도", "#주도성"),
    ("책임", "#책임감"),
    ("성장", "#성장마인드"),
    ("글로벌", "#글로벌"),
    ("고객", "#고객중심"),
    ("혁신", "#혁신"),
]

def load_user_profile():
    if USER_PROFILE_PATH.exists():
        return read_json(USER_PROFILE_PATH, DEFAULT_USER_PROFILE)
    write_json(USER_PROFILE_PATH, DEFAULT_USER_PROFILE)
    return DEFAULT_USER_PROFILE

def calculate_fit_score(item, profile):
    score = 50
    title_text = item.get("title", "")
    jd_text = item.get("deep_scraped", {}).get("jd_summary", "")
    combined_text = (title_text + " " + jd_text).lower()
    
    # Skills matching
    matched_skills = 0
    for skill in profile.get("skills", []):
        if keyword_in_text(skill, combined_text):
            matched_skills += 1
            score += 8
            
    # Location matching
    locs = item.get("extracted_info", {}).get("location", [])
    location_matched = False
    for loc in locs:
        for pref in profile.get("location_pref", []):
            if pref in loc:
                location_matched = True
                score += 10
                break
        if location_matched:
            break
                
    return min(100, max(0, score)), location_matched

def as_list(value, default=None):
    if isinstance(value, list):
        return value
    if value:
        return [value]
    return default or []


def format_welfare(deep_data):
    welfare = deep_data.get("welfare_tags", [])
    if isinstance(welfare, list):
        return ", ".join(welfare) if welfare else "정보없음"
    return str(welfare or "정보없음")


def normalize_company_insight(item):
    raw = item.get("company_insight", {}) if isinstance(item.get("company_insight", {}), dict) else {}
    stability = raw.get("stability") or raw.get("stability_score") or "보통 (국민연금 가입자 최근 1년 유지)"
    return {
        "company_size": raw.get("company_size", "중소기업"),
        "primary_industry": raw.get("primary_industry", "기타 서비스 및 IT"),
        "mid_long_term_plan": raw.get("mid_long_term_plan", "안정적 비즈니스 성장 및 핵심 디지털 파트너십 확장"),
        "stability": stability,
        "stability_score": stability,
    }


def clean_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text


def keyword_in_text(keyword, text):
    keyword = str(keyword or "").strip()
    text = str(text or "")
    if not keyword:
        return False
    if re.fullmatch(r"[A-Za-z0-9+#.\s-]+", keyword):
        pattern = r"(?<![A-Za-z0-9])" + re.escape(keyword.lower()) + r"(?![A-Za-z0-9])"
        return bool(re.search(pattern, text.lower()))
    return keyword.lower() in text.lower()


def clean_job_title(value):
    title = clean_text(value)
    title = re.sub(r"D[-_]?\d+\s*스크랩", "", title, flags=re.IGNORECASE)
    title = re.sub(r"D[-_]?\d+", "", title, flags=re.IGNORECASE)
    title = title.replace("스크랩", "")
    return clean_text(title)


def sanitize_image_url(value):
    url = clean_text(value)
    if "images.unsplash.com/photo-1586281380349-632531db7ed4" in url:
        return ""
    return url


def sanitize_jd_text(value):
    text = clean_text(value)
    if "images.unsplash.com/photo-1586281380349-632531db7ed4" in text:
        return ""
    return text


def is_fallback_text(value):
    text = clean_text(value)
    return any(marker in text for marker in TEXT_POOR_MARKERS)


def format_salary_amount(amount):
    try:
        number = int(str(amount).replace(",", ""))
    except Exception:
        return ""
    return f"{number:,}만원"


def extract_salary_text(*values):
    text = clean_text(" ".join(str(value or "") for value in values))
    if not text:
        return ""
    if re.search(r"회사\s*내규|내규에\s*따름|면접\s*후|협의|추후협의|미정", text):
        return "회사내규에 따름"
    range_match = re.search(r"(\d{3,5})\s*(?:~|-|부터|이상)\s*(\d{3,5})\s*만원", text)
    if range_match:
        return f"{format_salary_amount(range_match.group(1))}~{format_salary_amount(range_match.group(2))}"
    amount_match = re.search(r"(\d{1,3}(?:,\d{3})+|\d{3,5})\s*(?:만원|만\s*원)", text)
    if amount_match:
        return format_salary_amount(amount_match.group(1))
    annual_match = re.search(r"연봉\s*(\d{3,5})", text)
    if annual_match:
        return format_salary_amount(annual_match.group(1))
    return ""


def extract_location_text(*values):
    text = clean_text(" ".join(str(value or "") for value in values))
    matches = [
        match.group(0).strip()
        for match in re.finditer(
            r"(?:서울|경기|인천|부산|대구|대전|광주|울산|세종|강원|충북|충남|전북|전남|경북|경남|제주)(?:\s*[가-힣]{1,5}(?:구|군|시))?",
            text,
        )
    ]
    return ", ".join(dedupe_preserve_order(matches[:3]))


def normalize_schema_payload(payload, dispatch_type="PUSH", slack_user_id=""):
    payload = payload.copy()
    payload["dispatch_type"] = dispatch_type
    payload["slack_user_id"] = slack_user_id or payload.get("slack_user_id", "")

    salary = extract_salary_text(payload.get("salary", ""), payload.get("requirements", ""), payload.get("jd_summary", ""))
    payload["salary"] = salary or clean_text(payload.get("salary", "")) or "회사내규에 따름"

    location = extract_location_text(payload.get("location", ""))
    payload["location"] = location or clean_text(payload.get("location", "")) or "근무지역 확인 필요"

    keywords = payload.get("job_keywords", [])
    if not isinstance(keywords, list):
        keywords = [keywords] if keywords else []
    keywords = [str(keyword) if str(keyword).startswith("#") else f"#{keyword}" for keyword in keywords if clean_text(keyword)]
    keywords = [keyword for keyword in dedupe_preserve_order(keywords) if keyword not in GENERIC_KEYWORDS]
    if len(keywords) < 3:
        keywords = dedupe_preserve_order(keywords + ["#채용공고분석", "#기업비전", "#직무적합성"])
    payload["job_keywords"] = keywords[:5]

    image_url = sanitize_image_url(payload.get("image_url", ""))
    jd_summary = sanitize_jd_text(payload.get("jd_summary", ""))
    if (not jd_summary or "참조" in jd_summary or len(jd_summary) < 12 or is_poor_text(jd_summary)) and image_url:
        payload["jd_summary"] = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
    else:
        payload["jd_summary"] = jd_summary or "공고 상세 직무 내용 확인 필요"
    payload["image_url"] = image_url

    defaults = {
        "company": "",
        "title": "",
        "employment_type": "정규직",
        "requirements": "자격요건 확인 필요",
        "preferences": "우대요건 확인 필요",
        "detail_url": "",
        "company_career_url": "",
    }
    for key, value in defaults.items():
        payload[key] = clean_text(payload.get(key, "")) or value

    ordered = {key: payload.get(key, "") for key in OUTPUT_SCHEMA_KEYS}
    for key, value in payload.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


def is_poor_text(value):
    text = clean_text(value)
    if not text or len(text) < 12:
        return True
    return any(marker in text for marker in TEXT_POOR_MARKERS)


def split_chunks(text):
    text = clean_text(text)
    if not text:
        return []
    normalized = re.sub(r"\s*[•ㆍ·]\s*", "\n", text)
    normalized = re.sub(r"\s+(?=\[?\s*(?:주요업무|담당업무|자격요건|지원자격|필수요건|기본요건|우대사항|우대조건|우대요건|상세요강|모집부문|직무기술서|Responsibilities|Job Description|Qualifications|Required Qualifications|Preferred Qualifications|복리후생)\s*\]?\s*[:：]?)", "\n", normalized, flags=re.IGNORECASE)
    raw_chunks = re.split(r"[\n;]|(?<=[.!?。])\s+", normalized)
    chunks = []
    for chunk in raw_chunks:
        chunk = clean_text(chunk.strip(" -·ㆍ•"))
        if is_boilerplate_unit(chunk):
            continue
        if 8 <= len(chunk) <= 260:
            chunks.append(chunk)
    if not chunks and text:
        chunks.append(text[:260])
    return chunks


def trim_summary(text, limit=120):
    text = clean_text(text).strip(" -·ㆍ•")
    if len(text) <= limit:
        return text
    cut = text[:limit].rstrip()
    last_space = cut.rfind(" ")
    if last_space > 45:
        cut = cut[:last_space]
    return cut.rstrip(" ,/") + "..."


def is_boilerplate_unit(text):
    return is_fallback_text(text) or any(skip in text for skip in [
        "본 정보는 인크루트",
        "게재된 채용기업",
        "구직활동 이외의 용도",
        "지금 본 공고에 관심있는 지원자",
        "사람인에서 수집한 공고",
        "정보 수정이 필요할 경우",
        "정확한 상세요강은 반드시",
        "지원자 통계를 확인",
    ])


def extract_heading_section(text, headings):
    source = clean_text(text)
    if not source:
        return ""
    boundary = r"(?![가-힣A-Za-z0-9])"
    heading_pattern = "|".join(r"\[?\s*" + re.escape(h) + r"\s*\]?" + boundary for h in headings)
    stop_pattern = "|".join(r"\[?\s*" + re.escape(h) + r"\s*\]?" + boundary for h in SECTION_HEADINGS if h not in headings)
    pattern = rf"(?:{heading_pattern})\s*[:：]?\s*(.*?)(?=(?:{stop_pattern})\s*[:：]?|$)"
    match = re.search(pattern, source, re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return ""


def summarize_by_keywords(text, keywords, limit=120):
    chunks = split_chunks(text)
    selected = []
    for chunk in chunks:
        lowered = chunk.lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            selected.append(chunk)
        if len(selected) >= 2:
            break
    if not selected and chunks:
        selected = chunks[:2]
    return trim_summary(" / ".join(selected), limit)


def split_summary_units(text):
    text = clean_text(text)
    if not text:
        return []
    text = re.sub(r"\s*[•ㆍ·○]\s*", "\n", text)
    text = re.sub(r"\s+(?=\d+\))", "\n", text)
    text = re.sub(r"\s+(?=-\s)", "\n", text)
    text = re.sub(r"\s+(?=\[?\s*(?:우대사항|우대조건|우대요건|우대자격|자격요건|지원자격|기본요건|지원요건|주요업무|담당업무|상세요강|모집부문|직무기술서|Responsibilities|Job Description|Qualifications|Required Qualifications|Preferred Qualifications)\s*\]?\s*[:：]?)", "\n", text, flags=re.IGNORECASE)
    raw_units = re.split(r"[\n;]|(?<=[.!?。])\s+", text)
    units = []
    for unit in raw_units:
        unit = clean_text(unit.strip(" -·ㆍ•"))
        unit = re.sub(r"^\d+\)\s*", "", unit)
        unit = re.sub(r"^\[?\s*(?:우대사항|우대조건|우대요건|우대자격|자격요건|지원자격|기본요건|지원요건|주요업무|담당업무|상세요강|모집부문|직무기술서|Responsibilities|Job Description|Qualifications|Required Qualifications|Preferred Qualifications)\s*\]?\s*[:：]?\s*", "", unit, flags=re.IGNORECASE)
        if is_boilerplate_unit(unit):
            continue
        if 4 <= len(unit) <= 180:
            units.append(unit)
    return units


def pick_units(text, keywords, limit=3):
    units = split_summary_units(text)
    selected = []
    for unit in units:
        lowered = unit.lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            selected.append(unit)
        if len(selected) >= limit:
            break
    if not selected:
        selected = units[:limit]
    return selected


def normalized_dedupe(values):
    result = []
    seen = set()
    for value in values:
        value = clean_text(value).strip(" ,/")
        if not value:
            continue
        key = re.sub(r"[\s,./:：-]+", "", value).lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def summarize_long_field(text, field, title="", limit=None):
    text = clean_text(text)
    if not text:
        return ""
    if is_fallback_text(text):
        if field == "requirements":
            return "원본 공고 확인 필요"
        if field == "preferences":
            return "공고상 별도 우대요건 미기재"
        return ""

    limits = {
        "requirements": 100,
        "preferences": 120,
        "jd_summary": 150,
    }
    limit = limit or limits.get(field, 120)

    if field == "requirements":
        facts = []
        if re.search(r"학력\s*무관", text):
            facts.append("학력 무관")
        for match in re.findall(r"(?:4년제\s*)?대졸\s*(?:이상|↑)|학력무관", text):
            facts.append(match.replace(" ", ""))
        if re.search(r"(?:신입|경력)\s*(?:/|ㆍ|·|,)?", text):
            facts.append(re.search(r"(?:신입|경력)\s*(?:/|ㆍ|·|,)?", text).group(0).strip(" /ㆍ·,"))
        for match in re.findall(r"(?:관련)?경력\s*\d+\s*년\s*[~\-]\s*\d+\s*년|경력\s*\d+\s*년\s*이상|\d+\s*년\s*이상", text):
            facts.append(match)
        if re.search(r"공인\s*어학성적|TOEIC|TEPS|TOEFL|G-TELP|FLEX", text, flags=re.IGNORECASE):
            facts.append("공인어학성적 보유")
        major_match = re.search(r"((?:컴퓨터|인공지능|산업공학|통계|로봇|기계공학|전자공학|정보통신공학)[^。\n]{0,55}전공자)", text)
        if major_match:
            facts.append(trim_summary(major_match.group(1), 45))
        skill_hits = []
        for token in ["Python", "JavaScript", "TypeScript", "FastAPI", "React", "RAG", "Prompt Engineering", "Docker", "CI/CD", "PyTorch", "JAX", "ROS", "LLM"]:
            if re.search(re.escape(token), text, flags=re.IGNORECASE):
                skill_hits.append(token)
        if skill_hits:
            facts.append("/".join(normalized_dedupe(skill_hits)[:4]))
        if any(re.search(r"경력\s*\d", fact) for fact in facts):
            facts = [fact for fact in facts if fact != "경력"]
        strong_facts = normalized_dedupe(facts)
        if len(strong_facts) >= 3:
            return trim_summary(", ".join(strong_facts[:4]), limit)
        for match in re.findall(r"(?:초대졸|전문학사|학사|석사|박사)\s*이상", text, flags=re.IGNORECASE):
            facts.append(match)
        if re.search(r"신입\s*[·/]\s*경력", text):
            facts.append("신입·경력")
        elif re.search(r"경력\s*[:：]?\s*무관|경력[^,\n]{0,12}무관", text):
            facts.append("경력 무관")
        elif "신입" in text:
            facts.append("신입")
        for match in re.findall(r"\d+\s*년\s*이상", text):
            facts.append(match)
        for match in re.findall(r"(?:전공|학과)\s*[:：]?\s*[^/,\n]{2,35}", text):
            if any(skip in match for skip in ["전공시험", "면접", "임용"]):
                continue
            facts.append(match)
        for match in re.findall(r"(?:Python|SQL|AI|ML|LLM|Java|React|Spring|MLOps|클라우드|데이터)[^/,\n]{0,20}", text, flags=re.IGNORECASE):
            facts.append(match)
        units = [] if len(normalized_dedupe(facts)) >= 2 else pick_units(text, ["학사", "석사", "경력", "무관", "전공", "필수", "Python", "SQL", "AI", "데이터"], 2)
        summary = ", ".join(normalized_dedupe(facts + units)[:4])
        return trim_summary(summary or f"{title} 관련 핵심 자격요건 확인 필요", limit)

    if field == "preferences":
        if "별도 우대요건 미기재" in text:
            return "공고상 별도 우대요건 미기재"
        preferred_hits = []
        for token in ["LeRobot", "ALOHA", "Jetson", "TensorRT", "Isaac Sim", "MuJoCo", "RAG", "Prompt Engineering", "Docker", "CI/CD", "영어", "협업"]:
            if re.search(re.escape(token), text, flags=re.IGNORECASE):
                preferred_hits.append(token)
        if preferred_hits:
            return trim_summary(", ".join(normalized_dedupe(preferred_hits)[:4]) + " 경험 우대", limit)
        units = pick_units(text, ["우대", "경험", "경력자", "프로젝트", "자격증", "분석", "개선", "협업", "영어"], 3)
        cleaned_units = []
        for unit in units:
            unit = re.sub(r"^\[?\s*(?:우대사항|우대조건|우대요건|우대자격)\s*\]?\s*[:：]?\s*", "", unit)
            unit = clean_text(unit).strip(" ,/")
            cleaned_units.append(unit)
        summary = ", ".join(normalized_dedupe(cleaned_units)[:3])
        return trim_summary(summary or f"{title} 관련 핵심 우대요건 확인 필요", limit)

    if field == "jd_summary":
        text = re.sub(r"홈페이지\s*입사\s*지원하기", " ", text)
        role_match = re.search(r"(\((?:연구직|영업직|사무직)\)[^。.!?]{10,220})", text)
        if role_match:
            role_text = role_match.group(1)
            if "부문 " in role_text:
                role_text = role_text.split("부문 ", 1)[0] + "부문"
            return trim_summary(role_text, limit)
    units = pick_units(text, ["담당", "개발", "운영", "분석", "기획", "관리", "구축", "개선", "지원", "연구", "생산", "AI", "Agent", "Platform", "Engineer", "LLM", "RAG"], 4)
    if field == "jd_summary":
        units = [
            unit for unit in units
            if not any(skip in unit for skip in ["경영이념", "행복을 추구", "가치 창출", "성장해 나갈", "계열사로서"])
            and not ("채용공고" in unit and not re.search(r"개발|운영|분석|기획|관리|Agent|Platform|Engineer", unit, re.IGNORECASE))
            and not (re.search(r"^\d{4}년도\s+정규직", unit) and not re.search(r"개발|운영|분석|기획|관리|Agent|Platform|Engineer", unit, re.IGNORECASE))
        ] or units[:2]
    summary = " / ".join(normalized_dedupe(units)[:2])
    return trim_summary(summary or text, limit)


def extract_requirements_summary(jd_text, title=""):
    if is_poor_text(jd_text):
        return "원본 공고 확인 필요"
    section = extract_heading_section(jd_text, ["자격요건", "지원자격", "필수요건", "필수사항", "응시자격", "기본요건", "지원요건", "requirements", "qualifications", "Required Qualifications", "Basic Qualifications"])
    return summarize_long_field(section or jd_text, "requirements", title)


def extract_preferences_summary(jd_text, title=""):
    if is_poor_text(jd_text):
        return "공고상 별도 우대요건 미기재"
    section = extract_heading_section(jd_text, ["우대사항", "우대조건", "우대요건", "우대자격", "preferred", "Preferred Qualifications", "Preferences", "plus"])
    if not section and not re.search(r"우대|preferred|plus", jd_text, flags=re.IGNORECASE):
        return "공고상 별도 우대요건 미기재"
    return summarize_long_field(section or jd_text, "preferences", title)


def summarize_jd_text(jd_text, title=""):
    if is_poor_text(jd_text):
        return ""
    section = extract_heading_section(jd_text, ["주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할", "상세요강", "모집부문", "모집분야", "직무기술서", "Responsibilities", "Job Description", "Job Details"])
    summary = summarize_long_field(section or jd_text, "jd_summary", title)
    if not summary and title:
        return trim_summary(f"{title} 포지션의 주요 업무 수행", 120)
    return summary


def extract_structured_summaries(item, fallback_jd_summary=""):
    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    title = clean_job_title(item.get("title", ""))
    raw_jd = sanitize_jd_text(deep_data.get("jd_summary", ""))
    crawled_responsibilities = clean_text(deep_data.get("responsibilities", ""))
    crawled_requirements = clean_text(deep_data.get("requirements", ""))
    crawled_preferences = clean_text(deep_data.get("preferences", ""))
    jd_summary = summarize_long_field(crawled_responsibilities, "jd_summary", title) or summarize_jd_text(raw_jd, title) or clean_text(fallback_jd_summary)
    return {
        "requirements": summarize_long_field(crawled_requirements, "requirements", title) or extract_requirements_summary(raw_jd, title),
        "preferences": summarize_long_field(crawled_preferences, "preferences", title) or extract_preferences_summary(raw_jd, title),
        "jd_summary": jd_summary,
    }


def hashtag(value):
    text = re.sub(r"[^0-9A-Za-z가-힣]+", "", str(value or ""))
    return f"#{text}" if text else ""


def derive_job_keywords(item, profile, fallback_jd_summary):
    company_insight = normalize_company_insight(item)
    title = clean_job_title(item.get("title", ""))
    raw_jd = sanitize_jd_text(item.get("deep_scraped", {}).get("jd_summary", ""))
    context = clean_text(
        " ".join([
            title,
            raw_jd,
            fallback_jd_summary,
            company_insight.get("primary_industry", ""),
            company_insight.get("mid_long_term_plan", ""),
        ])
    )
    lowered = context.lower()
    keywords = []

    for skill in profile.get("skills", []):
        if keyword_in_text(skill, lowered):
            keywords.append(hashtag(skill))

    for skill in JOB_SKILL_KEYWORDS:
        if keyword_in_text(skill, lowered):
            keywords.append(hashtag(skill))

    for needle, tag in TALENT_KEYWORDS:
        if needle in context:
            keywords.append(tag)

    plan = company_insight.get("mid_long_term_plan", "")
    if "디지털" in plan or "자동화" in plan:
        keywords.append("#디지털전환")
    if "AI" in plan or "인공지능" in plan:
        keywords.append("#AI전략")
    if "글로벌" in plan:
        keywords.append("#글로벌확장")

    for token in re.findall(r"[A-Za-z]{2,}|[가-힣]{2,}", title):
        if token not in {"채용", "모집", "경력", "신입", "정규직", "계약직"}:
            keywords.append(hashtag(token))
        if len(keywords) >= 6:
            break

    if len(dedupe_preserve_order([kw for kw in keywords if kw])) < 3:
        industry = company_insight.get("primary_industry", "")
        for token in re.findall(r"[가-힣A-Za-z]{2,}", industry):
            keywords.append(hashtag(token))
            if len(keywords) >= 5:
                break

    keywords = [kw for kw in dedupe_preserve_order(keywords) if kw and kw not in GENERIC_KEYWORDS]
    return keywords[:5] if len(keywords) >= 3 else dedupe_preserve_order(keywords + ["#채용공고분석", "#기업비전", "#직무적합성"])[:5]


def should_accept_candidate_field(key, value):
    text = clean_text(value)
    if not text:
        return False
    if key in {"requirements", "preferences", "jd_summary"} and is_fallback_text(text):
        return False
    if key == "requirements" and text in GENERIC_REQUIREMENTS:
        return False
    if key == "preferences" and text in GENERIC_PREFERENCES:
        return False
    if key in {"requirements", "preferences", "jd_summary"} and len(text) < 8:
        return False
    return True


def build_architecture_fields(item, payload, location_matched):
    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    extracted = item.get("extracted_info", {}) if isinstance(item.get("extracted_info", {}), dict) else {}
    return {
        "analysis": {
            "job_category": extracted.get("job_category", "IT / 데이터 / AI"),
            "location_score": "95점 (선호 지역 일치)" if location_matched else "70점 (선호 지역 미확인)",
            "jd_summary": payload.get("jd_summary", "공고 상세 직무 내용 확인 필요"),
            "welfare": format_welfare(deep_data),
        },
        "company_insight": normalize_company_insight(item),
    }


def build_base_job_data(item, profile, fallback_jd_summary):
    company = item.get("company", "")
    title = clean_job_title(item.get("title", ""))
    detail_url = item.get("detail_url", "")
    image_url = sanitize_image_url(item.get("image_url", ""))
    deadline = item.get("deadline", "마감일 확인 필요")
    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    extracted = item.get("extracted_info", {}) if isinstance(item.get("extracted_info", {}), dict) else {}
    structured = extract_structured_summaries(item, fallback_jd_summary)
    fallback_jd_summary = structured["jd_summary"] or fallback_jd_summary or "공고 상세 직무 내용 확인 필요"
    job_keywords = derive_job_keywords(item, profile, fallback_jd_summary)
    
    career_url = deep_data.get("official_detail_url")
    if not career_url:
        encoded_company = urllib.parse.quote(company)
        career_url = f"https://www.google.com/search?q={encoded_company}+채용+페이지"
    
    requirements = structured["requirements"]
    preferences = structured["preferences"]
    
    fit_score, location_matched = calculate_fit_score(item, profile)
    locs = as_list(extracted.get("location"), ["서울"])
    
    payload = {
        "dispatch_type": "PUSH",
        "slack_user_id": "",
        "platform": item.get("platform", ""),
        "company": company,
        "title": title,
        "employment_type": deep_data.get("employment_type", "정규직"),
        "location": ", ".join(str(loc) for loc in locs),
        "salary": extract_salary_text(deep_data.get("jd_summary", ""), item.get("title", "")) or "회사내규에 따름",
        "requirements": requirements,
        "preferences": preferences,
        "jd_summary": fallback_jd_summary,
        "job_keywords": job_keywords,
        "detail_url": detail_url,
        "company_career_url": career_url,
        "deadline": deadline,
        "image_url": image_url,
        "fit_score": fit_score
    }
    payload.update(build_architecture_fields(item, payload, location_matched))
    return payload


def normalize_refined_data(item, profile, candidate, fallback_jd_summary):
    payload = build_base_job_data(item, profile, fallback_jd_summary)
    candidate = candidate if isinstance(candidate, dict) else {}
    pass_through_keys = [
        "company", "title", "employment_type", "location", "salary",
        "requirements", "preferences", "jd_summary", "company_career_url"
    ]

    for key in pass_through_keys:
        value = candidate.get(key)
        if should_accept_candidate_field(key, value):
            payload[key] = value

    kw = candidate.get("job_keywords", payload["job_keywords"])
    if not isinstance(kw, list):
        kw = payload["job_keywords"]
    kw = [keyword if str(keyword).startswith("#") else f"#{keyword}" for keyword in kw]
    kw = [keyword for keyword in kw if keyword not in GENERIC_KEYWORDS]
    if len(dedupe_preserve_order(kw)) < 3:
        kw = derive_job_keywords(item, profile, fallback_jd_summary)
    payload["job_keywords"] = dedupe_preserve_order(kw)[:5]

    structured = extract_structured_summaries(item, fallback_jd_summary)
    if clean_text(payload.get("requirements")) in GENERIC_REQUIREMENTS or not should_accept_candidate_field("requirements", payload.get("requirements")):
        payload["requirements"] = structured["requirements"]
    if clean_text(payload.get("preferences")) in GENERIC_PREFERENCES or not should_accept_candidate_field("preferences", payload.get("preferences")):
        payload["preferences"] = structured["preferences"]
    payload["requirements"] = summarize_long_field(payload.get("requirements", ""), "requirements", item.get("title", ""))
    payload["preferences"] = summarize_long_field(payload.get("preferences", ""), "preferences", item.get("title", ""))

    jd_val = str(payload.get("jd_summary", "")).strip()
    image_url = sanitize_image_url(item.get("image_url", ""))
    if is_poor_text(jd_val) and structured["jd_summary"] and not is_poor_text(structured["jd_summary"]):
        payload["jd_summary"] = structured["jd_summary"]
    elif (not jd_val or "참조" in jd_val or len(jd_val) < 12) and image_url:
        payload["jd_summary"] = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
    elif not jd_val or is_poor_text(jd_val):
        payload["jd_summary"] = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>" if image_url else "공고 상세 직무 내용 확인 필요"
    else:
        payload["jd_summary"] = summarize_long_field(payload["jd_summary"], "jd_summary", item.get("title", ""))

    sal_val = str(payload.get("salary", "")).strip()
    if not sal_val or any(token in sal_val for token in ["협의", "미정", "회사내규", "추후협의"]):
        payload["salary"] = "회사내규에 따름"

    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    official_url = deep_data.get("official_detail_url")
    if official_url:
        payload["company_career_url"] = official_url

    payload["detail_url"] = item.get("detail_url", "")
    payload["deadline"] = item.get("deadline", "마감일 확인 필요")
    payload["image_url"] = sanitize_image_url(item.get("image_url", "") or candidate.get("image_url", ""))
    payload["title"] = clean_job_title(payload.get("title", ""))

    fit_score, location_matched = calculate_fit_score(item, profile)
    payload["fit_score"] = fit_score
    payload.update(build_architecture_fields(item, payload, location_matched))
    return normalize_schema_payload(payload, "PUSH", payload.get("slack_user_id", ""))

def analyze_job_with_llm(item, profile, idx=None):
    company = item.get("company", "")
    title = clean_job_title(item.get("title", ""))
    image_url = sanitize_image_url(item.get("image_url", ""))
    
    deep_data = item.get("deep_scraped", {})
    jd_summary_raw = sanitize_jd_text(deep_data.get("jd_summary", ""))
    welfare_raw = deep_data.get("welfare_tags", [])
    if isinstance(welfare_raw, list):
        welfare_str = ", ".join(welfare_raw)
    else:
        welfare_str = str(welfare_raw)
        
    company_insight = item.get("company_insight", {})
    
    is_text_poor = is_poor_text(jd_summary_raw)
    
    fallback_jd_summary = summarize_jd_text(jd_summary_raw, title) or jd_summary_raw
    if is_text_poor and image_url:
        fallback_jd_summary = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기>"
        
    if not client:
        return normalize_refined_data(item, profile, {}, fallback_jd_summary)
        
    errors_context = ""
    if idx is not None and VERIFY_ERRORS_PATH.exists():
        all_errors = read_json(VERIFY_ERRORS_PATH, [])
        item_errors = [err for err in all_errors if f"Item {idx} " in err]
        if item_errors:
            errors_context = "\n### Previous Validation Errors for this Item to Correct:\n" + "\n".join(f"- {err}" for err in item_errors) + "\nVerify that you fix all these errors in your output JSON fields.\n"

    prompt = f"""
Analyze the following recruitment listing and the user's career profile to refine the job description and create writing suggestions for self-introduction letters.
Output must be in JSON format matching the schema.
{errors_context}
### User Profile:
Skills: {profile.get("skills", [])}
Preferred Locations: {profile.get("location_pref", [])}
Education: {profile.get("education", "")}
Desired Salary: {profile.get("desired_salary") or profile.get("희망연봉", "")}
Certifications: {profile.get("certifications") or profile.get("보유자격", [])}

### Job Listing Data:
Company: {company}
Title: {title}
Original Employment Type: {deep_data.get("employment_type", "정규직")}
Location Info: {item.get("extracted_info", {}).get("location", ["서울"])}
Welfare: {welfare_str}
Company Insight: {company_insight}
Original JD (Raw Text): {jd_summary_raw}
Fallback Image URL: {image_url}
Official Listing Details URL (Original Site): {deep_data.get("official_detail_url", "")}

### Instructions for fields:
- employment_type: extract work type (e.g. 정규직/계약직/인턴)
- location: exact work location/region as one normalized text value (e.g. "서울 강남구")
- salary: salary information as one normalized text value with numbers when present (e.g. "4,500만원"; use "회사내규에 따름" if not explicitly specified or if negotiations are required)
- requirements: summary of minimum qualifications / requirements
- preferences: summary of preferred skills / qualities
- jd_summary:
    - If the original raw text contains detailed responsibilities/tasks, refine and summarize it cleanly in one concise Korean sentence.
    - If the raw text is empty, poor, or just says "공고 참조", "상세 참조", you MUST return this markdown hyperlink exact format using the Fallback Image URL: "<Fallback_Image_URL|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
- job_keywords:
    - Compile 3 to 5 keywords helper for writing self-introduction.
    - Focus on company values, talent traits, key skills, and the company's business direction from Company Insight.
    - Do not use generic placeholders such as #직무역량, #자소서작성, #성장가능성.
    - MUST format each keyword as a string starting with "#" (e.g. "#인재상키워드", "#Python실무", "#자소서팁")
- company_career_url: If Official Listing Details URL (Original Site) is provided, you MUST output it as company_career_url. Otherwise, provide the official corporate career site link. If not found, use google search query format: "https://www.google.com/search?q={{company_encoded_name}}+채용+페이지" (where {{company_encoded_name}} is the company name)
- image_url: Provide the original listing image URL

JSON schema:
{{
  "dispatch_type": "PUSH",
  "slack_user_id": "",
  "company": "string",
  "title": "string",
  "employment_type": "string",
  "location": "string",
  "salary": "string",
  "requirements": "string",
  "preferences": "string",
  "jd_summary": "string",
  "job_keywords": ["string"],
  "detail_url": "string",
  "company_career_url": "string",
  "image_url": "string"
}}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a specialized AI data extraction agent that formats recruitment data for resume helpers."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.2
        )
        
        res_data = json.loads(response.choices[0].message.content)
        
        return normalize_refined_data(item, profile, res_data, fallback_jd_summary)
    except Exception as e:
        print(f"LLM extraction failed: {e}, using fallback", file=sys.stderr)
        return normalize_refined_data(item, profile, {}, fallback_jd_summary)

def main():
    print("Starting LLM Matching and Scoring Phase...")
    input_path = ENRICH_OUTPUT_PATH
    if not input_path.exists():
        print(f"Error: Input file {input_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    listings = read_json(input_path, [])
        
    profile = load_user_profile()
    scored_results = []
    
    for idx, item in enumerate(listings):
        company = item.get("company", "")
        title = item.get("title", "")
        print(f"Refining and formatting job data for: {company} - {title}")
        
        refined_data = analyze_job_with_llm(item, profile, idx)
        scored_results.append(refined_data)
        
    output_path = SCORE_OUTPUT_PATH
    write_json(output_path, scored_results)
    print(f"Saved refined data to {output_path}")

if __name__ == "__main__":
    main()
