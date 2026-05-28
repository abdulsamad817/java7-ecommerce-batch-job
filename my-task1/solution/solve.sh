#!/bin/bash
WORK="/tmp/order_job"
BUILD="$WORK/build"
SRC="$WORK/src"
rm -rf "$WORK"
mkdir -p "$BUILD" "$SRC"

cat >"$SRC/OrderProcessor.java" <<'JAVA'
import java.io.*;
import java.math.*;
import java.nio.charset.Charset;
import java.security.*;
import java.sql.*;
import java.text.*;
import java.util.*;

public class OrderProcessor {
  private static final Charset UTF8 = Charset.forName("UTF-8");
  private static final TimeZone UTC = TimeZone.getTimeZone("UTC");
  private static final SimpleDateFormat DF = new SimpleDateFormat("yyyy-MM-dd", Locale.US);
  private static final String JDBC_URL = "jdbc:h2:file:/app/output/orders;MODE=DB2";
  private static final String DB_USER = "sa";
  private static final String DB_PASS = "";
  private static final File CUSTOMERS = new File("/app/data/customers.csv");
  private static final File ORDERS = new File("/app/data/orders.csv");
  private static final File TAX_RULES = new File("/app/data/tax_rules.csv");
  private static final File SHIPPING_RATES = new File("/app/data/shipping_rates.csv");
  private static final File DISCOUNT_RULES = new File("/app/data/discount_rules.csv");
  private static final File PROCESS_DATE = new File("/app/data/process_date.txt");
  private static final File OUT_DIR = new File("/app/output");
  private static final File OUT_ORDERS = new File("/app/output/processed_orders.csv");
  private static final File OUT_REJECTIONS = new File("/app/output/rejected_orders.csv");
  private static final File OUT_SUMMARY = new File("/app/output/summary.json");

  static { DF.setLenient(false); DF.setTimeZone(UTC); }

  static final class Customer {
    final String id, country, region;
    final BigDecimal creditLimit;
    Customer(String id, String country, String region, BigDecimal creditLimit) {
      this.id=id; this.country=country; this.region=region; this.creditLimit=creditLimit;
    }
  }

  static final class OrderRow {
    final int row;
    final String orderId, customerId, itemCode, status, discountCode;
    final int quantity;
    final BigDecimal unitPrice, weightPerItem;
    final java.util.Date orderDate;
    OrderRow(int row, String orderId, String customerId, String itemCode, int quantity, BigDecimal unitPrice, BigDecimal weightPerItem, java.util.Date orderDate, String status, String discountCode) {
      this.row=row; this.orderId=orderId; this.customerId=customerId; this.itemCode=itemCode; this.quantity=quantity;
      this.unitPrice=unitPrice; this.weightPerItem=weightPerItem; this.orderDate=orderDate; this.status=status; this.discountCode=discountCode;
    }
  }

  static final class TaxRule {
    final String country, region;
    final BigDecimal taxRate, vatRate;
    TaxRule(String country, String region, BigDecimal taxRate, BigDecimal vatRate) {
      this.country=country; this.region=region; this.taxRate=taxRate; this.vatRate=vatRate;
    }
  }

  static final class ShippingRate {
    final String country;
    final BigDecimal weightMin, weightMax, cost, freeThreshold;
    ShippingRate(String country, BigDecimal weightMin, BigDecimal weightMax, BigDecimal cost, BigDecimal freeThreshold) {
      this.country=country; this.weightMin=weightMin; this.weightMax=weightMax; this.cost=cost; this.freeThreshold=freeThreshold;
    }
  }

  static final class DiscountRule {
    final String code, type;
    final BigDecimal value, minSubtotal;
    final java.util.Date expiryDate;
    DiscountRule(String code, String type, BigDecimal value, BigDecimal minSubtotal, java.util.Date expiryDate) {
      this.code=code; this.type=type; this.value=value; this.minSubtotal=minSubtotal; this.expiryDate=expiryDate;
    }
  }

  static final class Rejection { 
    final int row; 
    final String orderId, reason; 
    Rejection(int row, String orderId, String reason) { 
      this.row=row; this.orderId=orderId; this.reason=reason; 
    } 
  }

  static final class ProcessedOrder { 
    final String orderId, customerId; 
    final BigDecimal subtotal, discount, tax, shipping, total; 
    ProcessedOrder(String orderId, String customerId, BigDecimal subtotal, BigDecimal discount, BigDecimal tax, BigDecimal shipping, BigDecimal total) { 
      this.orderId=orderId; this.customerId=customerId; this.subtotal=subtotal; this.discount=discount; this.tax=tax; this.shipping=shipping; this.total=total; 
    } 
  }

  static final class Summary { 
    final String md5; 
    final BigDecimal totalRevenue; 
    Summary(String md5, BigDecimal totalRevenue) { 
      this.md5=md5; this.totalRevenue=totalRevenue; 
    } 
  }

  public static void main(String[] args) throws Exception {
    TimeZone.setDefault(UTC);
    prepOut();
    String procStr = readLine(PROCESS_DATE);
    java.util.Date procDate = parseDate(procStr);
    List<Customer> customers = readCustomers();
    Map<String, Customer> customerById = new HashMap<String, Customer>();
    for (Customer c : customers) customerById.put(c.id, c);
    List<OrderRow> orders = readOrders();
    List<TaxRule> taxRules = readTaxRules();
    Map<String,TaxRule> taxByRegion = new HashMap<String,TaxRule>();
    for (TaxRule t : taxRules) taxByRegion.put(t.country+"|"+t.region, t);
    List<ShippingRate> shippingRates = readShippingRates();
    Map<String, DiscountRule> discountById = readDiscountRules();
    List<ProcessedOrder> processed = new ArrayList<ProcessedOrder>();
    List<Rejection> rejections = new ArrayList<Rejection>();
    int validOrders = processOrders(procDate, orders, customerById, taxByRegion, shippingRates, discountById, processed, rejections);
    writeRejections(rejections);
    Summary summary = writeProcessedOrders(procStr, processed);
    writeStats(procStr, customers.size(), validOrders, rejections.size(), summary.totalRevenue, summary.md5);
    buildDb(procStr, customers, orders, validOrders, rejections.size(), summary.md5);
  }

  private static void prepOut() {
    OUT_DIR.mkdirs();
    File[] files = new File[] { OUT_ORDERS, OUT_REJECTIONS, OUT_SUMMARY, 
        new File("/app/output/orders.h2.db"), 
        new File("/app/output/orders.lock.db"), 
        new File("/app/output/orders.trace.db") };
    for (File f : files) if (f.exists()) f.delete();
  }

  private static int processOrders(java.util.Date procDate, List<OrderRow> orders, Map<String, Customer> customers, Map<String,TaxRule> taxRules, List<ShippingRate> shippingRates, Map<String, DiscountRule> discounts, List<ProcessedOrder> processed, List<Rejection> rejections) {
    Map<String, Integer> firstOrderRow = new HashMap<String, Integer>();
    for (OrderRow o : orders) if (!firstOrderRow.containsKey(o.orderId)) firstOrderRow.put(o.orderId, Integer.valueOf(o.row));
    int valid = 0;
    for (OrderRow o : orders) {
      String reason = null;
      Integer firstRow = firstOrderRow.get(o.orderId);
      if (firstRow != null && firstRow.intValue() != o.row) {
        reason = "ORDER_DUPLICATE";
      }
      Customer cust = customers.get(o.customerId);
      if (reason == null) {
        if (cust == null) reason = "CUSTOMER_NOT_FOUND";
      }
      if (reason == null) {
        if (o.orderDate.after(procDate)) reason = "ORDER_DATE_AFTER_PROCESS";
      }
      if (reason == null) {
        String statusTrimmed = o.status.trim();
        if (!"CONFIRMED".equals(statusTrimmed) && !"PENDING".equals(statusTrimmed)) {
          reason = statusTrimmed;
        }
      }
      BigDecimal subtotal = o.unitPrice.multiply(new BigDecimal(o.quantity)).setScale(2, RoundingMode.HALF_EVEN);
      BigDecimal discountAmount = BigDecimal.ZERO;
      String discCodeTrimmed = o.discountCode != null ? o.discountCode.trim() : "";
      if (reason == null && !discCodeTrimmed.isEmpty()) {
        DiscountRule dr = discounts.get(discCodeTrimmed);
        if (dr == null) {
          reason = "DISCOUNT_NOT_FOUND";
        }
        else if (dr.expiryDate.before(procDate)) {
          reason = "DISCOUNT_EXPIRED";
        }
        else if (subtotal.compareTo(dr.minSubtotal) < 0) {
          reason = "DISCOUNT_MIN_UNMET";
        }
        else {
          if ("PERCENT".equals(dr.type)) {
            discountAmount = subtotal.multiply(dr.value).setScale(2, RoundingMode.HALF_EVEN);
          } else {
            discountAmount = dr.value.setScale(2, RoundingMode.HALF_EVEN);
          }
          if (discountAmount.compareTo(subtotal) > 0) discountAmount = subtotal;
        }
      }
      if (reason != null) { rejections.add(new Rejection(o.row, o.orderId, reason)); continue; }
      BigDecimal adjustedSubtotal = subtotal.subtract(discountAmount).setScale(2, RoundingMode.HALF_EVEN);
      String taxKey = cust.country + "|" + cust.region;
      TaxRule tr = taxRules.get(taxKey);
      BigDecimal tax = BigDecimal.ZERO;
      if (tr != null) {
        BigDecimal rate = tr.country.equals("US") ? tr.taxRate : tr.vatRate;
        tax = adjustedSubtotal.multiply(rate).setScale(2, RoundingMode.HALF_EVEN);
      }
      BigDecimal totalWeight = o.weightPerItem.multiply(new BigDecimal(o.quantity)).setScale(2, RoundingMode.HALF_EVEN);
      BigDecimal shipping = findShippingCost(cust.country, totalWeight, adjustedSubtotal, shippingRates);
      BigDecimal total = adjustedSubtotal.add(tax).add(shipping).setScale(2, RoundingMode.HALF_EVEN);
      processed.add(new ProcessedOrder(o.orderId, o.customerId, subtotal, discountAmount, tax, shipping, total));
      valid++;
    }
    Collections.sort(rejections, new Comparator<Rejection>() { public int compare(Rejection a, Rejection b) { return a.row - b.row; } });
    return valid;
  }

  private static BigDecimal findShippingCost(String country, BigDecimal weight, BigDecimal subtotal, List<ShippingRate> rates) {
    for (ShippingRate sr : rates) {
      if (sr.country.equals(country) && weight.compareTo(sr.weightMin) >= 0 && weight.compareTo(sr.weightMax) <= 0) {
        if (subtotal.compareTo(sr.freeThreshold) >= 0) return BigDecimal.ZERO.setScale(2, RoundingMode.HALF_EVEN);
        return sr.cost.setScale(2, RoundingMode.HALF_EVEN);
      }
    }
    return BigDecimal.ZERO.setScale(2, RoundingMode.HALF_EVEN);
  }

  private static void writeRejections(List<Rejection> rejections) throws Exception {
    BufferedWriter w = new BufferedWriter(new OutputStreamWriter(new FileOutputStream(OUT_REJECTIONS), UTF8));
    try {
      w.write("row_num,order_id,reason\n");
      for (Rejection r : rejections) w.write(r.row + "," + r.orderId + "," + r.reason + "\n");
    } finally { w.close(); }
  }

  private static Summary writeProcessedOrders(String procStr, List<ProcessedOrder> processed) throws Exception {
    List<ProcessedOrder> sorted = new ArrayList<ProcessedOrder>(processed);
    Collections.sort(sorted, new Comparator<ProcessedOrder>() { public int compare(ProcessedOrder a, ProcessedOrder b) { return a.orderId.compareTo(b.orderId); } });
    MessageDigest md = MessageDigest.getInstance("MD5");
    DigestOutputStream dos = new DigestOutputStream(new FileOutputStream(OUT_ORDERS), md);
    BufferedWriter w = new BufferedWriter(new OutputStreamWriter(dos, UTF8));
    BigDecimal totalRevenue = BigDecimal.ZERO;
    try {
      w.write("order_id,customer_id,subtotal,discount_amount,tax_amount,shipping_cost,total_amount\n");
      for (ProcessedOrder po : sorted) {
        totalRevenue = totalRevenue.add(po.total);
        w.write(po.orderId + "," + po.customerId + "," + po.subtotal.setScale(2, RoundingMode.HALF_EVEN).toPlainString() + "," + po.discount.setScale(2, RoundingMode.HALF_EVEN).toPlainString() + "," + po.tax.setScale(2, RoundingMode.HALF_EVEN).toPlainString() + "," + po.shipping.setScale(2, RoundingMode.HALF_EVEN).toPlainString() + "," + po.total.setScale(2, RoundingMode.HALF_EVEN).toPlainString() + "\n");
      }
    } finally { w.close(); }
    return new Summary(toHex(md.digest()), totalRevenue.setScale(2, RoundingMode.HALF_EVEN));
  }

  private static void writeStats(String procDate, int customers, int processed, int rejected, BigDecimal totalRevenue, String md5) throws Exception {
    String j = "{\"process_date\":\"" + procDate + "\",\"total_orders\":" + (processed + rejected) + ",\"processed_orders\":" + processed + ",\"rejected_orders\":" + rejected + ",\"total_revenue\":\"" + totalRevenue.toPlainString() + "\",\"summary_md5\":\"" + md5 + "\"}";
    FileOutputStream out = new FileOutputStream(OUT_SUMMARY);
    try { out.write(j.getBytes(UTF8)); } finally { out.close(); }
  }

  private static void buildDb(String procDate, List<Customer> customers, List<OrderRow> orders, int processed, int rejected, String md5) throws Exception {
    Class.forName("org.h2.Driver");
    Connection c = DriverManager.getConnection(JDBC_URL, DB_USER, DB_PASS);
    try {
      c.setAutoCommit(false);
      Statement st = c.createStatement();
      st.execute("DROP TABLE IF EXISTS PROCESS_AUDIT");
      st.execute("DROP TABLE IF EXISTS ORDERS");
      st.execute("DROP TABLE IF EXISTS CUSTOMERS");
      st.execute("CREATE TABLE CUSTOMERS (customer_id VARCHAR PRIMARY KEY, country VARCHAR, region VARCHAR, credit_limit DECIMAL(18,2))");
      st.execute("CREATE TABLE ORDERS (row_num INT PRIMARY KEY, order_id VARCHAR, customer_id VARCHAR, item_code VARCHAR, quantity INT, unit_price DECIMAL(18,2), weight_per_item DECIMAL(18,2), order_date DATE, status VARCHAR, discount_code VARCHAR)");
      st.execute("CREATE TABLE PROCESS_AUDIT (jdbc_url VARCHAR, process_date VARCHAR, customers INT, processed_orders INT, rejected_orders INT, summary_md5 VARCHAR)");
      
      PreparedStatement pc = c.prepareStatement("INSERT INTO CUSTOMERS(customer_id,country,region,credit_limit) VALUES(?,?,?,?)");
      for (Customer cu : customers) {
        pc.setString(1, cu.id);
        pc.setString(2, cu.country);
        pc.setString(3, cu.region);
        pc.setBigDecimal(4, cu.creditLimit.setScale(2, RoundingMode.HALF_EVEN));
        pc.addBatch();
      }
      pc.executeBatch(); pc.close();
      
      PreparedStatement po = c.prepareStatement("INSERT INTO ORDERS(row_num,order_id,customer_id,item_code,quantity,unit_price,weight_per_item,order_date,status,discount_code) VALUES(?,?,?,?,?,?,?,?,?,?)");
      for (OrderRow or : orders) {
        po.setInt(1, or.row);
        po.setString(2, or.orderId);
        po.setString(3, or.customerId);
        po.setString(4, or.itemCode);
        po.setInt(5, or.quantity);
        po.setBigDecimal(6, or.unitPrice.setScale(2, RoundingMode.HALF_EVEN));
        po.setBigDecimal(7, or.weightPerItem.setScale(2, RoundingMode.HALF_EVEN));
        po.setDate(8, new java.sql.Date(or.orderDate.getTime()));
        po.setString(9, or.status);
        if (or.discountCode == null || or.discountCode.isEmpty()) po.setNull(10, Types.VARCHAR); else po.setString(10, or.discountCode);
        po.addBatch();
      }
      po.executeBatch(); po.close();
      
      PreparedStatement pa = c.prepareStatement("INSERT INTO PROCESS_AUDIT(jdbc_url,process_date,customers,processed_orders,rejected_orders,summary_md5) VALUES(?,?,?,?,?,?)");
      pa.setString(1, JDBC_URL);
      pa.setString(2, procDate);
      pa.setInt(3, customers.size());
      pa.setInt(4, processed);
      pa.setInt(5, rejected);
      pa.setString(6, md5);
      pa.executeUpdate(); pa.close();
      c.commit();
    } finally { c.close(); }
  }

  private static List<Customer> readCustomers() throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(CUSTOMERS), UTF8));
    try {
      String header = r.readLine(); if (header == null) return new ArrayList<Customer>();
      assertHeader(header, "customer_id","country","region","credit_limit");
      List<Customer> out = new ArrayList<Customer>();
      String line;
      while ((line = r.readLine()) != null) {
        if (line.trim().isEmpty()) continue;
        String[] p = split(line, 4);
        out.add(new Customer(p[0], p[1], p[2], new BigDecimal(p[3])));
      }
      return out;
    } finally { r.close(); }
  }

  private static List<OrderRow> readOrders() throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(ORDERS), UTF8));
    try {
      String header = r.readLine(); if (header == null) return new ArrayList<OrderRow>();
      assertHeader(header, "order_id","customer_id","item_code","quantity","unit_price","weight_per_item","order_date","status","discount_code");
      List<OrderRow> out = new ArrayList<OrderRow>();
      String line; int row = 0;
      while ((line = r.readLine()) != null) {
        if (line.trim().isEmpty()) continue;
        row++;
        String[] p = split(line, 9);
        String discCode = p[8].isEmpty() ? null : p[8];
        out.add(new OrderRow(row, p[0], p[1], p[2], Integer.parseInt(p[3]), new BigDecimal(p[4]), new BigDecimal(p[5]), parseDate(p[6]), p[7], discCode));
      }
      return out;
    } finally { r.close(); }
  }

  private static List<TaxRule> readTaxRules() throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(TAX_RULES), UTF8));
    try {
      String header = r.readLine(); if (header == null) return new ArrayList<TaxRule>();
      assertHeader(header, "country","region","tax_rate","vat_rate");
      List<TaxRule> out = new ArrayList<TaxRule>();
      String line;
      while ((line = r.readLine()) != null) {
        if (line.trim().isEmpty()) continue;
        String[] p = split(line, 4);
        out.add(new TaxRule(p[0], p[1], new BigDecimal(p[2]), new BigDecimal(p[3])));
      }
      return out;
    } finally { r.close(); }
  }

  private static List<ShippingRate> readShippingRates() throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(SHIPPING_RATES), UTF8));
    try {
      String header = r.readLine(); if (header == null) return new ArrayList<ShippingRate>();
      assertHeader(header, "country","weight_min","weight_max","cost","free_threshold");
      List<ShippingRate> out = new ArrayList<ShippingRate>();
      String line;
      while ((line = r.readLine()) != null) {
        if (line.trim().isEmpty()) continue;
        String[] p = split(line, 5);
        out.add(new ShippingRate(p[0], new BigDecimal(p[1]), new BigDecimal(p[2]), new BigDecimal(p[3]), new BigDecimal(p[4])));
      }
      return out;
    } finally { r.close(); }
  }

  private static Map<String, DiscountRule> readDiscountRules() throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(DISCOUNT_RULES), UTF8));
    try {
      String header = r.readLine(); if (header == null) return new HashMap<String, DiscountRule>();
      assertHeader(header, "code","discount_type","discount_value","min_subtotal","max_uses","expiry_date");
      Map<String, DiscountRule> out = new HashMap<String, DiscountRule>();
      String line;
      while ((line = r.readLine()) != null) {
        if (line.trim().isEmpty()) continue;
        String[] p = split(line, 6);
        out.put(p[0], new DiscountRule(p[0], p[1], new BigDecimal(p[2]), new BigDecimal(p[3]), parseDate(p[5])));
      }
      return out;
    } finally { r.close(); }
  }

  private static void assertHeader(String headerLine, String... cols) {
    String[] got = headerLine.split(",", -1);
    if (got.length != cols.length) die("bad CSV header");
    for (int i = 0; i < cols.length; i++) if (!cols[i].equals(got[i].trim())) die("bad CSV header col " + i);
  }

  private static String[] split(String line, int expected) { 
    String[] p = line.split(",", -1); 
    if (p.length != expected) die("bad CSV row"); 
    return p; 
  }

  private static String readLine(File f) throws Exception {
    BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(f), UTF8));
    try { String s = r.readLine(); if (s == null) die("missing process_date"); return s.trim(); } finally { r.close(); }
  }

  private static java.util.Date parseDate(String s) throws Exception { 
    synchronized (DF) { return DF.parse(s); } 
  }

  private static String toHex(byte[] b) { 
    StringBuilder sb = new StringBuilder(); 
    for (int i = 0; i < b.length; i++) { 
      int x = b[i] & 0xff; 
      if (x < 16) sb.append('0'); 
      sb.append(Integer.toHexString(x)); 
    } 
    return sb.toString(); 
  }

  private static void die(String msg) { 
    System.err.println(msg); 
    System.exit(1); 
  }
}
JAVA

javac -source 7 -target 7 -cp "/opt/legacy-lib/*" -d "$BUILD" "$SRC/OrderProcessor.java"
jar cf /app/job.jar -C "$BUILD" .
rm -rf /app/output
mkdir -p /app/output
java -cp "/app/job.jar:/opt/legacy-lib/*" OrderProcessor