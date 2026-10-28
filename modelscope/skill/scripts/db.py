"""Connection and SQL helpers for seekdb (MySQL protocol).

Environment variables (all optional):
  SEEKDB_HOST      default 127.0.0.1
  SEEKDB_PORT      default 2881
  SEEKDB_USER      default root
  SEEKDB_PASSWORD  default ""
  SEEKDB_DATABASE  default test
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager

DEFAULT_TABLE = "skill_retrieval_docs"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]{0,63}$")


def check_identifier(name: str, what: str = "表名") -> str:
    """SQL identifiers cannot be bound as parameters, so whitelist them."""
    if not _IDENTIFIER.match(name or ""):
        raise SystemExit(f"{what} {name!r} 非法：只允许字母、数字、下划线和 $，且不能以数字开头")
    return name


def connect(database: str | None = None):
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("需要 pymysql：pip install pymysql") from exc
    return pymysql.connect(
        host=os.getenv("SEEKDB_HOST", "127.0.0.1"),
        port=int(os.getenv("SEEKDB_PORT", "2881")),
        user=os.getenv("SEEKDB_USER", "root"),
        password=os.getenv("SEEKDB_PASSWORD", ""),
        database=database or os.getenv("SEEKDB_DATABASE", "test"),
        charset="utf8mb4",
        autocommit=True,
    )


@contextmanager
def cursor(conn):
    cur = conn.cursor()
    try:
        yield cur
    finally:
        cur.close()


def execute_all(cur, statements):
    for sql in statements:
        cur.execute(sql)


def query_rows(cur, sql, params=None):
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description] if cur.description else []
    return cols, list(cur.fetchall())


def vector_sql(dim: int) -> str:
    return f"vector({dim})"


def table_ddl(table: str, dim: int, parser: str = "ik") -> str:
    """Single CREATE TABLE carrying both indexes.

    Declaring the vector index and the fulltext index in the same statement is
    the pattern verified on seekdb 1.3.0.0: adding a vector index afterwards to
    a table that already owns a fulltext index can take the instance down.

    ``parser`` is the fulltext tokenizer; ``none`` / ``default`` / empty keeps
    the server default, which splits on whitespace and therefore cannot match
    Chinese text.
    """
    use_parser = parser and parser.lower() not in ("none", "default", "space")
    fulltext = ",\n  fulltext index ftidx (body)"
    if use_parser:
        fulltext = f",\n  fulltext index ftidx (body) with parser {parser}"
    return f"""create table if not exists {table}(
  doc_id varchar(64) primary key,
  title varchar(512),
  body varchar(4000) not null,
  category varchar(64),
  url varchar(1024),
  embedding {vector_sql(dim)} not null{fulltext},
  vector index vidx (embedding) with (distance=cosine, type=hnsw)
)"""


def drop_table(conn, table: str) -> None:
    with cursor(conn) as cur:
        cur.execute(f"drop table if exists {table}")


def require_table(conn, table: str) -> None:
    """Fail with a hint instead of a raw driver traceback when the table is absent."""
    with cursor(conn) as cur:
        cur.execute("select count(*) from information_schema.tables "
                    "where table_schema = database() and table_name = %s", (table.lower(),))
        if not cur.fetchone()[0]:
            raise SystemExit(
                f"表 {table!r} 不存在（或不在 {os.getenv('SEEKDB_DATABASE', 'test')} 库）。"
                f"先跑 build_index.py --table {table}")


def index_summary(conn, table: str) -> list:
    with cursor(conn) as cur:
        cur.execute(f"show index from {table}")
        return [{"name": r[2], "type": (r[10] or r[3]), "column": r[4]} for r in cur.fetchall()]
