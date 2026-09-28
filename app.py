from __future__ import annotations

import json
from collections import deque
from datetime import date as Date
from functools import lru_cache
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

try:
    import holidays
except ImportError:  # The fallback supports local execution before dependencies are installed.
    holidays = None

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
GRID_FILE = DATA_DIR / "grid.geojson"
SCORE_ROOT = DATA_DIR / "runscore_base_service_parquet"
CONTINUITY_FILE = DATA_DIR / "continuity_scores.parquet"

RunType = Literal[
    "basic_recommendation",
    "recovery_beginner",
    "pb_record",
    "hill_training",
    "scenic_run",
    "long_run",
]

RUN_TYPE_LABELS: dict[str, str] = {
    "basic_recommendation": "\uae30\ubcf8 \ucd94\ucc9c",
    "recovery_beginner": "\ud68c\ubcf5\u00b7\ucd08\ubcf4 \ub7ec\ub2dd",
    "pb_record": "\uae30\ub85d\u00b7PB \ub7ec\ub2dd",
    "hill_training": "\uacbd\uc0ac \uc120\ud638 \ucf54\uc2a4",
    "scenic_run": "\uacbd\uad00 \ub7ec\ub2dd",
    "long_run": "Long Run",
}

# Slope is a flatness score; hill_training uses its reverse direction below.
RUN_TYPE_WEIGHTS: dict[str, dict[str, float]] = {
    "basic_recommendation": {
        "air": 0.15, "thermal": 0.19, "crowd": 0.15, "green": 0.22,
        "terrain": 0.16, "convenience": 0.13, "continuity": 0.00,
    },
    "recovery_beginner": {
        "air": 0.14, "thermal": 0.20, "crowd": 0.18, "green": 0.10,
        "terrain": 0.24, "convenience": 0.14, "continuity": 0.00,
    },
    "pb_record": {
        "air": 0.13, "thermal": 0.17, "crowd": 0.20, "green": 0.04,
        "terrain": 0.25, "convenience": 0.04, "continuity": 0.17,
    },
    "hill_training": {
        "air": 0.17, "thermal": 0.20, "crowd": 0.08, "green": 0.05,
        "terrain": 0.42, "convenience": 0.08, "continuity": 0.00,
    },
    "scenic_run": {
        "air": 0.22, "thermal": 0.16, "crowd": 0.08, "green": 0.44,
        "terrain": 0.05, "convenience": 0.05, "continuity": 0.00,
    },
    "long_run": {
        "air": 0.12, "thermal": 0.18, "crowd": 0.10, "green": 0.20,
        "terrain": 0.10, "convenience": 0.15, "continuity": 0.15,
    },
}

FALLBACK_PUBLIC_HOLIDAYS = frozenset(
    Date.fromisoformat(value)
    for value in (
        "2023-01-01", "2023-01-21", "2023-01-22", "2023-01-23", "2023-01-24",
        "2023-03-01", "2023-05-05", "2023-05-27", "2023-06-06", "2023-08-15",
        "2023-09-28", "2023-09-29", "2023-09-30", "2023-10-03", "2023-10-09", "2023-12-25",
        "2024-01-01", "2024-02-09", "2024-02-10", "2024-02-11", "2024-02-12",
        "2024-03-01", "2024-05-05", "2024-05-06", "2024-05-15", "2024-06-06",
        "2024-08-15", "2024-09-16", "2024-09-17", "2024-09-18", "2024-10-03", "2024-10-09", "2024-12-25",
        "2025-01-01", "2025-01-28", "2025-01-29", "2025-01-30", "2025-03-01",
        "2025-03-03", "2025-05-05", "2025-05-06", "2025-06-06", "2025-08-15",
        "2025-10-03", "2025-10-05", "2025-10-06", "2025-10-07", "2025-10-08", "2025-10-09", "2025-12-25",
        "2026-01-01", "2026-02-16", "2026-02-17", "2026-02-18", "2026-03-01",
        "2026-03-02", "2026-05-05", "2026-05-24", "2026-05-25", "2026-06-06",
        "2026-08-15", "2026-10-03", "2026-10-05", "2026-10-06", "2026-10-07", "2026-10-08", "2026-10-09", "2026-12-25",
    )
)

app = FastAPI(title="\uc0ac\uc6a9\uc790 \ub9de\ucda4\ud615 \uaca9\uc790 \ub7ec\ub2dd\ucf54\uc2a4 \ucd94\ucc9c", version="0.2.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/assets", StaticFiles(directory=DATA_DIR), name="assets")

with GRID_FILE.open(encoding="utf-8") as file:
    GRID_GEOJSON = json.load(file)

GRID_FEATURES = GRID_GEOJSON["features"]
GRID_PROPERTIES = [feature["properties"] for feature in GRID_FEATURES]
GRID_GEOMETRIES = [shape(feature["geometry"]) for feature in GRID_FEATURES]
GRID_TREE = STRtree(GRID_GEOMETRIES)
GRID_BY_FEATURE_ID = {
    int(properties["feature_id"]): properties for properties in GRID_PROPERTIES
}
GRID_BY_COORD = {
    (int(properties["grid_col"]), int(properties["grid_row"])): int(properties["feature_id"])
    for properties in GRID_PROPERTIES
}
FEATURE_ID_BY_GRID_ID = {
    str(properties["grid_id"]): int(properties["feature_id"])
    for properties in GRID_PROPERTIES
}

CONTINUITY_FRAME = pd.read_parquet(CONTINUITY_FILE, columns=["grid_id", "continuity_score"])
CONTINUITY_FRAME["grid_id"] = CONTINUITY_FRAME["grid_id"].astype(str)
CONTINUITY_BY_GRID_ID = dict(
    zip(CONTINUITY_FRAME["grid_id"], CONTINUITY_FRAME["continuity_score"].astype(float), strict=True)
)
if set(CONTINUITY_BY_GRID_ID) != set(FEATURE_ID_BY_GRID_ID):
    raise RuntimeError("Continuity scores and map grids do not match.")

DIRECTION_ORDER = ("up", "right", "down", "left")
DIRECTION_DELTAS = {
    "up": (0, 1),
    "right": (1, 0),
    "down": (0, -1),
    "left": (-1, 0),
}
MOVE_TARGETS = {3: 12, 5: 20, 10: 40}
LONG_RUN_RECENT_GRID_LIMIT = 4
LONG_RUN_REVISIT_PENALTY = 15.0


class RouteRequest(BaseModel):
    date: str
    hour: int = Field(ge=0, le=23)
    distance_km: Literal[3, 5, 10]
    start_mode: Literal["current", "best_within_1km"]
    run_type: RunType = "basic_recommendation"
    longitude: float
    latitude: float


@lru_cache(maxsize=16)
def statutory_holidays(year: int):
    if holidays is None:
        return FALLBACK_PUBLIC_HOLIDAYS
    try:
        return holidays.country_holidays("KR", years=year)
    except Exception:
        return FALLBACK_PUBLIC_HOLIDAYS


def parse_condition(date_text: str, hour: int) -> tuple[int, str, int]:
    try:
        selected_date = Date.fromisoformat(date_text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="\ub0a0\uc9dc \ud615\uc2dd\uc774 \uc62c\ubc14\ub974\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4.") from error

    is_holiday = selected_date.weekday() >= 5 or selected_date in statutory_holidays(selected_date.year)
    return selected_date.month, "Holiday" if is_holiday else "Workday", hour


def score_file_path(month: int, day_type: str) -> Path:
    return SCORE_ROOT / f"month_{month:02d}" / f"day_type_{day_type}" / "runscore.parquet"


@lru_cache(maxsize=64)
def get_condition_components(month: int, day_type: str, hour: int) -> pd.DataFrame:
    score_file = score_file_path(month, day_type)
    if not score_file.exists():
        raise HTTPException(status_code=404, detail="\uc120\ud0dd\ud55c \uc870\uac74\uc758 \uc810\uc218 \ub370\uc774\ud130\uac00 \uc5c6\uc2b5\ub2c8\ub2e4.")

    table = pq.read_table(
        score_file,
        columns=[
            "grid_key", "hour", "air_score_100", "thermal_score_100", "crowd_score_100",
            "green_shade_score_100", "slope_score_100", "convenience_score_100",
        ],
    )
    frame = table.to_pandas()
    frame = frame.loc[frame["hour"].eq(hour)].copy()
    frame["grid_id"] = frame["grid_key"].astype("int64").astype(str)
    frame["feature_id"] = frame["grid_id"].map(FEATURE_ID_BY_GRID_ID)
    frame["continuity_score"] = frame["grid_id"].map(CONTINUITY_BY_GRID_ID)

    if len(frame) != len(GRID_FEATURES) or frame[["feature_id", "continuity_score"]].isna().any().any():
        raise HTTPException(status_code=500, detail="\uacbd\ub85c \uc810\uc218 \uc870\ud569\uc5d0 \uc2e4\ud328\ud588\uc2b5\ub2c8\ub2e4.")

    return frame


def calculate_scores(month: int, day_type: str, hour: int, run_type: RunType) -> pd.DataFrame:
    frame = get_condition_components(month, day_type, hour).copy()
    weights = RUN_TYPE_WEIGHTS[run_type]
    terrain_score = frame["slope_score_100"]
    if run_type == "hill_training":
        terrain_score = 100 - terrain_score

    frame["final_score"] = (
        weights["air"] * frame["air_score_100"]
        + weights["thermal"] * frame["thermal_score_100"]
        + weights["crowd"] * frame["crowd_score_100"]
        + weights["green"] * frame["green_shade_score_100"]
        + weights["terrain"] * terrain_score
        + weights["convenience"] * frame["convenience_score_100"]
        + weights["continuity"] * frame["continuity_score"]
    )
    return frame[["feature_id", "grid_id", "final_score"]]


def locate_feature(longitude: float, latitude: float) -> int:
    point = Point(longitude, latitude)
    candidate_indices = GRID_TREE.query(point)
    for candidate_index in candidate_indices:
        index = int(candidate_index)
        if GRID_GEOMETRIES[index].covers(point):
            return int(GRID_PROPERTIES[index]["feature_id"])

    raise HTTPException(
        status_code=422,
        detail="\uc120\ud0dd\ud55c \uc704\uce58\uac00 \ucd94\ucc9c \uac00\ub2a5\ud55c 250m \uaca9\uc790 \uc548\uc5d0 \uc5c6\uc2b5\ub2c8\ub2e4. \uc11c\uc6b8 \uaca9\uc790 \uc601\uc5ed\uc744 \ub2e4\uc2dc \ud074\ub9ad\ud574 \uc8fc\uc138\uc694.",
    )


def make_route_step(feature_id: int, score_map: dict[int, float], step: int) -> dict:
    properties = GRID_BY_FEATURE_ID[feature_id]
    return {
        "step": step,
        "feature_id": feature_id,
        "grid_id": properties["grid_id"],
        "longitude": properties["center_lng"],
        "latitude": properties["center_lat"],
        "district": properties.get("admin_district", properties["district"]),
        "address_label": properties["address_label"],
        "score": round(float(score_map[feature_id]), 1),
    }


def choose_best_start(current_feature_id: int, score_map: dict[int, float]) -> int:
    current = GRID_BY_FEATURE_ID[current_feature_id]
    current_col = int(current["grid_col"])
    current_row = int(current["grid_row"])
    candidates: list[int] = []

    for col_offset in range(-4, 5):
        for row_offset in range(-4, 5):
            if abs(col_offset) + abs(row_offset) > 4:
                continue
            feature_id = GRID_BY_COORD.get((current_col + col_offset, current_row + row_offset))
            if feature_id is not None and feature_id in score_map:
                candidates.append(feature_id)

    if not candidates:
        return current_feature_id

    return min(
        candidates,
        key=lambda feature_id: (
            -score_map[feature_id],
            abs(int(GRID_BY_FEATURE_ID[feature_id]["grid_col"]) - current_col)
            + abs(int(GRID_BY_FEATURE_ID[feature_id]["grid_row"]) - current_row),
            GRID_BY_FEATURE_ID[feature_id]["grid_id"],
        ),
    )


def build_greedy_route(
    start_feature_id: int,
    score_map: dict[int, float],
    distance_km: int,
) -> tuple[list[dict], str | None]:
    move_target = MOVE_TARGETS[distance_km]
    strict_no_revisit = distance_km in {3, 5}
    route = [start_feature_id]
    visited = {start_feature_id}
    visit_counts = {start_feature_id: 1}
    recent_grids = deque([start_feature_id], maxlen=LONG_RUN_RECENT_GRID_LIMIT)
    current_feature_id = start_feature_id
    last_direction: str | None = None
    termination_reason: str | None = None

    for _ in range(move_target):
        current = GRID_BY_FEATURE_ID[current_feature_id]
        current_col = int(current["grid_col"])
        current_row = int(current["grid_row"])
        candidates: list[tuple[int, float, int, int, str]] = []

        for direction_index, direction in enumerate(DIRECTION_ORDER):
            col_delta, row_delta = DIRECTION_DELTAS[direction]
            candidate_feature_id = GRID_BY_COORD.get((current_col + col_delta, current_row + row_delta))
            if candidate_feature_id is None or candidate_feature_id not in score_map:
                continue
            if strict_no_revisit and candidate_feature_id in visited:
                continue
            if not strict_no_revisit and candidate_feature_id in recent_grids:
                continue

            revisit_count = visit_counts.get(candidate_feature_id, 0)
            effective_score = score_map[candidate_feature_id]
            if not strict_no_revisit:
                effective_score -= LONG_RUN_REVISIT_PENALTY * revisit_count
            continue_penalty = 0 if direction == last_direction else 1
            candidates.append((candidate_feature_id, effective_score, continue_penalty, direction_index, direction))

        if not candidates:
            termination_reason = (
                "\ub354 \uc774\uc0c1 \uc774\ub3d9\ud560 \uc218 \uc788\ub294 \uc778\uc811 \uaca9\uc790\uac00 \uc5c6\uc5b4 \ud0d0\uc0c9\uc744 \uc885\ub8cc\ud588\uc2b5\ub2c8\ub2e4."
                if strict_no_revisit
                else "\ucd5c\uadfc \uacbd\ub85c\ub97c \ubc18\ubcf5\ud558\uc9c0 \uc54a\ub294 \uc778\uc811 \uaca9\uc790\uac00 \uc5c6\uc5b4 10km \ud0d0\uc0c9\uc744 \uc885\ub8cc\ud588\uc2b5\ub2c8\ub2e4."
            )
            break

        next_feature_id, _, _, _, next_direction = min(
            candidates,
            key=lambda item: (-item[1], item[2], item[3]),
        )
        route.append(next_feature_id)
        visited.add(next_feature_id)
        visit_counts[next_feature_id] = visit_counts.get(next_feature_id, 0) + 1
        recent_grids.append(next_feature_id)
        current_feature_id = next_feature_id
        last_direction = next_direction

    route_steps = [make_route_step(feature_id, score_map, step) for step, feature_id in enumerate(route)]
    return route_steps, termination_reason


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "grid_count": len(GRID_FEATURES),
        "score_mode": "actual_weighted",
        "run_types": RUN_TYPE_LABELS,
        "weights": RUN_TYPE_WEIGHTS,
        "message": "\uc2e4\uc81c \uc810\uc218 \ub370\uc774\ud130\uc640 \uc720\ud615\ubcc4 \uac00\uc911\uce58\ub97c \uc801\uc6a9\ud569\ub2c8\ub2e4.",
    }


@app.get("/api/scores")
def scores(
    date: str,
    hour: int = Query(ge=0, le=23),
    run_type: RunType = "basic_recommendation",
) -> dict:
    month, day_type, selected_hour = parse_condition(date, hour)
    score_frame = calculate_scores(month, day_type, selected_hour, run_type)
    return {
        "month": month,
        "day_type": day_type,
        "hour": selected_hour,
        "run_type": run_type,
        "run_type_label": RUN_TYPE_LABELS[run_type],
        "scores": [
            {
                "feature_id": int(row.feature_id),
                "final_score": round(float(row.final_score), 1),
            }
            for row in score_frame.itertuples(index=False)
        ],
    }


@app.post("/api/route")
def route(request: RouteRequest) -> dict:
    month, day_type, hour = parse_condition(request.date, request.hour)
    distance_km = 10 if request.run_type == "long_run" else request.distance_km
    score_frame = calculate_scores(month, day_type, hour, request.run_type)
    score_map = {
        int(row.feature_id): float(row.final_score)
        for row in score_frame.itertuples(index=False)
    }

    current_feature_id = locate_feature(request.longitude, request.latitude)
    start_feature_id = (
        current_feature_id
        if request.start_mode == "current"
        else choose_best_start(current_feature_id, score_map)
    )
    route_steps, termination_reason = build_greedy_route(start_feature_id, score_map, distance_km)

    return {
        "condition": {
            "month": month,
            "day_type": day_type,
            "hour": hour,
            "distance_km": distance_km,
            "start_mode": request.start_mode,
            "run_type": request.run_type,
            "run_type_label": RUN_TYPE_LABELS[request.run_type],
        },
        "clicked_feature_id": current_feature_id,
        "start_feature_id": start_feature_id,
        "route": route_steps,
        "target_moves": MOVE_TARGETS[distance_km],
        "actual_moves": len(route_steps) - 1,
        "estimated_distance_m": (len(route_steps) - 1) * 250,
        "mean_score": round(float(np.mean([step["score"] for step in route_steps])), 1),
        "termination_reason": termination_reason,
    }
