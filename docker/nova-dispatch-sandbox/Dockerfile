# Dockerfile — nova-dispatch-sandbox (86baf72qq/86barex1u groundwork, real
# scoped v1). Real sandboxing for headless coding-agent dispatch on the Omen.
#
# Real finding this exists to fix: nova_omen_dispatch.py's dispatch_headless_task()
# runs `claude -p --worktree` directly over bare SSH on the Omen's real shell --
# Claude Code's own native Bash/Read/Write/Edit tools have full, uncontained
# access to whatever the SSH user can reach (confirmed live, no path
# validation, no command denylist, nothing -- unlike nova_tools.py's
# run_command(), which is a DIFFERENT code path only used by
# nova_orchestrator.py's separate hand-rolled loop, not by headless dispatch
# at all). This is the highest-risk execution path in the whole project --
# fully unattended, nobody watching in real time -- and today it's the LEAST
# contained one.
#
# Deliberately minimal, not a full nova-env clone: git (worktree/commit
# operations) + python3/pip (many real Nova coding tasks need to run/verify
# Python) + the claude CLI itself. No torch/transformers/etc -- this sandbox's
# job is running Claude's own tool calls, not the ML stack.
#
# Build (on the Omen, where this actually runs):
#   docker build -t nova-dispatch-sandbox:latest .

FROM node:20-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    python3 \
    python3-pip \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

# No ENTRYPOINT/CMD baked in -- the real command (claude -p ...) is passed
# explicitly at `docker run` time by nova_omen_dispatch.py's sandboxed
# dispatch path, matching this project's own convention of never assuming a
# fixed invocation.
