# Build and validation notes — 1.6.0

- `app.py` and all Python test files pass `py_compile`.
- `static/js/app.js` passes `node --check`.
- 19 unit tests were run: 15 passed; 4 Flask API tests were skipped because Flask is not installed in this build container. Those API tests are included and run after `scripts/install.ps1` installs `requirements.txt`.
- Unit tests cover video-file source startup, dashed reticle drawing, box-size smoothing, short-gap track continuity, biometric threshold matching, tie rejection, disabled-engine behavior, three-frame relock, and upstream model checksum consistency.
- YuNet and SFace ONNX weights could not be downloaded into the build environment, so they are not bundled in this ZIP. `scripts/download-biometric-models.ps1` downloads them from the official OpenCV Zoo source and verifies SHA-256 before installing them.
- No physical webcam was available for end-to-end testing. Camera access, browser video playback, biometric-model loading, and real-world matching accuracy must be verified on the target Windows PC.
- Automatic biometric re-lock requires downloaded model files, global matching enabled, per-profile participant opt-in, and cooperative enrollment templates. Existing profiles with only a saved crop are not automatically enrolled.
