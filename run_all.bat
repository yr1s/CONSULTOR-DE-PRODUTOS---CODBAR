@echo off
REM One-click runner para Windows
setlocal enableextensions
cd /d %~dp0

REM Executa o PowerShell com permissões de script só nesta chamada
powershell -ExecutionPolicy Bypass -File ".\run_all.ps1"
