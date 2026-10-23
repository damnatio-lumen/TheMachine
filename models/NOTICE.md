# Model asset notice

`haarcascade_frontalface_default.xml` is the OpenCV Haar Cascade face-detection model distributed alongside the OpenCV project. It is included as a local detection asset so the application does not need to fetch a model file at runtime. See the OpenCV project and its license for the upstream terms: https://opencv.org/license/.


## Detection limits

The bundled `haarcascade_frontalface_default.xml` is a frontal-face detector. It can produce false positives and miss faces at oblique angles or under poor lighting. Version 1.3 adds stricter geometry filters, duplicate suppression, three-frame confirmation, smoothing, and a short hold when detections drop. These are noise-reduction measures, not a guarantee of zero false positives.
