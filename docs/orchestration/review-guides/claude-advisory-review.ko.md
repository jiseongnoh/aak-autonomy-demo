# Claude Advisory Review — 트리거 안내 (한글)

이 워크플로우(`.github/workflows/claude-advisory-review.yml`)는 PR에 대해 Claude가
**자문(advisory) 리뷰**를 남깁니다. 코드를 직접 고치거나 머지/승인하지 않으며, 최종
결정은 사람이 합니다.

## ⚠️ 자동 트리거는 없습니다 — 직접 트리거하세요

이 키트 버전은 **PR이 열릴 때 자동으로 돌지 않습니다.** (의도된 기본값: 토큰 비용/노이즈
억제 + on-demand 자문 원칙.) 리뷰가 필요하면 아래 둘 중 하나로 **직접 트리거**하세요.

### 방법 1 — PR 코멘트 (가장 간단)

리뷰받고 싶은 PR에 코멘트로 다음을 남깁니다:

```
@claude-review
```

- 코멘트 작성자가 **OWNER / MEMBER / COLLABORATOR** 여야 동작합니다(외부인 무시).
- 1~2분 뒤 봇이 인라인 제안 + 요약 코멘트를 답니다.

### 방법 2 — 수동 실행 (Actions 탭)

`Actions` → `claude-advisory-review` → **Run workflow** → 리뷰할 **PR 번호** 입력.
(닫힌/과거 PR도 가능. `focus`로 리뷰 관점 지정 가능.)

## 리뷰 형태

- **인라인 제안**: 문제가 있는 파일/라인에 직접 코멘트를 달고, 고칠 수 있는 부분은
  GitHub ```suggestion``` 블록으로 제안합니다 → 사람이 **"Apply/Commit suggestion"**
  버튼으로 바로 반영. (봇은 커밋하지 않음 = advisory 유지)
- **요약 코멘트**: 상단에 심각도별 정리 + `CLAUDE-REVIEW:v1` JSON.

## 사전 준비 (1회)

- GitHub Actions secret `CLAUDE_CODE_OAUTH_TOKEN` 등록 필요.
  - Claude Pro/Max/Team/Enterprise 구독 계정에서 `claude setup-token`으로 생성한 값.
  - 레포 소유 계정과 별개 계정이어도 됨(완전 분리).

## 자동 트리거가 필요하면

PR 열릴 때 자동 리뷰를 원하면 `on:`에 `pull_request` 트리거 + 전용 job을 추가하면
됩니다. 단 fork PR은 secret을 못 받으므로 `head.repo.full_name == github.repository`
가드로 걸러야 하고, `pull_request_target`(위험)은 쓰지 마세요. 모든 PR마다 토큰을
소모하므로 `synchronize`는 제외하는 것을 권장합니다.
