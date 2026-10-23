# MACHINE CAMERA MODE v1.6.0

A local Windows prototype inspired by the camera-identification interface in *Person of Interest*. It includes a cinematic Live Feed, Snapshot dossiers, an asset registry, optional local facial matching, a cosine-threshold slider, cooperative enrollment, and a supervised template-expansion queue.

## Key features

- Browser GUI, entirely in English, hosted on `127.0.0.1:8765`.
- Webcam or local video-file input, processed on the computer.
- YuNet face detection and SFace feature matching when the optional ONNX models are installed; Haar Cascade remains the fallback detector.
- Automatic re-lock to an **opted-in, cooperatively enrolled profile** after a track ID expires. A candidate must match for multiple fresh feature frames before being linked.
- A GUI slider for cosine similarity threshold (`0.30` to `0.70`; higher is stricter). Similarity scores are not probabilities, and the right threshold depends on the camera and use conditions.
- Per-profile biometric matching opt-in. No profile can be automatically matched unless the participant has explicitly opted in and the profile has active enrollment templates. Turning opt-in off deletes that profile’s biometric templates and pending candidates.
- Cooperative enrollment captures up to five local feature samples from the selected, manually linked face.
- Optional supervised expansion: high-similarity samples may be stored as pending candidates only when separately enabled for that profile. They do not affect matching until an operator approves them in the registry.
- Unknown or unmatched faces use the gray reticle. The system does not assign `THREAT`, `ADMIN`, or other role tags based on facial appearance.
- Local SQLite database, audit log, Snapshot history and local video archive.
- Optional biometric model files are downloaded from the official OpenCV Zoo repository by a script that verifies SHA-256. The ONNX weight files are **not bundled in this ZIP**.

## Requirements

- Windows 10/11, 64-bit.
- Python 3.11 or newer.
- Internet access for the first Python dependency installation.
- Optional biometric matching: run the model downloader below once. This fetches the YuNet detector and SFace recognizer (about 39 MB combined) from OpenCV Zoo/Hugging Face. After downloading, model inference runs locally.

## Install

Extract the ZIP to a writable path such as `C:\AI\MACHINE_CAMERA_MODE`. Open PowerShell in the project folder and run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install.ps1
```

To enable optional biometric matching, download and verify the models:

```powershell
.\scripts\download-biometric-models.ps1
```

Restart the backend after the download so the application loads both models:

```powershell
.\scripts\start-browser.ps1
```

Open `http://127.0.0.1:8765`. Stop the application from another PowerShell window with:

```powershell
.\scripts\stop.ps1
```

The application deliberately binds to loopback only. Do not expose it on a LAN or to the Internet without a separate authentication and security design.

## First-time cooperative enrollment

1. Run the downloader and restart the application.
2. In the left panel, enable **BIOMETRIC MATCHING** and set the **COSINE SIMILARITY THRESHOLD**. The default is `0.40`; higher values are stricter. Test with known consenting participants and representative lighting before choosing an operating point.
3. Start the camera or select a local video for development tests.
4. Select the visible face and choose **REGISTER NEW ASSET**. Assign a display alias and operator-controlled category.
5. Enable **OPTIONAL BIOMETRIC MATCHING** only after the participant explicitly opts in. If enabling supervised expansion, select its separate checkbox; candidate samples will remain pending for review.
6. Save the profile. It is manually linked to the selected track.
7. Press **ENROLL COOPERATIVE SAMPLES**. Ask the participant to look toward the camera and gently turn left and right while up to five samples are captured over several seconds.
8. Allow the track to disappear and reappear. The system can re-lock it after the same enrolled profile crosses the similarity threshold on multiple fresh feature frames. The card will display `BIOMETRIC RELOCK` and the cosine score.

The biometric enrollment button requires a currently visible face, a manually linked asset, explicit per-profile opt-in, the model files, and global biometric matching enabled. Re-enrollment replaces the active template set only after a new scan yields at least three valid samples; a failed scan preserves the previous set.

## Supervised template expansion

For profiles whose **supervised expansion** setting is enabled, a strong, repeated match may put a new embedding into **BIOMETRIC TEMPLATE EXPANSION REVIEW**. Pending embeddings are not used for matching. Review the asset and approve or reject each sample. Do not enable this feature unless the participant's opt-in covers storing additional derived samples. Automatic candidate generation is rate-limited per track/profile.

## Data created

- `data/machine.sqlite3`: asset metadata, settings, template embeddings, candidates, Snapshot index, audit log, and video source index.
- `data/profiles/`: locally stored reference crops.
- `data/snapshots/`: full frames and face crops.
- `data/videos/`: copied local video sources.
- `logs/machine.log`: diagnostics.
- `models/`: optional YuNet/SFace model files and upstream license notices after download.

The SQLite database, reference crops and embeddings are not encrypted in this release. Protect the Windows account and project folder; keep the folder out of unintended sync/backup services.

## Understanding the matching score

The score is a cosine similarity, not a percentage or probability. OpenCV's SFace example uses `0.363` as a reference threshold, but this is not a universal setting for every camera or environment. This application starts at `0.40` and exposes a slider from `0.30` to `0.70`. Lower values increase the chance of false matches; higher values increase the chance of missed matches. The matcher also rejects close ties between profiles. A matched profile is still not proof of identity. Use manual verification for consequential decisions.

## Safety and privacy design

- Biometric matching is **off by default**.
- Unknown and non-enrolled faces remain unclassified with a gray reticle.
- Profiles require explicit opt-in before their embeddings can be used in matching.
- `ADMIN`, `THREAT`, `VIOLENCE INDICATOR`, and other labels are operator-supplied profile metadata; the face model never infers them.
- Cooperative enrollment is the initial enrollment method.
- Expansion is a supervised candidate queue, never silent automatic template activation.
- Images and embeddings stay local in this prototype; no cloud API is called.
- The system is a prototype, not an access-control or law-enforcement identity system. False matches and false rejections can occur.

See `docs/BIOMETRICS_AND_RELOCK.md`, `docs/registration_protocol.md`, and `models/MODEL_SOURCES.md`.
