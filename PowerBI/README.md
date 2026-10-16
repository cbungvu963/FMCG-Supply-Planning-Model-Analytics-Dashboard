# FMCG Supply Planning Portfolio

An end-to-end FMCG supply planning portfolio that converts weekly retail sell-through into a traceable 8-week planning workflow:

**Retail demand → demand profile → statistical forecast → planner review / consensus demand plan → inventory balance → candidate MPS → RCCP → 1-level MRP → open-PO/material risk → consolidated exceptions → Power BI.**

---

## 1. Business Problem

The project asks a practical supply-planning question:

> How can a planner turn noisy retail demand into an 8-week replenishment and production plan, test inventory and rough-cut capacity feasibility, identify material/open-PO risks, and surface actionable exceptions?

The objective is not to reproduce an ERP/APS system. It is a portfolio decision-support model that demonstrates how demand, inventory, production, material, capacity and exception-management logic connect.

The detailed operational scope is **12 representative finished goods (FG)** over an **8-week weekly horizon**. Forecast benchmarking is performed on **47 full-history UPCs** before the detailed FG planning scope is selected.

---

## 2. Dataset

The demand source is the **dunnhumby “Breakfast at the Frat”** retail dataset.

Key source facts used in the project:

- 524,950 transaction rows
- 156 distinct weeks
- 55 UPCs in the transaction data
- 77 stores
- Date range: 2009-01-14 to 2012-01-04
- 47 UPCs with full 156-week history
- 8 UPCs with partial history
- 0 duplicate transaction business keys in the cleaned demand history

`Units_Sold` is treated as an observed **retail sell-through demand signal**, not as unconstrained true demand.

### Data provenance

The project explicitly separates three classes of information:

- **SOURCE** — fields originating from the retail dataset, such as UPC, week and units sold.
- **DERIVED** — calculated outputs such as CV, ABC/XYZ, forecast accuracy, consensus demand, PAB, MPS, MRP and RCCP status.
- **SIMULATED / PROJECT** — planning inputs that do not exist in the retail dataset, including BOM, supplier/open-PO data, planning lead times, opening inventories and capacity assumptions.

Therefore, simulated supplier, BOM, inventory and capacity values must not be described as actual dunnhumby operations.

---

## 3. Architecture

The final workflow is:

```text
Retail sell-through
        ↓
Demand profile + ABC/XYZ
        ↓
Forecast benchmark (47 full-history UPCs)
        ↓
8-week Statistical Forecast
        ↓
Planner Review / Consensus Demand Plan
        ↓
Inventory & Supply Balance
        ↓
Candidate MPS
       ↙  ↘
    RCCP   1-level BOM
              ↓
             MRP
              ↓
      Open PO / Material Risk
              ↓
     Consolidated Exceptions
              ↓
            Power BI
```

The Excel model is intentionally limited to seven business-facing sheets:

1. `00_Control`
2. `01_Planning_Data`
3. `02_Demand_Plan`
4. `03_Inventory_Supply`
5. `04_MPS`
6. `05_Material_Plan`
7. `06_RCCP_Exceptions`

Power BI is a reporting/decision layer; it does not recalculate the full planning engine.

### Power BI reporting model

The final Power BI model is intentionally lean and mirrors the planning outputs rather than rebuilding the Excel calculation engine.

Loaded model tables:

- Dimensions: `Dim_FG`, `Dim_Material`, `Dim_Week`
- Facts: `Fact_DemandPlan`, `Fact_Inventory`, `Fact_MaterialPlan`, `Fact_RCCP`, `Fact_Exceptions`
- Measure-only table: `Measure Table (DAX)`

`Stg_ForecastEvaluation` remains a staging query with load disabled after forecast attributes are merged into `Dim_FG`.

The public report contains three business pages:

1. `Executive Supply Planning`
2. `Demand & Inventory`
3. `Supply Risk`

Power BI remains a reporting and decision layer; Excel remains the calculation source of truth.

---

## 4. Methods

### Demand profiling

For 47 full-history UPCs, the model calculates weekly demand statistics, promotion share and ABC/XYZ segmentation.

- ABC: cumulative source spend
- XYZ: coefficient of variation (CV)
- 12 detailed FG are selected across all existing ABC×XYZ cells with category coverage

### Forecasting

Six candidate models are benchmarked:

- Naive
- Seasonal Naive (52 weeks)
- Moving Average 4
- Moving Average 8
- Moving Average 13
- Simple Exponential Smoothing

The time-series split is:

- 104 training weeks
- 26 validation weeks
- 26 holdout weeks

Validation uses rolling-origin one-step-ahead evaluation. Best model selection is lexicographic:

1. lowest validation WAPE
2. lowest validation MAE
3. lowest absolute validation bias

Holdout results evaluate the selected model but do not re-select it.

### Inventory planning

The model applies a weekly periodic-review policy.

Finished-goods safety stock:

```text
SS = z × weekly demand standard deviation × √(T + L_FG)
```

Target ending inventory protects the next planned week plus safety stock; Week 8 uses the locked terminal convention.

Projected available balance:

```text
PAB = BOH + Scheduled Receipt + MPS - Consensus Demand
```

### MPS

Candidate MPS is generated sequentially by FG and week:

```text
MPS = lot size × ROUNDUP(FG supply requirement / lot size)
```

The quantity represents planned FG completion/receipt in the week.

### RCCP

RCCP is a rough-cut reasonability test, not detailed production scheduling.

```text
Required Hours = Σ(MPS × standard hours/unit)
Load % = Required Hours / Available Hours
Capacity Gap = Available Hours - Required Hours
```

Portfolio policy:

- OK: Load < 90%
- AT_RISK: 90% ≤ Load ≤ 100%
- OVERLOAD: Load > 100%

### MRP and supply risk

The project uses a 1-level simulated BOM with 8 generic materials.

```text
Gross Requirement = Σ(MPS × BOM quantity)
Net Requirement = max(0, GR + Allocated + SS - Previous PAB - Scheduled Receipt)
Planned Receipt = lot-rounded Net Requirement
PAB = Previous PAB + Scheduled Receipt + Planned Receipt - GR - Allocated
```

Open POs are recognized in the week of their confirmed receipt. Shortage is only flagged after testing timing feasibility; a normal net requirement is not automatically a shortage.

---

## 5. Outputs

Verified core outputs include:

- 47-UPC forecast evaluation table
- 376-row 8-week demand-plan table for all full-history UPCs
- 12 detailed FG across 4 product categories
- 96 FG-week inventory rows
- 96 FG-week candidate-MPS rows
- 64 material-week MRP rows
- 32 work-center-week RCCP rows
- 15 consolidated supply/capacity exception rows
- 3-page Power BI planning report covering executive plan health, FG demand/inventory, and supply risk
- representative Excel ↔ Power BI reconciliation using `FG_06`, `RM_PIZZA`, and `WC_PIZZA`

For the 12 detailed FG scope:

- 8-week Consensus Demand: **197,499 units**
- Total candidate MPS: **219,500 units**
- FG stockout rows after candidate MPS: **0**
- Material shortage quantity in the simulated baseline: **12,000 units**
- Material-shortage cases: **7**
- Late open-PO cases: **2**
- RCCP overload work-center-weeks: **3**
- RCCP at-risk work-center-weeks: **3**
- Total overload magnitude: **234 hours**

A representative end-to-end trace is `FG_06`:

```text
UPC 7218063983
→ Best Model: Naive
→ Week-1 Demand Plan: 499
→ Week-1 FG requirement: 1,055
→ Candidate MPS: 1,100
→ WC_PIZZA Load: 110%
→ RM_PIZZA Gross Requirement: 1,100
→ RM_PIZZA Week-1 Shortage: 300
→ Recommended action: EXPEDITE / PULL_IN
```

These results describe the project model under its documented assumptions; they are not claims about an actual company's operations.

---

## 6. Planner Decisions

The project is designed around decision questions rather than calculation for its own sake.

Examples:

- **Forecast review:** A high-WAPE SKU is flagged for review, but the forecast is not overridden without forward-looking evidence.
- **FG replenishment:** Inventory position and target ending inventory determine FG supply requirement; lot sizing converts the requirement into candidate MPS.
- **Capacity:** RCCP tests whether the candidate MPS is reasonable before treating it as operationally feasible.
- **Material planning:** MPS is exploded through the 1-level BOM, then netted against inventory and confirmed open PO supply.
- **Late supply:** A late PO is tracked separately from material shortage; lateness does not automatically imply a quantity shortage.
- **Exception management:** Capacity overload, capacity at-risk, material shortage and late-PO signals are consolidated into an action-oriented exception table with affected FG and recommended planner response.

Typical actions include:

`EXPEDITE`, `PULL_IN`, `REVIEW_CAPACITY`, and `RESCHEDULE_MPS`.

These are recommended planning responses, not approved real-world execution actions.

---

## 7. Limitations

This project is intentionally bounded.

- Retail sell-through is used as the observed demand signal; lost sales and unconstrained demand are not modeled.
- BOM, supplier, opening inventory, open PO, lead time and capacity inputs are simulated for portfolio purposes.
- The BOM is 1-level only.
- RCCP is rough-cut capacity planning; the project does not implement detailed CRP or shop-floor scheduling.
- The MPS is a candidate weekly plan, not an optimized production schedule.
- No advanced supplier scoring, DRP, multi-echelon optimization, Monte Carlo simulation or machine-learning forecast models are included.
- Forecast review rules support planner attention; they do not automatically justify an override.
- Power BI is a visualization and decision layer, not the calculation source of truth.

---

## 8. Repository Structure

Recommended publishing layout:

```text
FMCG-Supply-Planning/
│
├── README.md
│
├── data/
│   └── README.md
│
├── src/
│   ├── 01_prepare_retail_data.py
│   └── 02_forecast_benchmark.py
│
├── model/
│   └── FMCG_Supply_Planning_Model_.xlsx
│
├── powerbi/
│   └── FMCG_Supply_Planning_Model.pbix
│
└── previews/
    ├── 01_Executive_Supply_Planning.png
    ├── 02_Demand_Inventory.png
    └── 03_Supply_Risk.png
```

The raw dunnhumby source data should not be redistributed publicly unless its redistribution terms have been checked. A safer public repository keeps a `data/README.md` that identifies the dataset, source, grain and preparation steps without republishing the raw source.

Intermediate forecast outputs may be retained privately or regenerated by the scripts. They are reproducibility evidence rather than headline portfolio deliverables.

Before publishing:

1. refresh the final PBIX against `FMCG_Supply_Planning_Model_.xlsx`;
2. verify the core regression benchmarks still reconcile;
3. hide any temporary QA page;
4. export the three current Power BI pages as the preview PNGs above.

Do not publish screenshots that no longer reconcile with the final workbook, and do not include Day-by-Day workbooks, QA backups, roadmap/handoff notes, debug images or course materials in the public repository.
