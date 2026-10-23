# Changelog

## 1.6.0
- Set the entire GUI document language and date/time formatting to English.
- Added optional OpenCV YuNet + SFace local biometric matching, loaded only when verified model files are present.
- Added a persisted GUI switch and cosine-similarity threshold slider (`0.30–0.70`, default `0.40`).
- Added opt-in cooperative enrollment with up to five feature samples per profile.
- Added thresholded, multi-frame automatic re-lock to opted-in profiles after a geometric track is lost.
- Added a per-profile supervised expansion option; strong matches may create pending embedding candidates which must be approved manually before use.
- Added candidate review controls, sample counts, biometric status, explicit consent checks, and opt-out cleanup.
- Added a PowerShell model downloader with upstream-verified SHA-256 checks. Model weights are not bundled in the ZIP.
- Added a profile-disambiguation margin: near-tied profiles do not trigger automatic re-lock.
- Cooperative re-enrollment replaces old templates only after a successful new scan; failed scans preserve the current templates.
- Disabling biometric opt-in deletes that profile’s stored biometric templates and candidate embeddings.
- The start script now records its PID so the stop script can terminate the correct backend process.
- Kept unknown faces gray and retained manual-only classifications such as `ADMIN` and `THREAT`.
