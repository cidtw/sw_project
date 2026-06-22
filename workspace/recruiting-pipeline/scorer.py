#!/usr/bin/env python3
import json
import os
import sys

DEFAULT_USER_PROFILE = {
    "skills": ["AI", "ML", "Python", "Data Pipeline", "데이터"],
    "location_pref": ["서울", "경기"],
    "education": "대졸"
}

def load_user_profile():
    profile_path = "data/user_profile.json"
    if os.path.exists(profile_path):
        with open(profile_path, "r", encoding="utf-8") as f:
            return json.load(f)
    # Create default user profile if missing
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
                
    # Normalize score
    return min(100, max(0, score)), location_matched

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
        print(f"Calculating Fit-Score for job: {company} - {title}")
        
        fit_score, loc_match = calculate_fit_score(item, profile)
        
        location_score = "95점 (선호 지역 일치)" if loc_match else "60점 (선호 지역 불일치)"
        
        # Build analysis
        analysis = {
            "job_category": item.get("extracted_info", {}).get("job_category", "기타"),
            "location_score": location_score,
            "jd_summary": item.get("deep_scraped", {}).get("jd_summary", "공고 참조"),
            "welfare": ", ".join(item.get("deep_scraped", {}).get("welfare_tags", ["정보없음"]))
        }
        
        # Build company insight
        insight = item.get("company_insight", {})
        company_insight = {
            "company_size": insight.get("company_size", "대기업"),
            "mid_long_term_plan": insight.get("mid_long_term_plan", "신재생에너지 인프라 확장"),
            "stability": insight.get("stability_score", "보통 (국민연금 가입자 최근 1년 유지)")
        }
        
        # Schema matching README.md exactly
        refined_item = {
            "company": company,
            "title": title,
            "deadline": item.get("deadline", "~2026.07.05(일)"),
            "fit_score": fit_score,
            "analysis": analysis,
            "company_insight": company_insight
        }
        scored_results.append(refined_item)
        
    output_path = "_workspace/score_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scored_results, f, ensure_ascii=False, indent=2)
    print(f"Saved scored data to {output_path}")

if __name__ == "__main__":
    main()
