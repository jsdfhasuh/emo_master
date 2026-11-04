# Intermediate Mock coverage failure

This preserves the intermediate failure observed in the tool transcript after historical panel recovery was implemented, before the Mock's source comparison was corrected. The first `selected-package-after.log` was subsequently reused for the passing targeted run; this note records that failure explicitly rather than treating the first attempt as a pass.

`test_recover_old_release_same_job_and_frozen_coverage` reached the recovered old view (HTTP 200) but failed at:

```text
self.assertEqual(recovered.json()["sourceProblems"], {})
AssertionError: {'count': 'SOURCE_NOT_CAPTURED · 当前任务未采集此输出；先发布，再明确启动新任务',
                 'image': 'SOURCE_NOT_CAPTURED · 当前任务未采集此输出；先发布，再明确启动新任务',
                 'verdict': 'SOURCE_NOT_CAPTURED · 当前任务未采集此输出；先发布，再明确启动新任务'} != {}
1 failed, 4 passed, 12 subtests passed in 3.66s
```

The diagnostic read compared the frozen source and official package source. Their difference was the optional field `name: null` in the request while the package omitted nulls. The real Runtime already compares source definitions. MockRuntime's bare-dictionary comparison was corrected to validate both through the official `DataSource` model. The assertion that all three old sources have no coverage problem remains unchanged. The new package still must report exactly the added `measurements` source as `SOURCE_NOT_CAPTURED`.

The original user-supplied failures, rerun before product edits, and the new regression's initial 404 failure remain in their original separate logs and reports. The full suite's later 409/404 compatibility failure is also preserved separately in `backend-full-before-context-status.log`.
