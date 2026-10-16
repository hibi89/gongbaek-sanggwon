# 공백상권 프로젝트 소스코드 복원본 (2026-10-16)

이 파일은 Google Drive 전국상가 백업 ZIP에서 추출한 경량 소스 복원 패키지입니다.
원본은 변경하지 않았으며, 대형 CSV/SQLite 원본은 포함하지 않습니다.

## 검증
- 분할본 4개 SHA-256: 모두 일치
- 재구성 원본 ZIP SHA-256: ef82b5fa2e85054fb649651ee98f1a05681a5f7904e849d552d32935b241524c 일치
- 원본 ZIP CRC: PASS (144개 내부 항목)
- 원본 내부 상가 데이터 ZIP: SHA-256 일치, CRC PASS, 지역별 CSV 16개
- 원본 SQLite: PRAGMA quick_check = ok, stores 2,772,484, categories 247
- Python unittest: 53/53 PASS, Linux에서 한글 글꼴 경로를 런타임에만 보정한 결과. 원본 코드는 수정하지 않음.
- JS publication gate: 유효 1 / 거부 9 / 총 10 테스트 PASS (Node 22 기반 시험)
- 실제 SQLite 분석: 정상, category 247, 전체 TOP20 20, A TOP10 10, publication_gate=passed

## 주의
- 원본 실행환경은 Windows Python 3.12.14, Node 24.21.0, n8n 2.40.7로 고정돼 있음.
- 이번 환경의 Python 3.13.5/Node 22 테스트는 Windows Production E2E 검증이 아님.
- GitHub에 아직 반영하지 않았음. 기존 저장소를 변경하지 않음.
- 배포/운영에는 로컬 `data/processed/semas_store_20260630.sqlite`와 별도의 API 자격증명이 필요.
- 레거시 `배달상권분석.json`은 사용하지 말 것. 이 경량 패키지에서 제외.
