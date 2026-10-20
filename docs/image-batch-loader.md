# Image Batch Loader

Operator: `vision.io.image_batch_loader` (toolbox category: preprocessing).

Each execution decodes exactly one image, in natural relative-path order
(`image1`, `image2`, `image10`). The first call snapshots the file list, not the
image contents. Supported suffixes are PNG, JPG, JPEG, BMP, TIF, TIFF and WEBP,
case-insensitively. Other files are ignored. Subfolders are opt-in.

## Parameters and ports

- `folderPath`: input directory, selected with the folder button.
- `colorMode`: `color` (BGR) or `grayscale`.
- `recursive`: include subfolders; defaults to false.
- Optional input `reset`: true rescans and loads the first image on this call.
  Leave it disconnected for normal iteration; holding it true repeats the first image.
- Outputs `image` and `frame`: same contracts as Image Loader.
- `imagePath`: absolute path of the image just loaded.
- `index`: zero-based index of that image.
- `total`: number of matching files in the snapshot.
- `hasNext`: whether another image remains AFTER the current image.

The last image is a normal successful output with `hasNext=false`. There is no
automatic wraparound and no null image sent downstream. Calling again after
the end returns `E_INPUT_EXHAUSTED`. Empty/missing folders return
`E_INPUT_MISSING`; corrupt images return `E_INPUT_DECODE` without advancing.
Reset rescans after files have been added or removed.

State belongs to the Runtime node instance within a Job, including While body
invocations. Different nodes are independent. A new Job, changed folder/options,
or explicit reset starts at the first image. Reusing the same body node in a
second loop during the same Job continues its sequence unless reset. Restarting
the entire workflow as a new Job does NOT continue the previous Job's cursor.

## While wiring

New While nodes now default to a boolean state condition and do not require a
separate condition workflow. Prefer
`examples/image_batch_while_boolean/image-batch-while-boolean.emoproj`: select
`hasNext` as the boolean condition port, provide initial `true` on the While
input, and return the loader's `hasNext` from the body's same-named output.
Each iteration checks the current state before running the body. See
`docs/while-boolean-condition.md` for the contract and explicit legacy conversion.

### Existing condition-workflow example (compatibility)

Open `examples/image_batch_while_portable/image-batch-while.emoproj`, a complete
three-workflow example. The Designer opens named `.emoproj` files as well as
legacy `project.json` files. A directory must contain exactly one project file
when opened by directory; otherwise select the intended file explicitly.

1. Open the example and set `body/load.folderPath` to your image directory using
   the folder picker. The included `images` directory is an empty placeholder,
   not a supplied image dataset.
2. Main initializes `hasNext=true` using Number(1) and Compare Number(eq 1),
   then passes it to the While node.
3. The condition workflow forwards input `hasNext` to output `continue`.
4. The body contains Image Batch Loader, followed by the image processing
   nodes. The example uses Blur; replace or extend it with your processing.
5. Connect the loader's `hasNext` to the body's `hasNext` output. This becomes
   the next condition input. Do not use `hasNext` to gate processing the current
   image: that would skip the last image.

The Runtime checks the condition BEFORE the body. Set `maxIterations` to at
least image count + 1 so the final false condition can be evaluated. The example
uses 10000, allowing up to 9999 images without changing that safeguard.
The example does not save/overwrite images. An empty directory fails explicitly.

Relative folder paths in a prepared Runtime project are resolved against the
project directory. Use an absolute path when executing nodes directly. Input
directories are protected against overlapping output paths in project
preparation; save results outside the source directory. This feature does not
automatically embed the selected folder in an exported project package.
