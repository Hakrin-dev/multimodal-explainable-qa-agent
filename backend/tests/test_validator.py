"""Unit tests for sqlglot-based SQL validation."""

from __future__ import annotations

import pytest

from app.nl2sql.validator import SQLValidator

SCHEMA = {
    "track": {"trackid": "bigint", "name": "varchar", "albumid": "bigint",
              "genreid": "bigint", "unitprice": "numeric", "milliseconds": "bigint"},
    "genre": {"genreid": "bigint", "name": "varchar"},
}


@pytest.fixture()
def validator() -> SQLValidator:
    return SQLValidator(SCHEMA, max_rows=50)


def test_valid_simple_select(validator):
    r = validator.validate("SELECT name, unitprice FROM track WHERE unitprice > 1")
    assert r.ok, r.errors


def test_rejects_write(validator):
    r = validator.validate("DELETE FROM track")
    assert not r.ok


def test_rejects_unknown_table(validator):
    r = validator.validate("SELECT * FROM users")
    assert not r.ok and any("未知的表" in e for e in r.errors)


def test_rejects_unknown_column(validator):
    r = validator.validate("SELECT foo FROM track")
    assert not r.ok


def test_rejects_multi_statement(validator):
    r = validator.validate("SELECT 1; SELECT 2")
    assert not r.ok


def test_forbidden_function(validator):
    r = validator.validate("SELECT pg_sleep(10)")
    assert not r.ok and any("禁止的函数" in e for e in r.errors)


def test_limit_clamp(validator):
    r = validator.validate("SELECT name FROM track")
    assert r.ok and "LIMIT 50" in r.sql.upper()

    r2 = validator.validate("SELECT name FROM track LIMIT 5000")
    assert r2.ok
    assert "LIMIT 50" in r2.sql.upper()


def test_case_insensitive_tables(validator):
    r = validator.validate("SELECT Name FROM TRACK WHERE UnitPrice > 1")
    assert r.ok, r.errors


def test_subquery_and_alias(validator):
    sql = """
    SELECT g.name, cnt FROM genre g
    JOIN (SELECT genreid, COUNT(*) AS cnt FROM track GROUP BY genreid) t
      ON t.genreid = g.genreid
    """
    r = validator.validate(sql)
    assert r.ok, r.errors


def test_with_cte(validator):
    sql = "WITH top AS (SELECT name FROM track) SELECT * FROM top"
    r = validator.validate(sql)
    assert r.ok, r.errors


def test_limit_clamp_only_outermost(validator):
    """Regression (W1-D2): clamping subqueries corrupted derived-table semantics."""
    sql = ("SELECT t.name FROM track t WHERE t.unitprice > "
           "(SELECT AVG(unitprice) FROM track)")
    r = validator.validate(sql)
    assert r.ok
    assert r.sql.upper().count("LIMIT") == 1            # only the outer clamp
    assert r.sql.upper().rstrip().endswith("LIMIT 50")  # and it's at the end

    # nested derived table also untouched
    sql2 = ("SELECT name FROM (SELECT name, SUM(unitprice) AS s FROM track GROUP BY name) sub "
            "WHERE s > (SELECT AVG(s) FROM (SELECT SUM(milliseconds) AS s FROM track GROUP BY albumid) d)")
    r2 = validator.validate(sql2)
    assert r2.ok
    assert r2.sql.upper().count("LIMIT 50") == 1
