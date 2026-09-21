# Rebuilding the image (builder pod + self-hosted runner)

Nothing unique lives on the builder pod — the whole build is reproducible from
this repo + Docker Hub. GitHub *hosted* runners are blocked for this account, so
builds run on a **self-hosted** runner on a cheap RunPod pod, and the image is
assembled with **crane** (RunPod pods can't run docker/buildkit: no daemon, no
privileged, no userns). The Docker Hub token only ever exists as the repo secret
`DOCKERHUB_TOKEN` — it never touches a local machine.

## Quick restore (~3 min) if the builder pod was terminated

1. Create any small RunPod pod with a public TCP SSH port (A40/RTX4000 is plenty,
   no GPU work happens here). Note its `ip:port`.
2. Install + register the runner (get a fresh token at
   https://github.com/muchosun/comfyui-h3-serverless/settings/actions/runners/new):
   ```bash
   ssh root@IP -p PORT
   apt-get update && apt-get install -y git curl jq
   mkdir -p ~/ar && cd ~/ar
   curl -fsSL -o r.tgz https://github.com/actions/runner/releases/download/v2.328.0/actions-runner-linux-x64-2.328.0.tar.gz
   tar xzf r.tgz
   export RUNNER_ALLOW_RUNASROOT=1
   ./config.sh --url https://github.com/muchosun/comfyui-h3-serverless --token <FRESH_TOKEN> --unattended --name builder --labels self-hosted
   nohup ./run.sh >/tmp/runner.log 2>&1 &
   ```
3. Trigger a build: `gh workflow run build-push --repo muchosun/comfyui-h3-serverless --ref master`
   (crane is downloaded by the job itself; nothing else to install).

## If the builder pod was only STOPPED (not terminated)
Just resume it in the RunPod console — the runner is already registered on its
disk and reconnects on its own. Then trigger the workflow.

## What the build produces
`docker.io/muchosun/comfyui-h3-serverless:v<N>` (public). Bump the tag in
`.github/workflows/build-push.yml`, push, run the workflow, then point the
serverless template `0dnakgvhf1` at the new tag.
