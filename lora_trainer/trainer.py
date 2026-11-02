"""One face-LoRA training run on Krea 2 Raw, with musubi-tuner.

Everything a run needs lives under BASE_DIR/trainer/, beside the app's
own models, so it survives a pod Stop with the rest of /workspace:

    trainer/musubi-tuner/      the trainer (cloned once)
    trainer/venv/              its interpreter (the app's packages on its
                               path, so the app's torch is reused)
    trainer/models/qwen_image_vae.safetensors
                               only if the app has no Qwen-Image VAE (a
                               V2-only pod has just the Wan one)
    trainer/models/raw_bf16_from_pod.safetensors
                               the app's Krea 2 Raw, dequantised to bf16
                               once (raw.safetensors instead, if musubi
                               refused that and the official one was
                               downloaded)
    trainer/runs/<name>/       dataset/, cache/, output/, train.log,
                               state.json

The base model, the VAE and the text encoder are the app's own files.
The text encoder is the abliterated one the app renders with, so the
captions are encoded during training exactly as they are encoded at
render time.

Phases, in order: setup → base_model → prepare → free_gpu → cache_latents
→ cache_text → train → install → done. Each subprocess's tqdm bar is
parsed as it streams, so state.json always says which phase is running,
step N of M, its speed and its ETA. With dry_run every command is
replaced by a fake that prints the same bars, which is how the page is
tested off a GPU.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import sysconfig
import threading
import time
import urllib.request
from pathlib import Path

MUSUBI_REPO = "https://github.com/kohya-ss/musubi-tuner"
RAW_REPO = "krea/Krea-2-Raw"
RAW_FILE = "raw.safetensors"
# The app's Raw UNet (catalogue file krea2_raw_fp8_scaled.safetensors),
# and what it becomes once dequantised for musubi.
POD_RAW_GLOB = "krea2_raw*.safetensors"
CONVERTED_FILE = "raw_bf16_from_pod.safetensors"
VAE_FILE = "qwen_image_vae.safetensors"
# Where the app itself fetches that VAE from (pipelines/krea2/constants.py).
VAE_REPO, VAE_PATH = "Comfy-Org/Krea-2", "vae/" + VAE_FILE
TEXT_ENCODERS = ("qwen3vl_4b_abliterated.safetensors",
                 "qwen3vl_4b_bf16.safetensors")
COMFY = "http://127.0.0.1:8188"
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

PHASES = ("setup", "base_model", "prepare", "free_gpu", "cache_latents",
          "cache_text", "train", "install", "done")

# What the page starts on: a face from 5-20 photos in about half an hour
# on a 5090. Rank 16 is plenty for one identity; 768 px trains roughly
# twice as fast as 1024 and the face still holds at 1024+ at render time.
DEFAULTS = {
    "trigger": "ohwx person",
    "caption": "a photo of {trigger}",
    "steps": 800,
    "rank": 16,
    "learning_rate": 1e-4,
    "resolution": 768,
    "save_every": 200,
    "blocks_to_swap": 0,
}

# tqdm: " 12%|█▎        | 96/800 [01:40<12:13,  1.04s/it, avr_loss=0.112]"
BAR_RE = re.compile(r"(\d+)/(\d+) \[([\d:]+)<([\d:?]+),\s*([\d.]+)\s*(s/it|it/s)")
LOSS_RE = re.compile(r"avr_loss=([\d.]+)")

# A ComfyUI fp8_scaled checkpoint → plain bf16, run in the trainer's venv:
# every fp8 weight times its own scale_weight, the scale and marker keys
# dropped, ComfyUI's "model.diffusion_model." prefix stripped. Written to a
# .part file first so a killed conversion never looks finished.
CONVERT_CODE = r"""
import os, sys, torch
from safetensors import safe_open
from safetensors.torch import save_file
from tqdm import tqdm
src, dst = sys.argv[1], sys.argv[2]
FP8 = (torch.float8_e4m3fn, torch.float8_e5m2)
PREFIX = "model.diffusion_model."
out = {}
with safe_open(src, "pt") as f:
    keys = list(f.keys())
    have = set(keys)
    for key in tqdm(keys, desc="convert"):
        if key.endswith((".scale_weight", ".scale_input")) or key == "scaled_fp8":
            continue
        t = f.get_tensor(key)
        if t.dtype in FP8:
            scale = key[:-len("weight")] + "scale_weight" if key.endswith(".weight") else None
            t = t.to(torch.float32)
            if scale in have:
                t = t * f.get_tensor(scale).to(torch.float32)
            t = t.to(torch.bfloat16)
        out[key[len(PREFIX):] if key.startswith(PREFIX) else key] = t
print("converted %d tensors" % len(out))
save_file(out, dst + ".part")
os.replace(dst + ".part", dst)
"""

# What musubi prints when the converted file's keys are not the layout
# its Krea 2 model expects — the cue to fall back to the official file.
LAYOUT_ERRORS = ("Missing key", "Unexpected key", "size mismatch",
                 "Error(s) in loading state_dict")


def clock_seconds(text: str) -> int | None:
    """"01:02:03" or "12:13" → seconds; None for tqdm's "?"."""
    if "?" in text:
        return None
    total = 0
    for part in text.split(":"):
        total = total * 60 + int(part)
    return total


class Run:
    """One run: its folders, its state, and the thread that drives it."""

    def __init__(self, root: Path, models_dir: Path, name: str,
                 options: dict, dry_run: bool = False):
        self.root = root
        self.models_dir = models_dir
        self.name = name
        self.options = {**DEFAULTS, **options}
        self.dry_run = dry_run
        self.dir = root / "runs" / name
        self.proc: subprocess.Popen | None = None
        self.dit: Path | None = None      # chosen by _base_model
        self.vae: Path | None = None      # chosen by _base_model
        self.stopping = False
        self.lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.state = {
            "name": name, "options": self.options, "phase": "setup",
            "status": "running", "error": None, "started_at": time.time(),
            "finished_at": None, "phase_started_at": time.time(),
            "progress": None, "loss": None, "phases": {},
            "checkpoints": [], "lora_file": None, "log_tail": [],
        }

    # ── state ───────────────────────────────────────────────────────

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        with self.lock:
            text = json.dumps(self.state, indent=1)
        with self.save_lock:
            tmp = self.dir / "state.json.tmp"
            tmp.write_text(text, encoding="utf-8")
            # Windows will not replace a file the page is reading this
            # instant; the next try a moment later goes through.
            for _ in range(20):
                try:
                    tmp.replace(self.dir / "state.json")
                    return
                except PermissionError:
                    time.sleep(0.05)

    def phase(self, name: str) -> None:
        now = time.time()
        with self.lock:
            previous = self.state["phase"]
            took = now - self.state["phase_started_at"]
            self.state["phases"][previous] = round(took, 1)
            self.state.update(phase=name, phase_started_at=now, progress=None)
        self.save()

    def line(self, text: str) -> None:
        """One line (or one \\r-redraw) of subprocess output."""
        text = text.strip()
        if not text:
            return
        bar = BAR_RE.search(text)
        with self.lock:
            if bar:
                done, total = int(bar.group(1)), int(bar.group(2))
                rate = float(bar.group(5))
                seconds_per_it = rate if bar.group(6) == "s/it" else (
                    1 / rate if rate else None)
                self.state["progress"] = {
                    "done": done, "total": total,
                    "elapsed": clock_seconds(bar.group(3)),
                    "eta": clock_seconds(bar.group(4)),
                    "seconds_per_it": seconds_per_it,
                }
                loss = LOSS_RE.search(text)
                if loss:
                    self.state["loss"] = float(loss.group(1))
            else:
                tail = self.state["log_tail"]
                tail.append(text[-300:])
                del tail[:-40]

    # ── the run ─────────────────────────────────────────────────────

    def start(self) -> threading.Thread:
        self.save()
        thread = threading.Thread(target=self._main, daemon=True)
        thread.start()
        return thread

    def stop(self) -> None:
        self.stopping = True
        proc = self.proc
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (AttributeError, ProcessLookupError, PermissionError):
                proc.terminate()

    def _main(self) -> None:
        try:
            self._setup()
            self._base_model()
            self._prepare()
            self._free_gpu()
            self._cache()
            self._train()
            self._install()
            self.phase("done")
            with self.lock:
                self.state.update(status="done", finished_at=time.time())
        except Exception as exc:
            with self.lock:
                self.state.update(
                    status="stopped" if self.stopping else "failed",
                    error=None if self.stopping else str(exc),
                    finished_at=time.time())
        self.save()

    def _run(self, args: list, cwd: Path | None = None,
             env: dict | None = None) -> None:
        """Run one command, stream its output into the state, raise on failure."""
        if self.stopping:
            raise RuntimeError("stopped")
        log_path = self.dir / "train.log"
        with log_path.open("a", encoding="utf-8") as log:
            log.write("\n$ %s\n" % " ".join(str(a) for a in args))
            self.proc = subprocess.Popen(
                [str(a) for a in args], cwd=cwd, env={**os.environ, **(env or {})},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                start_new_session=True)
            buffer = b""
            last_save = 0.0
            while True:
                chunk = self.proc.stdout.read1(4096)
                if not chunk:
                    break
                log.write(chunk.decode("utf-8", "replace"))
                buffer += chunk
                # tqdm redraws with \r; every redraw is a line to us.
                *lines, buffer = re.split(rb"[\r\n]", buffer)
                for raw in lines:
                    self.line(raw.decode("utf-8", "replace"))
                if time.time() - last_save > 1:
                    self.save()
                    last_save = time.time()
            self.line(buffer.decode("utf-8", "replace"))
            code = self.proc.wait()
        self.save()
        if self.stopping:
            raise RuntimeError("stopped")
        if code != 0:
            raise RuntimeError("%s exited with code %d — see train.log"
                               % (Path(str(args[0])).name, code))

    # ── phases ──────────────────────────────────────────────────────

    @property
    def musubi(self) -> Path:
        return self.root / "musubi-tuner"

    @property
    def python(self) -> Path:
        venv = self.root / "venv"
        return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")

    def _setup(self) -> None:
        """Clone musubi-tuner and install it into its own venv, once."""
        if self.dry_run:
            self._fake("setup", 5)
            return
        if not (self.musubi / ".git").exists():
            self._run(["git", "clone", "--depth", "1", MUSUBI_REPO,
                       self.musubi])
        marker = self.root / "venv" / ".installed"
        if not marker.exists():
            # The app's torch is reused rather than downloaded again. On
            # the pod it lives in /opt/venv, which --system-site-packages
            # would not reach (that means the *base* interpreter's), so a
            # .pth puts this interpreter's packages on the new venv's path
            # behind its own: pip sees torch as installed, and anything it
            # does add lands in trainer/venv, never in the app's venv.
            self._run([sys.executable, "-m", "venv", self.root / "venv"])
            child = subprocess.run(
                [str(self.python), "-c",
                 "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                capture_output=True, text=True, check=True).stdout.strip()
            parent = sysconfig.get_paths()["purelib"]
            (Path(child) / "_app_packages.pth").write_text(
                parent + "\n", encoding="utf-8")
            self.line("Reusing the app's packages from %s." % parent)
            self._run([self.python, "-m", "pip", "install",
                       "--index-url", "https://pypi.org/simple",
                       "-e", self.musubi])
            marker.write_text(time.ctime(), encoding="utf-8")

    def official_dit(self) -> Path:
        return self.root / "models" / RAW_FILE

    def converted_dit(self) -> Path:
        return self.root / "models" / CONVERTED_FILE

    def pod_raw(self) -> Path | None:
        """The app's own Krea 2 Raw UNet, if it has downloaded one."""
        folder = self.models_dir / "diffusion_models"
        found = sorted(folder.glob(POD_RAW_GLOB))
        return found[0] if found else None

    def _base_model(self) -> None:
        """Pick the DiT to train on, making it if it is not there yet.

        musubi loads a bf16 DiT and quantises it itself (--fp8_base
        --fp8_scaled); its Krea 2 loader refuses weights that are already
        fp8, which the pod's krea2_raw_fp8_scaled is. So the pod's file is
        dequantised once (weight × scale_weight → bf16, no download), and
        the official bf16 Raw is downloaded only when there is no pod file
        or musubi refuses the converted one (_train).
        """
        self.phase("base_model")
        if self.dry_run:
            self._fake("convert", 5)
            self.dit = self.vae = self.converted_dit()
            return
        self._pick_vae()
        for ready in (self.official_dit(), self.converted_dit()):
            if ready.exists():
                self.dit = ready
                return
        source = self.pod_raw()
        if source is None:
            self.line("No Krea 2 Raw in %s — downloading the official one."
                      % (self.models_dir / "diffusion_models"))
            self._download()
            return
        self.line("Converting the app's %s to bf16 for training." % source.name)
        target = self.converted_dit()
        target.parent.mkdir(parents=True, exist_ok=True)
        self._run([self.python, "-c", CONVERT_CODE, source, target])
        self.dit = target

    def _pick_vae(self) -> None:
        """The Qwen-Image VAE musubi encodes with: the app's, else fetched.

        A pod licensed only for the V2 tabs has the Wan 2.1 VAE alone
        (V2 decodes with it); the Qwen-Image one is ~250 MB from the repo
        the app itself downloads it from.
        """
        own = self.models_dir / "vae" / VAE_FILE
        if own.exists():
            self.vae = own
            return
        folder = self.root / "models"
        self.vae = folder / VAE_PATH
        if not self.vae.exists():
            self.line("The app has no %s — fetching it (~250 MB)." % VAE_FILE)
            self._hf(VAE_REPO, VAE_PATH, folder)

    def _hf(self, repo: str, path: str, folder: Path) -> None:
        """One file from Hugging Face into folder/path, HF_TOKEN if set."""
        code = ("import sys; from huggingface_hub import hf_hub_download; "
                "hf_hub_download(sys.argv[1], sys.argv[2], local_dir=sys.argv[3], "
                "token=__import__('os').environ.get('HF_TOKEN') or None)")
        self._run([self.python, "-c", code, repo, path, folder])

    def _download(self) -> None:
        """The official bf16 Raw DiT, once (~24 GB)."""
        target = self.official_dit()
        if not target.exists():
            self._hf(RAW_REPO, RAW_FILE, target.parent)
        self.dit = target

    def text_encoder(self) -> Path:
        for name in TEXT_ENCODERS:
            path = self.models_dir / "text_encoders" / name
            if path.exists():
                return path
        raise RuntimeError("No bf16 Qwen3-VL text encoder in %s/text_encoders "
                           "(looked for %s)" % (self.models_dir,
                                                ", ".join(TEXT_ENCODERS)))

    def _prepare(self) -> None:
        """Captions, and the dataset TOML musubi reads."""
        self.phase("prepare")
        dataset = self.dir / "dataset"
        images = sorted(p for p in dataset.iterdir()
                        if p.suffix.lower() in IMAGE_SUFFIXES)
        if not images:
            raise RuntimeError("No images uploaded.")
        caption = self.options["caption"].replace("{trigger}",
                                                  self.options["trigger"])
        for image in images:
            image.with_suffix(".txt").write_text(caption, encoding="utf-8")
        size = int(self.options["resolution"])
        toml = (
            "[general]\n"
            f"resolution = [{size}, {size}]\n"
            'caption_extension = ".txt"\n'
            "batch_size = 1\n"
            "enable_bucket = true\n"
            "bucket_no_upscale = false\n\n"
            "[[datasets]]\n"
            f"image_directory = {json.dumps(dataset.as_posix())}\n"
            f"cache_directory = {json.dumps((self.dir / 'cache').as_posix())}\n"
            "num_repeats = 1\n"
        )
        (self.dir / "dataset.toml").write_text(toml, encoding="utf-8")
        with self.lock:
            self.state["images"] = len(images)
        self.save()

    def _free_gpu(self) -> None:
        """Ask the app's ComfyUI to drop its models so training gets the VRAM."""
        self.phase("free_gpu")
        body = json.dumps({"unload_models": True, "free_memory": True}).encode()
        request = urllib.request.Request(
            COMFY + "/free", data=body, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(request, timeout=10).read()
            self.line("ComfyUI released its models.")
        except Exception as exc:
            self.line("ComfyUI not reachable (%s) — continuing." % exc)
        time.sleep(3)

    def _cache(self) -> None:
        if self.dry_run:
            self.phase("cache_latents")
            self._fake("cache_latents", 6)
            self.phase("cache_text")
            self._fake("cache_text", 6)
            return
        toml = self.dir / "dataset.toml"
        scripts = self.musubi / "src" / "musubi_tuner"
        self.phase("cache_latents")
        self._run([self.python, scripts / "krea2_cache_latents.py",
                   "--dataset_config", toml,
                   "--vae", self.vae,
                   "--batch_size", "1", "--skip_existing"], cwd=self.musubi)
        self.phase("cache_text")
        self._run([self.python, scripts / "krea2_cache_text_encoder_outputs.py",
                   "--dataset_config", toml,
                   "--text_encoder", self.text_encoder(),
                   "--batch_size", "1", "--skip_existing"], cwd=self.musubi)

    def _train(self) -> None:
        self.phase("train")
        o = self.options
        steps = int(o["steps"])
        if self.dry_run:
            self._fake("train", steps, loss=True)
            out = self.dir / "output"
            out.mkdir(parents=True, exist_ok=True)
            every = int(o["save_every"])
            for step in range(every, steps, every):
                (out / ("%s-step%08d.safetensors" % (self.name, step))
                 ).write_bytes(b"fake")
            (out / (self.name + ".safetensors")).write_bytes(b"fake")
            self._checkpoints()
            return
        try:
            self._run(self._train_args(), cwd=self.musubi)
        except RuntimeError:
            if self.stopping or self.dit != self.converted_dit() \
                    or not self._log_says(LAYOUT_ERRORS):
                raise
            # The converted pod file is not the layout musubi's model
            # wants. The official file is; fetch it and go again. The
            # next run finds it first and never converts.
            self.line("musubi refused the converted pod model — "
                      "downloading the official Krea 2 Raw and retrying.")
            self._download()
            self._run(self._train_args(), cwd=self.musubi)
        self._checkpoints()

    def _log_says(self, needles) -> bool:
        """Whether the end of train.log contains any of `needles`."""
        with (self.dir / "train.log").open("rb") as log:
            log.seek(0, os.SEEK_END)
            log.seek(max(0, log.tell() - 400_000))
            tail = log.read().decode("utf-8", "replace")
        return any(needle in tail for needle in needles)

    def _train_args(self) -> list:
        o = self.options
        args = [
            self.python, "-m", "accelerate.commands.launch",
            "--num_cpu_threads_per_process", "1", "--mixed_precision", "bf16",
            self.musubi / "src" / "musubi_tuner" / "krea2_train_network.py",
            "--dit", self.dit,
            "--vae", self.vae,
            "--dataset_config", self.dir / "dataset.toml",
            "--sdpa", "--mixed_precision", "bf16",
            "--timestep_sampling", "shift", "--weighting_scheme", "none",
            "--discrete_flow_shift", "2.5",
            "--optimizer_type", "adamw8bit",
            "--learning_rate", str(float(o["learning_rate"])),
            "--gradient_checkpointing",
            "--max_data_loader_n_workers", "2",
            "--persistent_data_loader_workers",
            "--network_module", "networks.lora_krea2",
            "--network_dim", str(int(o["rank"])),
            "--network_alpha", str(int(o["rank"])),
            "--fp8_base", "--fp8_scaled",
            "--max_train_steps", str(int(o["steps"])),
            "--save_every_n_steps", str(int(o["save_every"])),
            "--seed", "42",
            "--output_dir", self.dir / "output",
            "--output_name", self.name,
        ]
        swap = int(o.get("blocks_to_swap") or 0)
        if swap:
            args += ["--blocks_to_swap", str(min(swap, 26))]
        return args

    def _checkpoints(self) -> None:
        out = self.dir / "output"
        found = sorted(p.name for p in out.glob("*.safetensors"))
        with self.lock:
            self.state["checkpoints"] = found
        self.save()

    def _install(self) -> None:
        """Copy the final LoRA into the app's loras/ folder."""
        self.phase("install")
        final = self.dir / "output" / (self.name + ".safetensors")
        if not final.exists():
            raise RuntimeError("Training finished but %s is missing." % final.name)
        with self.lock:
            self.state["lora_file"] = install(final, self.models_dir)
        self.save()

    # ── dry run ─────────────────────────────────────────────────────

    def _fake(self, what: str, total: int, loss: bool = False) -> None:
        """Print a tqdm-shaped bar for `total` steps, fast."""
        code = (
            "import sys, time\n"
            "total = int(sys.argv[1]); loss = sys.argv[2] == '1'\n"
            "start = time.time()\n"
            "for i in range(1, total + 1):\n"
            "    time.sleep(min(0.05, 6 / total))\n"
            "    took = time.time() - start; rate = took / i\n"
            "    left = int(rate * (total - i))\n"
            "    post = ', avr_loss=%.4f' % (0.2 / (1 + i / 100)) if loss else ''\n"
            "    sys.stdout.write('\\r%s: %d%%| | %d/%d [%02d:%02d<%02d:%02d, %.2fs/it%s]'"
            " % (sys.argv[3], 100 * i // total, i, total, took // 60, took % 60,"
            " left // 60, left % 60, rate, post))\n"
            "    sys.stdout.flush()\n"
            "print()\n"
        )
        self._run([sys.executable, "-c", code, str(total),
                   "1" if loss else "0", what])


def install(lora: Path, models_dir: Path) -> str:
    """Copy one trained file into the app's loras/ folder; its name there.

    Prefixed so a trained face can never overwrite a catalogue LoRA.
    """
    name = lora.name if lora.name.startswith("face_") else "face_" + lora.name
    target = models_dir / "loras" / name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.stat().st_size != lora.stat().st_size:
        shutil.copyfile(lora, target)
    return name
