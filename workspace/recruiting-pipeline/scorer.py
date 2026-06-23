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
        
        # ────────── [수정 및 안전 보정 구간 시작] ──────────
        deep_data = item.get("deep_scraped", {})
        jd_summary = deep_data.get("jd_summary", "공고 참조")
        
        # 원본의 무의미한 문장이 그대로 있거나 비어있다면, 대안 분석 텍스트가 있는지 재검증
        if not jd_summary or "참조하십시오" in jd_summary:
            jd_summary = item.get("analysis", {}).get("jd_summary", jd_summary)

        # 복리후생 리스트 문자열 깔끔하게 결합 변환 처리
        welfare_raw = deep_data.get("welfare_tags", ["정보없음"])
        if isinstance(welfare_raw, list):
            welfare_str = ", ".join(welfare_raw)
        else:
            welfare_str = str(welfare_raw)
        # ────────── [수정 및 안전 보정 구간 끝] ────────────
        
        # Build analysis
        analysis = {
            "job_category": item.get("extracted_info", {}).get("job_category", "기타"),
            "location_score": location_score,
            "jd_summary": jd_summary,  # ◀ 보정된 변수 맵핑
            "welfare": welfare_str     # ◀ 보정된 변수 맵핑
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
            "detail_url": item.get("detail_url", ""), # ◀ 추가
            "deadline": item.get("deadline", "~2026.07.05(일)"),
            "fit_score": fit_score,
            "analysis": analysis,
            "company_insight": company_insight,
            "image_url": item.get("image_url", "") # ◀ 이 값이 Activepieces로 넘어갑니다.
        }
        scored_results.append(refined_item)
        
    output_path = "_workspace/score_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(scored_results, f, ensure_ascii=False, indent=2)
    print(f"Saved scored data to {output_path}")

if __name__ == "__main__":
    main()