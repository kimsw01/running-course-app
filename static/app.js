const state = {
  map: null,
  selected: null,
  selectedMarker: null,
  runnerMarker: null,
  scoreFeatureIds: [],
  routeFeatureIds: [],
  animationToken: 0,
};

const dateInput = document.getElementById("date-input");
const hourInput = document.getElementById("hour-input");
const routeButton = document.getElementById("route-button");
const routeTitle = document.getElementById("route-title");
const routeDetail = document.getElementById("route-detail");
const routeSummary = document.getElementById("route-summary");
const routeStop = document.getElementById("route-stop");

for (let hour = 0; hour < 24; hour += 1) {
  const option = document.createElement("option");
  option.value = String(hour);
  option.textContent = String(hour).padStart(2, "0") + ":00";
  hourInput.appendChild(option);
}

function selectedValue(name) {
  const choice = document.querySelector('input[name="' + name + '"]:checked');
  return choice ? choice.value : "";
}

function dayTypeLabel(dateText) {
  const day = new Date(dateText + "T12:00:00").getDay();
  return day === 0 || day === 6 ? "주말" : "평일";
}

function conditionsReady() {
  return Boolean(
    dateInput.value &&
    hourInput.value !== "" &&
    selectedValue("distance") &&
    selectedValue("start-mode") &&
    state.selected
  );
}

function updateRouteButton() {
  routeButton.disabled = !conditionsReady();
}

function resetSummary() {
  routeSummary.innerHTML = "";
  routeStop.hidden = true;
  routeStop.textContent = "";
}

function setPanel(title, detail) {
  routeTitle.textContent = title;
  routeDetail.textContent = detail;
}

function setSummary(items) {
  routeSummary.innerHTML = "";
  items.forEach(function(item) {
    const term = document.createElement("dt");
    const definition = document.createElement("dd");
    term.textContent = item[0];
    definition.textContent = item[1];
    routeSummary.appendChild(term);
    routeSummary.appendChild(definition);
  });
}

function basePaintExpression() {
  return [
    "case",
    ["boolean", ["feature-state", "visited"], false], "#ef7549",
    ["boolean", ["feature-state", "selected"], false], "#16765f",
    ["boolean", ["feature-state", "hasScore"], false],
    [
      "interpolate", ["linear"], ["feature-state", "score"],
      0, "#dbe6de",
      40, "#94cdb1",
      65, "#f7cd63",
      100, "#df6240"
    ],
    "#dbe6de"
  ];
}

function initializeMap() {
  const map = new maplibregl.Map({
    container: "map",
    style: "https://tiles.openfreemap.org/styles/liberty",
    center: [126.978, 37.5665],
    zoom: 10.5,
    minZoom: 9.5,
    maxZoom: 16,
    attributionControl: true,
  });
  state.map = map;
  map.addControl(new maplibregl.NavigationControl(), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: "metric" }));

  map.on("load", function() {
    map.addSource("grids", {
      type: "geojson",
      data: "/assets/grid.geojson",
      promoteId: "feature_id",
    });
    map.addLayer({
      id: "grid-fill",
      type: "fill",
      source: "grids",
      paint: {
        "fill-color": basePaintExpression(),
        "fill-opacity": 0.67,
      },
    });
    map.addLayer({
      id: "grid-outline",
      type: "line",
      source: "grids",
      paint: {
        "line-color": [
          "case",
          ["boolean", ["feature-state", "visited"], false], "#9d321d",
          ["boolean", ["feature-state", "selected"], false], "#0c5342",
          "#6f8077"
        ],
        "line-width": [
          "case",
          ["boolean", ["feature-state", "visited"], false], 2.2,
          ["boolean", ["feature-state", "selected"], false], 2,
          0.28
        ],
        "line-opacity": 0.62,
      },
    });
    map.addSource("route-line", {
      type: "geojson",
      data: { type: "Feature", geometry: { type: "LineString", coordinates: [] } },
    });
    map.addLayer({
      id: "route-line-layer",
      type: "line",
      source: "route-line",
      paint: {
        "line-color": "#9d321d",
        "line-width": 4,
        "line-opacity": 0.86,
      },
    });

    map.on("mouseenter", "grid-fill", function() {
      map.getCanvas().style.cursor = "crosshair";
    });
    map.on("mouseleave", "grid-fill", function() {
      map.getCanvas().style.cursor = "";
    });
    map.on("click", "grid-fill", selectGrid);
  });
}

function selectGrid(event) {
  const feature = event.features && event.features[0];
  if (!feature) return;
  const properties = feature.properties;
  if (state.selected) {
    state.map.setFeatureState(
      { source: "grids", id: state.selected.feature_id },
      { selected: false }
    );
  }
  state.selected = {
    feature_id: Number(properties.feature_id),
    longitude: Number(properties.center_lng),
    latitude: Number(properties.center_lat),
    district: properties.district,
    address_label: properties.address_label,
  };
  state.map.setFeatureState(
    { source: "grids", id: state.selected.feature_id },
    { selected: true }
  );

  if (state.selectedMarker) state.selectedMarker.remove();
  state.selectedMarker = new maplibregl.Marker({ color: "#16765f" })
    .setLngLat([state.selected.longitude, state.selected.latitude])
    .setPopup(
      new maplibregl.Popup({ offset: 22 }).setText(
        "선택 위치 · " + state.selected.address_label
      )
    )
    .addTo(state.map);

  setPanel("현재 위치 격자를 선택했습니다", state.selected.address_label);
  resetSummary();
  updateRouteButton();
}

async function refreshScores() {
  if (!dateInput.value || hourInput.value === "" || !state.map || !state.map.isStyleLoaded()) {
    return;
  }
  const url = "/api/scores?date=" + encodeURIComponent(dateInput.value) + "&hour=" + hourInput.value;
  const response = await fetch(url);
  if (!response.ok) {
    setPanel("점수 데이터를 불러오지 못했습니다", "날짜와 시간을 다시 확인해 주세요.");
    return;
  }
  const payload = await response.json();
  state.scoreFeatureIds.forEach(function(featureId) {
    state.map.setFeatureState({ source: "grids", id: featureId }, { hasScore: false, score: null });
  });
  state.scoreFeatureIds = payload.scores.map(function(score) { return score.feature_id; });
  payload.scores.forEach(function(score) {
    state.map.setFeatureState(
      { source: "grids", id: score.feature_id },
      { hasScore: true, score: score.final_score }
    );
  });
  if (!state.selected) {
    setPanel(
      "예시 점수 지도를 불러왔습니다",
      payload.month + "월 " + dayTypeLabel(dateInput.value) + " " + String(payload.hour).padStart(2, "0") + ":00 조건입니다."
    );
  }
}

function clearRoute() {
  state.animationToken += 1;
  state.routeFeatureIds.forEach(function(featureId) {
    state.map.setFeatureState({ source: "grids", id: featureId }, { visited: false });
  });
  state.routeFeatureIds = [];
  if (state.runnerMarker) {
    state.runnerMarker.remove();
    state.runnerMarker = null;
  }
  if (state.map && state.map.getSource("route-line")) {
    state.map.getSource("route-line").setData({
      type: "Feature",
      geometry: { type: "LineString", coordinates: [] },
    });
  }
}

function sleep(milliseconds) {
  return new Promise(function(resolve) { window.setTimeout(resolve, milliseconds); });
}

async function animateRoute(payload) {
  clearRoute();
  const token = state.animationToken;
  const route = payload.route;
  const runnerElement = document.createElement("div");
  runnerElement.className = "runner-marker";
  runnerElement.textContent = "🏃";
  state.runnerMarker = new maplibregl.Marker({ element: runnerElement, anchor: "center" })
    .setLngLat([route[0].longitude, route[0].latitude])
    .addTo(state.map);

  const coordinates = [];
  for (let index = 0; index < route.length; index += 1) {
    if (token !== state.animationToken) return;
    const step = route[index];
    coordinates.push([step.longitude, step.latitude]);
    state.routeFeatureIds.push(step.feature_id);
    state.map.setFeatureState({ source: "grids", id: step.feature_id }, { visited: true });
    state.runnerMarker.setLngLat([step.longitude, step.latitude]);
    state.map.getSource("route-line").setData({
      type: "Feature",
      geometry: { type: "LineString", coordinates: coordinates },
    });
    setPanel(
      "경로 탐색 " + index + " / " + (route.length - 1),
      step.address_label + " · Score " + step.score
    );
    setSummary([
      ["현재 위치", step.district],
      ["현재 격자", step.grid_id],
      ["누적 거리", (index * 250).toLocaleString() + "m"],
      ["현재 Score", String(step.score)],
    ]);
    if (index < route.length - 1) await sleep(1150);
  }

  setPanel("경로 탐색 완료", "방문한 격자와 이동 경로를 지도에 표시했습니다.");
  setSummary([
    ["출발 격자", route[0].grid_id],
    ["방문 격자", String(route.length) + "개"],
    ["예상 거리", payload.estimated_distance_m.toLocaleString() + "m"],
    ["평균 Score", String(payload.mean_score)],
  ]);
  if (payload.termination_reason) {
    routeStop.textContent = payload.termination_reason;
    routeStop.hidden = false;
  }
}

async function findRoute() {
  if (!conditionsReady()) return;
  clearRoute();
  routeButton.disabled = true;
  routeButton.textContent = "탐색 중";
  setPanel("최적 격자 경로를 계산하고 있습니다", "선택 조건의 예시 Final Score를 비교합니다.");
  resetSummary();

  const payload = {
    date: dateInput.value,
    hour: Number(hourInput.value),
    distance_km: Number(selectedValue("distance")),
    start_mode: selectedValue("start-mode"),
    longitude: state.selected.longitude,
    latitude: state.selected.latitude,
  };

  try {
    const response = await fetch("/api/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const routePayload = await response.json();
    if (!response.ok) throw new Error(routePayload.detail || "경로를 생성하지 못했습니다.");
    await animateRoute(routePayload);
  } catch (error) {
    setPanel("경로를 생성하지 못했습니다", error.message);
  } finally {
    routeButton.textContent = "경로 탐색";
    updateRouteButton();
  }
}

[dateInput, hourInput].forEach(function(element) {
  element.addEventListener("change", function() {
    refreshScores();
    updateRouteButton();
  });
});
document.querySelectorAll('input[name="distance"], input[name="start-mode"]').forEach(function(element) {
  element.addEventListener("change", updateRouteButton);
});
routeButton.addEventListener("click", findRoute);

initializeMap();
