@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Ambiente virtual nao encontrado. Rode antes, nesta pasta:
    echo     python -m venv .venv
    echo     .venv\Scripts\python -m pip install -r requirements.txt
    pause
    exit /b 1
)

".venv\Scripts\python.exe" painel.py
