#!/usr/bin/env python3
import json
import sys

from common import (
    ENRICH_OUTPUT_PATH,
    FETCH_OUTPUT_PATH,
    init_openai_client,
    normalize_company_name,
    read_json,
    write_json,
)

client = init_openai_client("enricher")

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
    
    cleaned_name = normalize_company_name(company_name)
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
    normalized_company = normalize_company_name(company_name)
    for key in LOCAL_DART_DB:
        normalized_key = normalize_company_name(key)
        if normalized_key and normalized_key in normalized_company:
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
    통이미지 채용공고 URL을 받아 Vision OCR로 JD, 자격요건, 우대요건을 JSON으로 추출
    """
    print(f"📸 통이미지 공고 감지 -> Vision OCR 분석 시작 (URL: {image_url})")
    if not client:
        print("⚠️ OpenAI client not initialized (missing API key). Using fallback mock vision data.")
        return {
            "jd_summary": "",
            "requirements": "",
            "preferences": "",
            "ocr_text": "",
            "welfare_tags": ["정보없음"],
        }
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
                            "text": (
                                "제공된 채용 공고 이미지를 OCR로 읽고 채용공고 핵심 정보를 JSON으로 반환해줘. "
                                "원문을 그대로 길게 복사하지 말고 각 필드는 한국어 한 문장 요약으로 작성해. "
                                "자격요건과 우대요건이 이미지 안에 명시되어 있으면 반드시 분리해 추출하고, 없으면 빈 문자열로 둬. "
                                "JSON schema: "
                                "{\"jd_summary\":\"주요 업무/직무기술서 한 문장\","
                                "\"requirements\":\"자격요건 한 문장\","
                                "\"preferences\":\"우대요건 한 문장\","
                                "\"ocr_text\":\"OCR로 읽은 핵심 원문 발췌\","
                                "\"welfare_tags\":[\"복리후생 키워드\"]}"
                            )
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
        return {
            "jd_summary": "",
            "requirements": "",
            "preferences": "",
            "ocr_text": "",
            "welfare_tags": ["정보없음"],
        }

def main():
    print("Starting Corporate Data Enrichment Phase...")
    input_path = FETCH_OUTPUT_PATH
    if not input_path.exists():
        print(f"Error: Input file {input_path} not found.", file=sys.stderr)
        sys.exit(1)
        
    listings = read_json(input_path, [])
        
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
        requirements = item["deep_scraped"].get("requirements", "")
        preferences = item["deep_scraped"].get("preferences", "")
        image_url = item.get("image_url", "")  # crawler가 채워준 이미지 원본 주소
        
        # [조건 최적화] 텍스트가 부실하거나 비어있거나 '참조' 문구가 포함된 경우 판별
        is_text_poor = not jd_summary or "참조" in str(jd_summary) or len(str(jd_summary)) < 30
        is_structured_poor = not str(requirements or "").strip() and not str(preferences or "").strip()
        
        if image_url and (is_text_poor or is_structured_poor):
            print(f"🚨 [조건 충족] 부실 텍스트 감지되어 GPT-4o Vision을 강제 호출합니다!")
            vision_data = extract_jd_from_image(image_url)
            for field in ["jd_summary", "requirements", "preferences"]:
                value = str(vision_data.get(field, "") or "").strip()
                if value:
                    item["deep_scraped"][field] = value
            ocr_text = str(vision_data.get("ocr_text", "") or "").strip()
            if ocr_text:
                item["deep_scraped"]["ocr_text"] = ocr_text
                if not item["deep_scraped"].get("jd_summary"):
                    item["deep_scraped"]["jd_summary"] = ocr_text
            item["deep_scraped"]["welfare_tags"] = vision_data.get("welfare_tags", ["정보없음"])
        else:
            print(f"⏩ Vision OCR 스킵 조건: PoorText={is_text_poor}, StructuredPoor={is_structured_poor}, HasImage={bool(image_url)}")
        
        enriched_results.append(item)
        
    output_path = ENRICH_OUTPUT_PATH
    write_json(output_path, enriched_results)
    print(f"Saved enriched data to {output_path}")

if __name__ == "__main__":
    main()
