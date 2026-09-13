@echo off
rem DoubaoVoicePortable launcher - runs as a standard user, no admin required
cd /d "%~dp0"
start "DoubaoVoice" "%~dp0app\DoubaoVoice.exe"
exit /b 0
