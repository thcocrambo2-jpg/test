# Video (Wan 2.2 image-to-video)

The **🎬 Video (Wan 2.2)** tab animates an uploaded image into a clip of
up to 5 s. For someone using the tab, and for someone sizing a pod for
it.

Feature key `wan_i2v` — that is what fetches the models and shows the
tab. Built by
[`ember/pipelines/wan/workflow.py`](../../ember/pipelines/wan/workflow.py),
with the model facts in
[`ember/pipelines/wan/constants.py`](../../ember/pipelines/wan/constants.py).

Two model families are switchable per job in the tab. All files
(~49 GB total) auto-download from `Comfy-Org/Wan_2.2_ComfyUI_Repackaged`,
and **only stock ComfyUI nodes are used** — no node pack is installed for
this feature.

## The two families

- **14B two-expert (I2V A14B)** — two 14 B fp8 "expert" models: a
  high-noise model for the early sampler steps and a low-noise one for
  the late ones, ~14.3 GB each, at 16 fps. Best quality. Two modes mirror
  the Krea turbo/raw split:
  - **Turbo** — the lightx2v *Lightning* distillation LoRAs (~1.2 GB
    each) on both experts, 4 steps (2 high + 2 low), CFG 1.0, shift 5.0.
    Roughly 5× faster; the default.
  - **Raw** — no LoRA, the undistilled 20-step (10 + 10) CFG 3.5 schedule
    at shift 8.0. Slightly better motion and detail, but expect 15–45+
    minutes per clip on an A40.
- **5B (TI2V 5B)** — a single dense ~10 GB fp16 model with its own
  higher-compression Wan 2.2 VAE (~1.4 GB), 24 fps, 20 steps / CFG 5 at
  shift 8. There is no Lightning distillation for it, so it has no
  turbo/raw choice. Lower quality than 14B, but it fits in about 18 GB of
  VRAM with no expert swap mid-run — the best choice when running the
  parallel video instance — and model switches to and from Krea are much
  faster.

Both families share the UMT5-XXL text encoder (~6.7 GB). `shift` is the
`ModelSamplingSD3` sigma shift each mode was tuned for.

## Resolution and output

Resolution is chosen as a 480p or 720p *area* while keeping the source
image's aspect ratio, with the sides snapped to a multiple of 16 (32 for
the 5B model).

Videos are saved as MP4 under `output/wan/` and show up in the Gallery
and in the zip download.

## The parallel instance

By default video jobs share the image tabs' ComfyUI queue, which is safe
and serial. Start with `KREA2_WAN_PARALLEL=1` to give video its own
ComfyUI instance on port **8189**, so quick image jobs do not wait behind
a long render.

Both instances then split the GPU via `--reserve-vram`, from
`KREA2_MAIN_RESERVE_VRAM` (default 26 GB left for Wan by the image
instance) and `KREA2_WAN_RESERVE_VRAM` (default 22 GB left for images by
the video instance) — tuned for a 48 GB A40. Note that the two workloads
also share compute, so each runs slower while they overlap.

`KREA2_WAN_PARALLEL` only buys a second ComfyUI instance when `wan_i2v`
is granted. The parallel knobs are environment, so they live in
[`ember/settings.py`](../../ember/settings.py) rather than with the model
facts; see `docs/configuration.md`.

## Disk

Make sure the pod volume has room. Krea (~32 GB) + Wan (~49 GB) +
ComfyUI itself needs a **≥ 100 GB** disk; a 120 GB volume fits with about
35 GB left for outputs.

## Related

- [minimax.md](minimax.md) — the other video pipeline, which comes back
  with sound and never rides the parallel instance.
- `docs/architecture/job-queue.md` — the lanes video jobs run in.
