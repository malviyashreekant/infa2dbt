{{ config(materialized='view') }}

-- Replaces Informatica source definition CUSTOMERS (owner SALES)
-- Type casting and renaming only; business filters live in the Source Qualifier model.
SELECT
    CAST(CUST_ID AS NUMBER(10,0)) AS CUST_ID,
    CAST(CUST_NAME AS VARCHAR2(50)) AS CUST_NAME,
    CAST(SEGMENT AS VARCHAR2(10)) AS SEGMENT
FROM {{ source('raw_sales', 'CUSTOMERS') }}
