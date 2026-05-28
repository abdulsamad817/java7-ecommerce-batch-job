public final class OrderProcessValidator {
    public static void main(String[] args) throws Exception {
        validateProcessedOrders();
        validateRejections();
        validateSummary();
        validateDatabase();
    }
    
    private static void validateProcessedOrders() {
        java.nio.file.Path path = java.nio.file.Paths.get("/app/output/processed_orders.csv");
        if (!java.nio.file.Files.exists(path)) throw new RuntimeException("processed_orders.csv missing");
        try {
            String content = new String(java.nio.file.Files.readAllBytes(path), java.nio.charset.StandardCharsets.UTF_8);
            String[] lines = content.split("\n");
            if (lines.length < 2) throw new RuntimeException("processed_orders.csv too short");
            if (!lines[0].contains("order_id")) throw new RuntimeException("processed_orders.csv header invalid");
            for (int i = 1; i < lines.length - 1; i++) {
                String[] cols = lines[i].split(",");
                if (cols.length != 7) throw new RuntimeException("processed_orders.csv row " + i + " has " + cols.length + " cols, expected 7");
            }
        } catch (Exception e) {
            throw new RuntimeException("processed_orders.csv validation failed: " + e.getMessage());
        }
    }
    
    private static void validateRejections() {
        java.nio.file.Path path = java.nio.file.Paths.get("/app/output/rejected_orders.csv");
        if (!java.nio.file.Files.exists(path)) throw new RuntimeException("rejected_orders.csv missing");
        try {
            String content = new String(java.nio.file.Files.readAllBytes(path), java.nio.charset.StandardCharsets.UTF_8);
            String[] lines = content.split("\n");
            if (lines.length < 1) throw new RuntimeException("rejected_orders.csv empty");
            if (!lines[0].contains("row_num")) throw new RuntimeException("rejected_orders.csv header invalid");
        } catch (Exception e) {
            throw new RuntimeException("rejected_orders.csv validation failed: " + e.getMessage());
        }
    }
    
    private static void validateSummary() {
        java.nio.file.Path path = java.nio.file.Paths.get("/app/output/summary.json");
        if (!java.nio.file.Files.exists(path)) throw new RuntimeException("summary.json missing");
        try {
            String content = new String(java.nio.file.Files.readAllBytes(path), java.nio.charset.StandardCharsets.UTF_8);
            if (!content.contains("process_date")) throw new RuntimeException("summary.json missing process_date");
            if (!content.contains("total_orders")) throw new RuntimeException("summary.json missing total_orders");
            if (!content.contains("processed_orders")) throw new RuntimeException("summary.json missing processed_orders");
            if (!content.contains("rejected_orders")) throw new RuntimeException("summary.json missing rejected_orders");
            if (!content.contains("total_revenue")) throw new RuntimeException("summary.json missing total_revenue");
            if (!content.contains("summary_md5")) throw new RuntimeException("summary.json missing summary_md5");
            if (content.contains("\n")) throw new RuntimeException("summary.json must be single-line");
        } catch (Exception e) {
            throw new RuntimeException("summary.json validation failed: " + e.getMessage());
        }
    }
    
    private static void validateDatabase() throws Exception {
        Class.forName("org.h2.Driver");
        java.sql.Connection conn = java.sql.DriverManager.getConnection("jdbc:h2:/app/output/orders;MODE=DB2;IFEXISTS=TRUE", "sa", "");
        try {
            java.sql.Statement st = conn.createStatement();
            java.sql.ResultSet rs = st.executeQuery("SELECT COUNT(*) FROM CUSTOMERS");
            if (!rs.next()) throw new RuntimeException("CUSTOMERS table missing");
            rs.close(); st.close();
            
            st = conn.createStatement();
            rs = st.executeQuery("SELECT COUNT(*) FROM ORDERS");
            if (!rs.next()) throw new RuntimeException("ORDERS table missing");
            rs.close(); st.close();
            
            st = conn.createStatement();
            rs = st.executeQuery("SELECT COUNT(*) FROM PROCESS_AUDIT");
            if (!rs.next()) throw new RuntimeException("PROCESS_AUDIT table missing");
            rs.close(); st.close();
        } finally { conn.close(); }
    }
}