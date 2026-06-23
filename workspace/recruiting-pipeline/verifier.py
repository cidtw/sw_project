#!/usr/bin/env python3
import re
import sys

from common import FETCH_OUTPUT_PATH, SCORE_OUTPUT_PATH, VERIFY_OUTPUT_PATH, read_json, write_json

def main():
    print("--- Running standalone verifier.py ---")
    input_path = SCORE_OUTPUT_PATH
    raw_path = FETCH_OUTPUT_PATH
    verify_output_path = VERIFY_OUTPUT_PATH
    
    if not input_path.exists() or not raw_path.exists():
        print("Missing score output or raw listings.", file=sys.stderr)
        sys.exit(1)
        
    try:
        scored_data = read_json(input_path, [])
        raw_data = read_json(raw_path, [])
    except Exception as e:
        print(f"Failed to read json files: {e}", file=sys.stderr)
        sys.exit(1)
        
    errors = []
    
    # Rule 4-1 & Rule 4-2 check per item
    for idx, item in enumerate(scored_data):
        # Rule 4-1: Format Verification
        required_keys = [
            "company", "title", "employment_type", "location", "salary", 
            "requirements", "preferences", "jd_summary", "job_keywords", 
            "detail_url", "company_career_url", "deadline", "image_url",
            "fit_score", "analysis", "company_insight"
        ]
        missing_keys = [k for k in required_keys if k not in item]
        if missing_keys:
            errors.append(f"Item {idx} missing keys: {missing_keys}")
            continue

        if not isinstance(item.get("fit_score"), int) or not 0 <= item["fit_score"] <= 100:
            errors.append(f"Item {idx} invalid fit_score: {item.get('fit_score')}")

        if not isinstance(item.get("job_keywords"), list) or len(item["job_keywords"]) < 3:
            errors.append(f"Item {idx} invalid job_keywords: {item.get('job_keywords')}")

        analysis = item.get("analysis")
        if not isinstance(analysis, dict):
            errors.append(f"Item {idx} analysis must be an object")
        else:
            missing_analysis = [k for k in ["job_category", "location_score", "jd_summary", "welfare"] if k not in analysis]
            if missing_analysis:
                errors.append(f"Item {idx} missing analysis keys: {missing_analysis}")

        company_insight = item.get("company_insight")
        if not isinstance(company_insight, dict):
            errors.append(f"Item {idx} company_insight must be an object")
        else:
            missing_insight = [k for k in ["company_size", "mid_long_term_plan", "stability"] if k not in company_insight]
            if missing_insight:
                errors.append(f"Item {idx} missing company_insight keys: {missing_insight}")
            
        # Rule 4-2: Data consistency check
        raw_item = next((r for r in raw_data if r.get("company") == item["company"] and r.get("title") == item["title"]), None)
        if raw_item:
            raw_deadline = raw_item.get("deadline", "")
            scored_deadline = item.get("deadline", "")
            raw_years = re.findall(r"\d{4}", raw_deadline)
            scored_years = re.findall(r"\d{4}", scored_deadline)
            if raw_years and scored_years and raw_years[0] != scored_years[0]:
                errors.append(f"Item {idx} year mismatch: Raw={raw_years[0]}, Scored={scored_years[0]}")
    
    if not errors:
        print("Verification passed! Rule 4-1 and Rule 4-2 satisfied.")
        try:
            write_json(verify_output_path, scored_data)
        except Exception as e:
            print(f"Failed to write verify output: {e}", file=sys.stderr)
            sys.exit(1)
        sys.exit(0)
    else:
        print("Verification failed with errors:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
