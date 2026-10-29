{{ config(
    materialized='incremental',
    alias='fct_orders_high',
    unique_key=['ORDER_ID'],
    post_hook=["DELETE FROM {{ this }} WHERE (ORDER_ID) IN (SELECT ORDER_ID FROM {{ ref('fct_orders_high__deletes') }})"]
) }}

-- Replaces Informatica target FCT_ORDERS_HIGH (instance FCT_ORDERS_HIGH) and Update Strategy UPD_HIGH
SELECT
    CAST(src.ORDER_ID AS NUMBER(10,0)) AS ORDER_ID,
    CAST(src.AMOUNT AS NUMBER(12,2)) AS AMOUNT,
    CAST(src.O_BAND AS VARCHAR2(4)) AS BAND,
    CAST(src.O_STATUS_DESC AS VARCHAR2(10)) AS STATUS_DESC,
    CAST(src.O_REGION AS VARCHAR2(30)) AS REGION,
    CAST(src.O_ORDER_SK AS NUMBER(10,0)) AS SK,
    CURRENT_TIMESTAMP AS DBT_UPDATED_AT
FROM {{ ref('int_upd_high') }} src
WHERE src.INFA_DML_FLAG IN (0, 1)
