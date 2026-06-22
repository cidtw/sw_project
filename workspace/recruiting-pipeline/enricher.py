#!/usr/bin/env python3
import json
import os
import sys

MOCK_DART_DB = {
    "포스코": {
        "company_size": "대기업",
        "primary_industry": "철강 및 신소재 제조",
        "mid_long_term_plan": "생산 공정 전반의 초자동화를 위한 인공지능(AI) 인프라 도입 및 디지털 트랜스포메이션 가속화"
    },
    "삼성": {
        "company_size": "대기업",
        "primary_industry": "전자 및 정보 기술",
        "mid_long_term_plan": "지능형 반도체 및 헬스케어 디바이스 글로벌 에코시스템 선도"
    },
    "세라젬": {
        "company_size": "중견기업",
        "primary_industry": "의료기기 및 헬스케어 가전",
        "mid_long_term_plan": "홈 메디컬 디바이스 라인업 강화 및 글로벌 홈 헬스케어 플랫폼 도약"
    },
    "현대건설": {
        "company_size": "대기업",
        "primary_industry": "종합 건설 및 해상 에너지 개발",
        "mid_long_term_plan": "해상풍력 및 신재생에너지 인프라 포트폴리오 다각화"
    }
}

MOCK_PENSION_DB = {
    "포스코": "최상 (국민연금 가입자 최근 1년 4.2% 증가)",
    "삼성": "최상 (국민연금 가입자 최근 1년 1.8% 증가)",
    "세라젬": "우수 (국민연금 가입자 최근 1년 3.1% 증가)",
    "현대건설": "최상 (국민연금 가입자 최근 1년 2.5% 증가)"
}

def enrich_company_info(company_name):
    # Find matching keyword
    matched_key = None
    for key in MOCK_DART_DB:
        if key in company_name:
            matched_key = key
            break
            
    if matched_key:
        insight = MOCK_DART_DB[matched_key].copy()
        insight["stability_score"] = MOCK_PENSION_DB[matched_key]
        return insight
        
    # Default fallback values
    return {
        "company_size": "중소기업",
        "primary_industry": "기타 서비스 및 IT",
        "mid_long_term_plan": "안정적 비즈니스 성장 및 핵심 디지털 파트너십 확장",
        "stability_score": "보통 (국민연금 가입자 최근 1년 유지)"
    }

def main():
    print("Starting Corporate Data Enrichment Phase...")
    input_path = "_workspace/fetch_output.json"
    if not os.path.exists(input_path):
        print(f"Error: Input file {input_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    with open(input_path, "r", encoding="utf-8") as f:
        listings = json.load(f)
        
    enriched_results = []
    for item in listings:
        company = item.get("company", "")
        print(f"Enriching company context for: {company}")
        company_insight = enrich_company_info(company)
        item["company_insight"] = company_insight
        enriched_results.append(item)
        
    output_path = "_workspace/enrich_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched_results, f, ensure_ascii=False, indent=2)
    print(f"Saved enriched data to {output_path}")

if __name__ == "__main__":
    main()
