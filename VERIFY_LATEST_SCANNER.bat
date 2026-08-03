@echo off
cd /d "%~dp0"
python -c "from archive_utils import find_latest_archive, resolve_project_path; p=find_latest_archive(resolve_project_path('ARCHIVE_DIR','archive')); print('LATEST SCANNER:', p if p else 'NOT FOUND')"
pause
