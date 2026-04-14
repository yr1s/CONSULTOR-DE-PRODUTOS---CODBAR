# -*- coding: utf-8 -*-
"""
app.py — Flask + Postgres para Consulta de Preço
- Mantém estrutura original
- Texto: múltiplos termos (ordem livre) + sinônimos/abreviações + variações 2.5/2,5
- Ranking: prioriza prefixo (sem depender da ordem) e depois descrição
- Fallback fuzzy com pg_trgm quando o exato retorna pouco/nada
"""

import os
import re
import logging
from typing import List

from flask import Flask, send_from_directory, jsonify, request
from dotenv import load_dotenv

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

# ------------------------------ Config ---------------------------------------
load_dotenv()

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "3000"))
API_KEY = os.getenv("API_KEY", "")

DATABASE_URL = (os.getenv("DATABASE_URL") or "").strip()
if not DATABASE_URL:
    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT = os.getenv("DB_PORT", "5432")
    DB_NAME = os.getenv("DB_NAME", "postgres")
    DB_USER = os.getenv("DB_USER", "postgres")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DATABASE_URL = (
        f"host={DB_HOST} port={DB_PORT} dbname={DB_NAME} "
        f"user={DB_USER} password={DB_PASSWORD} connect_timeout=5"
    )

ENABLE_CORS = os.getenv("ENABLE_CORS", "").lower() in ("1", "true", "yes", "on")
USE_UNACCENT_ENV = os.getenv("USE_UNACCENT", "1").lower() in ("1", "true", "yes", "on")
STATEMENT_TIMEOUT_MS = int(os.getenv("STATEMENT_TIMEOUT_MS", "30000"))

CAND_LIMIT = int(os.getenv("CAND_LIMIT", "100"))
RESULT_LIMIT = int(os.getenv("RESULT_LIMIT", "100"))

# Fuzzy configs
USE_FUZZY_ENV = os.getenv("USE_FUZZY", "1").lower() in ("1", "true", "yes", "on")
FUZZY_MIN_SIM = float(os.getenv("FUZZY_MIN_SIM", "0.32"))
FUZZY_CAND_LIMIT = int(os.getenv("FUZZY_CAND_LIMIT", "120"))
FUZZY_TRIGGER_RESULTS = int(os.getenv("FUZZY_TRIGGER_RESULTS", "0"))

# ------------------------------- App -----------------------------------------
app = Flask(__name__, static_folder="public", static_url_path="")

if ENABLE_CORS:
    try:
        from flask_cors import CORS
        CORS(app, resources={r"/api/*": {"origins": "*"}},
             methods=["GET", "OPTIONS"], allow_headers=["Content-Type", "x-api-key"])
        print("[INFO] CORS habilitado")
    except Exception as e:
        print("[WARN] ENABLE_CORS=1 mas flask-cors não instalado:", e)

# Pool
pool = SimpleConnectionPool(minconn=1, maxconn=5, dsn=DATABASE_URL)

def _get_conn():
    conn = pool.getconn()
    with conn.cursor() as cur:
        cur.execute(f"SET SESSION statement_timeout = {STATEMENT_TIMEOUT_MS}")
    return conn

def _release_conn(conn):
    if conn:
        pool.putconn(conn)

# ------------------------------ Helpers --------------------------------------
def _is_numeric_search(q: str) -> bool:
    has_letter = re.search(r"[A-Za-z]", q or "") is not None
    has_digit = re.search(r"\d", q or "") is not None
    return (not has_letter) and has_digit

def _has_unaccent(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='unaccent'")
            return cur.fetchone() is not None
    except Exception:
        return False

def _has_pg_trgm(conn) -> bool:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_extension WHERE extname='pg_trgm'")
            return cur.fetchone() is not None
    except Exception:
        return False

# Dicionário simples de sinônimos/abreviações relevantes
SYN = {
    "VM": ["VM", "VERMELHO"],
    "VERMELHO": ["VM", "VERMELHO"],
    "AZ": ["AZ", "AZUL"],
    "AZUL": ["AZ", "AZUL"],
    "PT": ["PT", "PRETO"],
    "PR": ["PR", "PRETO"],
    "PRETO": ["PT", "PR", "PRETO"],
    "BC": ["BC", "BRANCO"],
    "BRANCO": ["BC", "BRANCO"],
    "AM": ["AM", "AMARELO"],
    "AMARELO": ["AM", "AMARELO"],
    "VD": ["VD", "VERDE"],
    "VERDE": ["VD", "VERDE"],
    "CZ": ["CZ", "CINZA"],
    "CINZA": ["CZ", "CINZA"],
    "SIL": ["SIL", "SILICONE"],
    "SILICONE": ["SIL", "SILICONE"],
    "CABINHO": ["CABINHO", "CABO"],
    "CABO": ["CABO", "CABINHO"],
    "M": ["M", "MT", "MTS"],
    "MT": ["M", "MT", "MTS"],
    "MTS": ["M", "MT", "MTS"],
    "C/": ["C/", "COM"],
    "COM": ["COM", "C/"]
}

def _expand_token_alts(tok: str) -> List[str]:
    """Gera alternativas: original, troca .<->, e sinônimos (case-insensitive)."""
    alts = [tok]
    if "." in tok and "," not in tok:
        alts.append(tok.replace(".", ","))
    elif "," in tok and "." not in tok:
        alts.append(tok.replace(",", "."))

    key = tok.upper()
    if key in SYN:
        alts.extend(SYN[key])
    # dedup case-insensitive
    seen = set()
    out = []
    for a in alts:
        k = a.upper()
        if k not in seen:
            seen.add(k)
            out.append(a)
    return out

# ------------------------------- Rotas ---------------------------------------
@app.get("/")
def index():
    return send_from_directory("public", "consulta.html")

@app.get("/health")
def health():
    return jsonify(status="ok"), 200

@app.get("/ready")
def ready():
    conn = None
    try:
        conn = _get_conn()
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()
        return jsonify(ready=True), 200
    except Exception as e:
        logging.exception("READY check falhou: %s", e)
        return jsonify(ready=False, error=str(e)), 500
    finally:
        _release_conn(conn)

@app.get("/api/busca")
def api_busca():
    if API_KEY and request.headers.get("x-api-key") != API_KEY:
        return jsonify(error="unauthorized"), 401

    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify(error="missing q"), 400

    numeric_mode = _is_numeric_search(q)

    conn = None
    try:
        conn = _get_conn()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:

            if numeric_mode:
                # ------------------- BUSCA POR CÓDIGO (GIX/EAN) -------------------
                NUM_SQL = r"""
                WITH param AS (
                  SELECT ltrim(regexp_replace(%(q)s, '\D', '', 'g'), '0') AS qn
                ),
                alvo AS (
                  SELECT DISTINCT p.PRODCODI
                  FROM ARQPROD p, param i
                  WHERE i.qn <> '' AND (
                        ltrim(regexp_replace(p.PRODCODI::text,'\D','','g'),'0') = i.qn
                     OR ltrim(regexp_replace(p.PRODCODB::text,'\D','','g'),'0') = i.qn
                     OR ltrim(regexp_replace(p.PRODCODF::text,'\D','','g'),'0') = i.qn
                     OR ltrim(regexp_replace(p.PRODREFE::text,'\D','','g'),'0') = i.qn
                     OR ltrim(regexp_replace(p.PRODCODI::text,'\D','','g'),'0') LIKE i.qn || '%%'
                     OR ltrim(regexp_replace(p.PRODCODB::text,'\D','','g'),'0') LIKE i.qn || '%%'
                     OR ltrim(regexp_replace(p.PRODCODF::text,'\D','','g'),'0') LIKE i.qn || '%%'
                     OR ltrim(regexp_replace(p.PRODREFE::text,'\D','','g'),'0') LIKE i.qn || '%%'
                  )
                  UNION
                  SELECT DISTINCT c.CBARCODI
                  FROM ARQCBAR c, param i
                  WHERE i.qn <> ''
                    AND (ltrim(regexp_replace(c.CBARCODB::text,'\D','','g'),'0') = i.qn
                      OR ltrim(regexp_replace(c.CBARCODB::text,'\D','','g'),'0') LIKE i.qn || '%%')
                )
                SELECT
                  p.PRODCODI::text AS produto,
                  p.PRODDESC       AS descricao,
                  COALESCE(MAX(t.TABPPREC), 0) AS valor,
                  UPPER(l.LOCADZON) AS local,
                  GREATEST(SUM(s.SALDSALD - s.SALDRESE), 0) AS estoque,
                  SUM(s.SALDRESE) AS estoque_reservado,
                  array_remove(array_cat(
                    ARRAY[nullif(trim(p.PRODCODB::text),'')],
                    COALESCE(array_agg(DISTINCT cb.CBARCODB) FILTER (WHERE cb.CBARCODB IS NOT NULL), ARRAY[]::text[])
                  ), NULL) AS codigos_barras
                FROM alvo a
                JOIN ARQPROD p ON p.PRODCODI = a.PRODCODI
                JOIN ARQSALD s ON s.SALDCODI = p.PRODCODI
                JOIN ARQLOCA l ON l.LOCACODI = s.SALDONDE
                LEFT JOIN ARQTABP t ON t.TABPPROD = p.PRODCODI AND t.TABPCODI = 'P'
                LEFT JOIN ARQCBAR cb ON cb.CBARCODI = p.PRODCODI AND cb.CBARCUSO <> 'O'
                WHERE COALESCE(l.LOCADISP,'S') <> 'N'
                GROUP BY p.PRODCODI, p.PRODDESC, UPPER(l.LOCADZON)
                ORDER BY p.PRODDESC ASC, local ASC
                LIMIT %(reslim)s
                """
                cur.execute(NUM_SQL, {"q": q, "reslim": RESULT_LIMIT})
                res = cur.fetchall() or []

            else:
                # ----------- BUSCA POR NOME (ordem livre, sinônimos, 2 etapas) -----------
                q_text = " ".join(q.split())
                tokens = [t for t in q_text.split(" ") if t]
                if not tokens:
                    return jsonify([]), 200

                env_wants_unaccent = USE_UNACCENT_ENV
                use_unaccent = env_wants_unaccent and _has_unaccent(conn)

                def mk_clause(base_col: str, pname: str) -> str:
                    if use_unaccent:
                        return f"unaccent(lower({base_col})) LIKE unaccent(lower(%({pname})s))"
                    else:
                        return f"{base_col} ILIKE %({pname})s"

                params = {"lim": CAND_LIMIT, "reslim": RESULT_LIMIT}
                like_AND = []   # cada token → (alt1 OR alt2 OR ...)
                prefix_ORs = [] # para ranking: TRUE se alguma alt é prefixo

                for i, tok in enumerate(tokens):
                    alts = _expand_token_alts(tok)
                    ors_like = []
                    ors_prefix = []
                    for k, alt in enumerate(alts):
                        params[f"t{i}_{k}"] = f"%{alt}%"
                        params[f"p{i}_{k}"] = f"{alt}%"
                        ors_like.append(mk_clause("p.PRODDESC", f"t{i}_{k}"))
                        ors_prefix.append(mk_clause("p.PRODDESC", f"p{i}_{k}"))
                    like_AND.append("(" + " OR ".join(ors_like) + ")")
                    prefix_ORs.append("(" + " OR ".join(ors_prefix) + ")")

                where_all_tokens = " AND ".join(like_AND)
                rank_prefix = " + ".join([f"CASE WHEN {c} THEN 0 ELSE 1 END" for c in prefix_ORs]) or "0"

                TEXT_SQL = f"""
                WITH alvo AS (
                    SELECT p.PRODCODI, p.PRODDESC,
                           {rank_prefix} AS rank_prefix
                    FROM ARQPROD p
                    WHERE {where_all_tokens}
                    ORDER BY rank_prefix ASC, p.PRODDESC ASC
                    LIMIT %(lim)s
                )
                SELECT
                  p.PRODCODI::text AS produto,
                  p.PRODDESC       AS descricao,
                  COALESCE(MAX(t.TABPPREC), 0) AS valor,
                  UPPER(l.LOCADZON) AS local,
                  GREATEST(SUM(s.SALDSALD - s.SALDRESE), 0) AS estoque,
                  SUM(s.SALDRESE) AS estoque_reservado,
                  array_remove(array_cat(
                    ARRAY[nullif(trim(p.PRODCODB::text),'')],
                    COALESCE(array_agg(DISTINCT cb.CBARCODB) FILTER (WHERE cb.CBARCODB IS NOT NULL), ARRAY[]::text[])
                  ), NULL) AS codigos_barras,
                  MIN(a.rank_prefix) AS rk
                FROM alvo a
                JOIN ARQPROD p  ON p.PRODCODI = a.PRODCODI
                JOIN ARQSALD s  ON s.SALDCODI = p.PRODCODI
                JOIN ARQLOCA l  ON l.LOCACODI = s.SALDONDE
                LEFT JOIN ARQTABP t ON t.TABPPROD = p.PRODCODI AND t.TABPCODI = 'P'
                LEFT JOIN ARQCBAR cb ON cb.CBARCODI = p.PRODCODI AND cb.CBARCUSO <> 'O'
                WHERE COALESCE(l.LOCADISP,'S') <> 'N'
                GROUP BY p.PRODCODI, p.PRODDESC, UPPER(l.LOCADZON)
                ORDER BY rk ASC, p.PRODDESC ASC, local ASC
                LIMIT %(reslim)s
                """
                cur.execute(TEXT_SQL, params)
                res = cur.fetchall() or []

                # ---------- Fallback fuzzy (pg_trgm) se pouco/nada retornou ----------
                if USE_FUZZY_ENV and _has_pg_trgm(conn) and len(res) <= FUZZY_TRIGGER_RESULTS:
                    base_txt = "unaccent(lower(p.PRODDESC))" if use_unaccent else "lower(p.PRODDESC)"
                    base_col = "p.PRODDESC"
                    params_fz = {"lim": FUZZY_CAND_LIMIT, "reslim": RESULT_LIMIT, "sim": FUZZY_MIN_SIM}
                    sim_terms = []
                    where_sim = []
                    prefix_terms = []

                    for i, tok in enumerate(tokens):
                        alts = _expand_token_alts(tok)
                        # GREATEST(word_similarity(..., alt1), word_similarity(..., alt2), ...)
                        parts = []
                        pfx_or = []
                        for k, alt in enumerate(alts):
                            name = f"f{i}_{k}"
                            params_fz[name] = alt.lower()
                            parts.append(f"word_similarity({base_txt}, %({name})s)")
                            if use_unaccent:
                                pfx_or.append(f"unaccent(lower({base_col})) LIKE unaccent(lower(%(p{i}_{k})s))")
                            else:
                                pfx_or.append(f"{base_col} ILIKE %(p{i}_{k})s")
                            params_fz[f"p{i}_{k}"] = f"{alt}%"
                        sim_expr = "GREATEST(" + ", ".join(parts) + ")"
                        sim_terms.append(sim_expr)
                        where_sim.append(f"{sim_expr} >= %(sim)s")
                        prefix_terms.append("(" + " OR ".join(pfx_or) + ")")

                    sum_ws = " + ".join(sim_terms) if sim_terms else "0"
                    sum_px = " + ".join([f"CASE WHEN {c} THEN 1 ELSE 0 END" for c in prefix_terms]) if prefix_terms else "0"
                    where_and = " AND ".join(where_sim) if where_sim else "TRUE"

                    FUZZY_SQL = f"""
                    WITH cand AS (
                        SELECT p.PRODCODI, p.PRODDESC,
                               ({sum_ws}) AS ws,
                               ({sum_px}) AS px
                        FROM ARQPROD p
                        WHERE {where_and}
                        ORDER BY ws DESC, px DESC, p.PRODDESC ASC
                        LIMIT %(lim)s
                    )
                    SELECT
                      p.PRODCODI::text AS produto,
                      p.PRODDESC       AS descricao,
                      COALESCE(MAX(t.TABPPREC), 0) AS valor,
                      UPPER(l.LOCADZON) AS local,
                      GREATEST(SUM(s.SALDSALD - s.SALDRESE), 0) AS estoque,
                      SUM(s.SALDRESE) AS estoque_reservado,
                      array_remove(array_cat(
                        ARRAY[nullif(trim(p.PRODCODB::text),'')],
                        COALESCE(array_agg(DISTINCT cb.CBARCODB) FILTER (WHERE cb.CBARCODB IS NOT NULL), ARRAY[]::text[])
                      ), NULL) AS codigos_barras,
                      MIN(c.ws) AS ws, MIN(c.px) AS px
                    FROM cand c
                    JOIN ARQPROD p  ON p.PRODCODI = c.PRODCODI
                    JOIN ARQSALD s  ON s.SALDCODI = p.PRODCODI
                    JOIN ARQLOCA l  ON l.LOCACODI = s.SALDONDE
                    LEFT JOIN ARQTABP t ON t.TABPPROD = p.PRODCODI AND t.TABPCODI = 'P'
                    LEFT JOIN ARQCBAR cb ON cb.CBARCODI = p.PRODCODI AND cb.CBARCUSO <> 'O'
                    WHERE COALESCE(l.LOCADISP,'S') <> 'N'
                    GROUP BY p.PRODCODI, p.PRODDESC, UPPER(l.LOCADZON)
                    ORDER BY px DESC, ws DESC, p.PRODDESC ASC, local ASC
                    LIMIT %(reslim)s
                    """
                    cur.execute(FUZZY_SQL, params_fz)
                    res = cur.fetchall() or []

        # ----------------------- Monta resposta JSON ---------------------------
        data = []
        for r in res:
            data.append({
                "produto": str(r["produto"]),
                "descricao": r["descricao"],
                "valor": float(r.get("valor") or 0),
                "local": r["local"],
                "estoque": int(r.get("estoque") or 0),
                "estoque_reservado": int(r.get("estoque_reservado") or 0),
                "codigos_barras": list(r.get("codigos_barras") or []),
            })
        return jsonify(data), 200

    except Exception as e:
        logging.exception("Erro em /api/busca: %s", e)
        return jsonify(error="internal error"), 500
    finally:
        _release_conn(conn)

if __name__ == "__main__":
    app.run(host=HOST, port=PORT, debug=True, ssl_context="adhoc")
