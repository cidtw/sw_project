# AI 맞춤형 채용 파이프라인

JobKorea, Saramin, Incruit 공고를 수집하고, 기업/공고 정보를 정형화한 뒤 Slack Block Kit 기반 챗봇으로 최신 공고와 개인 맞춤 추천을 제공하는 로컬 운영형 서비스입니다.

## 현재 운영 방식

이전 Activepieces Catch Webhook 연동 방식은 제거했습니다. 현재 서비스는 다음 구조로 동작합니다.

1. `pipeline.py`가 채용공고를 수집, 보강, 점수화, 검증합니다.
2. 검증 결과는 `data/final_recruit_dashboard.json`과 SQLite DB에 저장됩니다.
3. `slack_interactive_app.py` FastAPI 서버가 Slack Interactivity, Slash Command, Modal 요청을 직접 처리합니다.
4. 로컬 FastAPI 서버는 ngrok public URL로 Slack에 연결합니다.
5. Slack 메시지는 Activepieces 템플릿이 아니라 Slack Block Kit JSON을 직접 반환합니다.

## 주요 기능

- 잡코리아, 사람인, 인크루트 통합 크롤링
- 잡코리아 이미지형/JavaScript 청크형 공고 OCR/HTML 텍스트 추출
- 자격요건, 우대요건, 직무기술서 핵심 요약
- 기업 컨텍스트 및 직무 키워드 기반 공고 정형화
- SQLite 기반 중복 저장 방지 및 처리 상태 관리
- Slack Slash Command 호출
- Slack Block Kit 버튼 기반 실시간 업데이트, 맞춤 추천, 개인정보 입력/수정, 환경설정
- 사용자 프로필 기반 맞춤 추천 점수 계산
- 경력 구분, 기술 스택, 프로젝트 요약, 어학 점수 기반 추천 보정
- 특정 채용사이트, 고용형태, 경력유형, 마감임박 공고 필터링

## 실행 구조

```text
workspace/recruiting-pipeline/
├── common.py
├── crawler.py
├── enricher.py
├── scorer.py
├── verifier.py
├── pipeline.py
├── chatbot_search.py
├── slack_interactive_app.py
├── slack-launcher-blocks.json
├── slack-search-preferences-modal.json
├── data/                 # git ignore
└── _workspace/           # git ignore
```

## Slack 설정

### Interactivity Request URL

Slack App의 `Interactivity & Shortcuts`에서 아래 URL을 지정합니다.

```text
https://<ngrok-domain>/slack/interactive
```

### Slash Command

Slack App의 `Slash Commands`에서 아래 명령어를 등록합니다.

```text
/recruit
```

Request URL:

```text
https://<ngrok-domain>/slack/command
```

Usage Hint:

```text
업데이트 | 검색 | 프로필 | 설정
```

사용 예:

```text
/recruit
/recruit 업데이트
/recruit 검색
/recruit 프로필
/recruit 설정
```

### 필요한 Slack Bot Token 권한

- `chat:write`
- `commands`
- `users:read`가 필요한 경우 사용자 표시명 확장에 사용

모달을 열기 위해 Slack App의 Interactivity 설정은 반드시 켜져 있어야 합니다.

## 로컬 실행

```powershell
cd C:\Users\MyDream\Desktop\git\project\workspace\recruiting-pipeline
$env:SLACK_BOT_TOKEN="xoxb-..."
python -X utf8 slack_interactive_app.py
```

ngrok:

```powershell
ngrok http 8000 --domain=<ngrok-domain>
```

파이프라인 단독 실행:

```powershell
cd C:\Users\MyDream\Desktop\git\project\workspace\recruiting-pipeline
python -X utf8 pipeline.py
```

## Slack 버튼 기능

- `공고 실시간 업데이트`: `pipeline.py`를 백그라운드 실행하고 최신순 채용 중 공고 10개를 카드 목록으로 표시합니다.
- `맞춤형 채용공고 찾기`: 저장된 개인정보와 환경설정을 기준으로 추천 공고 1건을 표시합니다.
- `다른 추천 채용`: 다음 추천 공고로 카드를 교체합니다.
- `개인정보 확인/수정`: Slack Modal로 사용자 프로필을 저장합니다.
- `입력 정보 전체 삭제`: 저장된 개인정보를 삭제하고 삭제 완료 모달로 전환합니다.
- `환경설정`: 제외 사이트, 제외 키워드, 고용형태, 마감임박 포함 여부, 경력 필터, 푸시 알림 여부를 저장합니다.

## 사용자 프로필 필드

- 연령 및 성별
- 최종 학력 및 전공
- 경력 구분 및 총 경력
- 근무 희망 지역
- 최소 희망 연봉
- 보유 자격증
- 핵심 보유 기술 스택
- 주요 경력/인턴십/프로젝트 한 줄 요약
- 어학 성적 및 보유 점수

## 데이터 흐름

```text
crawler.py
  -> data/recruitment.db
  -> _workspace/fetch_output.json
enricher.py
  -> _workspace/enrich_output.json
scorer.py
  -> _workspace/score_output.json
verifier.py
  -> _workspace/verify_output.json
pipeline.py
  -> data/final_recruit_dashboard.json
  -> pipeline_state.json / DB sent_status 업데이트
slack_interactive_app.py
  -> Slack Block Kit 직접 응답
```

## 삭제한 Activepieces 관련 파일

운영 방식이 ngrok + Slack Block Kit 직접 연결로 바뀌면서 아래 파일을 제거했습니다.

- `workspace/recruiting-pipeline/remind_pipeline.py`
- `workspace/recruiting-pipeline/send-activepieces-test.ps1`
- `workspace/recruiting-pipeline/activepieces-test-payload.json`

함께 제거한 코드:

- `pipeline.py`의 Activepieces webhook dispatch 함수
- `pipeline.py`의 Activepieces payload 전처리 함수
- `common.py`의 webhook 전송 helper `post_json`
- 파이프라인 종료 시 `remind_pipeline.py` 실행 경로

## 검증 명령

```powershell
cd C:\Users\MyDream\Desktop\git\project
python -m py_compile `
  workspace\recruiting-pipeline\common.py `
  workspace\recruiting-pipeline\crawler.py `
  workspace\recruiting-pipeline\enricher.py `
  workspace\recruiting-pipeline\scorer.py `
  workspace\recruiting-pipeline\pipeline.py `
  workspace\recruiting-pipeline\verifier.py `
  workspace\recruiting-pipeline\chatbot_search.py `
  workspace\recruiting-pipeline\slack_interactive_app.py
```

## 운영 파일 기준

현재 GitHub에 남길 운영 파일은 파이프라인 실행 파일, Slack FastAPI 앱, Block Kit JSON, 문서, requirements입니다. 로컬 DB, 중간 산출물, ngrok 프로세스 상태, Slack token, 테스트 payload는 커밋하지 않습니다.
