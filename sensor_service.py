import time
import random
from flask import Flask, Response, jsonify
from prometheus_client import Counter, Gauge, Histogram, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# Core metrics
REQUEST_COUNT = Counter("sensor_requests_total", "Total sensor requests")
CPU_SPIKE = Gauge("sensor_cpu_spike", "Simulated CPU spike state")
PROCESS_LATENCY = Histogram("sensor_processing_latency_seconds", "Processing time")

# Custom metric
CPU_SPIKE_DURATION = Histogram(
    "sensor_cpu_spike_duration_seconds",
    "Duration of simulated CPU spike",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5]
)

@app.route("/metrics")
def metrics():
    start = time.time()

    # Simulate CPU spike state and measure its duration
    spike_start = time.time()
    CPU_SPIKE.set(random.randint(0, 1))
    CPU_SPIKE_DURATION.observe(time.time() - spike_start)

    PROCESS_LATENCY.observe(time.time() - start)
    REQUEST_COUNT.inc()
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)

@app.route("/sensor")
def sensor():
    return jsonify({"status": "ok", "value": round(random.uniform(0, 100), 3)})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
