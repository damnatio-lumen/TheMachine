# Optional local biometric model files

The v1.6 ZIP does **not** redistribute the ONNX weights. Download them yourself from the upstream OpenCV model repository with `scripts/download-biometric-models.ps1`. The script verifies SHA-256 before installing each file.

## Files and sources

- YuNet detector: `face_detection_yunet_2023mar.onnx`
  - Official OpenCV Zoo: <https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx>
  - SHA-256: `8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4`
  - The upstream YuNet model directory documents an MIT license. Keep the downloaded `yunet_LICENSE` notice with the model.
- SFace recognizer: `face_recognition_sface_2021dec.onnx`
  - Official OpenCV Zoo: <https://huggingface.co/opencv/opencv_zoo/resolve/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx>
  - SHA-256: `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79`
  - The model directory publishes its own license notice. The download script attempts to save it as `sface_LICENSE`; review that notice and upstream terms before redistribution or commercial use.

## Important limitations

The matcher also refuses a relock when the best and second-best different profiles are closer than a 0.05 cosine-score margin. The cosine similarity score is not a probability. OpenCV's SFace demo uses a cosine threshold of 0.363 as an example starting point, but the appropriate operating point depends on camera quality, lighting, pose, and the consequences of false matches. This application defaults to 0.40 and offers a slider from 0.30 to 0.70. Higher values are stricter and can increase false rejections; lower values can increase false matches.

Biometric matching is disabled until the operator explicitly enables it and only compares profiles with participant opt-in and cooperative enrollment templates. The `THREAT`, `ADMIN`, and other labels are profile metadata set by an operator; the software never infers threat status from a face. Expansion candidates are queued only for profiles that separately opt in, and remain inactive until an operator approves them.
