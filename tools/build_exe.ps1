# 세션 등록 툴 exe 빌드 스크립트
#
# 사용법 (개발자 PC에서):
#   powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
#
# 산출물: tools\dist\세션등록.exe
# 배포 시 exe와 같은 폴더에 session_tool.config.json 을 함께 넣어 전달한다:
#   {"api_url": "http://<서버>:8100", "token": "<SESSION_API_TOKEN>"}

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

pip install --quiet pyinstaller selenium requests

pyinstaller `
    --onefile `
    --console `
    --name "세션등록" `
    --collect-all selenium `
    --distpath dist `
    --workpath build `
    --specpath build `
    register_session.py

Write-Host ""
Write-Host "✅ 빌드 완료: $PSScriptRoot\dist\세션등록.exe"
Write-Host "   session_tool.config.json 을 exe 옆에 넣어서 배포하세요."
