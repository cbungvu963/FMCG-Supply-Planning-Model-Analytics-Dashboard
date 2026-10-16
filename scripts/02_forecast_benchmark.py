#!/usr/bin/env python3
"""
02_forecast_benchmark.py
========================

FMCG Supply Planning Flagship — Day 3 Forecast Benchmark

Purpose
-------
Benchmark simple, explainable forecasting methods for the 47 FULL_HISTORY UPCs,
using a no-leakage rolling-origin one-step-ahead protocol.

Locked project rules implemented here
-------------------------------------
- Weekly demand signal: observed retail sales / retail sell-through.
- Scope: FULL_HISTORY UPCs only.
- Split: 104 train / 26 validation / 26 holdout weeks.
- Validation: rolling-origin, one-step-ahead.
- Candidate models:
    * Naive
    * Seasonal Naive (52 weeks)
    * MA(4)
    * MA(8)
    * MA(13)
    * SES with a fixed alpha supplied by CLI
- SES initialization: mean of the first 6 observations.
- Best model selection: lexicographic
    1) lowest validation WAPE
    2) lowest validation MAE
    3) lowest absolute validation Bias
- Holdout is evaluation only; it never re-selects the model.
- Final 8-week forecast:
    * Naive: recursive constant forecast
    * Seasonal Naive 52: corresponding 52-week lag
    * Moving Average: recursive multi-step forecast
    * SES: constant future level from final SES state

Metric conventions
------------------
Error = Actual - Forecast
MAE   = mean(abs(Error))
WAPE  = sum(abs(Actual - Forecast)) / sum(Actual)
Bias  = sum(Forecast - Actual) / sum(Actual)
        positive = overforecast
        negative = underforecast

Important
---------
The Master Architecture currently treats SES alpha as a fixed, explainable
project parameter. This script therefore requires --ses-alpha explicitly
rather than silently hard-coding a value.

Example
-------
python 02_forecast_benchmark.py \
    --input ../data/retail_demand_history.csv \
    --output-dir ../data/_intermediate \
    --ses-alpha 0.20
"""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MA_WINDOWS = (4, 8, 13)
SEASON_LENGTH = 52
SES_INIT_WEEKS = 6

TRAIN_WEEKS = 104
VALIDATION_WEEKS = 26
HOLDOUT_WEEKS = 26
FUTURE_HORIZON = 8

MODEL_ORDER = (
    "Naive",
    "SeasonalNaive52",
    "MA4",
    "MA8",
    "MA13",
    "SES",
)


@dataclass(frozen=True)
class SplitConfig:
    train: int = TRAIN_WEEKS
    validation: int = VALIDATION_WEEKS
    holdout: int = HOLDOUT_WEEKS

    @property
    def total(self) -> int:
        return self.train + self.validation + self.holdout


# ---------------------------------------------------------------------------
# I/O and data preparation
# ---------------------------------------------------------------------------

def read_input(path: Path) -> pd.DataFrame:
    """Read canonical retail demand history from .xlsx/.xls/.csv."""
    suffix = path.suffix.lower()

    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path)
    elif suffix == ".csv":
        df = pd.read_csv(path)
    else:
        raise ValueError(
            f"Unsupported input format: {suffix}. Use .xlsx, .xls, or .csv."
        )

    required = {"Week_End_Date", "UPC", "Units_Sold", "History_Flag"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {sorted(missing)}")

    return df


def prepare_weekly_series(
    df: pd.DataFrame,
    expected_upcs: int = 47,
    expected_weeks: int = 156,
) -> pd.DataFrame:
    """
    Aggregate the cleaned source to UPC × Week.

    Only FULL_HISTORY UPCs are retained.
    """
    work = df.copy()
    work["Week_End_Date"] = pd.to_datetime(work["Week_End_Date"], errors="raise")
    work["UPC"] = work["UPC"].astype(str)
    work["Units_Sold"] = pd.to_numeric(work["Units_Sold"], errors="raise")

    work = work.loc[work["History_Flag"].eq("FULL_HISTORY")]

    weekly = (
        work.groupby(["UPC", "Week_End_Date"], as_index=False, sort=True)["Units_Sold"]
        .sum()
        .sort_values(["UPC", "Week_End_Date"])
        .reset_index(drop=True)
    )

    upc_count = weekly["UPC"].nunique()
    if upc_count != expected_upcs:
        raise ValueError(
            f"Expected {expected_upcs} FULL_HISTORY UPCs, found {upc_count}."
        )

    weeks_per_upc = weekly.groupby("UPC")["Week_End_Date"].nunique()
    bad = weeks_per_upc[weeks_per_upc.ne(expected_weeks)]
    if not bad.empty:
        raise ValueError(
            "FULL_HISTORY validation failed. "
            f"Expected {expected_weeks} weeks for every UPC; mismatches: "
            f"{bad.to_dict()}"
        )

    duplicate_count = int(
        weekly.duplicated(["UPC", "Week_End_Date"], keep=False).sum()
    )
    if duplicate_count:
        raise ValueError(
            f"Found {duplicate_count} duplicate UPC×Week rows after aggregation."
        )

    return weekly


# ---------------------------------------------------------------------------
# Forecast methods
# ---------------------------------------------------------------------------

def forecast_next_naive(history: np.ndarray) -> float:
    if history.size < 1:
        raise ValueError("Naive requires at least 1 observation.")
    return float(history[-1])


def forecast_next_seasonal_naive(
    history: np.ndarray,
    season_length: int = SEASON_LENGTH,
) -> float:
    if history.size < season_length:
        raise ValueError(
            f"Seasonal Naive requires at least {season_length} observations."
        )
    return float(history[-season_length])


def forecast_next_ma(history: np.ndarray, window: int) -> float:
    if history.size < window:
        raise ValueError(f"MA({window}) requires at least {window} observations.")
    return float(np.mean(history[-window:]))


def ses_final_level(
    history: np.ndarray,
    alpha: float,
    init_weeks: int = SES_INIT_WEEKS,
) -> float:
    """
    SES state after observing all values in `history`.

    Initialization:
        initial level = average of the first `init_weeks` observations.

    Then update from observation init_weeks+1 onward:
        level_t = alpha * y_t + (1-alpha) * level_(t-1)

    The one-step-ahead forecast is the current level.
    """
    if history.size < init_weeks:
        raise ValueError(
            f"SES requires at least {init_weeks} observations for initialization."
        )
    if not (0 < alpha < 1):
        raise ValueError("SES alpha must satisfy 0 < alpha < 1.")

    level = float(np.mean(history[:init_weeks]))
    for y in history[init_weeks:]:
        level = alpha * float(y) + (1.0 - alpha) * level
    return level


def forecast_next(
    history: np.ndarray,
    model: str,
    ses_alpha: float,
) -> float:
    if model == "Naive":
        return forecast_next_naive(history)
    if model == "SeasonalNaive52":
        return forecast_next_seasonal_naive(history, SEASON_LENGTH)
    if model == "MA4":
        return forecast_next_ma(history, 4)
    if model == "MA8":
        return forecast_next_ma(history, 8)
    if model == "MA13":
        return forecast_next_ma(history, 13)
    if model == "SES":
        return ses_final_level(history, ses_alpha, SES_INIT_WEEKS)

    raise ValueError(f"Unknown model: {model}")


# ---------------------------------------------------------------------------
# Rolling-origin evaluation
# ---------------------------------------------------------------------------

def rolling_one_step_forecasts(
    values: np.ndarray,
    start_index: int,
    end_index_exclusive: int,
    model: str,
    ses_alpha: float,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Forecast each target t using only actual history values[:t].

    This is a rolling-origin one-step-ahead evaluation.
    """
    forecasts: list[float] = []
    actuals: list[float] = []

    for t in range(start_index, end_index_exclusive):
        history = values[:t]
        fcst = forecast_next(history, model, ses_alpha)
        forecasts.append(fcst)
        actuals.append(float(values[t]))

    return np.asarray(actuals, dtype=float), np.asarray(forecasts, dtype=float)


def calculate_metrics(
    actual: np.ndarray,
    forecast: np.ndarray,
) -> dict[str, float]:
    if actual.shape != forecast.shape:
        raise ValueError("Actual and forecast arrays must have the same shape.")
    if actual.size == 0:
        raise ValueError("Cannot calculate metrics on empty arrays.")

    error = actual - forecast
    abs_error = np.abs(error)

    mae = float(np.mean(abs_error))
    actual_sum = float(np.sum(actual))

    if math.isclose(actual_sum, 0.0):
        wape = np.nan
        bias = np.nan
    else:
        wape = float(np.sum(abs_error) / actual_sum)
        bias = float(np.sum(forecast - actual) / actual_sum)

    return {
        "MAE": mae,
        "WAPE": wape,
        "Bias": bias,
        "Abs_Bias": abs(bias) if not np.isnan(bias) else np.nan,
    }


def evaluate_model_for_upc(
    values: np.ndarray,
    dates: np.ndarray,
    model: str,
    ses_alpha: float,
    split: SplitConfig,
) -> tuple[dict[str, float], dict[str, float], pd.DataFrame]:
    """
    Evaluate one model on validation and holdout periods.

    Validation: used for model selection.
    Holdout: final evaluation only.
    """
    val_start = split.train
    val_end = split.train + split.validation
    holdout_start = val_end
    holdout_end = split.total

    val_actual, val_forecast = rolling_one_step_forecasts(
        values, val_start, val_end, model, ses_alpha
    )
    hold_actual, hold_forecast = rolling_one_step_forecasts(
        values, holdout_start, holdout_end, model, ses_alpha
    )

    val_metrics = calculate_metrics(val_actual, val_forecast)
    hold_metrics = calculate_metrics(hold_actual, hold_forecast)

    rows = []
    for t, actual, fcst in zip(
        range(val_start, val_end), val_actual, val_forecast
    ):
        rows.append(
            {
                "Split": "Validation",
                "Week_End_Date": dates[t],
                "Actual": actual,
                "Forecast": fcst,
                "Error": actual - fcst,
            }
        )

    for t, actual, fcst in zip(
        range(holdout_start, holdout_end), hold_actual, hold_forecast
    ):
        rows.append(
            {
                "Split": "Holdout",
                "Week_End_Date": dates[t],
                "Actual": actual,
                "Forecast": fcst,
                "Error": actual - fcst,
            }
        )

    detail = pd.DataFrame(rows)
    detail["Model"] = model

    return val_metrics, hold_metrics, detail


# ---------------------------------------------------------------------------
# Best-model selection
# ---------------------------------------------------------------------------

def choose_best_model(validation_rows: pd.DataFrame) -> pd.Series:
    """
    Deterministic selection:
      1) lowest validation WAPE
      2) lowest validation MAE
      3) lowest |Bias|
      4) MODEL_ORDER as final deterministic tie-break
    """
    work = validation_rows.copy()
    order_map = {name: i for i, name in enumerate(MODEL_ORDER)}
    work["Model_Order"] = work["Model"].map(order_map)

    work = work.sort_values(
        ["Validation_WAPE", "Validation_MAE", "Validation_Abs_Bias", "Model_Order"],
        ascending=[True, True, True, True],
        kind="mergesort",
    )

    return work.iloc[0]


# ---------------------------------------------------------------------------
# Final future 8-week forecast
# ---------------------------------------------------------------------------

def future_forecast(
    history: np.ndarray,
    model: str,
    horizon: int,
    ses_alpha: float,
    season_length: int = SEASON_LENGTH,
) -> np.ndarray:
    """
    Produce recursive h-step forecasts after all 156 observed weeks.
    """
    hist = [float(x) for x in history]
    out: list[float] = []

    if model == "Naive":
        return np.repeat(hist[-1], horizon).astype(float)

    if model == "SeasonalNaive52":
        if len(hist) < season_length:
            raise ValueError("Insufficient history for Seasonal Naive.")
        for h in range(1, horizon + 1):
            # For h <= season_length, use the actual observation at t+h-season.
            source_index = len(hist) + h - season_length - 1
            out.append(float(hist[source_index]))
        return np.asarray(out, dtype=float)

    if model.startswith("MA"):
        window = int(model.replace("MA", ""))
        recursive = hist.copy()
        for _ in range(horizon):
            fcst = float(np.mean(recursive[-window:]))
            out.append(fcst)
            recursive.append(fcst)
        return np.asarray(out, dtype=float)

    if model == "SES":
        level = ses_final_level(
            np.asarray(hist, dtype=float),
            alpha=ses_alpha,
            init_weeks=SES_INIT_WEEKS,
        )
        return np.repeat(level, horizon).astype(float)

    raise ValueError(f"Unknown model: {model}")


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(
    weekly: pd.DataFrame,
    output_dir: Path,
    ses_alpha: float,
    split: SplitConfig,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if split.total != 156:
        raise ValueError(
            "This project is locked to 156 full-history weeks. "
            f"Current split totals {split.total}."
        )

    metric_rows: list[dict] = []
    detail_frames: list[pd.DataFrame] = []
    selected_rows: list[dict] = []
    future_rows: list[dict] = []

    for upc, grp in weekly.groupby("UPC", sort=True):
        grp = grp.sort_values("Week_End_Date").reset_index(drop=True)
        values = grp["Units_Sold"].to_numpy(dtype=float)
        dates = grp["Week_End_Date"].to_numpy()

        if len(values) != split.total:
            raise ValueError(
                f"UPC {upc}: expected {split.total} observations, found {len(values)}."
            )

        per_model_rows = []

        for model in MODEL_ORDER:
            val_metrics, hold_metrics, detail = evaluate_model_for_upc(
                values=values,
                dates=dates,
                model=model,
                ses_alpha=ses_alpha,
                split=split,
            )

            row = {
                "UPC": upc,
                "Model": model,
                "Validation_MAE": val_metrics["MAE"],
                "Validation_WAPE": val_metrics["WAPE"],
                "Validation_Bias": val_metrics["Bias"],
                "Validation_Abs_Bias": val_metrics["Abs_Bias"],
                "Holdout_MAE": hold_metrics["MAE"],
                "Holdout_WAPE": hold_metrics["WAPE"],
                "Holdout_Bias": hold_metrics["Bias"],
                "Holdout_Abs_Bias": hold_metrics["Abs_Bias"],
            }
            metric_rows.append(row)
            per_model_rows.append(row)

            detail.insert(0, "UPC", upc)
            detail_frames.append(detail)

        per_model_df = pd.DataFrame(per_model_rows)
        best = choose_best_model(per_model_df)
        best_model = str(best["Model"])

        selected_rows.append(
            {
                "UPC": upc,
                "Best_Model": best_model,
                "Validation_MAE": best["Validation_MAE"],
                "Validation_WAPE": best["Validation_WAPE"],
                "Validation_Bias": best["Validation_Bias"],
                "Holdout_MAE": best["Holdout_MAE"],
                "Holdout_WAPE": best["Holdout_WAPE"],
                "Holdout_Bias": best["Holdout_Bias"],
                "SES_Alpha": ses_alpha if best_model == "SES" else np.nan,
            }
        )

        future = future_forecast(
            history=values,
            model=best_model,
            horizon=FUTURE_HORIZON,
            ses_alpha=ses_alpha,
        )

        last_date = pd.Timestamp(grp["Week_End_Date"].iloc[-1])
        for h, fcst in enumerate(future, start=1):
            future_rows.append(
                {
                    "UPC": upc,
                    "Horizon_Week": h,
                    "Week_End_Date": last_date + pd.Timedelta(days=7 * h),
                    "Best_Model": best_model,
                    "Statistical_Forecast": fcst,
                }
            )

    metrics = pd.DataFrame(metric_rows).sort_values(["UPC", "Model"])
    selected = pd.DataFrame(selected_rows).sort_values("UPC")
    future = pd.DataFrame(future_rows).sort_values(["UPC", "Horizon_Week"])
    detail = pd.concat(detail_frames, ignore_index=True)

    # Model-selection flag in the full metric table.
    selected_key = set(zip(selected["UPC"], selected["Best_Model"]))
    metrics["Selected_on_Validation"] = [
        (u, m) in selected_key for u, m in zip(metrics["UPC"], metrics["Model"])
    ]

    # Compact planner-facing result for Excel / Power BI ingestion.
    planner_result = selected.merge(
        future.groupby("UPC", as_index=False)["Statistical_Forecast"]
        .sum()
        .rename(columns={"Statistical_Forecast": "Forecast_8W_Total"}),
        on="UPC",
        how="left",
        validate="one_to_one",
    )

    # QA summary.
    qa = pd.DataFrame(
        [
            ["FULL_HISTORY_UPC", weekly["UPC"].nunique(), 47],
            ["WEEKS_PER_UPC_MIN", weekly.groupby("UPC").size().min(), 156],
            ["WEEKS_PER_UPC_MAX", weekly.groupby("UPC").size().max(), 156],
            ["CANDIDATE_MODELS", metrics["Model"].nunique(), len(MODEL_ORDER)],
            ["SELECTED_MODELS", selected.shape[0], 47],
            ["FUTURE_ROWS", future.shape[0], 47 * FUTURE_HORIZON],
        ],
        columns=["Check", "Actual", "Expected"],
    )
    qa["Status"] = np.where(qa["Actual"].eq(qa["Expected"]), "PASS", "CHECK")

    # Exports.
    metrics.to_csv(output_dir / "forecast_model_metrics.csv", index=False)
    selected.to_csv(output_dir / "forecast_selected_models.csv", index=False)
    future.to_csv(output_dir / "forecast_8week.csv", index=False)
    planner_result.to_csv(output_dir / "forecast_results.csv", index=False)
    detail.to_csv(output_dir / "forecast_rolling_detail.csv", index=False)
    qa.to_csv(output_dir / "forecast_QA.csv", index=False)

    # Console summary.
    print("\n=== Forecast benchmark completed ===")
    print(f"SES alpha: {ses_alpha:.4f}")
    print(f"UPCs: {weekly['UPC'].nunique()}")
    print(f"Candidate models: {', '.join(MODEL_ORDER)}")
    print("\nSelected model counts:")
    print(selected["Best_Model"].value_counts().to_string())
    print("\nQA:")
    print(qa.to_string(index=False))
    print(f"\nOutputs written to: {output_dir.resolve()}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Rolling-origin forecast benchmark for FMCG Supply Planning."
    )
    parser.add_argument(
        "--input",
        type=Path,
        required=True,
        help="Path to retail_demand_history.xlsx or .csv",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Directory for intermediate forecast outputs.",
    )
    parser.add_argument(
        "--ses-alpha",
        type=float,
        required=True,
        help=(
            "Fixed SES alpha (0 < alpha < 1). "
            "Pass 0.20 if that proposed project value is approved."
        ),
    )
    parser.add_argument(
        "--expected-upcs",
        type=int,
        default=47,
        help="Expected FULL_HISTORY UPC count. Default: 47.",
    )
    parser.add_argument(
        "--expected-weeks",
        type=int,
        default=156,
        help="Expected weeks per FULL_HISTORY UPC. Default: 156.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not (0 < args.ses_alpha < 1):
        raise ValueError("--ses-alpha must satisfy 0 < alpha < 1.")

    raw = read_input(args.input)
    weekly = prepare_weekly_series(
        raw,
        expected_upcs=args.expected_upcs,
        expected_weeks=args.expected_weeks,
    )

    run_benchmark(
        weekly=weekly,
        output_dir=args.output_dir,
        ses_alpha=args.ses_alpha,
        split=SplitConfig(),
    )


if __name__ == "__main__":
    main()
