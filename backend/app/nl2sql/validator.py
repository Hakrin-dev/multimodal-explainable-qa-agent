"""SQL validation via sqlglot (PLAN §4.2 step ⑥, v0).

Checks: single SELECT-only statement, tables/columns exist (qualify),
no dangerous functions, LIMIT clamped to the executor budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify

FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_read_file", "pg_ls_dir", "dblink",
    "lo_import", "lo_export", "pg_terminate_backend", "pg_cancel_backend",
    "copy_from", "set_config", "current_setting", "pg_reload_conf",
}


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    sql: str = ""  # normalized SQL (possibly with LIMIT appended)


class SQLValidator:
    def __init__(self, schema: dict[str, dict[str, str]], max_rows: int = 50):
        """schema: {table: {column: type}} from schema_meta.load_table_meta()."""
        self.schema = {t.lower(): {c.lower(): v for c, v in cols.items()}
                       for t, cols in schema.items()}
        self.max_rows = max_rows

    # -- public --

    def validate(self, sql: str) -> ValidationResult:
        sql = sql.strip().rstrip(";")
        errors: list[str] = []

        try:
            statements = sqlglot.parse(sql, read="postgres")
        except sqlglot.errors.ParseError as e:
            return ValidationResult(False, [f"SQL 语法错误: {e}"], sql)

        stmts = [s for s in statements if s is not None]
        if len(stmts) != 1:
            return ValidationResult(False, ["必须且只能是一条语句"], sql)

        root = stmts[0]
        if not isinstance(root, (exp.Select, exp.Union, exp.Intersect, exp.Except)):
            return ValidationResult(False, [f"仅允许 SELECT 查询，得到 {type(root).__name__}"], sql)

        # DML/DDL anywhere in the tree (e.g. hidden in subquery) is forbidden
        for bad in (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
                    exp.Alter, exp.Grant, exp.TruncateTable, exp.Copy):
            if root.find(bad):
                return ValidationResult(False, [f"禁止的语句类型: {bad.__name__}"], sql)

        # unknown tables (CTE names are legitimate "tables" for the outer query)
        cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
        for table in root.find_all(exp.Table):
            name = table.name.lower()
            if name and name not in self.schema and name not in cte_names:
                errors.append(f"未知的表: {name}")

        # dangerous functions (known classes + anonymous calls)
        for func in root.find_all(exp.Func):
            fname = ""
            if isinstance(func, exp.Anonymous):
                fname = (func.this or "").lower()
            else:
                fname = (func.sql_name() or "").lower()
            if fname in FORBIDDEN_FUNCTIONS:
                errors.append(f"禁止的函数: {fname}")

        if errors:
            return ValidationResult(False, errors, sql)

        # qualify: resolves aliases & validates all columns against schema
        try:
            qualified = qualify(root.copy(), schema=self.schema,
                                dialect="postgres", validate_qualify_columns=True,
                                quote_identifiers=False)
        except Exception as e:  # noqa: BLE001 — sqlglot raises assorted types
            return ValidationResult(False, [f"列/别名解析失败: {e}"], sql)

        # LIMIT clamp
        qualified = self._clamp_limit(qualified)

        return ValidationResult(True, [], qualified.sql(dialect="postgres"))

    # -- helpers --

    def _clamp_limit(self, tree: exp.Expression) -> exp.Expression:
        for select in tree.find_all(exp.Select):
            if select.args.get("limit") is None:
                select.limit(self.max_rows, copy=False)
            else:
                limit_expr = select.args["limit"].expression
                try:
                    n = int(limit_expr.this) if isinstance(limit_expr, exp.Literal) else None
                except Exception:
                    n = None
                if n is None or n > self.max_rows:
                    select.limit(self.max_rows, copy=False)
        return tree
