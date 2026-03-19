# Video Walkthrough Script
## DevOps Intern Assignment — 10xConstruction

---

### Intro

Hey, this is my walkthrough of the DevOps assignment. I'll cover the bugs I found, the stack choices I made, and why every decision came down to one hard constraint — 500 MB of RAM and a 300 MB budget for the entire observability stack.

---

### The Problem

We're deploying on an edge computing module inside a robot. 2-core CPU, 500 MB RAM, Docker installed. The default setup — Prometheus + Grafana + the sensor service — was already over 500 MB before we even fixed anything. So the entire assignment is really about working within a tight resource ceiling.

---

### Bug Fixes — `sensor_service.py`

There were three bugs, all in the `/metrics` endpoint.

**Bug 1** — a 5 MB string allocated at startup and held in RAM permanently. It was never used for anything meaningful, so I removed it entirely.

**Bug 2** — a `for _ in range(2_000_000): pass` loop that ran on every single Prometheus scrape. That's 1 to 3 seconds of pure CPU burn per request, with zero useful work. Removed completely.

**Bug 3** — that same 5 MB string being multiplied by a random number on every scrape, allocating 5 to 15 MB of RAM each time. Python's garbage collector had to clean it up before the next scrape, causing unpredictable latency spikes.

Combined, these three bugs meant every scrape spiked CPU, spiked memory, and often timed out. I removed all three and replaced the Flask dev server with gunicorn — one worker, two threads — which handles concurrent requests without the overhead of multiple Python processes.

I also switched the base image from `python:3.10` to `python:3.11-slim`. That drops the image from ~900 MB to ~60 MB, and slim uses standard glibc so all packages work as expected — unlike Alpine which can have compatibility issues.

---

### Metrics System — VictoriaMetrics over Prometheus

The assignment gave a choice between Prometheus and VictoriaMetrics. I chose VictoriaMetrics.

Prometheus idles at around 80 to 100 MB and climbs to 120 to 150 MB under load. VictoriaMetrics idles at 20 MB and peaks around 50 MB. That's the difference between fitting the budget and blowing past it.

The key thing is it's not a trade-off in functionality. VictoriaMetrics uses the exact same PromQL query language and the same scrape config format as Prometheus. Migrating was literally changing one image name and one config path.

I also changed the scrape interval from 5 seconds to 30 seconds. The original service took 1 to 3 seconds to respond, so a 5s interval meant scrapes were overlapping and timing out. 30 seconds is the right value for an edge device — still gives enough data points for `rate()` queries, and reduces load on both sides by 6x.

---

### Visualization — `/vmui` over Grafana

Grafana uses 150 to 200 MB at idle. After the sensor service and VictoriaMetrics, there's no room for it.

VictoriaMetrics ships with a built-in web UI at `/vmui` — zero extra RAM cost. It supports PromQL queries, time-range selection, and graphs. For an edge robot where no one is watching a live dashboard 24/7, that's more than enough. Engineers can open it when debugging. That's the actual use case.

---

### Custom Metric

I added a histogram called `sensor_cpu_spike_duration_seconds` with buckets at 1ms, 5ms, 10ms, 50ms, 100ms, and 500ms.

The reason I chose a histogram over a gauge or counter: a gauge tells you if a spike is happening right now, a counter tells you how often, but a histogram tells you how bad they get in the worst case. You can query the p99 spike duration and alert if it exceeds 100ms — which is the threshold where robot control loops start getting affected. That's a much more actionable signal.

---

### Docker Compose — Memory Limits

I added `mem_limit: 80m` to both services. Without limits, a memory leak in one container can OOM the entire device and kill unrelated robot processes. 80 MB gives each service roughly 2x headroom over measured usage, with total stack RAM at around 120 MB — well under the 300 MB ceiling.

Final budget:

| Component | RAM |
|---|---|
| sensor-service | ~40 MB |
| VictoriaMetrics | ~50 MB |
| Docker overhead | ~30 MB |
| **Total** | **~120 MB** |

Compare that to the original setup which was over 500 MB.

---

### Wrap Up

Every decision in this assignment came from the same question: does this component justify its RAM cost? Grafana doesn't — `/vmui` does the job. Prometheus doesn't — VictoriaMetrics is a drop-in replacement at a third of the memory. The bugs in the sensor service don't just make the service slow, they make observability itself unreliable, which defeats the point.

The principle is minimum components that satisfy the actual operational requirement — not the maximum you can squeeze under the limit.

That's the walkthrough. Thanks.
