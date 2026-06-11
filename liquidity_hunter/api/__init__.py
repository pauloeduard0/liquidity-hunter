"""API layer: HTTP interface exposing `app` output as JSON.

A FastAPI application that serves `DashboardData` snapshots assembled by
`app.load_dashboard_data` to external clients (e.g. a future web
frontend). Depends on `app` and `core`; no other layer depends on `api`.
"""
