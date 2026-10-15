# Calculator API requirements

REQ-HEALTH: GET /health must return HTTP 200 and JSON {"status":"ok"}.
REQ-ADD: GET /api/add with integer query parameters a and b must return HTTP 200
and the mathematical sum in JSON field result. For a=2 and b=3, result must be 5.
For a=-2 and b=3, result must be 1. For a=0 and b=0, result must be 0.
REQ-INVALID: GET /api/add with a missing or non-integer parameter must return HTTP 422.
SLO-ADD: Each GET /api/add latency smoke sample must complete within 1000 milliseconds
on the local reference environment, with 3 sequential samples and no concurrent load.

The API is read-only and does not require authentication. No fixture setup or cleanup is required.
