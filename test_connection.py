# -*- coding: utf-8 -*-
"""Teste simples de comunicação com o PostgreSQL.
Execute:  python test_connection.py
"""
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()

if DATABASE_URL:
    DSN = DATABASE_URL
    DSN_LOG = DATABASE_URL  # psycopg2 aceita URL diretamente
else:
    DB_HOST     = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT     = os.getenv("DB_PORT", "5432")
    DB_NAME     = os.getenv("DB_NAME", "postgres")
    DB_USER     = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DSN     = f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} user={DB_USER} password={DB_PASSWORD} connect_timeout=5"
    DSN_LOG = DSN.replace(DB_PASSWORD, "******") if DB_PASSWORD else DSN

print("Conectando...", DSN_LOG)
with psycopg2.connect(DSN) as conn:
    with conn.cursor() as cur:
        cur.execute("SELECT version();")
        print("Servidor:", cur.fetchone()[0])
        cur.execute("SELECT current_database(), current_user, inet_server_addr(), inet_server_port();")
        db, usr, ip, prt = cur.fetchone()
        print("DB:", db, "| Usuário:", usr, "| Host:", ip, "| Porta:", prt)
        cur.execute("SELECT current_date;")
        print("Data do servidor:", cur.fetchone()[0])
print("OK: conexão e queries básicas funcionando.")
