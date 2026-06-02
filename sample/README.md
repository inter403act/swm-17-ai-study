# PR Review Sample

AI PR Workflow Agent 평가를 위한 샘플 코드입니다.

이 프로젝트는 GitHub PR diff를 기반으로 변경 요약, 위험도 평가, 리뷰 체크리스트 생성을 테스트하기 위해 사용됩니다.

## 테스트용 브랜치 구성

테스트 시나리오에 맞게, 아래 브랜치들에 미리 commit을 생성해뒀으니 PR을 만들어 테스트하시면 됩니다.

```text
develop
├── low/update-readme-overview
├── low/error-message
├── medium/status-filter
├── medium/default-limit
├── high/token-expiration-boundary
└── high/remove-owner-check
```

## 테스트 PR 시나리오

| 위험도 | 브랜치명 | 커밋 메시지 | 변경 내용 |
|---|---|---|---|
| LOW | `low/update-readme-overview` | `docs: update sample project overview` | README 설명 보강 |
| LOW | `low/error-message` | `chore: improve task validation error message` | Task 생성 시 검증 에러 메시지 문구 수정 |
| MEDIUM | `medium/status-filter` | `feat: support task status filter` | Task 목록 조회에 status 필터 추가 |
| MEDIUM | `medium/default-limit` | `refactor: increase default task query limit` | 기본 조회 개수를 20개에서 50개로 변경 |
| HIGH | `high/token-expiration-boundary` | `fix: reject token at exact expiration time` | 인증 토큰 만료 경계값 검증 로직 변경 |
| HIGH | `high/remove-owner-check` | `fix: simplify task completion flow` | Task 완료 시 소유자 검증 로직 제거 |