# Biometrics, Thresholds, and Re-lock — v1.6.0

## What is different from v1.5?

Version 1.5 performed face-region detection and geometric tracking only. It did not compare a live face with a stored profile. The v1.6 optional engine uses OpenCV YuNet for face detection/landmarks and SFace for normalized face-feature vectors. The official model files are downloaded separately by the provided PowerShell script and SHA-256 verified.

## Re-lock sequence

1. YuNet detects a face and supplies landmark coordinates.
2. SFace aligns the face and produces a normalized embedding when biometric matching is enabled.
3. The embedding is compared with stored templates for profiles whose `biometric_opt_in` flag is enabled.
4. The highest cosine-similarity match must meet the GUI threshold on at least three fresh feature frames and exceed the second-best different profile by at least 0.05. Ambiguous matches are rejected rather than guessed.
5. Only then is a biometric profile linked to that current track. If a geometric track expires and the person is seen again, the matching stage can recreate the profile link on a new track ID.
6. Unknown faces, profiles without active templates, and faces below threshold remain gray/unclassified.

## Threshold meaning

The GUI exposes cosine similarity from `0.30` to `0.70`. Higher is stricter. The initial setting is `0.40`; OpenCV's public SFace demo lists `0.363` as an example reference threshold. Do not treat this as a universal operational threshold. A score of `0.50` does not mean “50% confidence.” Lighting, pose, image quality, demographic differences, and multiple candidate profiles can affect error rates. Test on a consented validation set before relying on the setting.

## Enrollment recommendation

Use a **cooperative initial scan** first. It creates a small, deliberate baseline with several current samples from the participant. For subsequent expansion, use the **supervised candidate queue**: opt-in high-match frames can be held for review, but only an operator can activate a candidate. Silent auto-expansion is disabled by design to limit template contamination from mistaken matches.

## Operational safeguards

- Global matching starts disabled.
- Only explicitly opted-in profiles enter matching.
- The initial profile-to-track link is manual.
- `ADMIN`, `THREAT`, and other categories are not predicted by SFace. They remain operator-assigned metadata attached to a profile after a match.
- Template embeddings and candidate embeddings are stored locally in SQLite, unencrypted in this prototype.
- No cloud service is called during detection, matching, or storage.
- This is a local prototype. It is not an authoritative identity verification system and should not make consequential decisions autonomously.
