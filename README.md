# AAK Autonomy Demo — 자율 리뷰·수정, 단 머지는 사람

에이전트 온보딩 세션의 진행자 데모용 저장소입니다.
**메시지: 리뷰와 수정은 자동이 될 수 있다. 머지(승인)는 사람이다.**

## 데모 시나리오 (5분)

1. **자동 리뷰** — PR을 열면:
   - `request-copilot-review`가 즉시 Copilot 리뷰를 자동 요청합니다 (사람 개입 0).
   - PR에 `@claude-review` 코멘트를 달면 Claude advisory 리뷰가 코멘트로 달립니다.
2. **자율 수정** — PR/이슈에 `@claude <지시>` 코멘트를 달면
   claude-code-action이 브랜치에 직접 커밋해서 고칩니다.
3. **사람 게이트** — 그 무엇도 스스로 머지되지 않습니다. Approve/Merge 버튼은 화면 앞의 사람 것.

## 준비 (1회)

```bash
gh secret set ANTHROPIC_API_KEY --repo jiseongnoh/aak-autonomy-demo   # Claude 리뷰·수정용
```
Copilot 자동 리뷰 요청은 계정에 Copilot 구독이 있으면 그대로 동작합니다.

## 시드 브랜치

- `agent/expiry-bug` — PR로 미리 열어 둠 (자동 리뷰는 `agent/*` 브랜치 PR에만 적용됩니다 — kit 정책): 리뷰가 잡아야 할 버그 포함 (결과 미리보기용)
- `agent/mask-bug` — 세션에서 **라이브로 PR을 여는** 용도: `gh pr create --head agent/mask-bug --fill`

이 저장소의 리뷰 워크플로·게이트 문서는 [agent-automation-kit](https://github.com/jiseongnoh/agent-automation-kit) v0.2.0 (review-only 모드)에서 설치됐습니다.
