# 📁 채용 파이프라인 프로젝트 구조 및 동작 다이어그램

이 문서는 채용 파이프라인(Recruiting Pipeline) 프로젝트의 디렉터리 레이아웃과 구체적인 작업 데이터 흐름을 설명합니다.

---

## 1. 디렉터리 트리 (Directory Tree)

`C:\Users\MyDream\Desktop\git\project`에 위치한 프로젝트의 전체 파일 구조는 다음과 같습니다.

```
project/
├── .claude/                             # 에이전틱 하네스(Agentic Harness) 설정
│   ├── agents/                          # AI 서브에이전트 정의 파일 (.md)
│   │   ├── recruiting-pipeline-agent.md # 메인 루프 에이전트 상세 스펙
│   │   ├── verifier-agent.md            # 자체 QA 및 데이터 검증 규칙
│   │   ├── caveman-agent.md             # 간결하고 직관적인 소통 담당 에이전트
│   │   └── ... (crawling-expert, backend-dev, frontend-dev, web-designer 등)
│   └── skills/                          # 오케스트레이션 스킬 정의
│       ├── recruiting-pipeline-orchestrator/
│       │   └── SKILL.md                 # 오케스트레이터 상태 머신 및 규칙
│       └── caveman-orchestrator/
│           └── SKILL.md                 # 케이브맨 오케스트레이션 설정
├── workspace/
│   └── recruiting-pipeline/             # 핵심 실행 스크립트 디렉터리
│       ├── pipeline.py                  # 코어 루프 컨트롤러 (서브프로세스 실행, 체크포인트 저장)
│       ├── crawler.py                   # FETCH 단계 (채용 사이트 스크래핑)
│       ├── enricher.py                  # ENRICH 단계 (DART 공시 및 국민연금 데이터 결합)
│       ├── scorer.py                    # SCORE 단계 (코사인 유사도 기반 매칭 점수 계산)
│       ├── remind_pipeline.py           # 리마인더 발송 스크립트
│       └── data/
│           └── pipeline_state.json      # 체크포인트 상태 파일 (current_phase, last_processed_id 기록)
├── data/                                # 영구 보존 데이터 및 마스터 데이터베이스
│   ├── recruitment.db                   # SQLite 마스터 DB (채용 공고 저장, UNIQUE 제약조건 및 sent_status 관리)
│   ├── user_profile.json                # 사용자 환경 설정 (희망 기술 스택, 타깃 지역 등)
│   ├── fetch_output.json                # 임시 스크래핑 결과물 (DB 중복 검사용)
│   └── final_recruit_dashboard.json     # Slack으로 전송될 최종 포맷팅된 JSON 데이터
├── _workspace/                          # 파이프라인 단계별 임시(Transient) 출력 디렉터리
│   ├── fetch_output.json                # 중복 제거된 RAW 상태의 채용 공고 데이터
│   ├── enrich_output.json               # DART 기업 정보 및 연금 통계가 추가된 데이터
│   ├── score_output.json                # AI 매칭 점수(유사도)가 부여된 데이터
│   └── verify_output.json               # 최종 검증이 완료된 채용 공고 데이터
├── README.md                            # 전체 시스템 명세 및 적합성 기준 정보
└── Agents.md                            # 루프 에이전트 상세 스펙 및 영구 메모리 규칙

```

---

## 2. 데이터 흐름 및 체크포인트 시퀀스

아래 다이어그램은 `workspace/recruiting-pipeline/` 내부의 스크립트들이 `_workspace/` 디렉터리를 거쳐 데이터를 가공하고, `data/` 내부의 영구 DB와 연동 및 제어되는 전체 흐름을 나타냅니다.

```mermaid
graph TD
    %% 디렉터리 경계 영역 정의
    subgraph ScriptWorkspace ["workspace/recruiting-pipeline/"]
        PL["pipeline.py (메인 루프 드라이버)"]
        CW["crawler.py (1단계: FETCH)"]
        ER["enricher.py (2단계: ENRICH)"]
        SC["scorer.py (3단계: SCORE)"]
        StateJSON["data/pipeline_state.json <br> (체크포인트 및 진행 단계 기록)"]
    end

    subgraph PhaseOutputs ["_workspace/ (임시 출력 디렉터리)"]
        FOut["fetch_output.json"]
        EOut["enrich_output.json"]
        SOut["score_output.json"]
        VOut["verify_output.json"]
    end

    subgraph MasterData ["data/ (영구 상태 및 마스터 데이터)"]
        DB[("recruitment.db <br> (SQLite 마스터 DB)")]
        Profile["user_profile.json <br> (타깃 기술/지역 설정)"]
        FinalJSON["final_recruit_dashboard.json"]
    end

    subgraph AgentHarness [".claude/ (에이전트 정의)"]
        VerifAgent["agents/verifier-agent.md <br> (포맷 및 정합성 검증 규칙)"]
    end

    %% 프로세스 실행 및 데이터 흐름
    PL -->|1. 상태 확인 및 업데이트| StateJSON
    PL -->|2. 서브프로세스 실행| CW
    CW -->|3. 원본 데이터 저장| FOut
    
    %% 중복 제거 단계
    FOut -->|4. 중복 필터링| PL
    DB <-->|회사명/공고명 고유성 및 전송 여부 확인| PL
    
    %% 기업 정보 보강 단계 (Enrichment)
    PL -->|5. 서브프로세스 실행| ER
    EOut -.->|원본 데이터 로드| ER
    ER -->|6. DART/국민연금 통계 결합| EOut
    
    %% AI 매칭 점수 산정 단계 (Scoring)
    PL -->|7. 서브프로세스 실행| SC
    EOut -.->|보강된 데이터 로드| SC
    Profile -.->|사용자 선호도 분석| SC
    SC -->|8. AI 코사인 유사도 분석 및 점수 부여| SOut
    
    %% 검증 루프 (회복탄력성 및 자가 수정 메커니즘)
    PL -->|9. 자체 QA 체크 요청| VerifAgent
    SOut -.->|포맷 오류 및 마감년도 불일치 검사| VerifAgent
    VerifAgent -->|실패: Scorer.py 재실행 (최대 3회)| SC
    VerifAgent -->|성공: 검증 완료 데이터 저장| VOut
    
    %% 데이터 전송 단계 (Dispatch)
    VOut -->|10. 최종 페이로드 복사| FinalJSON
    PL -->|11. 웹훅 발송| Activepieces["Activepieces / Slack"]
    Activepieces -->|전송 성공 시 sent_status = 1 변경| DB
    Activepieces -->|최종 체크포인트 갱신| StateJSON
    
    %% 스타일 정의
    style PL fill:#1a73e8,stroke:#1557b0,color:#fff,stroke-width:2px
    style StateJSON fill:#fef7e0,stroke:#f8c441,color:#000
    style DB fill:#e8f0fe,stroke:#1a73e8,color:#000
    style Profile fill:#e8f0fe,stroke:#1a73e8,color:#000
    style VerifAgent fill:#fce8e6,stroke:#d93025,color:#000
    style Activepieces fill:#e6f4ea,stroke:#137333,color:#000

```

---

## 3. 핵심 컴포넌트 상세 분석

### A. 체크포인트를 활용한 오류 복구 (State Checkpointing)

* **`data/pipeline_state.json`**: 시스템의 진행 상황을 실시간으로 기억하는 메모리 역할을 합니다. 예를 들어, 매칭 점수 산정(`SCORE`) 단계에서 예기치 못한 에러로 파이프라인이 중단되더라도, `pipeline.py`가 재시작될 때 `current_phase: "SCORE"`를 읽어와 수집(`FETCH`) 및 정보 보강(`ENRICH`) 단계를 건너뛰고 오류가 발생한 지점부터 즉시 작업을 재개합니다.
* **자가 수정 메커니즘 (QA Retry)**: `verifier-agent.md`에 정의된 검증 규칙(필수 키 누락 여부, 원본 데이터와 AI 가공 데이터 간의 마감년도 일치 여부 등)을 통과하지 못하면, `pipeline.py`는 자동으로 `scorer.py`를 재호출합니다. 연속 3회 실패할 경우에만 프로세스를 멈추고 에러 로그를 기록합니다.

### B. 철저한 중복 지원 예방

* **SQLite 데이터베이스 (`recruitment.db`)**: 이미 수집했거나 확인한 공고가 다시 처리되는 것을 원천 차단합니다. `UNIQUE(company, title)` 복합 인덱스 제약조건을 통해 데이터베이스 수준에서 중복 유입을 물리적으로 방지합니다.
* **`sent_status` 확인**: 스케줄러는 아직 Slack으로 전송되지 않은 공고(`sent_status = 0`)만 선별하여 정보 보강 및 AI 매칭 단계를 거치도록 제어합니다. 메신저로 전송이 성공적으로 완료되면 해당 값은 즉시 `1`로 업데이트됩니다.

### C. 보안 이미지 우회 및 렌더링 (Imgur Bypass)

* 포스코(POSCO) 사내 인트라넷 등 엄격한 보안 정책이 적용된 사이트의 이미지 주소는 Slack에서 직접 정상적으로 렌더링되지 않는 문제가 발생합니다. 이를 해결하기 위해 파이프라인 내부에서 보안 이미지를 로컬로 자동 다운로드한 후 익명 Imgur 저장소에 업로드합니다. 이후 변환된 고유 URL을 `final_recruit_dashboard.json`에 업데이트하여 Slack 웹훅 발송 시 이미지가 끊김 없이 깔끔하게 보이도록 보장합니다.