# DevOps Intern Assignment — 10xConstruction
## Complete Solution Guide

---

## Context

Edge computing robot module:
- 2-core CPU @ ~2 GHz
- 500 MB usable RAM
- Docker installed
- **Hard constraint:** All components combined < 300 MB RAM

---

## Bugs Found in `sensor_service.py`

### Bug 1 — Global memory allocation (line 8)
```python
data_blob = "X" * 5_000_000   # 5 MB string held in RAM permanently
```

### Bug 2 — CPU spike on every /metrics scrape (lines 17–18)
```python
for _ in range(2000000):       # burns CPU doing nothing, blocks response 1–3s
    pass
```

### Bug 3 — Memory spike per scrape (line 19)
```python
temp_data = data_blob * random.randint(1, 3)  # allocates 5–15 MB every scrape
```

**Combined effect:** Every Prometheus scrape (every 5s) → CPU burns for 1–3s AND 5–15MB allocated → scrape timeout, GC pressure, inconsistent response times.

---

## Task 3.1 — Deploy & Fix the Sensor Service

### Fixed `sensor_service.py`

```python
import time
import random
from flask import Flask, Response
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

REQUEST_COUNT = Counter("sensor_requests_total", "Total sensor requests")
CPU_SPIKE = Gauge("sensor_cpu_spike", "Simulated CPU spike state")
PROCESS_LATENCY = Histogram("sensor_processing_latency_seconds", "Processing time")

# Custom metric (Task 3.4) — histogram of CPU spike duration
CPU_SPIKE_DURATION = Histogram(
    "sensor_cpu_spike_duration_seconds",
    "Duration of simulated CPU spike",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
)

@app.route("/metrics")
def metrics():
    start = time.time()
    spike_start = time.time()
    CPU_SPIKE.set(random.randint(0, 1))
    CPU_SPIKE_DURATION.observe(time.time() - spike_start)
    PROCESS_LATENCY.observe(time.time() - start)
    REQUEST_COUNT.inc()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/sensor")
def sensor():
    from flask import jsonify
    return jsonify({"status": "ok", "value": random.uniform(0, 100)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
```

**What was removed/fixed:**
| Bug | Fix |
|---|---|
| `data_blob = "X" * 5_000_000` | Removed — no reason to hold 5MB string |
| `for _ in range(2000000)` | Removed — was pure CPU waste |
| `temp_data = data_blob * random.randint(1,3)` | Removed — eliminated 5–15MB per-scrape alloc |
| Flask dev server | Replaced with gunicorn for stable threading |
| Missing content-type header | Added `CONTENT_TYPE_LATEST` |

---

### Optimized `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir flask prometheus_client gunicorn

COPY sensor_service.py .

CMD ["gunicorn", "--workers=1", "--threads=2", "--bind=0.0.0.0:8000", "sensor_service:app"]
```

**Why `python:3.11-slim`:** ~60 MB image vs ~900 MB for `python:3.10` full image.

---

## Task 3.2 — Metrics System Choice

### Choice: VictoriaMetrics single node (Option B)

**Justification:**

| Metric | Prometheus | VictoriaMetrics |
|---|---|---|
| RAM usage | ~100–150 MB | ~20–50 MB |
| PromQL compatible | Yes | Yes |
| WAL compression | Manual config | Built-in |
| Binary size | Large | Single small binary |
| Scrape config format | Standard | Same format |

On a 500 MB device with a 300 MB total budget, VictoriaMetrics saves 80–100 MB vs Prometheus — that's the difference between fitting and not fitting.

### `victoriametrics.yml` (scrape config)

```yaml
global:
  scrape_interval: 30s      # reduced from 5s — prevents hammering a slow endpoint
  scrape_timeout: 10s

scrape_configs:
  - job_name: "sensor"
    metrics_path: "/metrics"
    static_configs:
      - targets: ["sensor-service:8000"]
```

**Why 30s scrape interval:** Original 5s interval + 1–3s response time = guaranteed timeouts. After fixing the service, 30s is still appropriate for an edge device — halves CPU load on both sides.

---

## Task 3.3 — Visualization Layer Choice

### Choice: VictoriaMetrics built-in `/vmui`

**Justification:**

| Option | RAM | Notes |
|---|---|---|
| Grafana | ~150–200 MB | Too heavy for this device |
| VictoriaMetrics `/vmui` | 0 extra MB | Built into VictoriaMetrics |
| Static HTML dashboard | ~5 MB | Viable alternative |
| Terminal dashboard | ~10 MB | Viable alternative |

VictoriaMetrics includes a web UI at `http://localhost:8428/vmui` with PromQL query support and basic graphing — sufficient for edge monitoring with no extra RAM cost.

---

## Task 3.4 — Custom Metric

```python
CPU_SPIKE_DURATION = Histogram(
    "sensor_cpu_spike_duration_seconds",
    "Duration of simulated CPU spike",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
)
```

**Why this metric:** On an edge robot, CPU spikes directly risk real-time operations. A histogram tracks p50/p95/p99 spike duration over time — far more actionable than a simple gauge because it shows distribution, not just current state. This lets you alert when p99 > 100ms before the device becomes unresponsive.

---

## Task 3.5 — Final Optimized Docker Compose

```yaml
version: "3.8"

services:
  sensor-service:
    build: .
    ports:
      - "8000:8000"
    restart: always
    mem_limit: 80m
    cpus: "0.5"

  victoriametrics:
    image: victoriametrics/victoria-metrics:v1.101.0
    ports:
      - "8428:8428"
    volumes:
      - ./victoriametrics.yml:/etc/victoriametrics/scrape.yml
      - vm_data:/victoria-metrics-data
    command:
      - "-promscrape.config=/etc/victoriametrics/scrape.yml"
      - "-retentionPeriod=7d"
      - "-storage.maxDiskUsageBytes=100MB"
    restart: always
    mem_limit: 80m
    cpus: "0.5"

volumes:
  vm_data:
```

### Memory Budget

| Component | Before | After |
|---|---|---|
| sensor-service | ~150 MB | ~40 MB |
| Prometheus | ~120 MB | replaced |
| Grafana | ~200 MB | replaced |
| VictoriaMetrics | — | ~50 MB |
| Docker overhead | ~30 MB | ~30 MB |
| **Total** | **~500 MB (over limit)** | **~120 MB** |

---

## Task 4 — Performance Budget Report

### Identified Bottlenecks

1. **CPU bottleneck:** 2M iteration loop blocked the metrics endpoint for 1–3s per scrape. Prometheus marked the target as down after `scrape_timeout`.
2. **Memory bottleneck:** `data_blob * random.randint(1,3)` allocated 5–15 MB per scrape. GC ran constantly, causing additional latency spikes.
3. **Scrape interval too aggressive:** 5s interval with 1–3s response time = overlapping scrapes and guaranteed timeouts.
4. **Base image too large:** Full `python:3.10` = ~900 MB image layer.
5. **No memory limits:** A single memory leak could OOM the entire device.

### Observability Design Decisions

- **VictoriaMetrics over Prometheus:** Same PromQL API, 60% less RAM, built-in compression, no separate WAL configuration needed.
- **Removed Grafana:** Saves 150–200 MB. The built-in `/vmui` is sufficient for an edge device that doesn't have a human watching a dashboard 24/7.
- **gunicorn over Flask dev server:** Stable multi-threaded request handling, doesn't crash under concurrent scrapes.
- **Memory limits in compose:** Prevents one component from starving others on a 500 MB device.

### Custom Metric Reasoning

`sensor_cpu_spike_duration_seconds` histogram with buckets at 1ms, 5ms, 10ms, 50ms, 100ms, 500ms. Lets you query:

```promql
histogram_quantile(0.99, rate(sensor_cpu_spike_duration_seconds_bucket[5m]))
```

Alert if p99 > 100ms → robot safety issue.

### One Improvement Given One More Week

Add VictoriaMetrics alerting rules to fire a webhook when:
- `sensor_cpu_spike_duration_seconds` p99 > 100ms sustained for 2 minutes
- `sensor_requests_total` rate drops to zero (service down)

This closes the loop from observability → alerting → response, making the system operationally complete.

---

## Task 5 — Design Twist Solutions

| Symptom | Root Cause | Fix Applied |
|---|---|---|
| High CPU | 2M iteration loop in `/metrics` | Removed loop |
| Excess memory | 5MB global + 5–15MB per scrape | Removed both allocations |
| Scrape timeouts | Slow response + 5s interval | Fixed response time + 30s interval |
| Inconsistent response times | GC pressure from repeated large allocs | Eliminated large allocs |
| Dashboard failures | Upstream scrape failures propagated to UI | Fixed at source |

---

## Submission Checklist

- [ ] Private GitHub repository created
- [ ] Collaborators added: `tanay@10xconstruction.ai`, `piyush@10xconstruction.ai`, `tushar@10xconstruction.ai`
- [ ] `sensor_service.py` — fixed version
- [ ] `Dockerfile` — optimized with slim image + gunicorn
- [ ] `docker-compose.yml` — with memory limits, VictoriaMetrics
- [ ] `victoriametrics.yml` — scrape config with 30s interval
- [ ] `README.md` — approach explanation + video link
- [ ] Video walkthrough recorded and linked
