"""Runs OrderProcessor and validates inputs, outputs, and DB state."""
import hashlib
import os
import secrets
import shutil
import subprocess
import zipfile
from datetime import date
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path

TWOP = Decimal("0.01")
DAYS = Decimal("365")

def _classfile_utf8_strings(raw: bytes) -> list:
    if raw[:4] != b"\xCA\xFE\xBA\xBE":
        return []
    out = []
    cp_count = int.from_bytes(raw[8:10], "big")
    i = 1
    p = 10
    while i < cp_count:
        tag = raw[p]
        p += 1
        if tag == 1:
            ln = int.from_bytes(raw[p:p + 2], "big")
            p += 2
            out.append(raw[p:p + ln].decode("utf-8", "replace"))
            p += ln
        elif tag in (3, 4):
            p += 4
        elif tag in (5, 6):
            p += 8
            i += 1
        elif tag in (7, 8, 16, 19, 20):
            p += 2
        elif tag in (9, 10, 11, 12, 18):
            p += 4
        elif tag == 15:
            p += 3
        else:
            break
        i += 1
    return out

def _build_jdbc_probe_agent(build_dir: Path) -> Path:
    agent_src_dir = build_dir / "src"
    agent_classes_dir = build_dir / "classes"
    agent_src_dir.mkdir(parents=True, exist_ok=True)
    agent_classes_dir.mkdir(parents=True, exist_ok=True)
    src = agent_src_dir / "JdbcProbeAgent.java"
    src.write_text(
        "package tbprobe; import java.io.FileOutputStream; import java.io.IOException; import java.lang.instrument.Instrumentation; import java.sql.Connection; import java.sql.Driver; import java.sql.DriverManager; import java.sql.DriverPropertyInfo; import java.sql.SQLException; import java.sql.SQLFeatureNotSupportedException; import java.util.Enumeration; import java.util.Properties; import java.util.logging.Logger;\n"
        "public final class JdbcProbeAgent { public static void premain(String agentArgs, Instrumentation inst) throws Exception { String markerPath = (agentArgs==null||agentArgs.length()==0)?\"/tmp/jdbc_probe.txt\":agentArgs; Enumeration<Driver> drivers = DriverManager.getDrivers(); while (drivers.hasMoreElements()) { Driver d = (Driver)drivers.nextElement(); if (\"org.h2.Driver\".equals(d.getClass().getName())) { DriverManager.deregisterDriver(d); } } DriverManager.registerDriver(new RecordingH2Driver(markerPath)); }\n"
        "private static final class RecordingH2Driver implements Driver { private final String markerPath; private final org.h2.Driver delegate = new org.h2.Driver(); RecordingH2Driver(String markerPath){this.markerPath=markerPath;} public boolean acceptsURL(String url) throws SQLException { return url!=null && url.startsWith(\"jdbc:h2:\"); }\n"
        "public Connection connect(String url, Properties info) throws SQLException { if (!acceptsURL(url)) return null; String user = info==null?null:info.getProperty(\"user\"); String pass = info==null?null:info.getProperty(\"password\"); record(url,user,pass); return delegate.connect(url, info);} public DriverPropertyInfo[] getPropertyInfo(String url, Properties info) throws SQLException { return delegate.getPropertyInfo(url, info);} public int getMajorVersion(){return delegate.getMajorVersion();} public int getMinorVersion(){return delegate.getMinorVersion();} public boolean jdbcCompliant(){return delegate.jdbcCompliant();} public Logger getParentLogger() throws SQLFeatureNotSupportedException { return Logger.getLogger(\"global\"); }\n"
        "private void record(String url,String user,String pass){ FileOutputStream out=null; try{ out=new FileOutputStream(markerPath); out.write(url.getBytes(\"UTF-8\")); out.write('\\n'); out.write((user==null?\"\":user).getBytes(\"UTF-8\")); out.write('\\n'); out.write((pass==null?\"\":pass).getBytes(\"UTF-8\")); } catch(IOException ignored){} finally{ if(out!=null){ try{out.close();}catch(IOException ignored2){} } } } } }\n",
        encoding="utf-8",
    )
    manifest = build_dir / "MANIFEST.MF"
    manifest.write_text(
        "Manifest-Version: 1.0\nPremain-Class: tbprobe.JdbcProbeAgent\n",
        encoding="utf-8",
    )
    subprocess.check_call([
        "javac", "-source", "1.7", "-target", "1.7", "-encoding", "UTF-8",
        "-cp", "/opt/legacy-lib/*", "-d", str(agent_classes_dir), str(src)
    ])
    agent_jar = build_dir / "jdbc-probe-agent.jar"
    if agent_jar.exists():
        agent_jar.unlink()
    subprocess.check_call(["jar", "cfm", str(agent_jar), str(manifest), "-C", str(agent_classes_dir), "."])
    return agent_jar

def _build_db_audit_check(build_dir: Path) -> Path:
    src_dir = build_dir / "src"
    classes_dir = build_dir / "classes"
    src_dir.mkdir(parents=True, exist_ok=True)
    classes_dir.mkdir(parents=True, exist_ok=True)
    src = src_dir / "DbAuditCheck.java"
    src.write_text(
        "import java.sql.Connection; import java.sql.DatabaseMetaData; import java.sql.DriverManager; import java.sql.ResultSet; import java.sql.Statement;\n"
        "public final class DbAuditCheck { public static void main(String[] args) throws Exception { if (args.length != 5) throw new IllegalArgumentException(\"expected 5 args\"); String expectedProcDate = args[0]; int expectedCustomers = Integer.parseInt(args[1]); int expectedProcessed = Integer.parseInt(args[2]); int expectedRejected = Integer.parseInt(args[3]); String expectedMd5 = args[4]; Class.forName(\"org.h2.Driver\"); Connection conn = DriverManager.getConnection(\"jdbc:h2:/app/output/orders;MODE=DB2;IFEXISTS=TRUE\", \"sa\", \"\"); try { assertTable(conn,\"CUSTOMERS\"); assertTable(conn,\"ORDERS\"); assertTable(conn,\"PROCESS_AUDIT\"); assertCount(conn,\"CUSTOMERS\", expectedCustomers); int orderCount = count(conn, \"ORDERS\"); Statement st = conn.createStatement(); ResultSet rs = st.executeQuery(\"SELECT MIN(row_num), MAX(row_num), COUNT(DISTINCT row_num) FROM ORDERS\"); rs.next(); int minRow = rs.getInt(1); int maxRow = rs.getInt(2); int distinctRow = rs.getInt(3); rs.close(); st.close(); if (minRow != 1 || maxRow != orderCount || distinctRow != orderCount) throw new RuntimeException(\"ORDERS row_num must be 1..N without gaps\"); st = conn.createStatement(); rs = st.executeQuery(\"SELECT jdbc_url,process_date,customers,processed_orders,rejected_orders,summary_md5 FROM PROCESS_AUDIT\"); if (!rs.next()) throw new RuntimeException(\"PROCESS_AUDIT missing row\"); String jdbcUrl = rs.getString(1); String procDate = rs.getString(2); int customers = rs.getInt(3); int processed = rs.getInt(4); int rejected = rs.getInt(5); String md5 = rs.getString(6); if (jdbcUrl == null || jdbcUrl.indexOf(\"jdbc:h2:\") != 0 || jdbcUrl.indexOf(\"/app/output/orders\") < 0 || jdbcUrl.indexOf(\"MODE=DB2\") < 0) throw new RuntimeException(\"PROCESS_AUDIT.jdbc_url must include jdbc:h2:, /app/output/orders, and MODE=DB2\"); if (!expectedProcDate.equals(procDate)) throw new RuntimeException(\"PROCESS_AUDIT.process_date mismatch\"); if (customers != expectedCustomers) throw new RuntimeException(\"PROCESS_AUDIT.customers mismatch\"); if (processed != expectedProcessed) throw new RuntimeException(\"PROCESS_AUDIT.processed_orders mismatch\"); if (rejected != expectedRejected) throw new RuntimeException(\"PROCESS_AUDIT.rejected_orders mismatch\"); if (!expectedMd5.equals(md5)) throw new RuntimeException(\"PROCESS_AUDIT.summary_md5 mismatch\"); if (rs.next()) throw new RuntimeException(\"PROCESS_AUDIT must have exactly one row\"); rs.close(); st.close(); } finally { conn.close(); } }\n"
        "private static void assertTable(Connection conn, String name) throws Exception { DatabaseMetaData md = conn.getMetaData(); ResultSet rs = md.getTables(null, null, name, null); try { if (!rs.next()) throw new RuntimeException(\"missing table: \" + name); } finally { rs.close(); } }\n"
        "private static int count(Connection conn, String table) throws Exception { Statement st = conn.createStatement(); ResultSet rs = st.executeQuery(\"SELECT COUNT(*) FROM \" + table); rs.next(); int out = rs.getInt(1); rs.close(); st.close(); return out; }\n"
        "private static void assertCount(Connection conn, String table, int expected) throws Exception { int got = count(conn, table); if (got != expected) throw new RuntimeException(table + \" row count mismatch\"); } }\n",
        encoding="utf-8",
    )
    subprocess.check_call([
        "javac", "-source", "1.7", "-target", "1.7", "-encoding", "UTF-8",
        "-cp", "/opt/legacy-lib/*", "-d", str(classes_dir), str(src)
    ])
    return classes_dir

def _read_csv(path: Path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = lines[0].split(",")
    rows = [ln.split(",", -1) for ln in lines[1:] if ln.strip()]
    return header, rows

def _parse_date(value: str) -> date:
    return date.fromisoformat(value)

def _compute_expected():
    proc_date_str = Path("/app/data/process_date.txt").read_text(encoding="utf-8").strip()
    proc_date = _parse_date(proc_date_str)

    cust_header, cust_rows = _read_csv(Path("/app/data/customers.csv"))
    assert cust_header == ["customer_id", "country", "region", "credit_limit"]
    customers = {}
    for row in cust_rows:
        customers[row[0]] = {"country": row[1], "region": row[2], "credit_limit": Decimal(row[3])}

    ord_header, ord_rows = _read_csv(Path("/app/data/orders.csv"))
    assert ord_header == ["order_id", "customer_id", "item_code", "quantity", "unit_price", "weight_per_item", "order_date", "status", "discount_code"]
    orders = []
    for i, row in enumerate(ord_rows, start=1):
        orders.append({
            "row_num": i,
            "order_id": row[0],
            "customer_id": row[1],
            "item_code": row[2],
            "quantity": int(row[3]),
            "unit_price": Decimal(row[4]),
            "weight_per_item": Decimal(row[5]),
            "order_date": _parse_date(row[6]),
            "status": row[7],
            "discount_code": row[8] or None,
        })

    tax_header, tax_rows = _read_csv(Path("/app/data/tax_rules.csv"))
    assert tax_header == ["country", "region", "tax_rate", "vat_rate"]
    tax_rules = {}
    for row in tax_rows:
        tax_rules[row[0] + "|" + row[1]] = {"tax_rate": Decimal(row[2]), "vat_rate": Decimal(row[3])}

    ship_header, ship_rows = _read_csv(Path("/app/data/shipping_rates.csv"))
    assert ship_header == ["country", "weight_min", "weight_max", "cost", "free_threshold"]
    shipping_rates = []
    for row in ship_rows:
        shipping_rates.append({
            "country": row[0],
            "weight_min": Decimal(row[1]),
            "weight_max": Decimal(row[2]),
            "cost": Decimal(row[3]),
            "free_threshold": Decimal(row[4]),
        })

    disc_header, disc_rows = _read_csv(Path("/app/data/discount_rules.csv"))
    assert disc_header == ["code", "discount_type", "discount_value", "min_subtotal", "max_uses", "expiry_date"]
    discounts = {}
    for row in disc_rows:
        discounts[row[0]] = {
            "type": row[1],
            "value": Decimal(row[2]),
            "min_subtotal": Decimal(row[3]),
            "expiry_date": _parse_date(row[5]),
        }

    first_order_row = {}
    for order in orders:
        if order["order_id"] not in first_order_row:
            first_order_row[order["order_id"]] = order["row_num"]

    processed = []
    rejected = []

    for order in orders:
        reason = None
        first_row = first_order_row.get(order["order_id"])

        if first_row is not None and first_row != order["row_num"]:
            reason = "ORDER_DUPLICATE"

        cust = customers.get(order["customer_id"])
        if reason is None and cust is None:
            reason = "CUSTOMER_NOT_FOUND"

        if reason is None and order["order_date"] > proc_date:
            reason = "ORDER_DATE_AFTER_PROCESS"

        if reason is None and order["status"] not in ("CONFIRMED", "PENDING"):
            reason = order["status"]

        subtotal = (Decimal(order["quantity"]) * order["unit_price"]).quantize(TWOP, rounding=ROUND_HALF_EVEN)
        discount_amount = Decimal("0.00")

        if reason is None and order["discount_code"]:
            disc = discounts.get(order["discount_code"])
            if disc is None:
                reason = "DISCOUNT_NOT_FOUND"
            elif disc["expiry_date"] < proc_date:
                reason = "DISCOUNT_EXPIRED"
            elif subtotal < disc["min_subtotal"]:
                reason = "DISCOUNT_MIN_UNMET"
            else:
                if disc["type"] == "PERCENT":
                    discount_amount = (subtotal * disc["value"]).quantize(TWOP, rounding=ROUND_HALF_EVEN)
                else:
                    discount_amount = disc["value"].quantize(TWOP, rounding=ROUND_HALF_EVEN)
                if discount_amount > subtotal:
                    discount_amount = subtotal

        if reason is not None:
            rejected.append((order["row_num"], order["order_id"], reason))
            continue

        adjusted_subtotal = (subtotal - discount_amount).quantize(TWOP, rounding=ROUND_HALF_EVEN)

        tax_key = cust["country"] + "|" + cust["region"]
        tr = tax_rules.get(tax_key)
        tax_amount = Decimal("0.00")
        if tr:
            rate = tr["tax_rate"] if cust["country"] == "US" else tr["vat_rate"]
            tax_amount = (adjusted_subtotal * rate).quantize(TWOP, rounding=ROUND_HALF_EVEN)

        total_weight = (order["weight_per_item"] * order["quantity"]).quantize(TWOP, rounding=ROUND_HALF_EVEN)
        shipping_cost = Decimal("0.00")
        for sr in shipping_rates:
            if (sr["country"] == cust["country"] and
                total_weight >= sr["weight_min"] and
                total_weight <= sr["weight_max"]):
                if adjusted_subtotal >= sr["free_threshold"]:
                    shipping_cost = Decimal("0.00").quantize(TWOP, rounding=ROUND_HALF_EVEN)
                else:
                    shipping_cost = sr["cost"].quantize(TWOP, rounding=ROUND_HALF_EVEN)
                break

        total = (adjusted_subtotal + tax_amount + shipping_cost).quantize(TWOP, rounding=ROUND_HALF_EVEN)
        processed.append((order["order_id"], order["customer_id"], subtotal, discount_amount, tax_amount, shipping_cost, total))

    rejected.sort(key=lambda x: x[0])

    rej_lines = ["row_num,order_id,reason"]
    rej_lines.extend([f"{r},{o},{re}" for r, o, re in rejected])
    rejections_csv = "\n".join(rej_lines) + "\n"

    proc_lines = ["order_id,customer_id,subtotal,discount_amount,tax_amount,shipping_cost,total_amount"]
    processed_sorted = sorted(processed, key=lambda x: x[0])
    total_revenue = Decimal("0.00")
    for proc_order in processed_sorted:
        total_revenue += proc_order[6]
        proc_lines.append(f"{proc_order[0]},{proc_order[1]},{proc_order[2]:.2f},{proc_order[3]:.2f},{proc_order[4]:.2f},{proc_order[5]:.2f},{proc_order[6]:.2f}")

    processed_csv = "\n".join(proc_lines) + "\n"
    total_revenue = total_revenue.quantize(TWOP, rounding=ROUND_HALF_EVEN)

    summary_md5 = hashlib.md5(processed_csv.encode("utf-8")).hexdigest()
    summary_json = f'{{"process_date":"{proc_date_str}","total_orders":{len(orders)},"processed_orders":{len(processed)},"rejected_orders":{len(rejected)},"total_revenue":"{total_revenue:.2f}","summary_md5":"{summary_md5}"}}'

    return processed_csv, rejections_csv, summary_json, len(processed), len(rejected), proc_date_str, len(customers), summary_md5

def test_outputs_via_java_validator():
    """Runs the job end-to-end and validates outputs + key runtime constraints."""
    out_dir = Path("/app/output")
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    data_files = {
        "/app/data/customers.csv",
        "/app/data/orders.csv",
        "/app/data/process_date.txt",
        "/app/data/tax_rules.csv",
        "/app/data/shipping_rates.csv",
        "/app/data/discount_rules.csv",
    }

    data_md5_before = {}
    for path in data_files:
        assert Path(path).exists(), f"input missing: {path}"
        data_md5_before[path] = hashlib.md5(Path(path).read_bytes()).hexdigest()

    job_jar = Path("/app/job.jar")
    assert job_jar.exists(), "/app/job.jar is missing"
    assert job_jar.stat().st_size > 0, "/app/job.jar is empty"

    with zipfile.ZipFile(job_jar, "r") as zf:
        class_entries = [n for n in zf.namelist() if n.endswith(".class")]
        assert class_entries, "/app/job.jar has no class files"

        for entry in class_entries:
            raw = zf.read(entry)
            assert raw[:4] == b"\xCA\xFE\xBA\xBE", "invalid class file in /app/job.jar"
            major = int.from_bytes(raw[6:8], "big")
            assert major == 51, f"/app/job.jar must be Java 7 bytecode (major=51), got {major}"

        literals = []
        for entry in class_entries:
            raw = zf.read(entry)
            literals.extend(_classfile_utf8_strings(raw))

        jdbc_literals = [
            s for s in literals
            if s.startswith("jdbc:h2:") and "/app/output/orders" in s and "MODE=DB2" in s
        ]
        assert jdbc_literals, "jar must include a JDBC URL literal with /app/output/orders and MODE=DB2"
        assert len(set(jdbc_literals)) == 1, "jar must include exactly one JDBC URL literal value"
        assert len(jdbc_literals) == 1, "jar must include the JDBC URL literal only once"

    agent_build = Path("/tmp/jdbc_probe_agent")
    if agent_build.exists():
        shutil.rmtree(agent_build)
    agent_build.mkdir(parents=True, exist_ok=True)
    agent_jar = _build_jdbc_probe_agent(agent_build)

    legacy_cp = "/opt/legacy-lib/*"
    env = os.environ.copy()

    def run_job(marker_path: Path) -> None:
        env["JAVA_TOOL_OPTIONS"] = f"-javaagent:{agent_jar}={marker_path}"
        subprocess.check_call(["java", "-cp", f"{job_jar}:{legacy_cp}", "OrderProcessor"], cwd="/app", env=env)
        marker_text = marker_path.read_text(encoding="utf-8") if marker_path.exists() else ""
        marker_lines = marker_text.splitlines()
        url = marker_lines[0] if len(marker_lines) > 0 else ""
        user = marker_lines[1] if len(marker_lines) > 1 else ""
        password = marker_lines[2] if len(marker_lines) > 2 else ""

        assert url.startswith("jdbc:h2:"), "job must open an H2 JDBC connection at runtime"
        assert "MODE=DB2" in url, "opened JDBC URL must include MODE=DB2"
        assert "/app/output/orders" in url, "opened JDBC URL must use the /app/output/orders database file"
        assert url in jdbc_literals, "opened JDBC URL must match a single literal in the jar"
        assert user == "sa", "H2 connection user must be 'sa'"
        assert password == "", "H2 connection password must be empty"

    run_job(agent_build / f"marker_{secrets.token_hex(8)}.txt")

    first_proc = Path("/app/output/processed_orders.csv").read_bytes()
    first_rej = Path("/app/output/rejected_orders.csv").read_bytes()
    first_summ = Path("/app/output/summary.json").read_bytes()

    run_job(agent_build / f"marker_{secrets.token_hex(8)}.txt")

    assert Path("/app/output/processed_orders.csv").read_bytes() == first_proc, "processed_orders.csv must be identical across re-runs"
    assert Path("/app/output/rejected_orders.csv").read_bytes() == first_rej, "rejected_orders.csv must be identical across re-runs"
    assert Path("/app/output/summary.json").read_bytes() == first_summ, "summary.json must be identical across re-runs"

    (expected_proc, expected_rej, expected_summ, expected_proc_count, expected_rej_count, expected_date, expected_cust_count, expected_md5) = _compute_expected()

    assert Path("/app/output/processed_orders.csv").read_text(encoding="utf-8") == expected_proc, "processed_orders.csv mismatch"
    assert Path("/app/output/rejected_orders.csv").read_text(encoding="utf-8") == expected_rej, "rejected_orders.csv mismatch"
    assert Path("/app/output/summary.json").read_text(encoding="utf-8") == expected_summ, "summary.json mismatch"

    db_file = Path("/app/output/orders.h2.db")
    assert db_file.exists(), "/app/output/orders.h2.db is missing"
    assert db_file.stat().st_size > 0, "/app/output/orders.h2.db is empty"

    actual_md5 = hashlib.md5(Path("/app/output/processed_orders.csv").read_bytes()).hexdigest()
    assert actual_md5 == expected_md5, "summary_md5 mismatch"

    audit_build = Path("/tmp/db_audit_check")
    if audit_build.exists():
        shutil.rmtree(audit_build)
    audit_build.mkdir(parents=True, exist_ok=True)
    audit_classes = _build_db_audit_check(audit_build)

    subprocess.check_call([
        "java", "-cp", f"{audit_classes}:{legacy_cp}", "DbAuditCheck",
        expected_date, str(expected_cust_count), str(expected_proc_count),
        str(expected_rej_count), expected_md5,
    ])

    src = Path(__file__).with_name("OrderProcessValidator.java")
    assert src.exists(), "OrderProcessValidator.java missing"
    build = Path("/tmp/validator")
    if build.exists():
        shutil.rmtree(build)
    build.mkdir(parents=True, exist_ok=True)
    subprocess.check_call([
        "javac", "-source", "1.7", "-target", "1.7", "-encoding", "UTF-8",
        "-cp", legacy_cp, "-d", str(build), str(src)
    ])
    subprocess.check_call(["java", "-cp", f"{build}:{legacy_cp}", "OrderProcessValidator"])

    for path in data_files:
        data_md5_after = hashlib.md5(Path(path).read_bytes()).hexdigest()
        assert data_md5_before[path] == data_md5_after, f"input file modified during execution: {path}"