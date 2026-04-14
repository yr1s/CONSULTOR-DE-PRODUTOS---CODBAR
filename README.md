# Serviço de Busca (Flask + PostgreSQL)

## O que é
API simples que expõe `GET /api/busca?q=...` para consultar produtos no Postgres,
com as regras validadas no DBeaver (texto estrito por palavra e/ou código GIX/EAN).

## Conteúdo
- `app.py` — servidor Flask com pool de conexões, endpoints `/health`, `/ready` e `/api/busca`.
- `.env` — credenciais do banco **(preenchidas para seu ambiente)**.
- `requirements.txt` — dependências Python.
- `test_connection.py` — script para testar a comunicação com o banco.

## Como rodar (Windows PowerShell)
```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# teste de conexão
python test_connection.py

# subir a API
python app.py
```

Abrir no navegador:
- Saúde: `http://localhost:3000/health`
- Banco OK: `http://localhost:3000/ready`
- Busca: `http://localhost:3000/api/busca?q=cabinho%20300`
