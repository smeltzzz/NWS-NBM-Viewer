"""Background jobs for unattended 24/7 operation.

``poller``  — NOAA active-cycle discovery → ``/runs/latest`` pointer → warm-up
``lock``    — single-owner advisory lock so N uvicorn workers run one poller
"""
