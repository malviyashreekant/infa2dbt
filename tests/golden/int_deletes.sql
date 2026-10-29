{{ config(materialized='view') }}

-- Keys flagged DD_DELETE by Update Strategy UPD_HIGH
SELECT CAST(src.ORDER_ID AS NUMBER(10,0)) AS ORDER_ID
FROM {{ ref('int_upd_high') }} src
WHERE src.INFA_DML_FLAG = 2
