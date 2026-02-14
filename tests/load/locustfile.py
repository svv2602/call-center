"""Load test configuration for Call Center AI.

Run: locust -f tests/load/locustfile.py --host=http://localhost:8080

Profiles:
  - Normal: 20 concurrent calls, 30 min
  - Peak: 50 concurrent calls, 15 min
  - Stress: 100+ concurrent calls (graceful degradation)

NFR targets:
  - p95 < 2 sec (normal), < 3 sec (peak)
  - 0% loss (normal), < 5% errors (peak)
"""

# Load tests will use SIPp for actual SIP calls.
# This file provides HTTP health check monitoring during load tests.

# TODO: Implement when SIPp test harness is ready
# from locust import HttpUser, task, between
#
# class CallCenterUser(HttpUser):
#     wait_time = between(1, 3)
#
#     @task
#     def health_check(self):
#         self.client.get("/health")
#
#     @task
#     def metrics(self):
#         self.client.get("/metrics")
