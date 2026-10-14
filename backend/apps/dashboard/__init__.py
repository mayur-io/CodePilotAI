"""Staff operations dashboard (Task.md §10, Phase 7).

A read-only, server-rendered console for inspecting tasks, proposed changes,
verification outcomes, and token/cost usage. It lives beside the JSON API and
reuses Django's session auth; it ships no models and therefore no migrations.
"""
