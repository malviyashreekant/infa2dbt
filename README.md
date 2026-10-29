# infa2dbt

Converts Informatica PowerCenter XML exports into a dbt project for Oracle, runs quality gates on the result,
and writes the evidence (coverage matrix, manual-review list, reconciliation SQL).

The tool promises **complete coverage and complete flagging**, not 100% automatic fidelity. It never silently drops
or guesses: anything it cannot translate with certainty is emitted with a `NEEDS_REVIEW` marker and listed in
`reports/manual_review.md`.

## Quick start

```bash
pip install -e ".[dev]"
infa2dbt convert path/to/exports/ --out dbt_out --config config.example.yml
infa2dbt check dbt_out            # re-run the static checks, e.g. in CI
pytest                            # 68 tests
```

`convert` accepts one XML file or a folder (recursive). Exit codes: `0` ok, `1` a gate failed (dropped component,
unparseable SQL or Jinja, parse error, no mappings found), `2` review items remain and `--fail-on-review` was given.

## What it generates

```
dbt_out/
  dbt_project.yml  packages.yml (dbt_utils)  profiles.yml.example
  models/staging/       stg_<source>.sql, stg_<source_qualifier>.sql, _sources.yml, schema yml
  models/intermediate/  int_<last_step>.sql  (a linear chain of steps = one model, one CTE per step)
  models/marts/         fct_<target>.sql / dim_<target>.sql  (+ schema.yml with tests)
  macros/               lkp_<name>.sql       (one per unconnected lookup)
  analyses/             recon_<mart>.sql     (source-to-target reconciliation)
  ddl/sequences.sql     only when sequence_mode = oracle_sequence
  reports/              coverage_matrix.md, manual_review.md, conversion_log.json
```

Output is deterministic: no timestamps, sorted keys, and mappings are converted in name order, so the same XML
always yields byte-identical files (tested), which keeps Git diffs reviewable.

## How components map

| PowerCenter | dbt / Oracle |
|---|---|
| Source definition | `stg_` view: `CAST` to Oracle types, renaming, `source()` |
| Source Qualifier | `stg_` view: user-defined join and source filter (copied as SQL, `$$PARAM` becomes `var()`), distinct, sorted ports |
| Expression | CTE; each **variable port** is its own CTE stage, so the dependency chain is preserved |
| Filter | CTE with `WHERE` |
| Router | one CTE per connected group (`WHERE` on the group condition); DEFAULT group is NULL-safe |
| Aggregator | `GROUP BY`; conditional aggregates become `SUM(CASE WHEN ...)` |
| Joiner | `INNER` / `LEFT` / `FULL OUTER JOIN` from the Join Type and condition |
| Connected Lookup | `LEFT JOIN` to a de-duplicated lookup CTE (`ROW_NUMBER()` per the multiple-match policy) |
| Unconnected Lookup | dbt macro returning a scalar subquery; `:LKP.x(a)` becomes a macro call |
| Sorter | `ORDER BY` with explicit `NULLS FIRST/LAST`; `DISTINCT` option |
| Union | `UNION ALL` of each input group |
| Sequence Generator | `ROW_NUMBER()` (default) or an Oracle sequence, see below |
| Update Strategy | `INFA_DML_FLAG` column; target becomes incremental with `unique_key`; `DD_DELETE` rows go to a `__deletes` view plus a `DELETE` post-hook; `DD_REJECT` rows go to a `__rejects` view |
| Target | `fct_`/`dim_` model: `CAST` to target types, unconnected columns as `NULL`, `DBT_UPDATED_AT = CURRENT_TIMESTAMP` |
| Rank, Normalizer, Stored Procedure, SQL, Custom, Java, Transaction Control, Mapplet | **not converted**: a pass-through stub is emitted and the item is flagged `UNSUPPORTED` in the coverage matrix and the review report |

### Expression translator

A real tokenizer and recursive-descent parser (`expr/`), then AST to Oracle SQL. It handles `IIF`, `DECODE` (including
`DECODE(TRUE, ...)`), `ISNULL`, `IN`, `TO_DATE`/`TO_CHAR` format conversion, date functions, string and regex
functions, aggregates, `$$params`, `SESSSTARTTIME`, `DD_*` constants and lookup calls. Oracle rules applied:
`''` is `NULL` (comparisons to `''` become `IS [NOT] NULL`), booleans become `1`/`0`, explicit `TO_DATE` formats.
Unmapped functions, syntax errors, `ERROR()`/`ABORT()`, `SETVARIABLE` and session parameters go to manual review.
Per-function overrides can be added in the config (`function_overrides`). No LLM is called.

## Quality gates (run on every `convert`)

1. **Zero-drop**: every `INSTANCE` and `TRANSFORMATION` in the XML must appear in the coverage matrix with a real
   artifact, and that artifact's CTE must exist in the written model. Connectors that were not applied are flagged.
2. **Expression gate**: every expression in Expression/Aggregator/Filter nodes must have been translated.
3. **Static SQL**: each model, macro and analysis is rendered through Jinja with dbt-style `ref/source/var/config`
   and parsed with `sqlglot` (Oracle dialect). The model graph is checked for missing refs, undeclared sources and cycles.

Tests in `schema.yml`: `not_null` and `unique` on target primary keys (`unique_combination_of_columns` for composite
keys), `not_null` on NOT NULL target fields, `accepted_values` derived from literal `IIF`/`DECODE` results, and
`dbt_utils.expression_is_true` derived from Filter and Router conditions on pass-through columns.

## Reconciliation

`analyses/recon_<mart>.sql` has two parts. Part 1 counts rows in each source table and in the target, and states
whether the mapping's filters/joins/aggregators mean the counts should differ. Part 2 compares the dbt target with the
table PowerCenter loaded (`var('legacy_schema')`): row count, `SUM` of numeric columns, non-null counts,
`SUM(ORA_HASH(col))` per column, and `MINUS` in both directions, with a PASS/FAIL status per metric.
The XML holds definitions, not data, so Part 2 needs a live connection to both schemas.

## Configuration

See `config.example.yml`. Decisions that were still open are switches with conservative defaults:

| Key | Default | Meaning |
|---|---|---|
| `sequence_mode` | `row_number` | `row_number` restarts every run; `oracle_sequence` emits DDL and forces a table materialization (Oracle forbids sequences in views) |
| `no_update_strategy_load` | `table` | target without Update Strategy: truncate-and-load, or `incremental_append` |
| `int_materialization` | `view` | `view` or `ephemeral` |
| `source_name`, `default_source_schema` | `raw`, none | dbt source naming for tables without an owner |
| `parameters` | none | overrides for `$$PARAM` defaults |
| `function_overrides` | none | `FUNC: "SQL with {0} {1}"` |

## Known limits (read before trusting the output)

* **Not run against a real Oracle database or dbt.** The generated SQL is parsed and Jinja-rendered, not executed.
  Run `dbt parse`, `dbt compile` and `dbt build` in a sandbox schema before relying on it. Incremental models use
  `unique_key` only, which is what the dbt-oracle documentation shows; confirm the upsert behaviour in your adapter version.
* **Tested on sanitized fixtures built by the author** (`tests/make_fixtures.py`), shaped like real exports. Real
  exports vary (port-type strings for lookups, Router/Union/Rank details, reusable definitions). Run it on a real
  export and read `reports/manual_review.md` first; the parser records mapping nodes it does not understand.
* **Row-by-row vs set-based semantics.** Variable ports that read their own previous value (running totals) are
  flagged, not converted. Lookup "first/last" order, Joiner NULL-key matching and `DATE_DIFF` truncation are called out
  as warnings. Sequence keys from `ROW_NUMBER()` are not stable across runs.
* **Unconnected lookup macros** are correlated scalar subqueries; on very large lookup tables a join may perform better.
* A target fed by several pipelines loads only the first (flagged). Mapplets are flagged, not expanded.
* `VALIDATE_CONVERSION` (used for `IS_NUMBER`/`IS_DATE`) needs Oracle 12.2 or later.
* Pre/post-session SQL, parameter files and session-level settings are not in the mapping XML; Source Qualifier
  Pre/Post SQL is flagged.
* sqlfluff linting and an LLM-assist mode for unmapped functions are not implemented.

## Extending

Add a handler to `handlers.py` (`HANDLERS[kind]`), a function to `expr/translator.py` (`f_<name>` method), or a
`function_overrides` entry. Add a fixture to `tests/make_fixtures.py` and a golden file (`UPDATE_GOLDEN=1 pytest`).
