# ChatGPT → Codex 자동 파일 전달: 운영 폴더에 .git 없는 경우

갱신: 2026-10-17. **사용자 PC Windows 코덱스의 성공은 아직 확인되지 않았다.** 아래 방법은 Git 저장소가 아닌 기존 분석 운영 폴더를 수정하지 않고, 임시 디렉터리에서 자산만 가져온다.

## 원인과 해결책

기존 전달 스크립트는 `E:\배달상권분석\배달상권분석` 폴더에 `.git`이 있다고 가정했다. Codex 보고상 그 경로에는 `.git`이 없었다. 운영 프로그램 파일이 있는 폴더는 반드시 Git worktree일 필요가 없다.

새 `scripts/transfer_verified_kmong_asset.py`에는 **운영 폴더에 .git이 필요 없다**. (1) 격리된 OS 임시 폴더에 비공개 GitHub 저장소를 `--no-checkout --depth 1`로 일시 clone → Git 원본 blob bytes 추출; 실패하면 (2) 이미 `gh`가 인증돼 있을 때 GitHub API에서 해당 blob의 base64 원본 bytes를 받는 방법을 시도한다. clone 폴더는 작업이 끝나면 삭제된다. `git pull`·운영 폴더 초기화·파일 덮어쓰기 없음. `gh`가 없다면 우회단계 실패를 정상적으로 보고한다.

## 코덱스가 실제 Windows에서 할 일

1. **비공개 GitHub 연결로** `hibi89/gongbaek-sanggwon` `main`의 UTF-8 소스 파일 `scripts/transfer_verified_kmong_asset.py`를 조회한다. 바이너리 ZIP을 GitHub 연결으로 직접 fetch하지 않는다.
2. 조회한 Python 스크립트 텍스트를 **코덱스가 직접** `$env:TEMP\transfer_verified_kmong_asset.py`에 정확한 UTF-8로 저장한다. 사용자가 다운로드하거나 파일을 붙여넣을 필요 없다. GitHub Git blob SHA-1 `17759452ba9c7cbf603c5813687c4b51a78880d3`과 기록을 대조한다.
3. 설치된 기존 Python 3.12.14로 아래 명령을 실행한다. **`--repo`나 `--no-fetch`를 넣지 않는다.** 프로그램이 임시 Git clone과 `gh` fallback을 알아서 처리한다.

```powershell
$py = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$script = Join-Path $env:TEMP 'transfer_verified_kmong_asset.py'
if (!(Test-Path -LiteralPath $py)) { throw '기존 Python 실행 파일 경로 확인 필요' }
if (!(Test-Path -LiteralPath $script)) { throw '먼저 GitHub 연결로 Python 스크립트를 임시 폴더에 저장할 것' }
& $py $script
if ($LASTEXITCODE -ne 0) { throw '자동 파일 수신 실패. JSON 오류 단계 확인. 동일 방법 반복 금지.' }
```

4. `PASS`라면 생성 파일 **`%LOCALAPPDATA%\GapAnalysis\verified_imports\KMONG_840642_FINAL_CORRECTED_247_20261017.zip`**의 실제 파일 크기, SHA-256, ZIP CRC, manifest 14/14를 확인한다. 파일은 실사용 전까지 검증용 별도 위치에 보관한다.
5. 두 방법 모두 실패하면 **Windows GitHub 인증/네트워크 차단**을 구분해 보고한다. `git`은 비공개 저장소 접근 권한이 필요하며 코덱스의 GitHub 연결 인증이 Windows Git CLI에 자동 전달된다는 보장은 없다. 비밀 토큰 출력·로그인 우회·계정설정 무단 변경 금지. 이후에는 로그인된 GitHub 웹 브라우저를 통한 코덱스의 직접 파일 저장, `gh auth` 연결(사용자 승인 필요), Drive 데스크톱 동기화 중 정책상 허용되는 방법을 검토한다.
6. 운영 n8n/DB, 크몽 #840642/#840848, 기존 분석 코드·워크트리는 모두 변경하지 않는다.

## 승인된 ZIP 원본

- 비공개 GitHub `main`: `판매자료/크몽_최종등록_20261017/KMONG_840642_FINAL_CORRECTED_247_20261017.zip`
- 크기 1,061,871바이트
- SHA-256 `13dafbac46b3ef7026bbac4b6bb9229183e7c49fda03640fb66425f07f5581cd`
- Git blob SHA-1 `9e5ac1caa20676be27eaf7e59688f6bf8ecbeb33`
- ZIP CRC PASS / manifest 14/14 PASS

## 이 대화의 독립 실행 시험 증거

- ChatGPT 격리 Linux에서 실제 최종 ZIP을 사용해 테스트용 `main` Git 저장소와 bare remote를 만들었다.
- **작업 디렉터리에 .git 없이**, 임시 clone → `git cat-file` 원본 bytes → SHA-256/ZIP CRC/manifest 14개 모두 통과했다.
- 동일 파일 두 번째 실행 PASS, 다른 내용의 기존 파일 덮어쓰기 거부 PASS.
- 테스트된 소스 파일을 GitHub에 교체 업로드한 뒤 Git blob SHA-1 `17759452ba9c7cbf603c5813687c4b51a78880d3` 일치 확인.
- **Windows Codex의 실제 GitHub 네트워크/인증 시험은 별도**다. 아직 사용자 PC에서 정상 전달 완료라고 보고하지 않는다.

## 두 방식 비교

| 방식 | 운영 폴더 .git | 인증/네트워크 | 결과 |
|---|---|---|---|
| GitHub 임시 clone → 원본 blob | 불필요 | Windows GitHub 인증 필수 | Linux 격리 시험 PASS, Windows 미검증 |
| `gh api`로 blob base64 수신 | 불필요 | gh 설치·인증·GitHub API 필수 | 구현, Windows 미검증 |
| Google Drive sediment 임시 다운로드 | 불필요 | 임시 다운로드 호스트 DNS | Codex 환경에서 DNS 실패 |
| Google Drive for desktop 동기화 | 불필요 | 데스크톱 앱 설치·계정 연결 | 미검증; 대용량 파일 용도로 추후 검토 |

**참고:** 판매용 소형 ZIP은 GitHub에 보관해도 되지만 전국 점포 원본 DB/CSV, n8n 암호키·credential, 고객주소·실행자료는 절대 GitHub에 업로드하지 않는다.
