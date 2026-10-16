# 📦 End-to-End FMCG Supply Planning & Analytics Model

![Supply Chain Architecture](assets/architecture_diagram.png)

## 📌 Executive Summary
This project delivers a **complete, operational End-to-End Supply Planning Model** tailored for a Fast-Moving Consumer Goods (FMCG) enterprise. Bridging raw retail Point-of-Sale (POS) data with executive decision-making, the system integrates **Python** for statistical forecasting, an **Excel-based Planning Engine** for operational planning (Demand Plan, Inventory Balance, MPS, RCCP, MRP), and a **Power BI Dashboard** for interactive decision-making.

The core goal is simple: **Convert complex planning logic into actionable business insights in under 5 seconds**, while maintaining strict 100% data reconciliation across all tools.

---

## 🛠️ Tech Stack & Key Tools
* **Data Processing & Statistical Forecasting:** Python (`pandas`, `statsmodels`)
* **Core Planning Engine (Calculation Truth):** Microsoft Excel (Advanced Formulas, Dynamic Tables, Data Model)
* **Reporting & Decision Layer:** Power BI Desktop (Star Schema Modeling, DAX Measures, Power Query ETL)
* **Dataset Source:** *dunnhumby Breakfast Cereal Dataset* (Supplemented with realistic simulated FMCG business assumptions)

---

## 🔄 End-to-End Supply Planning Workflow

```text
  [1. Python Engine]         [2. Excel Calculation Engine]            [3. Power BI Reporting]
┌────────────────────┐     ┌─────────────────────────────────┐     ┌────────────────────────┐
│ Statistical        │     │  • Consensus Demand Plan (DP)   │     │  • Page 1: Executive   │
│ Forecast Benchmarks│ ──► │  • Inventory Balancing & Safety │ ──► │  • Page 2: Demand & Inv│
│ (Naive, MA, SES)   │     │  • Master Production Schedule   │     │  • Page 3: Supply Risk │
└────────────────────┘     │  • Capacity Check (RCCP)        │     └────────────────────────┘
                           │  • Materials Requirement (MRP)  │
                           └─────────────────────────────────┘
