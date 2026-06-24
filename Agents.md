# 무인 채용 파이프라인 루프 에이전트 지침

## 목적

채용 공고 수집, 기업 컨텍스트 보강, 사용자 프로필 기반 스코어링, 검증, Slack Block Kit 직접 응답까지의 전체 사이클을 로컬 파이프라인과 FastAPI Slack 앱으로 운영한다.

Activepieces 연동은 사용하지 않는다. Slack 연동은 ngrok public URL을 통해 `slack_interactive_app.py`가 직접 처리한다.

## 루프 환경

- 작업 디렉터리: `workspace/recruiting-pipeline`
- 영구 상태: `data/pipeline_state.json`
- 수집 DB: `data/recruitment.db`
- Slack 사용자 DB: `data/slack_user_profiles.db`
- 중간 산출물: `_workspace/*.json`

## 상태 관리

루프 시작 시 `pipeline_state.json`을 읽고 아래 값을 기준으로 재개한다.

- `current_phase`
- `last_processed_id`
- `sent_job_ids`
- `user_profile_hash`

각 단계 진입 시 `current_phase`를 즉시 저장한다.

```text
FETCH -> ENRICH -> SCORE -> VERIFY -> DISPATCH -> IDLE
```

현재 `DISPATCH`는 외부 webhook 전송이 아니라, `verify_output.json`을 `final_recruit_dashboard.json`으로 확정하고 처리 완료 상태를 기록하는 단계다.

## 단계별 역할

### FETCH

- `crawler.py`로 JobKorea, Saramin, Incruit 공고를 수집한다.
- 공고는 SQLite `jobs` 테이블에 저장한다.
- 미처리 공고는 `_workspace/fetch_output.json`으로 출력한다.
- `sent_job_ids`와 DB 중복키를 이용해 이미 처리한 공고를 제외한다.

### ENRICH

- `enricher.py`로 기업 규모, 산업, 안정성, 성장 맥락을 보강한다.
- 로컬 DART/국민연금 캐시를 우선 사용한다.
- OpenAI API가 설정된 경우에만 LLM 보강을 사용한다.

### SCORE

- `scorer.py`로 공고를 Slack payload schema에 맞춰 정형화한다.
- 자격요건, 우대요건, 직무기술서는 원문 전체가 아니라 핵심 요약으로 압축한다.
- 사용자 프로필과 공고 내용을 비교해 `fit_score`를 계산한다.

### VERIFY

- `verifier.py`로 필수 필드, 타입, 마감일 정합성, 부실 fallback 문구, placeholder 키워드를 검사한다.
- 실패 시 `scorer.py`를 재실행하며 최대 3회까지 보정한다.

### DISPATCH

- 외부 서비스로 전송하지 않는다.
- `verify_output.json`을 `final_recruit_dashboard.json`으로 저장한다.
- 처리 완료 공고의 `sent_job_ids`, `last_processed_id`, DB `sent_status`를 갱신한다.
- Slack 화면 출력은 `slack_interactive_app.py`가 `/slack/interactive`와 `/slack/command`에서 직접 처리한다.

## Slack 직접 연동

### FastAPI endpoints

- `POST /slack/interactive`: 버튼과 모달 interaction 처리
- `POST /slack/command`: `/recruit` slash command 처리
- `GET /slack/launcher-blocks`: 런처 Block Kit JSON 반환
- `GET /health`: 상태 확인

### Slash Command

```text
/recruit
/recruit 업데이트
/recruit 검색
/recruit 프로필
/recruit 설정
```

### 환경 변수

- `SLACK_BOT_TOKEN`
- 선택: `OPENAI_API_KEY`

토큰과 DB, 중간 산출물은 커밋하지 않는다.
