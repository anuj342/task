# Step-by-Step Testing Guide
## DevOps Assignment — 10xConstruction

---

## Prerequisites

Before testing, ensure you have installed:
- Docker Desktop (or Docker Engine on Linux)
- Docker Compose v2
- curl or a browser
- (Optional) `hey` or `wrk` for load testing

Verify Docker is running:
```bash
docker --version
docker compose version
```

---

## Phase 1 — Build and Start the Stack

### Step 1.1 — Build the sensor service image

```bash
cd C:\task
docker compose build --no-cache sensor-service
```

Expected output:
```
=> [sensor-service] FROM docker.io/library/python:3.11-slim
=> [sensor-service] RUN pip install --no-cache-dir flask prometheus_client gunicorn
=> [sensor-service] COPY sensor_service.py .
=> exporting to image
```

### Step 1.2 — Start all services

```bash
docker compose up -d
```

Expected output:
```
[+] Running 3/3
 ✔ Network task_default             Created
 ✔ Container task-sensor-service-1  Started
 ✔ Container task-victoriametrics-1 Started
```

### Step 1.3 — Verify all containers are running

```bash
docker compose ps
```

Expected output:
```
NAME                       STATUS    PORTS
task-sensor-service-1      running   0.0.0.0:8000->8000/tcp
task-victoriametrics-1     running   0.0.0.0:8428->8428/tcp
```

---

## Phase 2 — Test the Sensor Service

### Step 2.1 — Test the /metrics endpoint

```bash
curl http://localhost:8000/metrics
```

Expected: Prometheus-format text output like:
```
# HELP sensor_requests_total Total sensor requests
# TYPE sensor_requests_total counter
sensor_requests_total 1.0
# HELP sensor_cpu_spike Simulated CPU spike state
...
# HELP sensor_cpu_spike_duration_seconds Duration of simulated CPU spike
...
```

### Step 2.2 — Test the /sensor endpoint

```bash
curl http://localhost:8000/sensor
```

Expected:
```json
{"status": "ok", "value": 47.832}
```

### Step 2.3 — Measure response time of /metrics

```bash
curl -o /dev/null -s -w "Time: %{time_total}s\n" http://localhost:8000/metrics
```

Expected: **< 0.1s** (was 1–3s before fixes)

Run it 5 times to confirm consistency:
```bash
for i in 1 2 3 4 5; do curl -o /dev/null -s -w "Request $i: %{time_total}s\n" http://localhost:8000/metrics; done
```

---

## Phase 3 — Test Memory Usage

### Step 3.1 — Check live memory usage per container

```bash
docker stats --no-stream
```

Expected output:
```
NAME                       MEM USAGE / LIMIT     MEM %
task-sensor-service-1      ~40MiB / 80MiB        ~50%
task-victoriametrics-1     ~50MiB / 80MiB        ~62%
```

**Pass criteria:** Total RSS < 300 MB combined.

### Step 3.2 — Verify memory limits are enforced

```bash
docker inspect task-sensor-service-1 | grep -i memory
docker inspect task-victoriametrics-1 | grep -i memory
```

Expected: `"Memory": 83886080` (80MB = 80 * 1024 * 1024)

### Step 3.3 — Watch memory over time (30 seconds)

```bash
docker stats --format "table {{.Name}}\t{{.MemUsage}}" task-sensor-service-1 task-victoriametrics-1
```

Watch for 30 seconds. Memory should stay flat — no growth pattern means no memory leak.

---

## Phase 4 — Test VictoriaMetrics Scraping

### Step 4.1 — Check VictoriaMetrics is scraping successfully

Open in browser: `http://localhost:8428/targets`

Or via curl:
```bash
curl http://localhost:8428/api/v1/targets
```

Expected: The `sensor` job shows `state: "up"`.

### Step 4.2 — Query a metric via PromQL

```bash
curl "http://localhost:8428/api/v1/query?query=sensor_requests_total"
```

Expected:
```json
{
  "status": "success",
  "data": {
    "resultType": "vector",
    "result": [{"metric": {"__name__": "sensor_requests_total", "job": "sensor"}, "value": [...]}]
  }
}
```

### Step 4.3 — Query the custom metric

```bash
curl "http://localhost:8428/api/v1/query?query=sensor_cpu_spike_duration_seconds_bucket"
```

Expected: Multiple bucket values returned.

### Step 4.4 — Open the VictoriaMetrics UI

Open in browser: `http://localhost:8428/vmui`

1. Type `sensor_requests_total` in the query box
2. Click **Execute**
3. Switch to **Graph** tab
4. Verify the metric is increasing over time

---

## Phase 5 — Test the Custom Metric (Task 3.4)

### Step 5.1 — Generate some scrape traffic

```bash
for i in $(seq 1 20); do curl -s http://localhost:8000/metrics > /dev/null; done
```

### Step 5.2 — Query the p99 spike duration

```bash
curl "http://localhost:8428/api/v1/query?query=histogram_quantile(0.99,rate(sensor_cpu_spike_duration_seconds_bucket[5m]))"
```

Expected: A small value (milliseconds range), confirming spikes are no longer taking seconds.

### Step 5.3 — Verify histogram buckets are populated

```bash
curl "http://localhost:8428/api/v1/query?query=sensor_cpu_spike_duration_seconds_count"
```

Expected: Count > 0.

---

## Phase 6 — Load / Stress Test

### Step 6.1 — Hammer the metrics endpoint (simulates Prometheus scraping)

```bash
for i in $(seq 1 50); do curl -s -o /dev/null -w "%{time_total}\n" http://localhost:8000/metrics; done
```

All 50 requests should complete in under 0.5s each. No timeouts.

### Step 6.2 — Check memory held steady under load

```bash
docker stats --no-stream
```

Memory should not have grown significantly vs Phase 3 baseline.

### Step 6.3 — Check for container restarts (OOM kills)

```bash
docker compose ps
```

All containers should show `running`, not `restarting`. Restart count should be 0:
```bash
docker inspect task-sensor-service-1 --format='{{.RestartCount}}'
```

Expected: `0`

---

## Phase 7 — Compare Before vs After (Optional)

To demonstrate the original bugs, temporarily revert to the broken service:

```bash
# Run original broken service on a different port
docker run -d -p 8001:8000 --name broken-sensor \
  -e PYTHONPATH=/app \
  python:3.10 bash -c "pip install flask prometheus_client && python -c \"
import time, random
from flask import Flask
from prometheus_client import Counter, generate_latest
app = Flask(__name__)
data_blob = 'X' * 5_000_000
@app.route('/metrics')
def metrics():
    for _ in range(2000000): pass
    temp = data_blob * random.randint(1,3)
    return generate_latest()
app.run(host='0.0.0.0', port=8000)
\""
```

Then compare:
```bash
# Broken service — expect 1–3 seconds
curl -o /dev/null -s -w "Broken: %{time_total}s\n" http://localhost:8001/metrics

# Fixed service — expect < 0.1 seconds
curl -o /dev/null -s -w "Fixed: %{time_total}s\n" http://localhost:8000/metrics
```

Clean up:
```bash
docker stop broken-sensor && docker rm broken-sensor
```

---

## Phase 8 — Final Validation Checklist

Run through each item and confirm pass/fail:

| Test | Command | Pass Criteria |
|---|---|---|
| Sensor /metrics responds | `curl http://localhost:8000/metrics` | HTTP 200, Prometheus text format |
| Sensor /sensor responds | `curl http://localhost:8000/sensor` | `{"status": "ok", ...}` |
| Response time < 0.5s | `curl -w "%{time_total}" http://localhost:8000/metrics` | < 0.5 |
| Total RAM < 300 MB | `docker stats --no-stream` | Sum of all containers < 300MB |
| Sensor RAM < 80 MB | `docker stats --no-stream` | sensor-service < 80MiB |
| VictoriaMetrics RAM < 80 MB | `docker stats --no-stream` | victoriametrics < 80MiB |
| VM scraping sensor | `curl http://localhost:8428/api/v1/targets` | state = "up" |
| PromQL query works | `curl "http://localhost:8428/api/v1/query?query=sensor_requests_total"` | status = "success" |
| Custom metric exists | `curl "http://localhost:8428/api/v1/query?query=sensor_cpu_spike_duration_seconds_count"` | value > 0 |
| No container restarts | `docker compose ps` | All "running", restarts = 0 |

---

## Cleanup

Stop and remove all containers and volumes:

```bash
docker compose down -v
```

Remove built images:
```bash
docker compose down --rmi local -v
```

---

## Troubleshooting

### Container exits immediately
```bash
docker compose logs sensor-service
```
Check for Python import errors or port conflicts.

### VictoriaMetrics shows sensor as "down"
```bash
docker compose logs victoriametrics
```
Verify the scrape config path is correct and `sensor-service:8000` is reachable within the Docker network.

### Port already in use
```bash
# Find what's using port 8000
netstat -ano | findstr :8000   # Windows
lsof -i :8000                  # Linux/Mac
```

### Memory limit hit (container OOM killed)
```bash
docker inspect task-sensor-service-1 | grep OOMKilled
```
If `true`, increase `mem_limit` in docker-compose.yml or investigate memory regression.
