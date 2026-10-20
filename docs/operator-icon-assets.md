# Operator Icon Assets

The three v1 examples are original, code-authored geometric SVGs created for
Emo Master on 2026-09-10. They contain no vendor logos, copied artwork, fonts,
or external references. No external icon license is required. No new license
grant is introduced here; distribution terms remain a repository-owner decision.

| Operator | Resource relative to builtins | Motif | Accent |
| --- | --- | --- | --- |
| `vision.edge.canny` | `canny_edge/assets/icon.svg` | Paired stepped contours | `#0891B2` |
| `vision.io.huaray_camera` | `huaray_camera/assets/icon.svg` | Camera body and lens | `#059669` |
| `vision.inference.yolo` | `yolo_inference/assets/icon.svg` | Detection boxes and frame | `#DB2777` |

All use a transparent 24 x 24 viewBox, explicit colors and the restricted
v1 primitive/path subset. Plugin and operator metadata patch versions advance
together; no algorithm, parameter, or port changes accompany these assets.
Other operators retain their existing category fallback. These three examples
are not full-catalog icon coverage.

The package self-test records the frozen SHA-256 and checks accent-colored
pixels separately from the fallback. Replacing an example requires updating
its expected digest in the self-test and restarting Runtime to register it.

| Operator | Version | SHA-256 |
| --- | --- | --- |
| `vision.edge.canny` | `1.1.1` | `1ecbff9850c560ade9f1526e3439dfb6979d1a619f769e41eafadaa0c0537a37` |
| `vision.io.huaray_camera` | `1.1.1` | `23dd34d1889baed1531108e3c48174d1fed081ec96c80446c51b3ddad5f62e70` |
| `vision.inference.yolo` | `1.2.1` | `989138d077ff17257b7cb96f4db0d5f8e0cac694b04c62f04a2cf0b76a1cdcaf` |

The manifest-relative path is `assets/icon.svg` for each example. These SVGs
use LF line endings; `.gitattributes` preserves them on Windows so the
checked-in bytes and pinned self-test digests agree across checkouts.
