# Third-party notices

This project bundles or installs the following third-party software. It is a
personal-use tool: the operator is responsible for downloading only content they are
entitled to download, and nothing produced by it is redistributed.

| Component | Version | Licence | How it is used |
|-----------|---------|---------|----------------|
| yt-dlp | 2026.10.26 | Unlicense | Invoked as a subprocess in the worker image to extract media |
| FFmpeg (yt-dlp/FFmpeg-Builds, GPL build) | autobuild-2026-11-15-21-54, `ffmpeg-N-128946-g33a5956d8b-linuxarm64-gpl` | GPL v3 (build configuration) | Invoked as a subprocess to remux with stream copy; installed at image build time, not redistributed |
| Deno | v2.9.8 | MIT | JavaScript runtime required by yt-dlp for YouTube challenge solving |
| boto3 / botocore | 1.44.3 | Apache-2.0 | AWS SDK used by both Lambda functions |
| pytest, pytest-cov, ruff | 9.2.0, 7.1.0, 0.17.5 | MIT | Development and test tooling only, never in a deployed image |

## Redistribution

The container images are built for the operator's own account and are not published.
The GPL FFmpeg build is installed inside a private image and invoked as a separate
process; no FFmpeg source or binary is distributed by this repository.

## Acceptable use

This project must not be used to operate a public download service, to bypass DRM or
paid access, or to redistribute copyrighted material.
