#!/usr/bin/env python3
import json
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
        if skill.lower() in combined_text:
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


def build_architecture_fields(item, payload, location_matched):
    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    extracted = item.get("extracted_info", {}) if isinstance(item.get("extracted_info", {}), dict) else {}
    return {
        "analysis": {
            "job_category": extracted.get("job_category", "IT / 데이터 / AI"),
            "location_score": "95점 (선호 지역 일치)" if location_matched else "70점 (선호 지역 미확인)",
            "jd_summary": payload.get("jd_summary", "공고 상세 직무 내용을 참조하십시오."),
            "welfare": format_welfare(deep_data),
        },
        "company_insight": normalize_company_insight(item),
    }


def build_base_job_data(item, profile, fallback_jd_summary):
    company = item.get("company", "")
    title = item.get("title", "")
    detail_url = item.get("detail_url", "")
    image_url = item.get("image_url", "")
    deadline = item.get("deadline", "~2026.07.05(일)")
    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    extracted = item.get("extracted_info", {}) if isinstance(item.get("extracted_info", {}), dict) else {}
    fallback_jd_summary = fallback_jd_summary or "공고 상세 직무 내용을 참조하십시오."
    
    job_keywords = []
    combined_text = (title + " " + fallback_jd_summary).lower()
    for skill in profile.get("skills", []):
        if skill.lower() in combined_text:
            job_keywords.append(f"#{skill}")
    
    if len(job_keywords) < 3:
        job_keywords.extend(["#직무역량", "#자소서작성", "#성장가능성"])
    job_keywords = dedupe_preserve_order(job_keywords)[:5]
    
    career_url = deep_data.get("official_detail_url")
    if not career_url:
        encoded_company = urllib.parse.quote(company)
        career_url = f"https://www.google.com/search?q={encoded_company}+채용+페이지"
    
    requirements = "공고 자격요건 및 전공 요건 참조"
    preferences = "우대 스택 및 동종 업계 경력 우대"
    
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
        if value:
            payload[key] = value

    kw = candidate.get("job_keywords", payload["job_keywords"])
    if not isinstance(kw, list):
        kw = payload["job_keywords"]
    kw = [keyword if str(keyword).startswith("#") else f"#{keyword}" for keyword in kw]
    if len(kw) < 3:
        kw.extend(["#직무역량", "#성장지향", "#자소서팁"])
    payload["job_keywords"] = dedupe_preserve_order(kw)[:5]

    jd_val = str(payload.get("jd_summary", "")).strip()
    image_url = item.get("image_url", "")
    if (not jd_val or "참조" in jd_val or len(jd_val) < 20) and image_url:
        payload["jd_summary"] = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
    elif not jd_val:
        payload["jd_summary"] = "공고 상세 직무 내용을 참조하십시오."

    sal_val = str(payload.get("salary", "")).strip()
    if not sal_val or any(token in sal_val for token in ["협의", "미정", "회사내규", "추후협의"]):
        payload["salary"] = "회사내규에 따름"

    deep_data = item.get("deep_scraped", {}) if isinstance(item.get("deep_scraped", {}), dict) else {}
    official_url = deep_data.get("official_detail_url")
    if official_url:
        payload["company_career_url"] = official_url

    payload["detail_url"] = item.get("detail_url", "")
    payload["deadline"] = item.get("deadline", "~2026.07.05(일)")
    payload["image_url"] = item.get("image_url", "") or candidate.get("image_url", "")

    fit_score, location_matched = calculate_fit_score(item, profile)
    payload["fit_score"] = fit_score
    payload.update(build_architecture_fields(item, payload, location_matched))
    return payload

def analyze_job_with_llm(item, profile):
    company = item.get("company", "")
    title = item.get("title", "")
    image_url = item.get("image_url", "")
    
    deep_data = item.get("deep_scraped", {})
    jd_summary_raw = deep_data.get("jd_summary", "")
    welfare_raw = deep_data.get("welfare_tags", [])
    if isinstance(welfare_raw, list):
        welfare_str = ", ".join(welfare_raw)
    else:
        welfare_str = str(welfare_raw)
        
    company_insight = item.get("company_insight", {})
    
    is_text_poor = not jd_summary_raw or "참조" in str(jd_summary_raw) or len(str(jd_summary_raw)) < 30
    
    fallback_jd_summary = jd_summary_raw
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
    - If the original raw text contains detailed responsibilities/tasks, refine and summarize it cleanly.
    - If the raw text is empty, poor, or just says "공고 참조", "상세 참조", you MUST return this markdown hyperlink exact format using the Fallback Image URL: "<Fallback_Image_URL|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
- job_keywords:
    - Compile 3 to 5 keywords helper for writing self-introduction.
    - Focus on company values, talent traits, key skills, and tips for personal essays.
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
