# Changes Made

---

## `sensor_service.py`

**Removed:**
- `data_blob = "X" * 5_000_000` — 5 MB string held in RAM permanently at startup
- `for _ in range(2000000): pass` — CPU-burning empty loop on every `/metrics` scrape
- `temp_data = data_blob * random.randint(1, 3)` — allocated 5–15 MB per scrape
- `/sensor` endpoint returning `data_blob` 20% of the time (sending 5 MB JSON to a metrics scraper)

**Added:**
- `Response` and `CONTENT_TYPE_LATEST` imports — `/metrics` now returns proper content-type header
- `CPU_SPIKE_DURATION` histogram metric (`sensor_cpu_spike_duration_seconds`) with buckets at 1ms, 5ms, 10ms, 50ms, 100ms, 500ms
- `spike_start` timing to measure and observe CPU spike duration
- `/sensor` returns `{"status": "ok", "value": <random float>}` consistently

---

## `Dockerfile`

| | Before | After |
|---|---|---|
| Base image | `python:3.10` | `python:3.11-slim` |
| pip install | `flask prometheus_client` | `flask prometheus_client gunicorn` (with `--no-cache-dir`) |
| WORKDIR | none | `/app` |
| COPY destination | `/sensor_service.py` | `.` |
| CMD | `python sensor_service.py` | `gunicorn --workers=1 --threads=2 --bind=0.0.0.0:8000 sensor_service:app` |

---

## `docker-compose.yml`

**Removed:**
- `prometheus` service (`prom/prometheus:latest`, port 9090)
- `grafana` service (`grafana/grafana:latest`, port 3000)

**Added:**
- `mem_limit: 80m` and `cpus: "0.5"` on `sensor-service`
- `victoriametrics` service (`victoriametrics/victoria-metrics:v1.101.0`, port 8428)
  - Mounts `victoriametrics.yml` as scrape config
  - Mounts `vm_data` volume for persistent storage
  - Flags: `-retentionPeriod=7d`, `-storage.minFreeDiskSpaceBytes=100MB`
  - `mem_limit: 80m`, `cpus: "0.5"`
- `volumes: vm_data:` block

---

## `prometheus.yml` → `victoriametrics.yml`

| | Before | After |
|---|---|---|
| File name | `prometheus.yml` | `victoriametrics.yml` |
| `scrape_interval` | `5s` | `30s` |
| `evaluation_interval` | `5s` | removed |
| `scrape_timeout` | not set | `10s` |
