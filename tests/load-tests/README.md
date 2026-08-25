# Chatbot Load & Performance Testing Suite

This directory contains the [k6](https://k6.io/) load testing framework designed to evaluate baseline latency, peak concurrency thresholds, and document processing limits for the module-ttt APIs.

---

## Directory Structure

```text
load-tests/
├── assets/
│   └── Ask-Microsoft-transparency-note.pdf  # Sample document asset
├── config/
│   └── thresholds.js                       # Target SLAs and pass/fail criteria
├── scenarios/
│   ├── document-query.js                   # Q&A / query endpoint scenario
│   └── document-upload.js                  # Document ingestion endpoint scenario
├── main.js                                 # Root test suite orchestrator
└── README.md

```

---

## Test Stages & Execution Timeline

The test suite runs three isolated phases to avoid metric corruption:

1. **Baseline Query (`0:00 - 2:00`)**: Evaluates system response time under a constant 2 Virtual Users (VUs) load to verify normal operation SLAs.
2. **Document Upload (`2:15 - 4:15`)**: Measures the overhead of single-file PDF ingestion in isolation.
3. **Peak Query Load (`4:30 - 10:30`)**: Ramps concurrent users from 0 to 10 VUs to identify system throughput limits and latency degradation points.

---

## Prerequisites

Install **k6** on your local machine:

* **macOS** (Homebrew):
```bash
brew install k6

```


* **Linux** (Debian/Ubuntu):
```bash
sudo gpg -k
sudo gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C513093E43968012100A86B925C479A59B6C17F3
echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" | sudo tee /etc/apt/sources.list.d/k6.list
sudo apt-get update
sudo apt-get install k6

```


* **Windows** (Chocolatey):
```powershell
choco install k6

```



---

## How to Run

### 1. Local Execution

Export your target API key and execute the main test script:

```bash
export API_KEY="your-actual-api-key-of-module-ttt-service"
export GRAPH_ID="your-graph'-id-to-query-from"

k6 run tests/load-tests/main.js

```

Alternatively, pass the `API_KEY` inline:

```bash
k6 run -e API_KEY="your-actual-api-key" -e GRAPH_ID="your-actual-graph-id-to-query-from" tests/load-tests/main.js

```

### 2. Export Summary Results to JSON

To dump the telemetry report for CI/CD ingestion or performance tracking:

```bash
k6 run -e API_KEY="your-actual-api-key" -e GRAPH_ID="your-actual-graph-id-to-query-from" --summary-export=tests/load-tests/report.json tests/load-tests/main.js

```

---

## SLA Thresholds

The test run will pass or fail based on the rules defined in `config/thresholds.js`:

| Scenario | Target Metric | SLA Threshold |
| --- | --- | --- |
| **Baseline Query** | Failure Rate | `0%` errors |
| **Baseline Query** | Response Latency | `p(95) < 20000ms` |
| **Document Upload** | Failure Rate | `< 5%` errors |
| **Document Upload** | Upload Latency | `p(95) < 20000ms` |
| **Peak Query** | Failure Rate | `< 5%` errors |
| **Peak Query** | Response Latency | `p(95) < 30000ms` |