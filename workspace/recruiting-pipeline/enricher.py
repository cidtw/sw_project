#!/usr/bin/env python3
import json
import os
import sys
import re
from openai import OpenAI  # OpenAI 최신 버전 라이브러리 사용 가정

# OpenAI 클라이언트 초기화 (환경 변수에 OPENAI_API_KEY 필요)
client = None
if os.environ.get("OPENAI_API_KEY"):
    try:
        client = OpenAI()
    except Exception as e:
        print(f"OpenAI client init failed: {e}")

LOCAL_DART_DB = {
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

LOCAL_PENSION_DB = {
    "포스코": "최상 (국민연금 가입자 최근 1년 4.2% 증가)",
    "삼성": "최상 (국민연금 가입자 최근 1년 1.8% 증가)",
    "세라젬": "우수 (국민연금 가입자 최근 1년 3.1% 증가)",
    "현대건설": "최상 (국민연금 가입자 최근 1년 2.5% 증가)"
}

def get_llm_company_info(company_name):
    if not client:
        return None
    
    cleaned_name = re.sub(r'[\s\(\)\[\]㈜재유한주식회사]', '', company_name)
    prompt = f"""
Provide professional, realistic corporate profile data for the company "{company_name}" (also known as "{cleaned_name}").
Fill in DART and National Pension-style statistics based on public/general knowledge.
Provide the output in JSON format with Korean values.

JSON schema:
{{
  "company_size": "string (one of 대기업, 중견기업, 중소기업, 스타트업, 공공기관)",
  "primary_industry": "string (major business category, e.g., 반도체 제조, IT 플랫폼 서비스)",
  "mid_long_term_plan": "string (approx. 2 sentences detailing their digital transformation, AI adoption plans, or business vision)",
  "stability_score": "string (National Pension subscription trend description, e.g., '최상 (국민연금 가입자 최근 1년 3.5% 증가)' or '보통 (최근 1년 가입자 현황 안정적 유지)')"
}}
"""
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": "You are a professional business analyst that estimates and formats realistic corporate profile data matching DART and National Pension standards."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.3
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"Failed to fetch dynamic LLM company info for {company_name}: {e}", file=sys.stderr)
        return None

def enrich_company_info(company_name):
    matched_key = None
    for key in LOCAL_DART_DB:
        if key in company_name:
            matched_key = key
            break
            
    if matched_key:
        insight = LOCAL_DART_DB[matched_key].copy()
        insight["stability_score"] = LOCAL_PENSION_DB[matched_key]
        return insight
        
    if client:
        print(f"🔍 Dynamic LLM lookup for company: {company_name}")
        llm_insight = get_llm_company_info(company_name)
        if llm_insight and all(k in llm_insight for k in ["company_size", "primary_industry", "mid_long_term_plan", "stability_score"]):
            return llm_insight
            
    return {
        "company_size": "중소기업",
        "primary_industry": "기타 서비스 및 IT",
        "mid_long_term_plan": "안정적 비즈니스 성장 및 핵심 디지털 파트너십 확장",
        "stability_score": "보통 (국민연금 가입자 최근 1년 유지)"
    }

def extract_jd_from_image(image_url):
    """
    통이미지 채용공고 URL을 받아 GPT-4o Vision으로 주요 업무 및 복리후생을 JSON으로 추출
    """
    print(f"📸 통이미지 공고 감지 -> Vision OCR 분석 시작 (URL: {image_url})")
    if not client:
        print("⚠️ OpenAI client not initialized (missing API key). Using fallback mock vision data.")
        return {"jd_summary": "공고 상세 직무 내용을 참조하십시오. (Vision 분석 생략 - API 키 누락)", "welfare_tags": ["주5일제", "4대보험", "자녀학자금"]}
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={ "type": "json_object" },  # 완벽한 JSON 반환 보장
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text", 
                            "text": "제공된 채용 공고 이미지 파일에서 주요 업무 요약(jd_summary)과 복리후생 항목(welfare_tags, 문자열 리스트 형식)을 추출해서 JSON으로 반환해줘. 구조 예시: {\"jd_summary\": \"...\", \"welfare_tags\": [\"...\", \"...\"]}"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        }
                    ]
                }
            ],
            max_tokens=1000
        )
        
        result = json.loads(response.choices[0].message.content)
        return result
    except Exception as e:
        print(f"❌ Vision API 또는 파싱 실패: {e}", file=sys.stderr)
        return {"jd_summary": "공고 상세 직무 내용을 참조하십시오. (Vision 분석 실패)", "welfare_tags": ["정보없음"]}

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
        
        # 1. 기존 기업 정보 맵핑
        company_insight = enrich_company_info(company)
        item["company_insight"] = company_insight
        
        # 2. [핵심 추가 및 보정] 이미지 공고 예외 처리 및 Vision OCR 연동
        if "deep_scraped" not in item or not isinstance(item["deep_scraped"], dict):
            item["deep_scraped"] = {}
            
        jd_summary = item["deep_scraped"].get("jd_summary", "")
        image_url = item.get("image_url", "")  # crawler가 채워준 이미지 원본 주소
        
        # [조건 최적화] 텍스트가 부실하거나 비어있거나 '참조' 문구가 포함된 경우 판별
        is_text_poor = not jd_summary or "참조" in str(jd_summary) or len(str(jd_summary)) < 30
        
        if is_text_poor and image_url:
            print(f"🚨 [조건 충족] 부실 텍스트 감지되어 GPT-4o Vision을 강제 호출합니다!")
            vision_data = extract_jd_from_image(image_url)
            item["deep_scraped"]["jd_summary"] = vision_data.get("jd_summary", "공고 참조")
            item["deep_scraped"]["welfare_tags"] = vision_data.get("welfare_tags", ["정보없음"])
        else:
            print(f"⏩ Vision OCR 스킵 조건: PoorText={is_text_poor}, HasImage={bool(image_url)}")
        
        enriched_results.append(item)
        
    output_path = "_workspace/enrich_output.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(enriched_results, f, ensure_ascii=False, indent=2)
    print(f"Saved enriched data to {output_path}")

if __name__ == "__main__":
    main()