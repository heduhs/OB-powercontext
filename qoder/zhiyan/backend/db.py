"""seekdb access layer for ZhiYan: connection, schema and index management.

The schema deliberately declares the HNSW vector index and the Chinese fulltext
index (ik parser) in a single CREATE TABLE: adding a vector index afterwards to
a table that already carries a fulltext index is not reliable on seekdb 1.3.x.
"""

from __future__ import annotations

import re
from typing import List, Optional

import pymysql

from .config import settings

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")

TABLE = "zhiyan_docs"

DDL = """create table if not exists {table}(
  doc_id varchar(64) primary key,
  title varchar(512),
  section varchar(512),
  doc_type varchar(32),
  tags varchar(512),
  text_body varchar(4000) not null,
  source_url varchar(512),
  embedding vector({dim}) not null,
  fulltext index ftx (text_body) with parser ik,
  vector index vidx (embedding) with (distance=cosine, type=hnsw)
)"""


def check_identifier(name: str) -> str:
    if not _IDENTIFIER.match(name or ""):
        raise ValueError(f"非法标识符：{name!r}")
    return name


def connect(database: Optional[str] = None):
    return pymysql.connect(
        host=settings.SEEKDB_HOST,
        port=settings.SEEKDB_PORT,
        user=settings.SEEKDB_USER,
        password=settings.SEEKDB_PASSWORD,
        database=database or settings.SEEKDB_DATABASE,
        charset="utf8mb4",
        autocommit=True,
    )


def connect_server():
    """Connect without selecting a database (used before it exists)."""
    return pymysql.connect(
        host=settings.SEEKDB_HOST,
        port=settings.SEEKDB_PORT,
        user=settings.SEEKDB_USER,
        password=settings.SEEKDB_PASSWORD,
        charset="utf8mb4",
        autocommit=True,
    )


def ensure_database() -> None:
    conn = connect_server()
    try:
        with conn.cursor() as cur:
            cur.execute(f"create database if not exists {check_identifier(settings.SEEKDB_DATABASE)}")
    finally:
        conn.close()


def reset_table(conn, dim: int) -> None:
    with conn.cursor() as cur:
        cur.execute(f"drop table if exists {TABLE}")
        cur.execute(DDL.format(table=TABLE, dim=dim))


def table_exists(conn) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "select count(*) from information_schema.tables "
            "where table_schema = database() and table_name = %s", (TABLE,))
        return bool(cur.fetchone()[0])


def vector_dim(conn) -> Optional[int]:
    if not table_exists(conn):
        return None
    with conn.cursor() as cur:
        cur.execute("show create table " + TABLE)
        ddl = cur.fetchone()[1]
    m = re.search(r"VECTOR\((\d+)\)", ddl, re.I)
    return int(m.group(1)) if m else None


def indexes(conn) -> List[dict]:
    if not table_exists(conn):
        return []
    with conn.cursor() as cur:
        cur.execute("show index from " + TABLE)
        rows = cur.fetchall()
    return [{"name": r[2], "column": r[4], "type": (r[10] or r[3])} for r in rows]


def engine_version(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("select version()")
        return str(cur.fetchone()[0])


def insert_documents(conn, rows: List[dict]) -> int:
    """Upsert documents so re-running ingestion is idempotent."""
    sql = (f"insert into {TABLE}(doc_id,title,section,doc_type,tags,text_body,source_url,embedding) "
           "values(%s,%s,%s,%s,%s,%s,%s,%s) "
           "on duplicate key update title=values(title), section=values(section), "
           "doc_type=values(doc_type), tags=values(tags), text_body=values(text_body), "
           "source_url=values(source_url), embedding=values(embedding)")
    with conn.cursor() as cur:
        for r in rows:
            cur.execute(sql, (r["doc_id"], r.get("title", ""), r.get("section", ""),
                              r.get("doc_type", ""), r.get("tags", ""),
                              r["text_body"][:4000], r.get("source_url", ""),
                              r["embedding"]))
    return len(rows)


def count(conn) -> int:
    if not table_exists(conn):
        return 0
    with conn.cursor() as cur:
        cur.execute("select count(*) from " + TABLE)
        return int(cur.fetchone()[0])
