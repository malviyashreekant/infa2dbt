# Informatica-to-dbt coverage matrix

## Mapping `m_customer_summary`

| Informatica name | Type | dbt artifact | Layer | Status | Notes |
|---|---|---|---|---|---|
| CUSTOMERS | Source Definition | `stg_customers` | stg | CONVERTED |  |
| ORDERS | Source Definition | `stg_orders` | stg | CONVERTED |  |
| DIM_CUST_SUMMARY | Target Definition | `dim_cust_summary` | mart | CONVERTED | no Update Strategy: assumed truncate-and-load (table materialization) |
| SQ_CUSTOMERS | Source Qualifier | `stg_sq_customers` | stg | CONVERTED |  |
| SQ_ORDERS | Source Qualifier | `stg_sq_orders` | stg | CONVERTED |  |
| JNR_CUST_ORDERS | Joiner | `int_rnk_top.jnr_cust_orders` | int | CONVERTED | 1 expression(s), 0 flagged; join keys: PowerCenter may treat NULL keys as equal, SQL does not; verify NULL join keys |
| AGG_CUST | Aggregator | `int_rnk_top.agg_cust` | int | CONVERTED | 4 expression(s), 0 flagged |
| LKP_SEG | Lookup Procedure | `int_rnk_top.lkp_seg` | int | CONVERTED | first/last match uses PowerCenter's default ORDER BY on the lookup ports; verify |
| SRT_CUST | Sorter | `int_rnk_top.srt_cust` | int | CONVERTED | ORDER BY is kept for traceability; it only matters if a later step depends on row order |
| RNK_TOP | Rank | `int_rnk_top.rnk_top` | int | UNSUPPORTED |  |
| SP_AUDIT | Stored Procedure | `(none)` | int | UNSUPPORTED | not connected;  |

Instances: 11 (rows incl. router groups: 11) | converted: 9 | needs review: 0 | unsupported: 2

## Mapping `m_orders`

| Informatica name | Type | dbt artifact | Layer | Status | Notes |
|---|---|---|---|---|---|
| ORDERS | Source Definition | `stg_orders` | stg | CONVERTED |  |
| CUSTOMERS | Source Definition | `stg_customers` | stg | CONVERTED |  |
| FCT_ORDERS_HIGH | Target Definition | `fct_orders_high` | mart | CONVERTED |  |
| FCT_ORDERS_LOW | Target Definition | `fct_orders_low` | mart | CONVERTED | no Update Strategy: assumed truncate-and-load (table materialization) |
| SQ_ORDERS_CUSTOMERS | Source Qualifier | `stg_sq_orders_customers` | stg | CONVERTED | Source Qualifier join/filter copied verbatim (SQL); check column qualifiers |
| SEQ_KEY | Sequence | `inline expression in EXP_CALC` | int | CONVERTED | mode=row_number; ROW_NUMBER() surrogate keys restart every run and are not stable across runs |
| EXP_CALC | Expression | `int_fil_valid.exp_calc` | int | CONVERTED | 7 expression(s), 0 flagged |
| LKP_REGION | Lookup (unconnected) | `macro lkp_lkp_region` | int | CONVERTED | first/last match uses PowerCenter's default ORDER BY on the lookup ports; verify |
| FIL_VALID | Filter | `int_fil_valid.fil_valid` | int | CONVERTED | 1 expression(s), 0 flagged |
| RTR_SPLIT | Router | `int_upd_high.rtr_split_high, int_rtr_split_low.rtr_split_low` | int | CONVERTED | 3 group(s) |
| UPD_HIGH | Update Strategy | `int_upd_high.upd_high` | int | CONVERTED | 1 expression(s), 0 flagged |
| RTR_SPLIT.HIGH | Router Group | `int_upd_high.rtr_split_high` | int | CONVERTED | o_BAND = 'HIGH' |
| RTR_SPLIT.LOW | Router Group | `int_rtr_split_low.rtr_split_low` | int | CONVERTED | o_BAND = 'LOW' |

Instances: 11 (rows incl. router groups: 13) | converted: 11 | needs review: 0 | unsupported: 0
