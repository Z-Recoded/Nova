# Dockerfile — nova-aci-sandbox (86bbch95y). Isolated execution environment
# for the coding specialist's constrained action-space interface
# (nova_coding_aci.py) -- real design decision recorded in
# docs/coding-specialist-aci-sandbox-decision.md, built from there.
#
# Deliberately minimal, and deliberately NOT a copy of
# docker/nova-dispatch-sandbox/Dockerfile: that image bundles Node.js/npm
# and the claude CLI because the MODEL runs inside it (full unattended
# `claude -p` dispatch, git-worktree-aware). Here the model runs elsewhere
# (Ollama, serving Qwen2.5-Coder-7B) -- this container's only job is running
# nova_coding_aci.py's file operations and its structural gate (ruff) in
# isolation. No git, no Node, no ML stack.
#
# Base image and ruff version are not guesses -- checked directly against
# this repo's own pins before choosing (docs/coding-specialist-aci-sandbox-
# decision.md): nova-env's real interpreter is Python 3.13.14,
# pyproject.toml's [tool.ruff] targets "py313", and requirements.txt pins
# ruff==0.15.22.
#
# Build (from the repo root):
#   docker build -t nova-aci-sandbox:latest -f docker/nova-aci-sandbox/Dockerfile .

FROM python:3.13-slim

RUN pip install --no-cache-dir ruff==0.15.22

# nova_coding_aci.py and its one real dependency (nova_tools.py, for
# _resolve_within_root's path-safety check) are copied in, not bind-mounted
# -- the ACI module itself is part of the sandbox's fixed toolset, not
# per-task content. The per-task exercise/worktree directory IS bind-mounted
# at container-run time (mirroring nova_omen_dispatch.py's existing
# sandboxed-dispatch mount pattern, see the sandbox-decision doc) -- that's
# what actually changes between runs, not this image.
COPY nova_coding_aci.py nova_tools.py nova_sources.py /opt/nova/

# No ENTRYPOINT/CMD baked in -- matches nova-dispatch-sandbox/Dockerfile's
# own convention of never assuming a fixed invocation; the real command is
# passed at `docker run` time by whatever harness drives the ACI.
