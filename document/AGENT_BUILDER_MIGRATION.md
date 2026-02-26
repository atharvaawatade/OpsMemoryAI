# 🛑 STOP: READ THIS FOR TOOL CREATION

You are looking at the "Create a new tool" screen with 3 types.
Here is exactly what to select for each tool you need:

## 1. First Tool: Semantic Search
👉 **Select Type:** `Index search`
*   **Name:** `incident_memory_search`
*   **Index:** `ops-incidents`
*   **Fields:** `description`, `root_cause`

## 2. Second Tool: Pattern Detector (The "Smart" One)
👉 **Select Type:** `ES|QL`
*   **Name:** `cascading_pattern_detector`
*   **Query:** (Copy this exactly)
    ```sql
    FROM ops-incidents
    | WHERE service == "?service_name"
    | EVAL window = DATE_TRUNC(1 hour, created_at)
    | STATS incident_count = COUNT(*), causes = VALUES(root_cause) BY window
    | WHERE incident_count > 1
    | SORT incident_count DESC
    | LIMIT 5
    ```
*   **Parameters:**
    *   `service_name` (Type: `string`, Required: `true`)

## 3. Third Tool: Policy Check
👉 **Select Type:** `Index search`
*   **Name:** `policy_search`
*   **Index:** `ops-decisions`
*   **Fields:** `content`, `title`

---
**AFTER CREATING ALL 3:**
Go to **Agents -> Create Agent**, add them, and paste the **Agent ID** here.
