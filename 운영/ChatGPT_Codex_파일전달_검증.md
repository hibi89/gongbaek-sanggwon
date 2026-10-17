# ChatGPT → GitHub → Codex 바이너리 파일 전달 (안전한 우회 경로)

작성: 2026-10-17. **Google Drive 직접 로컬 다운로드 장애가 완전히 해결된 것은 아니다.** 이 문서는 실무적으로 동일한 산출물을 사용자 수동 다운로드 없이 전달하는 Git 우회책이다.

## 테스트 결론과 접근 2안 비교

| 방식 | 검증 결과 | 비용·속도·유지보수 |
|---|---|---|
| **A. Google Drive 연결 → Codex 로컬 파일** | 메타데이터 조회·sediment 참조 생성은 성공, Codex에서 임시 다운로드 호스트 DNS 실패. 파일 무결성 미검증 | 별도 개발 필요 없으나 현재 연결 장애, 재시도 반복 비권장 |
| **B. ChatGPT 검증 → 비공개 GitHub 보관 → Codex Git CLI 바이너리 추출** | ChatGPT에서 ZIP을 GitHub에 저장·Git blob 크기/SHA 확인. 별도 실험용 Git 원격→로컬 clone/fetch→바이너리 추출→SHA·CRC·manifest **실행 테스트 PASS**. 실제 사용자 Windows Codex 환경은 아직 재검증 필요 | 무료·가벼운 판매 자료(1MB)에 적합, 기존 인증된 Git 재사용. 대용량 원본 DB/CSV·비밀키 업로드 불가 |

## 확정 파일

- GitHub 비공개 저장소 `hibi89/gongbaek-sanggwon`, `main`
- 바이너리: `판매자료/크몽_최종등록_20261017/KMONG_840642_FINAL_CORRECTED_247_20261017.zip`
- 전용 검증기: `scripts/transfer_verified_kmong_asset.py`
- 크기 **1,061,871바이트**
- SHA-256 `13dafbac46b3ef7026bbac4b6bb9229183e7c49fda03640fb66425f07f5581cd`
- Git blob SHA-1 `9e5ac1caa20676be27eaf7e59688f6bf8ecbeb33`
- ZIP 15개 파일, manifest 14개 파일 해시, ZIP CRC 모두 확인 후에만 로컬 저장
- 기본 출력은 `%LOCALAPPDATA%\GapAnalysis\verified_imports\KMONG_840642_FINAL_CORRECTED_247_20261017.zip`이며 원본 프로젝트 코드·n8n·DB를 변경하지 않는다.

## Codex의 Windows 로컬 검증 절차

기존 사용자 PC 프로젝트 위치 `E:\배달상권분석\배달상권분석`를 기준으로 한다. **사용자에게 ZIP을 다시 다운로드·첨부하라고 요청하지 말 것.**

1. 프로젝트 Git 상태와 origin 접근 권한을 읽기 전용 확인한다. `git fetch --no-tags origin main`으로 `FETCH_HEAD`만 갱신한다. `git pull`, `reset --hard`, `checkout` 금지.
2. 로컬 작업트리에 새 파일을 덮어쓰기하지 않고 `FETCH_HEAD:scripts/transfer_verified_kmong_asset.py` 소스만 `git show`로 **Python `subprocess.check_output` 바이너리 출력을 사용해** %TEMP%에 저장한다. PowerShell의 Git 바이너리 `>` 리디렉션으로 ZIP 내용을 저장하는 방식은 금지.
3. 기존 Python 3.12 실행기로 임시 검증기를 `--repo E:\배달상권분석\배달상권분석 --no-fetch` 인자를 주어 실행한다.
4. `status=PASS`, size=1061871, SHA-256 일치, CRC PASS, manifest 14/14 PASS, 실제 `saved_to` 존재와 파일 해시를 확인한다.
5. 실패하면 단계별 원인(원격 Git 접근 문제, .git 미존재, Git blob 부재, SHA/CRC 오류, Windows 로컬 폴더 권한)을 구분해 보고한다. 실패한 Git 인증을 반복·우회하지 말 것.
6. 민감정보·원본 대용량 DB·고객 주문 데이터는 절대로 외부 업로드하지 않는다. 크몽 #840642/#840848, 운영 n8n은 변경하지 않는다.

Codex가 직접 실행할 수 있는 PowerShell 예시:

```powershell
$repo = 'E:\배달상권분석\배달상권분석'
$py = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
if (-not (Test-Path -LiteralPath $py)) { throw '기존 Python 실행 파일 경로 확인 필요' }

& git -C $repo fetch --no-tags origin main
if ($LASTEXITCODE -ne 0) { throw 'Git fetch 실패: 기존 Git 인증 및 remote를 확인할 것' }

& $py -c 'import subprocess,sys,tempfile,pathlib; repo=sys.argv[1]; p=pathlib.Path(tempfile.gettempdir())/"kmong-verified-transfer.py"; p.write_bytes(subprocess.check_output(["git","-C",repo,"show","FETCH_HEAD:scripts/transfer_verified_kmong_asset.py"])); print(p)' $repo
if ($LASTEXITCODE -ne 0) { throw '전달 스크립트 추출 실패' }

& $py (Join-Path $env:TEMP 'kmong-verified-transfer.py') --repo $repo --no-fetch
if ($LASTEXITCODE -ne 0) { throw '최종 ZIP 검증 실패' }
```

*참고: 위 `$py` 경로는 기존 현장 경로를 전제로 한다. 경로가 바뀌었으면 현재 승인된 Python 실행 파일 경로만 확인하고, 무단 신규 설치/환경 재구성을 하지 않는다.*

## ChatGPT 측 검증 증거

- 공식 검증 ZIP 원본 bytes=1,061,871, SHA-256 일치.
- GitHub에 실파일 업로드되어 Git object SHA-1과 byte size 확인.
- Linux 격리 환경에서 같은 ZIP을 Git repo의 `main`에 커밋, bare remote → client clone, `git fetch origin main` 후 `git cat-file blob`으로 추출; **크기·SHA256·ZIP CRC·manifest 14/14 통과**.
- 동일 파일 재실행도 PASS, 다른 내용이 이미 있는 출력 파일 **덮어쓰기 차단 PASS**.
- 이것은 **코덱스 Windows PC 환경의 실측 성공이 아니다.** 위 실측 단계가 완료되어야 최종 사용자 PC 전달 PASS라 판정한다.

## 100MB 이상 파일

GitHub 일반 저장소는 대형 원본 CSV/SQLite 전달 경로가 아니다. 대용량 파일은 Google Drive에서 사용자의 Windows에 설치된 **Google Drive for desktop의 드라이브 마운트/동기화** 방식이 가능한지 Codex가 읽기 전용으로 확인한 뒤 파일이 실제 로컬에서 열리고 SHA-256 검증 가능한 경우에만 사용한다. 설치·로그인·공유 범위 변경은 사용자 승인 필요. Drive 연결의 `sediment://` 참조만 보고 Windows 파일이 있다고 단정하지 않는다.
