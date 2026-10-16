# 원본/샘플 데이터 위치

- 원본 보관: 사용자의 Google Drive / 로컬 파일 `공백상권_전국상가_원본` (약 630MiB ZIP); GitHub에서는 제외.
- 로컬 작업 백업 루트: `배달상권분석/` (GitHub에는 저장소 루트로 경로 정리)
- 전국 점포 공식 원천 ZIP: 2026-06-30 기준, `data/raw/semas_store_20260630.zip` (복구 후 로컬에만 존재)
- 분석 SQLite: `data/processed/semas_store_20260630.sqlite` (로컬에만 존재)
- 안전한 원본 데이터 메타: `data/raw/manifest_20260630.json`, `data/processed/snapshot_validation_20260630.json`
- 비식별 테스트 fixture: `data/results/*.json`; 샘플 PDF/XLSX: `output/samples/gap-271/`

로컬 원본의 최신성 및 정확성은 공식 데이터 제공처와 비교해서 별도 검증해야 합니다. Google Drive 공유 권한은 사용자가 검토해야 합니다.
