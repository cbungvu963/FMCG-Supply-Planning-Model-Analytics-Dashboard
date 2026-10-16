"""
01_prepare_retail_data.py

Purpose
-------
Prepare the dunnhumby "Breakfast at the Frat" retail source
for the FMCG Supply Planning flagship project.

Main logic
----------
1. Read Transaction / Product Lookup / Store Lookup.
2. Validate actual workbook schema.
3. Resolve duplicated Store IDs safely.
4. Join Product and Store attributes without inflating rows.
5. Standardize field names.
6. Add data-quality and history flags.
7. Validate row count, key integrity and reconciliation.
8. Export retail_demand_history.csv.

IMPORTANT
---------
- Retail UNITS is interpreted as observed retail sell-through,
  NOT true unconstrained demand.
- No source outlier is automatically deleted.
- No manufacturing data is invented in Day 1.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd


# ============================================================
# 1. PROJECT / SOURCE CONSTANTS
# ============================================================

TRANSACTION_SHEET = "dh Transaction Data"
PRODUCT_SHEET = "dh Products Lookup"
STORE_SHEET = "dh Store Lookup"


# Facts supported by the source / locked audit.
# These are used as QA gates, not as values used to build the data.
EXPECTED_TRANSACTION_ROWS = 524_950
EXPECTED_WEEKS = 156
EXPECTED_PRODUCT_LOOKUP_ROWS = 58
EXPECTED_STORE_LOOKUP_ROWS = 79

# Previous workbook audit recorded in the project handoff.
EXPECTED_TRANSACTION_UPC = 55
EXPECTED_TRANSACTION_STORES = 77
EXPECTED_FULL_HISTORY_UPC = 47
EXPECTED_PARTIAL_HISTORY_UPC = 8


# ------------------------------------------------------------
# IMPORTANT:
# The User Guide suggests examining Units/Visit and
# Visits/Household for possible outliers, but DOES NOT define
# a formal threshold.
#
# Therefore we calculate the ratios but DO NOT invent a cutoff.
# ------------------------------------------------------------

UNITS_PER_VISIT_REVIEW_THRESHOLD = None
VISITS_PER_HOUSEHOLD_REVIEW_THRESHOLD = None


# ============================================================
# 2. SMALL HELPER FUNCTIONS
# ============================================================

def require_columns(
    df: pd.DataFrame,
    required_columns: list[str],
    table_name: str,
) -> None:
    """
    Stop execution if the workbook schema is not what the
    project expects.

    Why:
    Silently continuing after a source schema change is dangerous.
    """
    missing = [col for col in required_columns if col not in df.columns]

    if missing:
        raise ValueError(
            f"{table_name}: missing required columns: {missing}\n"
            f"Actual columns: {list(df.columns)}"
        )


def normalize_id(series: pd.Series) -> pd.Series:
    """
    Treat business identifiers as strings.

    Examples:
    UPC and Store ID are identifiers, not quantities.
    We should not perform arithmetic on them.
    """
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\.0$", "", regex=True)
    )


def coerce_week_end_date(series: pd.Series) -> pd.Series:
    """
    Convert WEEK_END_DATE safely.

    Normally pandas/openpyxl should recognize Excel dates.
    The fallback also handles raw Excel serial numbers.
    """

    # Case 1: Excel date was already recognized correctly.
    if pd.api.types.is_datetime64_any_dtype(series):
        result = pd.to_datetime(series, errors="coerce")

    # Case 2: Excel serial numbers such as 39827.
    elif pd.api.types.is_numeric_dtype(series):
        result = pd.to_datetime(
            series,
            unit="D",
            origin="1899-12-30",
            errors="coerce",
        )

    # Case 3: Date stored as text.
    else:
        result = pd.to_datetime(series, errors="coerce")

    if result.isna().any():
        bad_count = int(result.isna().sum())

        raise ValueError(
            f"WEEK_END_DATE contains {bad_count} values "
            "that could not be converted to dates."
        )

    return result.dt.normalize()


def assert_equal(name: str, actual, expected) -> None:
    """
    Simple QA assertion with a readable error message.
    """
    if actual != expected:
        raise AssertionError(
            f"{name}: expected {expected}, found {actual}"
        )


# ============================================================
# 3. READ SOURCE TABLES
# ============================================================

def read_source_tables(
    workbook_path: Path,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Workbook not found:\n{workbook_path}"
        )

    # Excel row 1 contains the workbook title.
    # Actual field headers start at Excel row 2.
    # Therefore pandas header=1 (zero-based index).
    with pd.ExcelFile(
        workbook_path,
        engine="openpyxl",
    ) as xls:

        required_sheets = {
            TRANSACTION_SHEET,
            PRODUCT_SHEET,
            STORE_SHEET,
        }

        actual_sheets = set(xls.sheet_names)

        missing_sheets = required_sheets - actual_sheets

        if missing_sheets:
            raise ValueError(
                f"Workbook missing sheets: {sorted(missing_sheets)}\n"
                f"Actual sheets: {xls.sheet_names}"
            )

        # ----------------------------------------------------
        # Transaction
        # ----------------------------------------------------
        transaction = pd.read_excel(
            xls,
            sheet_name=TRANSACTION_SHEET,
            header=1,
            usecols="A:L",
            dtype={
                "STORE_NUM": "string",
                "UPC": "string",
            },
        )

        # ----------------------------------------------------
        # Product Lookup
        # ----------------------------------------------------
        products = pd.read_excel(
            xls,
            sheet_name=PRODUCT_SHEET,
            header=1,
            usecols="A:F",
            dtype={
                "UPC": "string",
            },
        )

        # ----------------------------------------------------
        # Store Lookup
        #
        # IMPORTANT:
        # Actual workbook uses STORE_ID in this sheet,
        # while Transaction uses STORE_NUM.
        # ----------------------------------------------------
        stores = pd.read_excel(
            xls,
            sheet_name=STORE_SHEET,
            header=1,
            usecols="A:I",
            dtype={
                "STORE_ID": "string",
            },
        )

    return transaction, products, stores


# ============================================================
# 4. VALIDATE RAW SCHEMA
# ============================================================

def validate_raw_schema(
    transaction: pd.DataFrame,
    products: pd.DataFrame,
    stores: pd.DataFrame,
) -> None:

    transaction_columns = [
        "WEEK_END_DATE",
        "STORE_NUM",
        "UPC",
        "UNITS",
        "VISITS",
        "HHS",
        "SPEND",
        "PRICE",
        "BASE_PRICE",
        "FEATURE",
        "DISPLAY",
        "TPR_ONLY",
    ]

    product_columns = [
        "UPC",
        "DESCRIPTION",
        "MANUFACTURER",
        "CATEGORY",
        "SUB_CATEGORY",
        "PRODUCT_SIZE",
    ]

    store_columns = [
        "STORE_ID",
        "ADDRESS_STATE_PROV_CODE",
        "SALES_AREA_SIZE_NUM",
        "SEG_VALUE_NAME",
    ]

    require_columns(
        transaction,
        transaction_columns,
        TRANSACTION_SHEET,
    )

    require_columns(
        products,
        product_columns,
        PRODUCT_SHEET,
    )

    require_columns(
        stores,
        store_columns,
        STORE_SHEET,
    )


# ============================================================
# 5. CLEAN BASIC TYPES
# ============================================================

def clean_basic_types(
    transaction: pd.DataFrame,
    products: pd.DataFrame,
    stores: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:

    transaction = transaction.copy()
    products = products.copy()
    stores = stores.copy()

    # -----------------------
    # Business IDs
    # -----------------------
    transaction["STORE_NUM"] = normalize_id(
        transaction["STORE_NUM"]
    )

    transaction["UPC"] = normalize_id(
        transaction["UPC"]
    )

    products["UPC"] = normalize_id(
        products["UPC"]
    )

    stores["STORE_ID"] = normalize_id(
        stores["STORE_ID"]
    )

    # Store lookup key must align with Transaction key.
    stores = stores.rename(
        columns={
            "STORE_ID": "STORE_NUM"
        }
    )

    # -----------------------
    # Date
    # -----------------------
    transaction["WEEK_END_DATE"] = coerce_week_end_date(
        transaction["WEEK_END_DATE"]
    )

    # -----------------------
    # Numeric source fields
    # -----------------------
    numeric_columns = [
        "UNITS",
        "VISITS",
        "HHS",
        "SPEND",
        "PRICE",
        "BASE_PRICE",
    ]

    for col in numeric_columns:
        transaction[col] = pd.to_numeric(
            transaction[col],
            errors="coerce",
        )

    # These are required transactional measures.
    required_numeric = [
        "UNITS",
        "VISITS",
        "HHS",
        "SPEND",
    ]

    for col in required_numeric:
        if transaction[col].isna().any():
            raise ValueError(
                f"{col} contains "
                f"{int(transaction[col].isna().sum())} missing values."
            )

    # -----------------------
    # Promotion indicators
    # -----------------------
    promotion_columns = [
        "FEATURE",
        "DISPLAY",
        "TPR_ONLY",
    ]

    for col in promotion_columns:

        transaction[col] = pd.to_numeric(
            transaction[col],
            errors="coerce",
        )

        invalid = (
            transaction[col].notna()
            & ~transaction[col].isin([0, 1])
        )

        if invalid.any():
            raise ValueError(
                f"{col} contains values outside 0/1."
            )

        transaction[col] = (
            transaction[col]
            .astype("Int8")
        )

    return transaction, products, stores


# ============================================================
# 6. RESOLVE STORE LOOKUP CONFLICT
# ============================================================

def resolve_store_lookup(
    stores: pd.DataFrame,
) -> tuple[pd.DataFrame, list[str]]:

    """
    Store Lookup contains duplicated Store IDs.

    We MUST NOT merge it directly because a many-to-many join
    could duplicate transaction rows and inflate Units/Spend.

    Logic:
    1. Check whether duplicated Store IDs disagree in any
       non-segment attributes.
    2. If another attribute conflicts -> STOP.
    3. If only SEG_VALUE_NAME conflicts:
         Store_Segment = NA
         Quality Flag  = SEGMENT_CONFLICT
    4. Return exactly one row per Store ID.
    """

    stores = stores.copy()

    non_segment_columns = [
        col
        for col in stores.columns
        if col not in {
            "STORE_NUM",
            "SEG_VALUE_NAME",
        }
    ]

    # --------------------------------------------------------
    # Check whether duplicate Store IDs disagree on fields
    # other than segment.
    # --------------------------------------------------------
    serious_conflicts: dict[str, list[str]] = {}

    for col in non_segment_columns:

        unique_count = (
            stores
            .groupby("STORE_NUM", dropna=False)[col]
            .nunique(dropna=True)
        )

        bad_ids = unique_count[
            unique_count > 1
        ].index.astype(str).tolist()

        if bad_ids:
            serious_conflicts[col] = bad_ids

    if serious_conflicts:
        raise ValueError(
            "Store Lookup contains conflicts outside "
            "SEG_VALUE_NAME.\n"
            f"{serious_conflicts}\n"
            "Do not choose a value automatically."
        )

    # --------------------------------------------------------
    # Detect segment conflict
    # --------------------------------------------------------
    segment_nunique = (
        stores
        .groupby("STORE_NUM")["SEG_VALUE_NAME"]
        .nunique(dropna=True)
    )

    segment_conflict_ids = (
        segment_nunique[
            segment_nunique > 1
        ]
        .index
        .astype(str)
        .tolist()
    )

    # --------------------------------------------------------
    # All other information is consistent, so retain one row.
    # --------------------------------------------------------
    resolved = (
        stores
        .drop_duplicates(
            subset=["STORE_NUM"],
            keep="first",
        )
        .copy()
    )

    # Never pick Mainstream/Upscale if source conflicts.
    conflict_mask = resolved["STORE_NUM"].isin(
        segment_conflict_ids
    )

    resolved.loc[
        conflict_mask,
        "SEG_VALUE_NAME",
    ] = pd.NA

    resolved["Store_Segment_Quality_Flag"] = pd.NA

    resolved.loc[
        conflict_mask,
        "Store_Segment_Quality_Flag",
    ] = "SEGMENT_CONFLICT"

    # One row per store is mandatory before merge.
    if resolved["STORE_NUM"].duplicated().any():
        raise AssertionError(
            "Store resolution failed: STORE_NUM still duplicated."
        )

    return resolved, segment_conflict_ids


# ============================================================
# 7. VALIDATE PRODUCT LOOKUP
# ============================================================

def validate_product_lookup(
    products: pd.DataFrame,
) -> None:

    duplicated_upc = products[
        products["UPC"].duplicated(
            keep=False
        )
    ]

    if not duplicated_upc.empty:
        raise ValueError(
            "Product Lookup contains duplicate UPCs.\n"
            "Cannot safely perform a many-to-one merge."
        )


# ============================================================
# 8. JOIN SOURCE TABLES
# ============================================================

def join_source_tables(
    transaction: pd.DataFrame,
    products: pd.DataFrame,
    stores: pd.DataFrame,
) -> pd.DataFrame:

    source_rows = len(transaction)

    # ========================================================
    # PRODUCT JOIN
    #
    # validate="many_to_one":
    # many transaction rows can point to one UPC lookup row.
    # ========================================================
    merged = transaction.merge(
        products,
        how="left",
        on="UPC",
        validate="many_to_one",
        indicator="_product_merge",
    )

    unmatched_product = int(
        (merged["_product_merge"] != "both").sum()
    )

    if unmatched_product > 0:
        raise ValueError(
            f"{unmatched_product} transaction rows "
            "have no Product Lookup match."
        )

    merged = merged.drop(
        columns="_product_merge"
    )

    # ========================================================
    # STORE JOIN
    # ========================================================
    merged = merged.merge(
        stores,
        how="left",
        on="STORE_NUM",
        validate="many_to_one",
        indicator="_store_merge",
    )

    unmatched_store = int(
        (merged["_store_merge"] != "both").sum()
    )

    if unmatched_store > 0:
        raise ValueError(
            f"{unmatched_store} transaction rows "
            "have no Store Lookup match."
        )

    merged = merged.drop(
        columns="_store_merge"
    )

    # --------------------------------------------------------
    # Critical reconciliation:
    # joining dimensions must NEVER increase transaction rows.
    # --------------------------------------------------------
    if len(merged) != source_rows:
        raise AssertionError(
            "Row count changed after lookup joins.\n"
            f"Before = {source_rows:,}\n"
            f"After  = {len(merged):,}"
        )

    return merged


# ============================================================
# 9. STANDARDIZE OUTPUT NAMES
# ============================================================

def standardize_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:

    rename_map = {
        "WEEK_END_DATE": "Week_End_Date",
        "STORE_NUM": "Store_ID",
        "DESCRIPTION": "Product_Description",
        "MANUFACTURER": "Manufacturer",
        "CATEGORY": "Category",
        "SUB_CATEGORY": "Sub_Category",
        "PRODUCT_SIZE": "Product_Size",
        "UNITS": "Units_Sold",
        "VISITS": "Visits",
        "HHS": "Households",
        "SPEND": "Spend",
        "PRICE": "Shelf_Price",
        "BASE_PRICE": "Base_Price",
        "FEATURE": "Feature",
        "DISPLAY": "Display",
        "TPR_ONLY": "TPR_Only",
        "ADDRESS_STATE_PROV_CODE": "State",
        "SALES_AREA_SIZE_NUM": "Sales_Area",
        "SEG_VALUE_NAME": "Store_Segment",
    }

    return df.rename(
        columns=rename_map
    )


# ============================================================
# 10. DATA-QUALITY + REVIEW METRICS
# ============================================================

def add_quality_fields(
    df: pd.DataFrame,
) -> pd.DataFrame:

    df = df.copy()

    # --------------------------------------------------------
    # Missing price is FLAGGED, not filled automatically.
    # --------------------------------------------------------
    df["Price_Missing_Flag"] = (
        df["Shelf_Price"].isna()
    )

    df["Base_Price_Missing_Flag"] = (
        df["Base_Price"].isna()
    )

    # --------------------------------------------------------
    # User Guide suggests examining these ratios for outliers.
    #
    # These are DERIVED.
    # No rows are deleted.
    # --------------------------------------------------------
    df["Units_Per_Visit"] = (
        df["Units_Sold"]
        .div(
            df["Visits"].where(
                df["Visits"] != 0
            )
        )
    )

    df["Visits_Per_Household"] = (
        df["Visits"]
        .div(
            df["Households"].where(
                df["Households"] != 0
            )
        )
    )

    # --------------------------------------------------------
    # DO NOT invent an outlier threshold.
    #
    # Only create classification if a threshold is explicitly
    # approved later.
    # --------------------------------------------------------
    if UNITS_PER_VISIT_REVIEW_THRESHOLD is not None:

        df["Units_Per_Visit_Review_Flag"] = (
            df["Units_Per_Visit"]
            > UNITS_PER_VISIT_REVIEW_THRESHOLD
        )

    if VISITS_PER_HOUSEHOLD_REVIEW_THRESHOLD is not None:

        df["Visits_Per_Household_Review_Flag"] = (
            df["Visits_Per_Household"]
            > VISITS_PER_HOUSEHOLD_REVIEW_THRESHOLD
        )

    return df


# ============================================================
# 11. FULL / PARTIAL HISTORY
# ============================================================

def add_history_flag(
    df: pd.DataFrame,
) -> pd.DataFrame:

    df = df.copy()

    # Number of distinct weeks in the complete source.
    total_weeks = df["Week_End_Date"].nunique()

    # For each UPC:
    # in how many distinct source weeks does it appear?
    upc_week_count = (
        df
        .groupby("UPC")["Week_End_Date"]
        .nunique()
    )

    df["UPC_Week_Count"] = (
        df["UPC"]
        .map(upc_week_count)
        .astype("Int16")
    )

    # If a UPC appears in every source week:
    # FULL_HISTORY.
    #
    # Otherwise:
    # PARTIAL_HISTORY.
    #
    # We deliberately DO NOT call it new/discontinued.
    full_mask = (
        df["UPC_Week_Count"]
        == total_weeks
    )

    df["History_Flag"] = (
        full_mask.map(
            {
                True: "FULL_HISTORY",
                False: "PARTIAL_HISTORY",
            }
        )
        .astype("string")
    )

    return df


# ============================================================
# 12. FINAL COLUMN ORDER
# ============================================================

def select_output_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:

    output_columns = [
        # -----------------------
        # SOURCE
        # -----------------------
        "Week_End_Date",
        "Store_ID",
        "UPC",
        "Product_Description",
        "Manufacturer",
        "Category",
        "Sub_Category",
        "Product_Size",
        "Units_Sold",
        "Visits",
        "Households",
        "Spend",
        "Shelf_Price",
        "Base_Price",
        "Feature",
        "Display",
        "TPR_Only",
        "State",
        "Sales_Area",
        "Store_Segment",

        # -----------------------
        # DERIVED / DATA QUALITY
        # -----------------------
        "Price_Missing_Flag",
        "Base_Price_Missing_Flag",
        "Store_Segment_Quality_Flag",
        "Units_Per_Visit",
        "Visits_Per_Household",
        "UPC_Week_Count",
        "History_Flag",
    ]

    # Add optional outlier flags only if thresholds exist.
    optional_columns = [
        "Units_Per_Visit_Review_Flag",
        "Visits_Per_Household_Review_Flag",
    ]

    for col in optional_columns:
        if col in df.columns:
            output_columns.append(col)

    result = df[output_columns].copy()

    result = result.sort_values(
        by=[
            "Week_End_Date",
            "Store_ID",
            "UPC",
        ],
        kind="stable",
    )

    return result


# ============================================================
# 13. QA GATE
# ============================================================

def validate_final_data(
    transaction: pd.DataFrame,
    products: pd.DataFrame,
    raw_stores: pd.DataFrame,
    resolved_stores: pd.DataFrame,
    final: pd.DataFrame,
    segment_conflict_ids: list[str],
) -> None:

    print("\n" + "=" * 68)
    print("DAY 1 DATA QA")
    print("=" * 68)

    # --------------------------------------------------------
    # Basic counts
    # --------------------------------------------------------
    transaction_rows = len(transaction)

    weeks = transaction["WEEK_END_DATE"].nunique()

    upc_count = transaction["UPC"].nunique()

    store_count = transaction["STORE_NUM"].nunique()

    product_rows = len(products)

    store_lookup_rows = len(raw_stores)

    resolved_store_count = (
        resolved_stores["STORE_NUM"].nunique()
    )

    # --------------------------------------------------------
    # Business key
    # --------------------------------------------------------
    duplicate_key_count = int(
        transaction.duplicated(
            subset=[
                "WEEK_END_DATE",
                "STORE_NUM",
                "UPC",
            ]
        ).sum()
    )

    # --------------------------------------------------------
    # Source measure checks
    # --------------------------------------------------------
    negative_units = int(
        (transaction["UNITS"] < 0).sum()
    )

    negative_spend = int(
        (transaction["SPEND"] < 0).sum()
    )

    missing_price = int(
        transaction["PRICE"].isna().sum()
    )

    missing_base_price = int(
        transaction["BASE_PRICE"].isna().sum()
    )

    total_units_before = transaction["UNITS"].sum()

    total_spend_before = transaction["SPEND"].sum()

    total_units_after = final["Units_Sold"].sum()

    total_spend_after = final["Spend"].sum()

    # --------------------------------------------------------
    # History
    # --------------------------------------------------------
    history_by_upc = (
        final[
            ["UPC", "History_Flag"]
        ]
        .drop_duplicates()
    )

    full_history = int(
        (
            history_by_upc["History_Flag"]
            == "FULL_HISTORY"
        ).sum()
    )

    partial_history = int(
        (
            history_by_upc["History_Flag"]
            == "PARTIAL_HISTORY"
        ).sum()
    )

    # --------------------------------------------------------
    # Date range
    # --------------------------------------------------------
    min_date = transaction[
        "WEEK_END_DATE"
    ].min()

    max_date = transaction[
        "WEEK_END_DATE"
    ].max()

    # ========================================================
    # PRINT AUDIT
    # ========================================================

    print(f"Transaction rows      : {transaction_rows:,}")
    print(f"Distinct weeks        : {weeks:,}")
    print(f"Distinct UPC          : {upc_count:,}")
    print(f"Distinct stores       : {store_count:,}")

    print(
        f"Date range            : "
        f"{min_date.date()} -> {max_date.date()}"
    )

    print(f"Product lookup rows   : {product_rows:,}")
    print(f"Store lookup rows     : {store_lookup_rows:,}")
    print(f"Resolved store IDs    : {resolved_store_count:,}")

    print(
        f"Segment conflicts     : "
        f"{len(segment_conflict_ids)} "
        f"{sorted(segment_conflict_ids)}"
    )

    print(f"Duplicate business key: {duplicate_key_count:,}")

    print(f"Missing PRICE         : {missing_price:,}")
    print(f"Missing BASE_PRICE    : {missing_base_price:,}")

    print(f"Negative UNITS        : {negative_units:,}")
    print(f"Negative SPEND        : {negative_spend:,}")

    print(f"Total Units           : {total_units_before:,.0f}")
    print(f"Total Spend           : {total_spend_before:,.2f}")

    print(f"FULL_HISTORY UPC      : {full_history:,}")
    print(f"PARTIAL_HISTORY UPC   : {partial_history:,}")

    # ========================================================
    # HARD QA ASSERTIONS
    # ========================================================

    assert_equal(
        "Transaction rows",
        transaction_rows,
        EXPECTED_TRANSACTION_ROWS,
    )

    assert_equal(
        "Distinct weeks",
        weeks,
        EXPECTED_WEEKS,
    )

    assert_equal(
        "Product Lookup rows",
        product_rows,
        EXPECTED_PRODUCT_LOOKUP_ROWS,
    )

    assert_equal(
        "Store Lookup rows",
        store_lookup_rows,
        EXPECTED_STORE_LOOKUP_ROWS,
    )

    assert_equal(
        "Transaction UPC count",
        upc_count,
        EXPECTED_TRANSACTION_UPC,
    )

    assert_equal(
        "Transaction store count",
        store_count,
        EXPECTED_TRANSACTION_STORES,
    )

    assert_equal(
        "Duplicate Week-Store-UPC key",
        duplicate_key_count,
        0,
    )

    assert_equal(
        "FULL_HISTORY UPC count",
        full_history,
        EXPECTED_FULL_HISTORY_UPC,
    )

    assert_equal(
        "PARTIAL_HISTORY UPC count",
        partial_history,
        EXPECTED_PARTIAL_HISTORY_UPC,
    )

    assert_equal(
        "Negative unit rows",
        negative_units,
        0,
    )

    assert_equal(
        "Negative spend rows",
        negative_spend,
        0,
    )

    # --------------------------------------------------------
    # Join reconciliation
    # --------------------------------------------------------
    assert_equal(
        "Final row count",
        len(final),
        transaction_rows,
    )

    if not math.isclose(
        float(total_units_before),
        float(total_units_after),
        rel_tol=0,
        abs_tol=1e-9,
    ):
        raise AssertionError(
            "Total Units changed after joins."
        )

    if not math.isclose(
        float(total_spend_before),
        float(total_spend_after),
        rel_tol=0,
        abs_tol=1e-6,
    ):
        raise AssertionError(
            "Total Spend changed after joins."
        )

    print("-" * 68)
    print("QA STATUS: PASS")
    print("=" * 68)


# ============================================================
# 14. MAIN PIPELINE
# ============================================================

def main() -> None:

    parser = argparse.ArgumentParser(
        description=(
            "Prepare dunnhumby retail source "
            "for the FMCG Supply Planning project."
        )
    )

    parser.add_argument(
        "--input",
        required=False,
        default=None,
        help="Path to dunnhumby Excel source workbook.",
    )

    parser.add_argument(
        "--output",
        default=None,
        help=(
            "Output CSV path. "
            "Default: <project_root>/data/"
            "retail_demand_history.csv"
        ),
    )

    args = parser.parse_args()
#-----------
    # Script đang nằm tại:
    # __Project/src/01_prepare_retail_data.py
    #
    # parents[1] => thư mục __Project
    project_root = (
        Path(__file__)
        .resolve()
        .parents[1]
    )

    # Nếu người dùng không truyền --input,
    # tự tìm workbook trong thư mục data/
    if args.input is None:
        input_path = (
            project_root
            / "data"
            / "dunnhumby - Breakfast at the Frat.xlsx"
        )
    else:
        input_path = (
            Path(args.input)
            .expanduser()
            .resolve()
        )
#---------------
    if args.output is None:
        output_path = (
            project_root
            / "data"
            / "retail_demand_history.csv"
        )
    else:
        output_path = (
            Path(args.output)
            .expanduser()
            .resolve()
        )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("Reading source workbook...")

    transaction, products, stores = read_source_tables(
        input_path
    )

    print("Validating raw schema...")

    validate_raw_schema(
        transaction,
        products,
        stores,
    )

    print("Cleaning basic types...")

    transaction, products, stores = clean_basic_types(
        transaction,
        products,
        stores,
    )

    # --------------------------------------------------------
    # Keep raw store table for QA before resolving duplicates.
    # --------------------------------------------------------
    raw_stores = stores.copy()

    print("Validating Product Lookup...")

    validate_product_lookup(
        products
    )

    print("Resolving Store Lookup conflicts...")

    resolved_stores, segment_conflict_ids = (
        resolve_store_lookup(
            stores
        )
    )

    print(
        "Segment-conflict Store IDs:",
        sorted(segment_conflict_ids),
    )

    print("Joining Product + Store lookups...")

    merged = join_source_tables(
        transaction,
        products,
        resolved_stores,
    )

    print("Standardizing columns...")

    final = standardize_columns(
        merged
    )

    print("Adding data-quality fields...")

    final = add_quality_fields(
        final
    )

    print("Classifying UPC history...")

    final = add_history_flag(
        final
    )

    final = select_output_columns(
        final
    )

    print("Running QA gate...")

    validate_final_data(
        transaction=transaction,
        products=products,
        raw_stores=raw_stores,
        resolved_stores=resolved_stores,
        final=final,
        segment_conflict_ids=segment_conflict_ids,
    )

    # --------------------------------------------------------
    # Export ONLY after QA passes.
    # --------------------------------------------------------
    final.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
        date_format="%Y-%m-%d",
    )

    print("\nOutput created:")
    print(output_path)


if __name__ == "__main__":
    main()