param(
  [switch]$OnlyTest
)
# One-click: ativa venv, instala deps, testa banco e sobe a API.
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $Here

Write-Host "==> Pasta:" (Get-Location)

# 1) Cria venv se não existir
if (-not (Test-Path ".\.venv")) {
  Write-Host "==> Criando venv (.venv)"
  py -3 -m venv .venv
}

# 2) Ativa venv
Write-Host "==> Ativando venv"
. .\.venv\Scripts\Activate.ps1

# 3) Instala deps
Write-Host "==> Instalando dependências"
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

# 4) Teste de conexão
Write-Host "==> Testando comunicação com o banco..."
python test_connection.py

if ($OnlyTest) {
  Write-Host "==> Somente teste executado. Encerrando."
  exit 0
}

# 5) Sobe API Flask
Write-Host "==> Subindo API Flask em http://localhost:3000"
python app.py
