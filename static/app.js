const state = {
  map: null,
  selected: null,
  selectedMarker: null,
  runnerMarker: null,
  scoreFeatureIds: [],
  routeFeatureIds: [],
  routeFeatureIdSet: new Set(),
  routeComplete: false,
  routeHoverPopup: null,
  animationToken: 0,
  isRouting: false,
};

const dateInput = document.getElementById("date-input");
const hourInput = document.getElementById("hour-input");
const routeButton = document.getElementById("route-button");
const routeTitle = document.getElementById("route-title");
const routeDetail = document.getElementById("route-detail");
const routeSummary = document.getElementById("route-summary");
const routeStop = document.getElementById("route-stop");
const validationModal = document.getElementById("validation-modal");
const missingConditions = document.getElementById("missing-conditions");
const validationClose = document.getElementById("validation-close");

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

function syncLongRunDistance() {
  const isLongRun = selectedValue("run-type") === "long_run";
  const distanceInputs = document.querySelectorAll('input[name="distance"]');
  distanceInputs.forEach(function(input) {
    const isLocked = isLongRun && input.value !== "10";
    input.disabled = isLocked;
    input.closest("label").classList.toggle("is-locked", isLocked);
  });
  if (isLongRun) {
    document.querySelector('input[name="distance"][value="10"]').checked = true;
  }
}

function dayTypeLabel(dayType) {
  return dayType === "Holiday" ? "휴일" : "평일";
}

function missingConditionLabels() {
  const missing = [];
  if (!dateInput.value) missing.push("날짜");
  if (hourInput.value === "") missing.push("시간");
  if (!selectedValue("distance")) missing.push("목표 거리");
  if (!selectedValue("start-mode")) missing.push("출발지");
  if (!state.selected) missing.push("현재 위치 격자");
  return missing;
}

function conditionsReady() {
  return missingConditionLabels().length === 0;
}

function updateRouteButton() {
  routeButton.disabled = state.isRouting;
}

function showValidationModal(missing) {
  missingConditions.innerHTML = "";
  missing.forEach(function(label) {
    const item = document.createElement("li");
    item.textContent = label;
    missingConditions.appendChild(item);
  });
  validationModal.hidden = false;
  validationClose.focus();
}

function hideValidationModal() {
  validationModal.hidden = true;
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
    map.on("mousemove", "grid-fill", showRouteGridLabel);
    map.on("mouseleave", "grid-fill", function() {
      map.getCanvas().style.cursor = "";
      hideRouteGridLabel();
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
  const runType = selectedValue("run-type") || "basic_recommendation";
  const url = "/api/scores?date=" + encodeURIComponent(dateInput.value) + "&hour=" + hourInput.value + "&run_type=" + encodeURIComponent(runType);
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
      "선택 조건의 맞춤 점수 지도를 불러왔습니다",
      payload.run_type_label + " · " + payload.month + "월 " + dayTypeLabel(payload.day_type) + " " + String(payload.hour).padStart(2, "0") + ":00 조건입니다."
    );
  }
}

function clearRoute() {
  state.animationToken += 1;
  state.routeComplete = false;
  hideRouteGridLabel();
  state.routeFeatureIds.forEach(function(featureId) {
    state.map.setFeatureState({ source: "grids", id: featureId }, { visited: false });
  });
  state.routeFeatureIds = [];
  state.routeFeatureIdSet = new Set();
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

function setRouteLine(coordinates) {
  const source = state.map.getSource("route-line");
  if (!source) return;
  source.setData({
    type: "Feature",
    geometry: { type: "LineString", coordinates: coordinates },
  });
}

function hideRouteGridLabel() {
  if (state.routeHoverPopup) state.routeHoverPopup.remove();
}

function showRouteGridLabel(event) {
  const feature = event.features && event.features[0];
  const featureId = feature ? Number(feature.properties.feature_id) : null;
  if (!state.routeComplete || !state.routeFeatureIdSet.has(featureId)) {
    hideRouteGridLabel();
    return;
  }

  const district = feature.properties.admin_district || feature.properties.district;
  const dong = feature.properties.admin_dong;
  const label = dong ? district + " " + dong : district;
  if (!state.routeHoverPopup) {
    state.routeHoverPopup = new maplibregl.Popup({
      closeButton: false,
      closeOnClick: false,
      offset: 12,
    });
  }
  state.routeHoverPopup
    .setLngLat(event.lngLat)
    .setText(label + " · 격자 중심점 기준")
    .addTo(state.map);
}

function updateRouteProgress(route, stepIndex) {
  const step = route[stepIndex];
  setPanel(
    "\uacbd\ub85c \ud0d0\uc0c9 " + stepIndex + " / " + (route.length - 1),
    step.address_label + " \u00b7 Score " + step.score
  );
  setSummary([
    ["\ud604\uc7ac \uc704\uce58", step.district],
    ["\ud604\uc7ac \uaca9\uc790", step.grid_id],
    ["\ub204\uc801 \uac70\ub9ac", (stepIndex * 250).toLocaleString() + "m"],
    ["\ud604\uc7ac Score", String(step.score)],
  ]);
}

function animateRunnerRoute(route, token) {
  const segmentDuration = 700;
  const finalStepIndex = route.length - 1;
  const routeCoordinates = route.map(function(step) {
    return [step.longitude, step.latitude];
  });
  let startedAt = null;
  let lastVisitedIndex = 0;
  let lastDisplayedIndex = 0;

  return new Promise(function(resolve) {
    function frame() {
      if (token !== state.animationToken || !state.runnerMarker) {
        resolve(false);
        return;
      }

      try {
        const now = performance.now();
        if (startedAt === null) startedAt = now;
        const elapsed = Math.max(0, now - startedAt);
        const routeProgress = Math.min(elapsed / segmentDuration, finalStepIndex);
        const segmentIndex = Math.max(0, Math.min(Math.floor(routeProgress), finalStepIndex - 1));
        const segmentProgress = routeProgress - segmentIndex;
        const from = routeCoordinates[segmentIndex];
        const to = routeCoordinates[segmentIndex + 1];
        const position = [
          from[0] + (to[0] - from[0]) * segmentProgress,
          from[1] + (to[1] - from[1]) * segmentProgress,
        ];

        state.runnerMarker.setLngLat(position);

        const completedIndex = Math.min(Math.floor(routeProgress), finalStepIndex);
        while (lastVisitedIndex < completedIndex) {
          lastVisitedIndex += 1;
          state.map.setFeatureState(
            { source: "grids", id: route[lastVisitedIndex].feature_id },
            { visited: true }
          );
        }
        if (completedIndex > lastDisplayedIndex) {
          lastDisplayedIndex = completedIndex;
          updateRouteProgress(route, completedIndex);
        }

        if (routeProgress < finalStepIndex) {
          window.requestAnimationFrame(frame);
        } else {
          resolve(true);
        }
      } catch (error) {
        console.error("Runner animation failed:", error);
        resolve(false);
      }
    }
    window.requestAnimationFrame(frame);
  });
}

async function animateRoute(payload) {
  clearRoute();
  const token = state.animationToken;
  const route = payload.route;
  const runnerElement = document.createElement("div");
  runnerElement.className = "runner-marker";
  runnerElement.textContent = "\ud83c\udfc3";
  state.runnerMarker = new maplibregl.Marker({ element: runnerElement, anchor: "center" })
    .setLngLat([route[0].longitude, route[0].latitude])
    .addTo(state.map);

  state.routeFeatureIds = route.map(function(step) { return step.feature_id; });
  state.routeFeatureIdSet = new Set(state.routeFeatureIds);
  state.map.setFeatureState({ source: "grids", id: route[0].feature_id }, { visited: true });
  setRouteLine(route.map(function(step) {
    return [step.longitude, step.latitude];
  }));
  updateRouteProgress(route, 0);

  const completed = route.length === 1 || await animateRunnerRoute(route, token);
  if (!completed || token !== state.animationToken) return;

  state.routeComplete = true;
  setPanel("\uacbd\ub85c \ud0d0\uc0c9 \uc644\ub8cc", "\ubc29\ubb38\ud55c \uaca9\uc790\uc5d0 \ub9c8\uc6b0\uc2a4\ub97c \uc62c\ub9ac\uba74 \uc790\uce58\uad6c\uc640 \ud589\uc815\ub3d9\uc744 \ud655\uc778\ud560 \uc218 \uc788\uc2b5\ub2c8\ub2e4.");
  setSummary([
    ["\ucd9c\ubc1c \uaca9\uc790", route[0].grid_id],
    ["\ubc29\ubb38 \uaca9\uc790", String(route.length) + "\uac1c"],
    ["\uc608\uc0c1 \uac70\ub9ac", payload.estimated_distance_m.toLocaleString() + "m"],
    ["\ud3c9\uade0 Score", String(payload.mean_score)],
  ]);
  if (payload.termination_reason) {
    routeStop.textContent = payload.termination_reason;
    routeStop.hidden = false;
  }
}

async function findRoute() {
  const missing = missingConditionLabels();
  if (missing.length) {
    showValidationModal(missing);
    setPanel("경로 탐색 전 조건을 확인해주세요", "선택하지 않은 항목을 화면 중앙에서 확인할 수 있습니다.");
    return;
  }
  hideValidationModal();
  clearRoute();
  state.isRouting = true;
  routeButton.disabled = true;
  routeButton.textContent = "탐색 중";
  setPanel("최적 격자 경로를 계산하고 있습니다", "선택한 날짜·시간과 러닝 유형의 가중 점수를 비교합니다.");
  resetSummary();

  const payload = {
    date: dateInput.value,
    hour: Number(hourInput.value),
    distance_km: Number(selectedValue("distance")),
    start_mode: selectedValue("start-mode"),
    run_type: selectedValue("run-type") || null,
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
    state.isRouting = false;
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
document.querySelectorAll('input[name="run-type"]').forEach(function(element) {
  element.addEventListener("change", function() {
    syncLongRunDistance();
    refreshScores();
    updateRouteButton();
  });
});
routeButton.addEventListener("click", findRoute);
validationClose.addEventListener("click", hideValidationModal);
validationModal.addEventListener("click", function(event) {
  if (event.target === validationModal) hideValidationModal();
});
document.addEventListener("keydown", function(event) {
  if (event.key === "Escape") hideValidationModal();
});

syncLongRunDistance();
initializeMap();
