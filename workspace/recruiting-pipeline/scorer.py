#!/usr/bin/env python3
import json
import re
import sys
import urllib.parse

from common import (
    ENRICH_OUTPUT_PATH,
    SCORE_OUTPUT_PATH,
    USER_PROFILE_PATH,
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

TEXT_POOR_MARKERS = ("참조", "분석 생략", "분석 실패", "정보없음", "추출 불가")
GENERIC_REQUIREMENTS = {
    "공고 자격요건 및 전공 요건 참조",
    "자격요건 참조",
}
GENERIC_PREFERENCES = {
    "우대 스택 및 동종 업계 경력 우대",
    "우대사항 참조",
}
GENERIC_KEYWORDS = {"#직무역량", "#자소서작성", "#성장가능성", "#성장지향", "#자소서팁"}
SECTION_HEADINGS = (
    "주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할",
    "자격요건", "지원자격", "필수요건", "필수사항", "응시자격",
    "우대사항", "우대조건", "우대요건", "preferred",
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
    normalized = re.sub(r"\s+(?=(?:주요업무|담당업무|자격요건|지원자격|필수요건|우대사항|우대조건|복리후생)\s*[:：])", "\n", normalized)
    raw_chunks = re.split(r"[\n;]|(?<=[.!?。])\s+", normalized)
    chunks = []
    for chunk in raw_chunks:
        chunk = clean_text(chunk.strip(" -·ㆍ•"))
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


def extract_heading_section(text, headings):
    source = clean_text(text)
    if not source:
        return ""
    heading_pattern = "|".join(re.escape(h) for h in headings)
    stop_pattern = "|".join(re.escape(h) for h in SECTION_HEADINGS if h not in headings)
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
    text = re.sub(r"\s+(?=\d+\))", "\n", text)
    text = re.sub(r"\s+(?=-\s)", "\n", text)
    text = re.sub(r"\s+(?=(?:우대사항|자격요건|지원자격|주요업무|담당업무)\s*[:：])", "\n", text)
    raw_units = re.split(r"[\n;]|(?<=[.!?。])\s+", text)
    units = []
    for unit in raw_units:
        unit = clean_text(unit.strip(" -·ㆍ•"))
        unit = re.sub(r"^\d+\)\s*", "", unit)
        unit = re.sub(r"^(?:우대사항|자격요건|지원자격|주요업무|담당업무)\s*[:：]\s*", "", unit)
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

    limits = {
        "requirements": 100,
        "preferences": 120,
        "jd_summary": 150,
    }
    limit = limit or limits.get(field, 120)

    if field == "requirements":
        facts = []
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
            facts.append(match)
        for match in re.findall(r"(?:Python|SQL|AI|ML|LLM|Java|React|Spring|MLOps|클라우드|데이터)[^/,\n]{0,20}", text, flags=re.IGNORECASE):
            facts.append(match)
        units = [] if len(normalized_dedupe(facts)) >= 2 else pick_units(text, ["학사", "석사", "경력", "무관", "전공", "필수", "Python", "SQL", "AI", "데이터"], 2)
        summary = ", ".join(normalized_dedupe(facts + units)[:4])
        return trim_summary(summary or f"{title} 관련 핵심 자격요건 확인 필요", limit)

    if field == "preferences":
        units = pick_units(text, ["우대", "경험", "경력자", "프로젝트", "자격증", "분석", "개선", "협업", "영어"], 3)
        cleaned_units = []
        for unit in units:
            unit = re.sub(r"^(?:우대사항|우대조건|우대요건)\s*[:：]?\s*", "", unit)
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
    units = pick_units(text, ["담당", "개발", "운영", "분석", "기획", "관리", "구축", "개선", "지원", "연구", "생산"], 4)
    if field == "jd_summary":
        units = [
            unit for unit in units
            if not any(skip in unit for skip in ["경영이념", "행복을 추구", "가치 창출", "성장해 나갈", "계열사로서"])
        ] or units[:2]
    summary = " / ".join(normalized_dedupe(units)[:2])
    return trim_summary(summary or text, limit)


def extract_requirements_summary(jd_text, title=""):
    if is_poor_text(jd_text):
        return trim_summary(f"{title} 관련 상세 자격요건은 원본 공고 확인 필요", 100)
    section = extract_heading_section(jd_text, ["자격요건", "지원자격", "필수요건", "필수사항", "응시자격", "requirements", "qualifications"])
    return summarize_long_field(section or jd_text, "requirements", title)


def extract_preferences_summary(jd_text, title=""):
    if is_poor_text(jd_text):
        return trim_summary(f"{title} 관련 우대요건은 원본 공고 확인 필요", 100)
    section = extract_heading_section(jd_text, ["우대사항", "우대조건", "우대요건", "preferred", "plus"])
    return summarize_long_field(section or jd_text, "preferences", title)


def summarize_jd_text(jd_text, title=""):
    if is_poor_text(jd_text):
        return ""
    section = extract_heading_section(jd_text, ["주요업무", "담당업무", "업무내용", "직무내용", "수행업무", "역할"])
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
        "company": company,
        "title": title,
        "employment_type": deep_data.get("employment_type", "정규직"),
        "location": ", ".join(str(loc) for loc in locs),
        "salary": "회사내규에 따름",
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
        payload["jd_summary"] = "공고 상세 직무 내용 확인 필요"
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
    return payload

def analyze_job_with_llm(item, profile):
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
        
    prompt = f"""
Analyze the following recruitment listing and the user's career profile to refine the job description and create writing suggestions for self-introduction letters.
Output must be in JSON format matching the schema.

### User Profile:
Skills: {profile.get("skills", [])}
Preferred Locations: {profile.get("location_pref", [])}
Education: {profile.get("education", "")}

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
- location: exact work location/region
- salary: salary information (use "회사내규에 따름" if not explicitly specified or if negotiations are required)
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
    
    for item in listings:
        company = item.get("company", "")
        title = item.get("title", "")
        print(f"Refining and formatting job data for: {company} - {title}")
        
        refined_data = analyze_job_with_llm(item, profile)
        scored_results.append(refined_data)
        
    output_path = SCORE_OUTPUT_PATH
    write_json(output_path, scored_results)
    print(f"Saved refined data to {output_path}")

if __name__ == "__main__":
    main()
