 E-commerce order processing batch for Java 7.
  Put OrderProcessor in /app/job.jar, compile to Java 7 bytecode, and run it.
  Running it again on the same day should give the same results.
  
  Inputs are in /app/data:
   - customers.csv
   - orders.csv
   - process_date.txt
   - tax_rules.csv
   - shipping_rates.csv
   - discount_rules.csv

  Outputs are in /app/output:
   - processed_orders.csv, LF + trailing newline.
     Order_id, customer_id, subtotal, discount_amount, tax_amount, shipping_cost, total_amount.
     All amounts 2dp, sorted by order_id.

   - rejected_orders.csv, LF + trailing newline.
     Title: row_num, order_id, and reason.
     Row_num is 1-based data row.

   - summary.json is single-line JSON without newlines.
     Fields: process_date (string), total_orders (number), processed_orders (number), rejected_orders (number), total_revenue (quoted string, 2dp), summary_md5 (string).
     summary_md5 is md5 of processed_orders.csv bytes. Example: {"process_date":"2024-01-15","total_orders":100,"processed_orders":95,"rejected_orders":5,"total_revenue":"853.07","summary_md5":"abc123..."}

   - orders.h2.db H2 database with CUSTOMERS and ORDERS (row_num 1..N).
     There is one row in PROCESS_AUDIT: jdbc_url, process_date, customers,
     processed_orders, rejected_orders, summary_md5.

  Use these rejection reasons in precedence order:
   - ORDER_DUPLICATE (order_id after first appearance, even if first row was invalid)
   - CUSTOMER_NOT_FOUND (customer_id not in customers.csv)
   - ORDER_DATE_AFTER_PROCESS (order_date > process_date)
   - STATUS_INVALID (status != CONFIRMED or PENDING; use actual status)
   - DISCOUNT_EXPIRED (discount_code expiry_date < process_date)
   - DISCOUNT_NOT_FOUND (discount_code not in discount_rules.csv, if referenced)
   - DISCOUNT_MIN_UNMET (subtotal < discount min_subtotal)

  Discount: PERCENT applies percentage to subtotal; FLAT deducts fixed amount.
  Clamped to [0, subtotal].
  Discount amount = discount_value * subtotal (PERCENT) or discount_value (FLAT),
  rounded HALF_EVEN 2dp.
  Adjusted subtotal = subtotal - discount, rounded HALF_EVEN 2dp.

  Tax/VAT: US uses tax_rate (sales tax); EU uses vat_rate (VAT).
  Look up by country+region.
  Tax amount = adjusted_subtotal * (tax_rate OR vat_rate), rounded HALF_EVEN 2dp.

  Shipping: Match country + weight (sum of all items).
  Use exact weight range [weight_min, weight_max].
  Shipping is free if adjusted_subtotal >= free_threshold, else use cost.
  Rounded HALF_EVEN 2dp.

  Total = adjusted_subtotal + tax + shipping, rounded HALF_EVEN 2dp.

  EXAMPLE: ORD2, CUST00002 (US,CA), qty=10, price=200 → subtotal=2000, DISC10(10%)=200, adj=1800, tax=148.50, ship=25, total=1973.50

  KEY POINTS:
  - Duplicate orders: reject after first
  - Expired discount: reject if expiry_date < process_date
  - Discount min unmet: reject if subtotal < min_subtotal
  - No discount code: skip discount step
  - Missing customer: reject order
  - US=tax_rate, EU=vat_rate
  - Sort output: lexicographic (ORD1, ORD10, ORD100, ORD2)
  - All amounts: 2dp, no $

  INPUT FILES:
  customers.csv: CUST00001,US,CA,50000.00 (5000 rows)
  orders.csv: ORD1,CUST00001,ITEM-A001,5,150.00,2.5,2024-01-30,CONFIRMED, (12000 rows)
  discount_rules.csv: DISC10,PERCENT,0.10,100.00,100,2024-01-31 (910 rows)
  tax_rules.csv: US,CA,0.0825,0.00 (3350 rows)
  shipping_rates.csv: US,0.00,5.00,10.00,50.00 (6490 rows)
  process_date.txt: 2024-01-15

  Don't touch /app/data.
  H2 1.3 DB2 mode; user sa has blank password.
  JDBC URL must be a single literal string that starts with "jdbc:h2:"
  and has "MODE=DB2" and "/app/output/orders" in it.
  Use at runtime is required.
  Java 7-only bytecode.