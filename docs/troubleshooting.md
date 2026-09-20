# Troubleshooting

For someone whose start did not go the way it should. The first section is
what a healthy start looks like, because most of the questions here are
really "did that step work?".

## What a healthy start logs

In order, before the UI comes up:

```
Weights → <EMBER_BASE_DIR>/models · images → <EMBER_BASE_DIR>/output
Features — ...
Catalogue — 7 model(s), 12 LoRA(s) from server
PyTorch OK — torch 2.8.0+cu128 (CUDA 12.8) on NVIDIA GeForce RTX 5050 Laptop GPU [sm_120]
SageAttention 1.0.6 OK on sm_120 — ComfyUI starts with --use-sage-attention
>>> OPEN THE UI HERE: https://<words>.trycloudflare.com/#k=...
```

- The **Weights** line says where everything will land. If that is not
  where you meant, `EMBER_BASE_DIR` is not set the way you think.
- The **Features** line is what your licence key granted — that is what
  decides which tabs exist and how many gigabytes get downloaded.
- The **Catalogue** line names the source it used: the licence server, the
  cached `.catalog.json`, or a file named by `EMBER_CATALOG_FILE`.
- The **PyTorch** line only prints after a real matmul has run on the GPU.
- The **SageAttention** line is optional; without it the app runs with
  PyTorch attention, which costs speed and nothing else.

To check the GPU stack on its own — PowerShell or bash:

```powershell
python -c "import torch, torchvision, torchaudio; print(torch.__version__, torch.version.cuda, torch.cuda.get_device_capability())"
nvidia-smi
```

## Startup

| Symptom | Cause and fix |
| --- | --- |
| Exits within seconds, nothing downloads | `EMBER_LICENSE_KEY` or `EMBER_NODE_TAG` is unset or malformed. The seat is taken before any expensive work, on purpose. The tag has to be one bare DNS label — see [Configuration](configuration.md). |
| Weights land somewhere unexpected | `EMBER_BASE_DIR` is unset, so the pod default `/workspace/ember` applied — which on Windows resolves to `C:\workspace\ember`. |
| A Krea tab says the catalogue lists no model for it | The pod could not read the model and LoRA catalogue: the licence server did not answer `POST /v1/catalog` and there is no `.catalog.json` from an earlier start, or the tab's feature has no enabled model in the database. The `Catalogue — ...` line says which source was used. Fix the database (`npm run assets` in `license-validator/`) or the network, then restart. |
| The app keeps running with the licence server down | Expected. An unreachable server is treated as transient and tolerated for `EMBER_LICENSE_GRACE` seconds (default 1800) so a blip does not kill a video render 40 minutes in; after that the app stops and says so. A `403` is different — the licence really is gone, and the app stops at once. |
| A tab you paid for is missing | The `Features — ...` line is the whole answer: the tabs come from the key. See [Licensing and features](architecture/licensing-and-features.md). |

## PyTorch and the GPU

| Symptom | Cause and fix |
| --- | --- |
| `OSError: [WinError 127] The specified procedure could not be found` | `torchvision` or `torchaudio` compiled against a different torch. `setup.ensure_torch()` repairs this automatically; it only surfaces if something installed a mismatch afterwards. |
| `no kernel image is available for execution on the device` | torch has no kernels for this GPU — the usual case on a Blackwell card (RTX 50xx, `sm_120`) with a pre-CUDA-12.8 wheel. The startup check catches it and prints the install command; run that. |
| The app raises instead of replacing a working-looking torch | Deliberate. On a pod, the installed torch is the tested base image, so the app names the fix rather than moving underneath you. Only `torchvision`/`torchaudio` are auto-repaired, with `--no-deps`. |
| A tab's output looks wrong on a card where it did not | Set `EMBER_SAGE_ATTENTION=0` and try again. SageAttention is approximate and applies to every tab. |
| Generation is minutes per image, not seconds | Not enough VRAM for the UNet, so ComfyUI is offloading to system RAM. See the hardware table in [Windows](running/windows.md#hardware). |

## The public URL

| Symptom | Cause and fix |
| --- | --- |
| `WinError 193` from `cloudflared` | A Linux `cloudflared` is cached where the Windows one belongs. Delete `cloudflared.exe` under `EMBER_BASE_DIR` and restart: `serve.RELEASES` picks the asset by `sys.platform`, and `scripts/windows_start.ps1` verifies a sha256 before putting one there. |
| `note: could not fetch the tunnel helper` at startup | The Hugging Face mirror was unreachable. Harmless — the app downloads cloudflared from the GitHub release instead. |
| No public URL, the tunnel times out | cloudflared could not reach the Cloudflare edge. The app says so and keeps serving the local port; on a pod that is still reachable through RunPod's own proxy. |
| The tunnel URL will not resolve for a minute | Some resolvers cache the NXDOMAIN for a hostname that did not exist a second ago. The local port works throughout. |
| The UI opens but immediately asks for a token again | The access token rides in the URL fragment and is traded for an HttpOnly cookie. A link that lost its `#k=...` cannot do that. Re-copy the whole line the app printed. |

## Ports and processes

Port 7860 still held after a crash — PowerShell:

```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 7860).OwningProcess -Force
```

bash:

```bash
fuser -k 7860/tcp
```

ComfyUI's own output goes to `comfyui.log` under `EMBER_BASE_DIR`, and the
parallel video instance to `comfyui_wan.log`. A generation that fails with
nothing useful in the app's log has its real error there.

## Docker

| Symptom | Cause and fix |
| --- | --- |
| `torch.cuda.is_available()` is False in the log | Docker Desktop is on the Hyper-V backend, which cannot pass a GPU through at all. Switch to the WSL2 backend and check the host's NVIDIA driver. |
| The container will not start for want of CUDA 13 | The image needs an **R580 or newer** driver on the host. On RunPod, the template's CUDA version filter has to be 13.0. |
| Compose refuses to start | There is no `.env`. Copy `.env.example` and fill in the two values. |
| Models downloaded twice | `docker compose down -v` deleted the named volume. `down` on its own keeps it. |
| No room on `C:` | The `krea2-data` volume lives in the WSL2 virtual disk. Docker Desktop → Settings → Resources → Advanced → *Disk image location*. |

More in [Docker](running/docker.md).

## Inspecting a generated image

Every generated image carries its workflow. To read it back — PowerShell or
bash:

```bash
python3 scripts/inspect_image_metadata.py                  # ComfyUI's input dir
python3 scripts/inspect_image_metadata.py path/to/dir
python3 scripts/inspect_image_metadata.py a.png b.jpg
python3 scripts/inspect_image_metadata.py --full           # no truncation
python3 scripts/inspect_image_metadata.py --json           # machine-readable
```

That is the fastest way to answer "which model and which LoRA actually
produced this", and the first thing to check when a recipe reloads with a
control that looks wrong.

## When nothing above fits

Reproduce it without the GPU first: `scripts/dryrun.py` needs no card, no
licence and no weights, and it isolates "the UI is wrong" from "the
generation is wrong". See [Dry run](running/dry-run.md).

If the graph itself is suspect, the checks in
[Checks](development/checks.md) compare every workflow the app builds
against a committed snapshot, byte for byte.
