# 사용자 맞춤형 격자 코스 추천

브라우저에서 날짜, 시간, 목표 거리, 출발 방식을 고르고 서울 250m 격자를 클릭하면 그리디 방식으로 코스를 보여주는 데모 웹앱입니다.

## 현재 데모 범위
- 격자 형상은 프로젝트의 `running_map_share_final` 폴더를 참고해 생성합니다.
- 해당 폴더의 기존 점수 컬럼은 사용하지 않습니다.
- `data/demo_final_scores.parquet`는 화면 작동을 확인하기 위한 결정론적 예시 점수입니다.
- 실제 최종 점수가 준비되면 같은 `feature_id` 또는 `grid_id`, `month`, `day_type`, `hour`, `final_score` 구조로 교체합니다.

## 최초 데이터 준비
프로젝트 루트에서 아래 명령을 한 번 실행합니다.

    py -3 running-course-app/scripts/prepare_demo_data.py

실행 후 `data/grid.geojson`, `data/demo_final_scores.parquet`, `data/demo_score_manifest.json`이 생성됩니다.

## 앱 실행
`running-course-app` 폴더로 이동한 뒤 실행합니다.

    cd running-course-app
    py -3 -m uvicorn app:app --reload --port 8000

브라우저에서 아래 주소를 엽니다.

    http://127.0.0.1:8000

## 사용 방법
1. 날짜와 시간을 고릅니다.
2. 3km 또는 5km를 고릅니다.
3. 현재 위치 격자 또는 1km 이내 최고 Score 출발을 고릅니다.
4. 지도 위 250m 격자를 클릭합니다.
5. 경로 탐색을 누릅니다.

## 주요 파일
- `app.py`: FastAPI API, 점수 조회, 시작 격자 선택, 그리디 경로 탐색
- `static/index.html`: 화면 구조
- `static/styles.css`: 화면 스타일
- `static/app.js`: 지도, 사용자 입력, 격자 색상, 러너 애니메이션
- `scripts/prepare_demo_data.py`: 참고 격자에서 웹용 격자와 예시 점수 생성
- `data/grid.geojson`: 웹 지도용 250m 격자
- `data/demo_final_scores.parquet`: 조건별 예시 Final Score

## 실제 점수 교체 시
실제 점수 데이터는 격자별로 연결되어야 합니다. 서버는 월, 평일/주말, 시간 조건에 맞는 8,736개 격자 점수를 읽습니다. 실제 데이터의 점수 테이블 구조가 다르면 `app.py`의 `get_condition_scores` 함수만 실제 파일 구조에 맞게 바꾸면 됩니다.
