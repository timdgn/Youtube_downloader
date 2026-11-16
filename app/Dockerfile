# syntax=docker/dockerfile:1
#
# One Dockerfile, two final targets: `api` and `worker`.
# Build both for linux/arm64:
#   docker buildx build --platform linux/arm64 --target api    -t repo:tag-api    app
#   docker buildx build --platform linux/arm64 --target worker -t repo:tag-worker app
#
# Every tool is pinned by version and, for downloaded binaries, by SHA-256.

ARG PYTHON_IMAGE=public.ecr.aws/lambda/python:3.12

# ------------------------------------------------------------------------------
# Python dependencies, shared by both targets
# ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS deps
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore
COPY requirements.txt ${LAMBDA_TASK_ROOT}/requirements.txt
RUN python -m pip install -r ${LAMBDA_TASK_ROOT}/requirements.txt

# ------------------------------------------------------------------------------
# Media tooling, fetched and verified once, copied into the worker only
# ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS media-tools
ARG FFMPEG_RELEASE=autobuild-2026-11-15-21-54
ARG FFMPEG_ASSET=ffmpeg-N-128946-g33a5956d8b-linuxarm64-gpl.tar.xz
ARG FFMPEG_SHA256=3d6fd3906a050cfffea18d551ad28396835326f67ff9aeccf734ce253659c591
ARG DENO_VERSION=v2.9.8
ARG DENO_SHA256=d49596c20b9788cf8f5d8ddf644103d4bde56ed239753f0bdb92ba9361d51905

# `dnf` in the Lambda base image is microdnf, which rejects `install_weak_deps=False`.
RUN dnf install -y tar xz unzip \
 && dnf clean all

WORKDIR /opt/build
RUN curl -fsSL -o ffmpeg.tar.xz \
      "https://github.com/yt-dlp/FFmpeg-Builds/releases/download/${FFMPEG_RELEASE}/${FFMPEG_ASSET}" \
 && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum -c - \
 && mkdir -p /opt/ffmpeg \
 && tar -xJf ffmpeg.tar.xz -C /opt/ffmpeg --strip-components=1 \
 && rm -f ffmpeg.tar.xz

RUN curl -fsSL -o deno.zip \
      "https://github.com/denoland/deno/releases/download/${DENO_VERSION}/deno-aarch64-unknown-linux-gnu.zip" \
 && echo "${DENO_SHA256}  deno.zip" | sha256sum -c - \
 && unzip -q deno.zip -d /opt/deno \
 && rm -f deno.zip

# ------------------------------------------------------------------------------
# API target: validation, storage records, queueing, URL signing. No media tools.
# ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS api
ENV PYTHONPATH=/var/task/src
COPY --from=deps ${LAMBDA_TASK_ROOT}/ ${LAMBDA_TASK_ROOT}/
# The one source of the extraction version; /info reports what the worker runs.
COPY ytdlp-version.txt ${LAMBDA_TASK_ROOT}/ytdlp-version.txt
COPY src/ ${LAMBDA_TASK_ROOT}/src/
CMD ["ytdl.api.handler.handler"]

# ------------------------------------------------------------------------------
# Worker target: probe, download, remux, upload.
# ------------------------------------------------------------------------------
FROM ${PYTHON_IMAGE} AS worker
ENV PYTHONPATH=/var/task/src
COPY --from=deps ${LAMBDA_TASK_ROOT}/ ${LAMBDA_TASK_ROOT}/
COPY --from=media-tools /opt/ffmpeg/bin/ffmpeg /opt/ffmpeg/bin/ffprobe /usr/local/bin/
COPY --from=media-tools /opt/deno/deno /usr/local/bin/deno
# The version is read from one file, so the image and the plan cannot drift
# apart (research D-13).
COPY ytdlp-version.txt /tmp/ytdlp-version.txt
RUN chmod +x /usr/local/bin/ffmpeg /usr/local/bin/ffprobe /usr/local/bin/deno \
 && python -m pip install --no-cache-dir "yt-dlp[default]==$(cat /tmp/ytdlp-version.txt)" \
 && python -m pip show yt-dlp-ejs | grep -E '^(Name|Version):' \
 && yt-dlp --version \
 && ffmpeg -version | head -n 1 \
 && deno --version | head -n 1
COPY ytdlp-version.txt ${LAMBDA_TASK_ROOT}/ytdlp-version.txt
COPY src/ ${LAMBDA_TASK_ROOT}/src/
CMD ["ytdl.worker.handler.handler"]
