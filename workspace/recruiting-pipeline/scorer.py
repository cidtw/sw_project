#!/usr/bin/env python3
import json
import os
import sys
import hashlib
import urllib.parse
from openai import OpenAI

DEFAULT_USER_PROFILE = {
    "skills": ["AI", "ML", "Python", "Data Pipeline", "데이터"],
    "location_pref": ["서울", "경기"],
    "education": "대졸"
}

client = None
if os.environ.get("OPENAI_API_KEY"):
    try:
        client = OpenAI()
    except Exception as e:
        print(f"OpenAI client init failed: {e}")

def load_user_profile():
    profile_path = "data/user_profile.json"
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    os.makedirs("data", exist_ok=True)
    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(DEFAULT_USER_PROFILE, f, ensure_ascii=False, indent=2)
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
                
    return min(100, max(0, score)), location_matched

def get_fallback_job_data(item, profile, fallback_jd_summary):
    company = item.get("company", "")
    title = item.get("title", "")
    detail_url = item.get("detail_url", "")
    image_url = item.get("image_url", "")
    deadline = item.get("deadline", "~2026.07.05(일)")
    deep_data = item.get("deep_scraped", {})
    
    job_keywords = []
    combined_text = (title + " " + fallback_jd_summary).lower()
    for skill in profile.get("skills", []):
        if skill.lower() in combined_text:
            job_keywords.append(f"#{skill}")
    
    if len(job_keywords) < 3:
        job_keywords.extend(["#직무역량", "#자소서작성", "#성장가능성"])
    job_keywords = list(set(job_keywords))[:5]
    
    career_url = deep_data.get("official_detail_url")
    if not career_url:
        encoded_company = urllib.parse.quote(company)
        career_url = f"https://www.google.com/search?q={encoded_company}+채용+페이지"
    
    requirements = "공고 자격요건 및 전공 요건 참조"
    preferences = "우대 스택 및 동종 업계 경력 우대"
    
    fit_score, _ = calculate_fit_score(item, profile)
    
    return {
        "company": company,
        "title": title,
        "employment_type": deep_data.get("employment_type", "정규직"),
        "location": ", ".join(item.get("extracted_info", {}).get("location", ["서울"])),
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

def analyze_job_with_llm(item, profile):
    company = item.get("company", "")
    title = item.get("title", "")
    detail_url = item.get("detail_url", "")
    image_url = item.get("image_url", "")
    deadline = item.get("deadline", "~2026.07.05(일)")
    
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
        return get_fallback_job_data(item, profile, fallback_jd_summary)
        
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
        
        jd_val = res_data.get("jd_summary", "")
        if not jd_val or "참조" in jd_val or len(jd_val) < 20:
            if image_url:
                res_data["jd_summary"] = f"<{image_url}|🖼️ 채용 공고 원본 이미지 확인하기 (클릭 시 이동)>"
                
        kw = res_data.get("job_keywords", [])
        if not isinstance(kw, list) or len(kw) < 3:
            res_data["job_keywords"] = ["#직무역량", "#성장지향", "#자소서팁"]
        else:
            res_data["job_keywords"] = [k if k.startswith("#") else f"#{k}" for k in kw[:5]]
            
        sal_val = res_data.get("salary", "").strip()
        if not sal_val or "협의" in sal_val or "미정" in sal_val or "회사내규" in sal_val or sal_val == "추후협의":
            res_data["salary"] = "회사내규에 따름"
            
        res_data["detail_url"] = detail_url
        res_data["deadline"] = deadline
        res_data["image_url"] = image_url if image_url else res_data.get("image_url", "")
        
        official_url = deep_data.get("official_detail_url")
        if official_url:
            res_data["company_career_url"] = official_url
        elif not res_data.get("company_career_url"):
            encoded_company = urllib.parse.quote(company)
            res_data["company_career_url"] = f"https://www.google.com/search?q={encoded_company}+채용+페이지"
        
        fit_score, _ = calculate_fit_score(item, profile)
        res_data["fit_score"] = fit_score
        
        return res_data
    except Exception as e:
        print(f"LLM extraction failed: {e}, using fallback", file=sys.stderr)
        return get_fallback_job_data(item, profile, fallback_jd_summary)

def main():
    print("Starting LLM Matching and Scoring Phase...")
    input_path = "_workspace/enrich_output.json"
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    with open(input_path, "r", encoding="utf-8") as f:
        listings = json.load(f)
        
    profile = load_user_profile()
    scored_results = []
    
    for item in listings:
        company = item.get("company", "")
        title = item.get("title", "")
        print(f"Refining and formatting job data for: {company} - {title}")
        
        refined_data = analyze_job_with_llm(item, profile)
        scored_results.append(refined_data)
        
    output_path = "_workspace/score_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scored_results, f, ensure_ascii=False, indent=2)
    print(f"Saved refined data to {output_path}")

if __name__ == "__main__":
    main()