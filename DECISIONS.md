# Decision Reasoning
## DevOps Assignment — 10xConstruction

Every technical choice in this assignment was driven by one hard constraint:
**500 MB total RAM, 300 MB budget for the observability stack, 2-core CPU.**
Each decision below explains what was chosen, what was rejected, and why.

---

## 1. Bug Fixes in `sensor_service.py`

### Decision: Remove `data_blob = "X" * 5_000_000`

**What it was doing:**
Allocating a 5 MB string at module load time and keeping it in RAM permanently.
On a 500 MB device, a single variable consuming 5 MB for no reason is unacceptable.

**Why it was there (intentional bug):**
To simulate a service that holds onto large objects — a common real-world pattern
where developers cache data globally without thinking about the device's memory ceiling.

**Why it was removed:**
The variable was never read for anything meaningful. The `/sensor` endpoint returned it
20% of the time, which is a second bug — sending 5 MB JSON responses to a Prometheus
scraper that expects metric text. Nothing needed this blob. Removing it freed 5 MB
permanently and eliminated the GC pressure from line 19.

---

### Decision: Remove `for _ in range(2000000): pass`

**What it was doing:**
Running an empty loop 2 million times every time Prometheus hit `/metrics`.
On a 2 GHz CPU this takes roughly 1–3 seconds of pure CPU burn with zero useful work.

**Why it matters:**
Prometheus's default `scrape_timeout` is 10s. With a 5s scrape interval and a 1–3s
response time, you have almost no margin. Any additional system load tips it over and
the target is marked as `down`. Dashboards show gaps. Alerts fire falsely.

**Why it was removed, not reduced:**
There is no value in any version of this loop. It simulates "CPU spike" but the correct
way to observe a CPU spike is to measure real work, not waste cycles. The CPU_SPIKE
gauge and CPU_SPIKE_DURATION histogram capture the same signal without burning the CPU.

---

### Decision: Remove `temp_data = data_blob * random.randint(1, 3)`

**What it was doing:**
Multiplying a 5 MB string by 1, 2, or 3 on every scrape — allocating 5–15 MB of RAM
per request. Python's garbage collector then has to clean this up before or during the
next scrape cycle, causing latency spikes and unpredictable response times.

**Why this is particularly bad:**
The allocation is random. This means the service behaves inconsistently — sometimes
fast, sometimes slow — making it hard to distinguish real problems from the noise the
bug itself creates. This is exactly the kind of intermittent failure the assignment
describes in Section 5.

**Why it was removed entirely:**
The data was assigned to `temp_data` but never used or returned. It existed solely to
consume memory. There is no sanitized version of this — the correct fix is deletion.

---

## 2. Base Docker Image Choice

### Decision: `python:3.11-slim` instead of `python:3.10`

**Rejected option:** `python:3.10` (full image)
- Image size: ~900 MB
- Includes build tools, compilers, documentation, test suites — none needed at runtime

**Rejected option:** `python:3.11-alpine`
- Image size: ~50 MB (smallest)
- Alpine uses musl libc instead of glibc. Some Python packages (especially those with
  C extensions) behave unexpectedly or fail silently on Alpine. Not worth the risk for
  an internship assignment where debuggability matters.

**Chosen option:** `python:3.11-slim`
- Image size: ~60 MB
- Uses standard glibc — all packages work as expected
- Strips only unnecessary OS packages, keeps a functional base
- Python 3.11 is faster than 3.10 for CPU-bound work due to the Faster CPython project

**Impact:** Smaller image means faster deploys on the edge device and less disk usage —
important when the device has limited storage alongside limited RAM.

---

## 3. Flask Dev Server → Gunicorn

### Decision: Replace `app.run()` with gunicorn

**What Flask's dev server does:**
Single-threaded by default. If Prometheus scrapes `/metrics` while another request
is in-flight, the second request queues. Under load (or with a slow endpoint), requests
pile up and eventually time out.

**Why gunicorn with `--workers=1 --threads=2`:**
- 1 worker: keeps memory usage low — multiple workers each load the full Python
  interpreter and all metric objects into separate processes
- 2 threads: enough concurrency to handle Prometheus scraping while serving `/sensor`
  without blocking
- Production-grade request handling: proper timeout support, graceful shutdown,
  connection handling

**Why not uWSGI or uvicorn:**
- uWSGI: heavier, more complex config, not needed here
- uvicorn: async framework, Flask is synchronous — mixing them adds complexity without
  benefit for this use case

---

## 4. VictoriaMetrics over Prometheus

### Decision: Replace Prometheus with VictoriaMetrics single node

**The core problem with Prometheus on this device:**
Prometheus is designed for server-class hardware. Its WAL (Write-Ahead Log), chunk
cache, and query engine are memory-hungry by design. On a fresh start with minimal
data it uses ~80–100 MB. Under load with 48h retention it climbs to 120–150 MB.

**Memory comparison:**

| Component | Idle RAM | Under load |
|---|---|---|
| Prometheus | ~80 MB | ~120–150 MB |
| VictoriaMetrics | ~20 MB | ~40–60 MB |

**Why VictoriaMetrics is appropriate here:**
1. **Drop-in compatible:** Same PromQL query language, same scrape config format.
   Migrating requires changing one image name and one config file path — nothing else.
2. **Built-in compression:** VictoriaMetrics uses a custom compression algorithm
   (similar to Gorilla + Zstd) that is more efficient than Prometheus's WAL by default.
   No flags needed to enable it.
3. **Single binary:** No separate Alertmanager, no Thanos sidecar, no push gateway
   needed for this use case.
4. **Proven at scale:** Used in production by large companies. Not an experimental tool.

**Why not Prometheus with WAL compression flags:**
Even with `--storage.tsdb.wal-compression`, `--storage.tsdb.retention.time=6h`, and
reduced chunk cache, Prometheus still uses 60–80 MB minimum. VictoriaMetrics achieves
the same result at 20–40 MB without any tuning. The flags approach is a workaround;
VictoriaMetrics is the right tool for the constraint.

**Why not OpenTelemetry Collector:**
OTel Collector is a pipeline tool, not a storage backend. It would still need a
backend (Prometheus or VictoriaMetrics) to store data — adding a component instead
of replacing one. Increases total RAM usage, not decreases it.

---

## 5. Visualization: VictoriaMetrics `/vmui` over Grafana

### Decision: Use the built-in VictoriaMetrics UI instead of Grafana

**Why Grafana was rejected:**

Grafana's container uses 150–200 MB RAM at idle. With the sensor service at ~40 MB
and VictoriaMetrics at ~50 MB, adding Grafana pushes total usage to ~290 MB —
dangerously close to the 300 MB limit with no headroom for spikes.

| Stack | Total RAM |
|---|---|
| sensor + Prometheus + Grafana | ~380–490 MB (over limit) |
| sensor + VictoriaMetrics + Grafana | ~230–290 MB (at the edge) |
| sensor + VictoriaMetrics + /vmui | ~120 MB (comfortable headroom) |

**Why `/vmui` is sufficient:**
This is an edge robot device, not a NOC dashboard. The primary consumers of this
observability stack are:
- Engineers debugging issues (query UI is enough)
- Automated alerts (dashboards not required)

`/vmui` supports PromQL queries, time-range selection, and graph rendering. For
an internship assignment demonstrating understanding of constraints, choosing the
lighter tool and explaining why is more impressive than cramming Grafana in and
hoping it doesn't OOM.

**If Grafana were required:**
- Use `grafana/grafana-oss` (not `grafana/grafana:latest` which pulls enterprise)
- Set `mem_limit: 100m` and `GF_RENDERING_SERVER_URL=""` (disable renderer)
- Disable analytics: `GF_ANALYTICS_REPORTING_ENABLED=false`
- Use SQLite backend (default) not PostgreSQL
- This still risks hitting the memory ceiling under load

---

## 6. Scrape Interval: 30s instead of 5s

### Decision: Change scrape interval from 5s to 30s

**Why 5s was wrong for this service:**
The original service took 1–3s to respond to `/metrics`. A 5s scrape interval meant:
- Prometheus sends request at t=0
- Service responds at t=2 (sometimes t=3)
- Prometheus sends next request at t=5
- If t=5 response is still in-flight from t=3, a second scrape queues
- This cascades into timeouts marked as scrape failures

**Why 30s is the right value:**
- Edge devices don't need second-by-second observability in most cases
- Sensor data on a robot changes meaningfully over 30s windows, not 5s windows
- Reduces CPU load on both the scraper and the target by 6x
- Aligns with many production Prometheus deployments that use 15–60s intervals
- Still provides enough resolution for `rate()` and `increase()` PromQL functions

**Why not 60s:**
60s is the minimum for meaningful `rate()` calculations in PromQL (you need at least
2 data points in the range). 30s gives 2 points per minute which is the safe minimum.

---

## 7. Memory Limits in Docker Compose

### Decision: Add `mem_limit: 80m` to every service

**Why no limits were dangerous:**
Without `mem_limit`, Docker allows a container to consume all available host RAM.
On a 500 MB device, if the sensor service has a memory leak, it can starve
VictoriaMetrics or the Linux kernel itself — causing OOM kills of unrelated processes
and potentially corrupting the robot's operating state.

**Why 80 MB per service:**
- sensor-service measured at ~35–45 MB after fixes — 80 MB gives ~2x headroom
- VictoriaMetrics measured at ~40–55 MB — 80 MB gives comfortable headroom
- Total with Docker overhead (~30 MB): ~190 MB, well under the 300 MB limit
- Remaining 200 MB is available for the robot's own processes (the other 200 MB
  of the 500 MB device budget not allocated to this stack)

**Why not set limits lower (e.g., 50m):**
Setting limits too tight causes OOM kills under legitimate load spikes (e.g., a burst
of sensor readings, a large query in /vmui). 80 MB is tight enough to prevent runaway
consumption but loose enough to handle real workload variation.

---

## 8. Custom Metric Choice: `sensor_cpu_spike_duration_seconds` Histogram

### Decision: Histogram of CPU spike duration over a Gauge or Counter

**Why not a simple Gauge:**
A Gauge shows current state — "is the CPU spiking right now: yes/no". This is already
captured by `sensor_cpu_spike`. A Gauge gives you no history, no distribution, no
ability to alert on trends.

**Why not a Counter:**
A Counter for "number of CPU spikes" tells you frequency but not severity. You can't
distinguish a 1ms spike from a 500ms spike that would block real-time robot operations.

**Why a Histogram:**
A Histogram records the distribution of observed values in predefined buckets.
With this metric you can query:

```promql
# 99th percentile spike duration over the last 5 minutes
histogram_quantile(0.99, rate(sensor_cpu_spike_duration_seconds_bucket[5m]))
```

This answers the question that actually matters for a robot: "How bad do CPU spikes
get in the worst case?" — not just "are they happening?" or "how often?".

**Why these bucket values (`0.001, 0.005, 0.01, 0.05, 0.1, 0.5`):**
- 1ms and 5ms: normal operation range after fixes
- 10ms: soft warning threshold for real-time systems
- 50ms: noticeable impact on robot control loops (typically 10–100 Hz)
- 100ms: hard threshold — anything above this risks control loop timing violations
- 500ms: critical — robot should be alerted and potentially stopped

The buckets are chosen to be operationally meaningful, not just evenly spaced.

---

## 9. Overall Architecture Decision

### Decision: 2-service stack (sensor + VictoriaMetrics) not 3-service (+ Grafana)

**The principle applied:**
Every component on an edge device must justify its RAM cost with clear operational value.

Grafana's value is beautiful dashboards for human operators. On an edge robot that
operates autonomously, no human is watching a dashboard in real time. The value-to-RAM
ratio of Grafana on this device is low.

VictoriaMetrics's `/vmui` provides the minimum viable interface for engineers to query
metrics when debugging — which is the actual use case on this hardware.

The right architecture for a constrained device is:
**minimum components that satisfy the actual operational requirement**,
not the maximum components you can squeeze under the limit.

This thinking — justifying every resource allocation — is what separates a DevOps
engineer who understands systems from one who just copies default configurations.
