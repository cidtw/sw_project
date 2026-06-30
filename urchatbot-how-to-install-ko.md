# `urchatbot` Slack 워크스페이스 설치 가이드

이 문서는 외부 사용자가 `urchatbot`을 자신의 Slack 워크스페이스에 설치하고 사용하는 방법을 안내합니다.

[![Install (for users)](https://img.shields.io/badge/Install%20%28for%20users%29-4A154B?style=for-the-badge&logo=slack&logoColor=white)](https://slack.com/oauth/v2/authorize?client_id=11358846796466.11397955311362&scope=chat:write,commands,app_mentions:read)

## 앱 정보

- 앱 이름: `urchatbot`
- Slack Client ID: `11358846796466.11397955311362`

`Client ID`는 Slack OAuth 설치 과정에서 앱을 식별하는 값입니다. 이 값은 공개해도 괜찮지만, `Client Secret`은 절대 외부에 공유하면 안 됩니다.

## 누가 설치할 수 있나요?

`urchatbot`은 아래 사용자들이 설치할 수 있습니다.

- 워크스페이스 관리자 또는 소유자
- 앱 설치 권한이 있는 일반 멤버
- 관리자 승인 요청을 보낼 수 있는 멤버

회사 또는 조직의 Slack 정책상 외부 앱 설치가 제한되어 있다면, 워크스페이스 관리자의 승인이 먼저 필요할 수 있습니다.

## 설치 링크

위 버튼을 누르거나 아래 직접 링크를 사용하면 됩니다.

```text
https://slack.com/oauth/v2/authorize?client_id=11358846796466.11397955311362&scope=chat:write,commands,app_mentions:read
```

## 사용자 설치 절차

1. `Install (for users)` 버튼을 클릭합니다.
2. 필요하면 Slack 계정으로 로그인합니다.
3. `urchatbot`을 설치할 워크스페이스를 선택합니다.
4. 앱이 요청하는 권한 목록을 확인합니다.
5. `Allow` 또는 `허용` 버튼을 클릭합니다.
6. 설치 완료 후 안내 페이지로 이동합니다.
7. Slack으로 돌아가 `Apps` 목록에서 `urchatbot`을 엽니다.

워크스페이스 정책상 관리자 승인이 필요한 경우에는 `Allow` 대신 `Request to Install`이 표시될 수 있습니다.

## 설치 화면 흐름

### 1. Slack 로그인

설치 대상 워크스페이스에 접근할 수 있는 Slack 계정으로 로그인합니다.

### 2. 워크스페이스 선택

`urchatbot`을 설치할 워크스페이스를 선택합니다.

### 3. 권한 검토

Slack은 `urchatbot`이 요청하는 권한을 표시합니다. 예를 들면 아래와 같습니다.

- 봇으로 메시지 보내기
- 앱 멘션 읽기
- 슬래시 명령어 실행

### 4. 설치 승인

사용자는 권한을 확인한 뒤 `Allow`를 눌러 설치를 완료합니다.

앱 설치가 제한된 워크스페이스라면, 이 단계에서 관리자 승인 요청으로 전환될 수 있습니다.

## 설치 후 첫 사용 방법

1. Slack 왼쪽 사이드바에서 `urchatbot`을 엽니다.
2. App Home 또는 봇 DM의 시작 안내를 확인합니다.
3. 채널에서 사용하려면 봇을 채널에 초대합니다.
4. 지원되는 명령어나 멘션으로 동작을 테스트합니다.

예시:

```text
@urchatbot hello
```

슬래시 명령어를 지원한다면 아래처럼 테스트할 수도 있습니다.

```text
/urchatbot
```

## 채널에서 `urchatbot` 사용하기

워크스페이스에 앱을 설치했다고 해서 모든 채널에 자동으로 추가되지는 않습니다.

특정 채널에서 `urchatbot`을 사용하려면 먼저 해당 채널에 초대해야 할 수 있습니다.

```text
/invite @urchatbot
```

## 문제 해결

### 설치 버튼이 동작하지 않아요

- 앱 설치 링크가 정확한지 확인합니다.
- 외부 설치가 필요하다면 Public Distribution이 활성화되어 있는지 확인합니다.
- Slack 앱 설정과 배포된 백엔드 설정이 일치하는지 확인합니다.

### `Request to Install`이 보여요

- 현재 워크스페이스에서는 관리자 승인이 필요하다는 뜻입니다.
- 워크스페이스 관리자 또는 소유자가 설치를 승인해야 합니다.

### 설치는 됐는데 봇이 응답하지 않아요

- 봇이 올바른 채널에 초대되어 있는지 확인합니다.
- 슬래시 명령어나 이벤트 구독 설정이 올바른지 확인합니다.

## 보안 참고 사항

- `Client ID`는 공개해도 괜찮습니다.
- `Client Secret`은 절대 공개하면 안 됩니다.
- `urchatbot`에 꼭 필요한 최소 권한만 요청하는 것이 좋습니다.
