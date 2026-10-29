{{ config(materialized='view') }}

-- Replaces Informatica source definition ORDERS (owner SALES)
-- Type casting and renaming only; business filters live in the Source Qualifier model.
SELECT
    CAST(ORDER_ID AS NUMBER(10,0)) AS ORDER_ID,
    CAST(CUST_ID AS NUMBER(10,0)) AS CUST_ID,
    CAST(AMOUNT AS NUMBER(12,2)) AS AMOUNT,
    CAST(STATUS AS VARCHAR2(1)) AS STATUS,
    CAST(ORDER_DT AS DATE) AS ORDER_DT
FROM {{ source('raw_sales', 'ORDERS') }}
