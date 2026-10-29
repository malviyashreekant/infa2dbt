{{ config(materialized='view') }}

-- Replaces Informatica: RTR_SPLIT.LOW (Router Group)
WITH
src_fil_valid AS (
  SELECT * FROM {{ ref('int_fil_valid') }}
),
rtr_split_low AS (
  SELECT src.ORDER_ID AS ORDER_ID2,
         src.AMOUNT AS AMOUNT2,
         src.O_BAND AS O_BAND2,
         src.O_STATUS_DESC AS O_STATUS_DESC2,
         src.O_REGION AS O_REGION2,
         src.O_ORDER_SK AS O_ORDER_SK2
    FROM src_fil_valid src
   WHERE src.O_BAND = 'LOW'
)

SELECT * FROM rtr_split_low
