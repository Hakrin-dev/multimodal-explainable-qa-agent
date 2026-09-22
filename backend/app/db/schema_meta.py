"""Schema metadata reader — raw material for semantic schema cards (W3 #4).

W1 scope: tables, columns (types, nullability), PK/FK graph, row counts and
top sample values.  Semantic enrichment (business descriptions, embedding
indexing) lands in W3; this module keeps a stable interface:
`build_schema_context(tables=None) -> str` feeding NL2SQL prompts.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .session import get_conn

# Chinook public schema only — project/system tables stay out of prompts.
_SYSTEM_SCHEMAS = ("pg_catalog", "information_schema")
_PROJECT_TABLES = {"biz_term", "kb_doc", "kb_chunk", "trace_turn", "trace_event",
                   "app_session"}


@dataclass
class ForeignKey:
    column: str
    ref_table: str
    ref_column: str


@dataclass
class ColumnMeta:
    name: str
    type: str
    nullable: bool
    is_primary: bool = False
    fks: list[ForeignKey] = field(default_factory=list)


@dataclass
class TableMeta:
    name: str
    columns: list[ColumnMeta] = field(default_factory=list)
    row_count: int = 0
    comment: str = ""

    @property
    def fk_edges(self) -> list[tuple[str, str, str, str]]:
        return [(self.name, fk.ref_table, c.name, fk.ref_column)
                for c in self.columns for fk in c.fks]


def load_table_meta(table_names: list[str] | None = None,
                    include_project_tables: bool = False) -> list[TableMeta]:
    """Read table/column/FK metadata + row counts from the live database."""
    tables: list[TableMeta] = []
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.relname, obj_description(c.oid) AS comment
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
            """
        )
        for name, comment in cur.fetchall():
            if not include_project_tables and name in _PROJECT_TABLES:
                continue
            if table_names is not None and name not in table_names:
                continue
            tables.append(TableMeta(name=name, comment=comment or ""))

        for t in tables:
            cur.execute(
                """
                SELECT a.attname, format_type(a.atttypid, a.atttypmod),
                       NOT a.attnotnull,
                       COALESCE(i.indisprimary, false)
                FROM pg_attribute a
                LEFT JOIN pg_index i
                       ON i.indrelid = a.attrelid AND a.attnum = ANY(i.indkey)
                WHERE a.attrelid = %s::regclass AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY a.attnum
                """,
                (t.name,),
            )
            for col_name, col_type, nullable, is_pk in cur.fetchall():
                t.columns.append(
                    ColumnMeta(name=col_name, type=col_type, nullable=nullable,
                               is_primary=is_pk or False)
                )

            cur.execute(
                """
                SELECT a.attname, cf.relname, af.attname
                FROM pg_constraint con
                JOIN pg_class c  ON c.oid = con.conrelid
                JOIN pg_class cf ON cf.oid = con.confrelid
                JOIN pg_attribute a  ON a.attrelid = c.oid  AND a.attnum  = ANY(con.conkey)
                JOIN pg_attribute af ON af.attrelid = cf.oid AND af.attnum = ANY(con.confkey)
                WHERE con.contype = 'f' AND c.relname = %s
                """,
                (t.name,),
            )
            for col_name, ref_table, ref_col in cur.fetchall():
                for c in t.columns:
                    if c.name == col_name:
                        c.fks.append(ForeignKey(column=col_name,
                                                ref_table=ref_table, ref_column=ref_col))

            cur.execute(f'SELECT count(*) FROM "{t.name}"')
            t.row_count = cur.fetchone()[0]

    return tables


def top_values(table: str, column: str, limit: int = 5) -> list[str]:
    """Sample distinct values for a column — prompt-friendly enum hints."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT %s',
            (limit,),
        )
        return [str(r[0]) for r in cur.fetchall()]


def _render_column(c: ColumnMeta) -> str:
    parts = [f'"{c.name}" {c.type}']
    if c.is_primary:
        parts.append("PRIMARY KEY")
    if c.fks:
        fk = c.fks[0]
        parts.append(f"REFERENCES {fk.ref_table}({fk.ref_column})")
    return " ".join(parts)


def build_schema_context(tables: list[TableMeta] | None = None,
                         with_samples: bool = True) -> str:
    """Render compact DDL-like context for prompts (W3 will add semantic cards)."""
    tables = tables if tables is not None else load_table_meta()
    blocks: list[str] = []
    for t in tables:
        cols = ", ".join(_render_column(c) for c in t.columns)
        header = f"TABLE {t.name}  -- {t.row_count} rows"
        if t.comment:
            header += f"  -- {t.comment}"
        block = [header, f"  ({cols})"]
        if with_samples:
            for c in t.columns:
                if c.is_primary or c.fks:
                    continue
                vals = top_values(t.name, c.name, limit=3)
                if vals and all(len(x) <= 30 for x in vals):
                    block.append(f"    -- {c.name} sample: {', '.join(vals)}")
        blocks.append("\n".join(block))
    return "\n\n".join(blocks)
