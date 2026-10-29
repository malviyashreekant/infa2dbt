{{ config(materialized='view') }}

-- Rows flagged DD_REJECT by Update Strategy UPD_HIGH (PowerCenter writes these to the reject file)
SELECT src.* FROM {{ ref('int_upd_high') }} src WHERE src.INFA_DML_FLAG = 3
