# Local Video Sampling

Operator: `vision.io.video_loader`, displayed as local video sampling in the
preprocessing toolbox. Uses the existing OpenCV file decoder, not a camera.

## Parameters and outputs

- `videoPath`: local video file on the Runtime computer. The file picker lists
  MP4, AVI, MKV, MOV, WMV, M4V, WEBM and MPEG. Actual codec support depends on
  the installed OpenCV backend.
- `intervalSec`: positive sampling interval in seconds, default 1.0. Fractions
  such as 0.25 are supported. This refers to video time, not wall-clock waiting.
- `colorMode`: color (BGR) or grayscale.
- Optional input `reset`: true restarts at the beginning on this invocation.
- `image`, `frame`: standard image and coordinate-frame outputs for downstream
  processing. Each sampled source frame has a distinct coordinate source ID.
- `videoPath`: resolved input path; `index`: zero-based sample index;
  `frameIndex`: zero-based source-frame index; `timestampSec`: sampled frame time.
- `hasNext`: whether another sampled image remains after this one.

Sampling starts at zero and uses the first available frame at or after each
interval boundary. For example, at 10 FPS and interval 0.25 seconds, source
frames 0, 3, 5, 8, 10 are selected. A source frame is never emitted twice when
the interval is shorter than its duration. Decoder timestamps are used when
available; backends without advancing timestamps fall back to frame index/FPS.

Each execution returns one image. The last sample succeeds with `hasNext=false`;
a further call returns `E_INPUT_EXHAUSTED`. No null image is passed downstream.
The reader scans sequentially and retains only one prefetched sampled image,
not the entire video. Opening/decoding failures are explicit errors. OpenCV
does not distinguish every midstream decoder failure from normal EOF.

State is per node and Job. A new Job, changed video/interval/color mode, or reset
starts at the beginning. Normal completion, cancellation and Runtime lifecycle
cleanup release the capture. There is no automatic wraparound or background
playback. Single-node debug advances one sample per explicit execution.

## Workflow

Open `examples/video_sample_while/video-sample-while.emoproj`, select a video
in the body's video node and set the interval. The example contains:

1. Main: initialize `hasNext=true`, then enter a boolean While.
2. Body: read a video sample and pass `image`/`frame` to Blur. Replace or extend
   Blur with the required processing nodes.
3. Return the reader's `hasNext` as the body's `hasNext` output.

Do not use `hasNext` to gate processing of the current image: that would skip
the last sample. For a long video, set While's `maxIterations` above the number
of samples (the example defaults to 10000). A standalone node without a loop
reads only the first sample in each new Job.

Project-relative paths resolve against the project directory. Single-node debug
requires an absolute Runtime-local path. Selecting a Designer-local file does
not automatically upload it to a different Runtime computer. This example does
not include video data or write any output files.
