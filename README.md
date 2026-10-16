# 공백상권 (Gongbaek Sanggwon)

공공데이터 기반 전국 점포 분포 분석 프로젝트의 복원 소스입니다. 기준 데이터는 2026-06-30 소상공인시장진흥공단 전국 상가 자료이며, 검증 대상 업종은 247개입니다.

## 현재 기능

- 주소 1건을 Kakao Local API로 좌표 변환하고 로컬 Python 분석기가 반경 1~5km 점포 수를 조회
- 전체 업종의 희소도 TOP20 및 A 태그 업종의 TOP10 계산 (수요·매출·수익성 예측 아님)
- n8n 워크플로 JSON: `배달상권분석_공백상권_V1.json`
- 고객 납품용 PDF/XLSX 생성, 발행 게이트 및 만료 자료 정리 CLI

## 시작하기

운영 환경: Windows, Python 3.12.14, Node.js 24.21.0, n8n 2.40.7. `requirements.lock`을 사용하세요.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

분석기/워크플로 실행 전 [로컬 실행 안내](설계/08_로컬실행_준비.md)와 [먼저 읽기](설계/00_먼저_읽기.md)를 읽으세요. 운영 토큰과 Kakao API credential을 워크플로 JSON 또는 GitHub에 저장하지 마세요.

## 데이터 보관 분리

원본 CSV ZIP과 SQLite(전국 점포 2,772,484개)는 용량 및 보안 이유로 GitHub에 없습니다. 기존 Google Drive 원본과 로컬 원본을 유지하세요. GitHub에는 분석 코드, 스키마, 비식별 샘플, 경량 검증 메타데이터만 포함합니다. 관련 파일 목록은 `DATA_SOURCES.md`에 있습니다. SQLite가 없는 환경에서는 전체 전국 지리 분석 및 운영 `/health ready` 확인이 불가능합니다.

## 테스트

- Windows 검증 기준: `python -m unittest discover -s tests -v` (53개)
- n8n 발행 게이트: `node scripts/verify_publication_gate.cjs` (10개 시나리오)
- Linux에서 Windows 전용 `C:\Windows\Fonts\malgun.ttf`가 없으면 PDF 출력 통합 테스트 1건은 실패할 수 있습니다. 운영용 글꼴 경로를 임의로 바꾸지 마세요.

## 범위와 현재 검증

2026-10-16 복원 시, 비식별 테스트 자료를 보강한 소스에 대해 테스트 53/53 및 n8n 게이트 10/10 통과(테스트 환경에서만 글꼴 경로 재지정). 실서비스 Windows/n8n 연동, 현재 API 연결, 실제 고객 납품은 재검증되지 않았습니다. 자세한 범위는 [STATUS.md](STATUS.md)를 참조하세요.
