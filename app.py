from __future__ import annotations

import json
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

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
STATIC_DIR = APP_DIR / "static"
GRID_FILE = DATA_DIR / "grid.geojson"
SCORE_FILE = DATA_DIR / "demo_final_scores.parquet"

app = FastAPI(title="사용자 맞춤형 격자 코스 추천", version="0.1.0")
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
SCORE_PARQUET = pq.ParquetFile(SCORE_FILE)
DIRECTION_ORDER = ("up", "right", "down", "left")
DIRECTION_DELTAS = {
    "up": (0, 1),
    "right": (1, 0),
    "down": (0, -1),
    "left": (-1, 0),
}


class RouteRequest(BaseModel):
    date: str
    hour: int = Field(ge=0, le=23)
    distance_km: Literal[3, 5]
    start_mode: Literal["current", "best_within_1km"]
    longitude: float
    latitude: float


def parse_condition(date_text: str, hour: int) -> tuple[int, str, int]:
    try:
        selected_date = Date.fromisoformat(date_text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="날짜 형식이 올바르지 않습니다.") from error

    day_type = "weekday" if selected_date.weekday() < 5 else "weekend"
    return selected_date.month, day_type, hour


def row_group_index(month: int, day_type: str, hour: int) -> int:
    day_index = 0 if day_type == "weekday" else 1
    return ((month - 1) * 2 + day_index) * 24 + hour


@lru_cache(maxsize=64)
def get_condition_scores(month: int, day_type: str, hour: int) -> pd.DataFrame:
    index = row_group_index(month, day_type, hour)
    if index >= SCORE_PARQUET.metadata.num_row_groups:
        raise HTTPException(status_code=404, detail="선택 조건의 예시 점수 데이터가 없습니다.")

    table = SCORE_PARQUET.read_row_group(
        index,
        columns=["feature_id", "grid_id", "final_score"],
    )
    return table.to_pandas()


def locate_feature(longitude: float, latitude: float) -> int:
    point = Point(longitude, latitude)
    candidate_indices = GRID_TREE.query(point)
    for candidate_index in candidate_indices:
        index = int(candidate_index)
        if GRID_GEOMETRIES[index].covers(point):
            return int(GRID_PROPERTIES[index]["feature_id"])

    raise HTTPException(
        status_code=422,
        detail="선택한 위치가 추천 가능한 250m 격자 안에 없습니다. 서울 격자 영역을 다시 클릭해 주세요.",
    )


def make_route_step(feature_id: int, score_map: dict[int, float], step: int) -> dict:
    properties = GRID_BY_FEATURE_ID[feature_id]
    return {
        "step": step,
        "feature_id": feature_id,
        "grid_id": properties["grid_id"],
        "longitude": properties["center_lng"],
        "latitude": properties["center_lat"],
        "district": properties["district"],
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
            feature_id = GRID_BY_COORD.get(
                (current_col + col_offset, current_row + row_offset)
            )
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
    move_target = 12 if distance_km == 3 else 20
    route = [start_feature_id]
    visited = {start_feature_id}
    current_feature_id = start_feature_id
    last_direction: str | None = None
    termination_reason: str | None = None

    for _ in range(move_target):
        current = GRID_BY_FEATURE_ID[current_feature_id]
        current_col = int(current["grid_col"])
        current_row = int(current["grid_row"])
        candidates: list[tuple[int, int, int, str]] = []

        for direction_index, direction in enumerate(DIRECTION_ORDER):
            col_delta, row_delta = DIRECTION_DELTAS[direction]
            candidate_feature_id = GRID_BY_COORD.get(
                (current_col + col_delta, current_row + row_delta)
            )
            if candidate_feature_id is None or candidate_feature_id in visited:
                continue
            if candidate_feature_id not in score_map:
                continue

            continue_penalty = 0 if direction == last_direction else 1
            candidates.append(
                (candidate_feature_id, continue_penalty, direction_index, direction)
            )

        if not candidates:
            termination_reason = "더 이상 방문하지 않은 유효 인접 격자가 없어 탐색을 종료했습니다."
            break

        next_feature_id, _, _, next_direction = min(
            candidates,
            key=lambda item: (-score_map[item[0]], item[1], item[2]),
        )
        route.append(next_feature_id)
        visited.add(next_feature_id)
        current_feature_id = next_feature_id
        last_direction = next_direction

    route_steps = [
        make_route_step(feature_id, score_map, step)
        for step, feature_id in enumerate(route)
    ]
    return route_steps, termination_reason


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/meta")
def meta() -> dict:
    return {
        "grid_count": len(GRID_FEATURES),
        "score_mode": "demo",
        "message": "현재 화면은 예시용 Final Score로 작동합니다.",
    }


@app.get("/api/scores")
def scores(date: str, hour: int = Query(ge=0, le=23)) -> dict:
    month, day_type, selected_hour = parse_condition(date, hour)
    score_frame = get_condition_scores(month, day_type, selected_hour)
    return {
        "month": month,
        "day_type": day_type,
        "hour": selected_hour,
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
    score_frame = get_condition_scores(month, day_type, hour)
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
    route_steps, termination_reason = build_greedy_route(
        start_feature_id,
        score_map,
        request.distance_km,
    )

    return {
        "condition": {
            "month": month,
            "day_type": day_type,
            "hour": hour,
            "distance_km": request.distance_km,
            "start_mode": request.start_mode,
        },
        "clicked_feature_id": current_feature_id,
        "start_feature_id": start_feature_id,
        "route": route_steps,
        "target_moves": 12 if request.distance_km == 3 else 20,
        "actual_moves": len(route_steps) - 1,
        "estimated_distance_m": (len(route_steps) - 1) * 250,
        "mean_score": round(float(np.mean([step["score"] for step in route_steps])), 1),
        "termination_reason": termination_reason,
    }
