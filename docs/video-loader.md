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
- Optional input `videoPath`: an absolute video path on the Runtime computer.
  When supplied, it takes precedence over the fixed `videoPath` parameter.
  A supplied empty, relative or invalid input does not fall back to the fixed
  parameter. When the input is absent, existing fixed-path projects behave as before.
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

## Multiple videos in a folder

Use `vision.io.file_list` (文件列表), the existing ForEach v2, and the video
reader's `videoPath` input. No specialized batch video decoder is needed.

1. File List lists the Runtime folder once, without decoding files. Configure
   `folderPath`, `patterns` (for example `["*.mp4", "*.avi"]`) and `recursive`
   (off by default). Matching is case-insensitive and uses filenames, not paths.
   Results are naturally sorted (`clip2` before `clip10`), and overlapping
   patterns do not duplicate a file. Outputs are `paths:list<string>` (absolute
   Runtime paths) and `count:integer`. An empty result is `[]`, count zero;
   a missing or unreadable directory is an error.
   In Designer, **文件类型** offers all files, video formats, image formats, or
   custom filenames. Check one or more formats (for example MP4 and AVI) rather
   than editing JSON. Custom rules are one per line, such as `camera_*.mp4`.
   Empty selections are reported, never treated as all files. Existing saved
   filters use this editor without changing their arrays merely on opening;
   the stored `patterns` contract and Runtime matching are unchanged.
2. Connect `paths` to ForEach `items`. Set the body's `videoPath:string` as
   `itemInputPort`; optionally map the file ordinal with `indexInputPort`.
3. The per-video body initializes `hasNext=true` and `reset=true` and invokes
   the inner While. Carry `videoPath`, `hasNext` and `reset` as While state.
4. Connect the state's path and reset to Video Loader. Process the returned
   image, then return `hasNext`, the path and `reset=false`. Thus each video
   restarts once, including intentional repeated paths; later frames continue
   the same capture. The last image still reaches downstream processing.

The ready-to-edit schema 2.7 example is
`examples/video_files_foreach/video-files-foreach.emoproj`. Select a Runtime
folder in its `main/files` node and adjust `frame/load.intervalSec`. Its Blur
node is a placeholder for downstream processing. No media is bundled.

`hasNext`, `index`, `frameIndex` and `timestampSec` keep their **single-video**
meaning. Use the ForEach file ordinal or a Job-scoped sample counter in output
filenames; source frame indices alone collide across videos. Aggregate only
paths or small summaries from ForEach, not all decoded images.

When the path input is connected, Designer retains the fixed-path draft but
disables its editor, including open editors after wiring changes. Compilation,
project path preparation and Runtime input-file freezing defer that inactive
field; execution validates the actual input. Disconnecting restores
fixed-path editing and validation. Global-variable substitution of file fields
remains prohibited. Directory inputs retain the existing output-overlap checks.

Relative fixed folder/file parameters resolve against the project directory.
Dynamic video inputs and single-node debug require absolute Runtime paths;
Designer-local files are not uploaded implicitly. Cancellation releases the
current capture. Unreadable videos fail explicitly and are not silently skipped.
