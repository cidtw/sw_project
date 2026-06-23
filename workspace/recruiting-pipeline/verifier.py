#!/usr/bin/env python3
import json
import os
import re
import sys

def main():
    print("--- Running standalone verifier.py ---")
    input_path = "_workspace/score_output.json"
    raw_path = "_workspace/fetch_output.json"
    verify_output_path = "_workspace/verify_output.json"
    
    if not os.path.exists(input_path) or not os.path.exists(raw_path):
        print("Missing score output or raw listings.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(input_path, "r", encoding="utf-8") as f:
            scored_data = json.load(f)
        with open(raw_path, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
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
            "detail_url", "company_career_url", "image_url"
        ]
        missing_keys = [k for k in required_keys if k not in item]
        if missing_keys:
            errors.append(f"Item {idx} missing keys: {missing_keys}")
            continue
            
        # Rule 4-2: Data consistency check
        raw_item = next((r for r in raw_data if r["company"] == item["company"] and r["title"] == item["title"]), None)
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
            os.makedirs(os.path.dirname(verify_output_path), exist_ok=True)
            with open(verify_output_path, "w", encoding="utf-8") as f:
                json.dump(scored_data, f, ensure_ascii=False, indent=2)
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
