from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import mapping

APP_DIR = Path(__file__).resolve().parents[1]
PROJECT_DIR = APP_DIR.parent
DATA_DIR = APP_DIR / "data"
REFERENCE_DIR = (
    PROJECT_DIR
    / "running_map_share_final"
    / "processed"
    / "sceneario_outputs"
    / "run_20260927_014459"
)
GRID_OUTPUT = DATA_DIR / "grid.geojson"
SCORE_OUTPUT = DATA_DIR / "demo_final_scores.parquet"
MANIFEST_OUTPUT = DATA_DIR / "demo_score_manifest.json"


def clean_district(value: object) -> str:
    district = str(value)
    if not district or district.lower() == "nan" or "\ufffd" in district:
        return "서울"
    return district


def deterministic_scores(
    feature_ids: np.ndarray,
    grid_cols: np.ndarray,
    grid_rows: np.ndarray,
    month: int,
    day_type: str,
    hour: int,
) -> np.ndarray:
    spatial_wave = (
        np.sin(grid_cols * 0.37 + grid_rows * 0.13) * 9
        + np.cos(grid_rows * 0.29) * 6
    )
    pseudo_noise = np.sin(feature_ids * 12.9898 + month * 78.233 + hour * 37.719) * 4
    seasonal = 8 * np.cos((month - 5) * np.pi / 6)
    evening_bonus = 9 * np.exp(-((hour - 19) ** 2) / 18)
    morning_bonus = 5 * np.exp(-((hour - 7) ** 2) / 10)
    night_penalty = -8 if hour <= 5 else 0
    weekend_bonus = 3 if day_type == "weekend" else 0

    score = 60 + spatial_wave + pseudo_noise + seasonal + evening_bonus + morning_bonus
    score = score + night_penalty + weekend_bonus
    return np.clip(score, 5, 98).astype("float32")


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    source_files = sorted(REFERENCE_DIR.glob("grid_running_final_*.gpkg"))
    if not source_files:
        raise FileNotFoundError(f"참고 격자 파일을 찾지 못했습니다: {REFERENCE_DIR}")

    source_path = source_files[0]
    grid = gpd.read_file(
        source_path,
        columns=["grid_id", "x_center", "y_center", "district", "geometry"],
    ).dropna(subset=["x_center", "y_center", "geometry"])
    center_points = gpd.GeoSeries(
        gpd.points_from_xy(grid["x_center"], grid["y_center"]),
        crs=grid.crs,
    ).to_crs(4326)
    grid["center_lng"] = center_points.x.to_numpy()
    grid["center_lat"] = center_points.y.to_numpy()
    grid = grid.to_crs(4326).copy()

    min_x = float(grid["x_center"].min())
    min_y = float(grid["y_center"].min())
    grid["grid_col"] = np.rint((grid["x_center"] - min_x) / 250).astype(int)
    grid["grid_row"] = np.rint((grid["y_center"] - min_y) / 250).astype(int)
    grid = grid.sort_values(["grid_col", "grid_row"]).reset_index(drop=True)
    grid["feature_id"] = np.arange(len(grid), dtype=np.int32)
    grid["app_grid_id"] = [
        f"g_{col:03d}_{row:03d}"
        for col, row in zip(grid["grid_col"], grid["grid_row"])
    ]
    grid["district_label"] = grid["district"].map(clean_district)

    features = []
    for index, row in grid.iterrows():
        district = row["district_label"]
        features.append(
            {
                "type": "Feature",
                "id": int(row["feature_id"]),
                "properties": {
                    "feature_id": int(row["feature_id"]),
                    "grid_id": row["app_grid_id"],
                    "grid_col": int(row["grid_col"]),
                    "grid_row": int(row["grid_row"]),
                    "center_lng": round(float(row["center_lng"]), 7),
                    "center_lat": round(float(row["center_lat"]), 7),
                    "district": district,
                    "address_label": f"{district} 250m 격자",
                },
                "geometry": mapping(row.geometry),
            }
        )

    GRID_OUTPUT.write_text(
        json.dumps(
            {"type": "FeatureCollection", "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    feature_ids = grid["feature_id"].to_numpy(dtype=np.int32)
    grid_ids = grid["app_grid_id"].to_numpy()
    grid_cols = grid["grid_col"].to_numpy(dtype=np.float32)
    grid_rows = grid["grid_row"].to_numpy(dtype=np.float32)
    schema = pa.schema(
        [
            ("feature_id", pa.int32()),
            ("grid_id", pa.string()),
            ("month", pa.int8()),
            ("day_type", pa.string()),
            ("hour", pa.int8()),
            ("final_score", pa.float32()),
        ]
    )

    with pq.ParquetWriter(
        SCORE_OUTPUT,
        schema,
        compression="zstd",
        use_dictionary=["grid_id", "day_type"],
    ) as writer:
        for month in range(1, 13):
            for day_type in ("weekday", "weekend"):
                for hour in range(24):
                    score = deterministic_scores(
                        feature_ids,
                        grid_cols,
                        grid_rows,
                        month,
                        day_type,
                        hour,
                    )
                    writer.write_table(
                        pa.table(
                            {
                                "feature_id": feature_ids,
                                "grid_id": grid_ids,
                                "month": np.full(len(grid), month, dtype=np.int8),
                                "day_type": np.full(len(grid), day_type),
                                "hour": np.full(len(grid), hour, dtype=np.int8),
                                "final_score": score,
                            },
                            schema=schema,
                        )
                    )

    manifest = {
        "mode": "demo",
        "source_grid_reference": str(source_path.relative_to(PROJECT_DIR)),
        "grid_count": int(len(grid)),
        "condition_count": 12 * 2 * 24,
        "score_rows": int(len(grid) * 12 * 2 * 24),
        "score_formula": "Deterministic synthetic demo score. Replace with actual final grid score data.",
    }
    MANIFEST_OUTPUT.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Grid GeoJSON: {GRID_OUTPUT} ({len(grid):,} cells)")
    print(f"Demo score Parquet: {SCORE_OUTPUT} ({manifest['score_rows']:,} rows)")


if __name__ == "__main__":
    main()
