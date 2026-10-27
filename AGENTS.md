> **2026-10-28 해총 전 저장소 최상위 공통 운영 공지 (소유자 승인):** [개발 가속·로컬검증·병목제거 통합 정본](https://github.com/hibi89/hackathon-command-center-2026/blob/main/docs/DEV_ACCELERATION_AND_VERIFICATION_20261028.md)을 새 채팅에서 먼저 확인한다. 현재 활성 개발 4개 슬롯은 **KIC(+DL)·PayPal·UGREEN·LA**만이다. 본 프로젝트는 **임시 개발 대기(폐기·브랜치 삭제 아님)**: 소유자 재개 지시 전 `ㄱ`으로 임의 기능 구현·CI·배포하지 않는다. 활성 총괄의 일반 구현·검증은 **GPT-6 Instant**, 최초 설계·고위험 문제·해총 감독은 높은 추론. 파일 단위 GitHub 왕복·다중 상태 문서 갱신·중복 CI를 축소하며 검증된 기능과 정확한 Git SHA/PASS·FAIL·SKIP만 보고한다. 기존 설계 동결·보안·고객 데이터·외부 서비스·금전/제출 승인 규칙은 유지한다. 본 공지는 제품 기능 변경이나 검증 완료가 아니다.

# AI 에이전트 작업 지침

1. **새 채팅 최우선:** 최신 GitHub main HEAD의 `HANDOFF.md` → `STATUS.md` → `REQUIREMENTS.md` → `설계/00_먼저_읽기.md` 및 실제 코드를 읽는다. 과거 채팅/기억보다 확인된 최신 소스와 테스트를 우선한다.
2. 원본 전국 점포 ZIP/CSV, 대형 SQLite, n8n DB/WAL/SHM, `.env`, Kakao/n8n credential, encryption key, 고객 주소/실행자료를 커밋하거나 외부 전송하지 않는다.
3. 설계/소스 변경 전 최소 2개 접근을 비용·성능·유지보수로 비교하고 사용자 승인 후 구현한다. 기존 로컬 운영 기능 및 백업을 보존한다.
4. Python unittest 53개와 n8n publication gate 모의 10개를 검증한다. Linux에서 Windows 전용 한글 글꼴 경로는 시험 런타임에서만 보정한다.
5. 로컬 테스트 성공, 과거 라이브 테스트, Vercel Ready와 **현재 실서비스 성공**을 구분한다. 현재 Windows n8n/Kakao live E2E, 실제 크몽 상품, 실제 주문은 별도 검증 대상이다.
6. 외부 업로드, 운영 DB 변경, 신규 배포, 권한·보안·자동정리 정책 변경, 크몽 실제 수정·게시·가격 변경은 사용자 명시 승인 없이는 하지 않는다.
7. 같은 실패 방법을 근거 없이 반복하지 않는다. 원인 진단 후 1회 실질적 변경을 시도하고 진전 없으면 다른 접근으로 전환한다.
8. **매 주요 작업 종료/채팅 전환 전에 `STATUS.md`·`HANDOFF.md`를 최신화하고 실제 GitHub 커밋을 확인**한다. 단순 질의나 상태 변경이 없으면 불필요한 커밋 금지. 최신 SHA는 자기 참조 문구가 아니라 GitHub main HEAD에서 확인한다.
