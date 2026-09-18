# Jev service handoff

Read README.md and docs/INTEGRATION.md first. To use this service from another project,
consume http://127.0.0.1:8776/openapi.json and the integration guide; no Jev dependency is
needed in the calling app. Call from a backend, not cross-origin browser JavaScript.

Preserve .env, data/, and the dedicated Chrome profile. Never print credentials.
Do not edit vendor/jev-ultrafast for service features; wrapper code is in jev_service/.
Run offline tests with .venv/bin/python -m pytest. Live browser examples spend model credits.
Only one API process is supported. Restart with ./launchd/control.sh restart after changes.
Do not equate Jev DONE with independently verified success. Never replay interrupted jobs.
