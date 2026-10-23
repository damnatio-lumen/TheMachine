# Asset Registration and Cooperative Biometric Enrollment Protocol

Version 1.6.0

## 1. Purpose

This protocol describes how to create a local asset profile, optionally enroll a consenting participant for facial matching, and review later expansion samples. An asset profile is a record in the local application. It is not a verified identity claim.

## 2. Register a profile

1. Start a local camera or video feed.
2. Select a currently visible face track.
3. Choose **REGISTER NEW ASSET**.
4. Enter an alias/display name, manually assigned classification and notes.
5. Confirm authorization to store the reference image.
6. Save the profile. The profile is manually linked to the current track for the current session.

An asset's category is metadata. Never set `THREAT` based only on a face, appearance, ethnicity, expression, or biometric match. Roles such as `ADMIN` must be assigned from a trusted, independent source.

## 3. Optional biometric opt-in

Enable **OPTIONAL BIOMETRIC MATCHING** only after the participant has explicitly agreed to local facial matching and storage of derived face templates. The application records this choice as a per-profile flag. The participant should be informed that the system compares their enrolled template with faces detected in the active local feed, that false matches are possible, where data is stored, and how to remove it.

Biometric matching is off globally by default and cannot be enabled until the local YuNet/SFace model files are available. The operator must enable matching in the left-side settings panel.

## 4. Initial cooperative enrollment

1. Manually link the live track to the saved profile.
2. Ensure the profile has biometric opt-in enabled and the global biometric engine is enabled.
3. Ask the participant to face the camera, remain within clear view, and gently turn left and right during the scan.
4. Choose **ENROLL COOPERATIVE SAMPLES** and confirm the prompt.
5. The application collects up to five feature samples from fresh detected frames. At least three valid samples are required.
6. The samples are saved locally and used by the matcher for that profile.

If enrollment fails, improve lighting and face visibility, then repeat with the participant's permission. Do not enrol by secretly processing stored photographs or unrelated footage.

## 5. Automatic re-lock

When biometric matching is enabled, every eligible detected face is compared with the active templates of opted-in profiles. The same candidate must exceed the configured cosine threshold on multiple fresh feature frames before the profile is attached to a track. When the geometric track ID expires, a later detection can be associated with the profile again if the biometric match succeeds.

This is probabilistic matching, not certainty. The UI displays `BIOMETRIC RELOCK` and the similarity score. The score is not a probability. A stricter threshold may reject the same participant under different lighting or pose; a permissive threshold can cause false matches. Manually verify any result that affects a person.

## 6. Supervised template expansion

A profile may separately opt in to **Allow high-match samples to enter a review queue**. This setting stores candidate embeddings after a strong match; candidate embeddings are not added to active templates automatically.

The operator must review each pending item in **BIOMETRIC TEMPLATE EXPANSION REVIEW** and explicitly approve or reject it. Approval adds the template to active matching; rejection leaves it inactive. Do not enable candidate generation unless the participant's agreement covers the additional sample storage.

Recommended policy: cooperative initial enrollment plus supervised expansion review. Do not use silent, unbounded auto-expansion because a false match can contaminate the template set and make future matches less reliable.

## 7. Removing consent / data

Edit the profile to disable biometric opt-in and supervised expansion. The application stops using the profile's templates for matching and clears current automatic matches to that profile. To remove all biometric data, delete the profile; template and candidate rows are configured to cascade with asset deletion. Before deletion, use the application backup script if a local backup is required.

## 8. Local storage

- Profile reference crops: `data/profiles/`
- Snapshot files: `data/snapshots/`
- Template embeddings and pending candidates: `data/machine.sqlite3`
- Operational log: `logs/machine.log`

Data is not encrypted by this version. Restrict Windows folder permissions and avoid unintended cloud syncing. The backend binds to `127.0.0.1`; do not expose it to a network without additional security work.
