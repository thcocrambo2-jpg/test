# Face LoRA

The 🎭 Face LoRA page trains a LoRA on a few photos of one face, puts it on
the Krea tabs of the pod that trained it, and publishes it as a **private
LoRA** — one the licence server sends only to the licences it is granted to.

It is for hand-picked licences: the feature key is `face_lora`, it is on no
plan, it is off on the pricing page, and `issue-key --features all` leaves it
out. Grant it by name.

## Using it

1. **Photos and settings.** Name the run, add 5–20 photos of the face (pick
   files, pick a folder, or drop either), set a made-up trigger word. The
   defaults — 800 steps, rank 16, 768 px — train in about half an hour on a
   5090.
2. **Train.** The run is a job on the GPU queue: it waits for the renders
   ahead of it, renders queued behind it wait for it, it shows in the queue
   panel, and the panel's ✕ (or the page's Stop) stops it. Before training
   it asks ComfyUI to unload its models, so the card has its whole VRAM.
3. **Try it.** A finished LoRA is a row on the 🔶 Krea2 V2 (and 🎨 Krea2)
   LoRA stack straight away, named "🎭 <name> (trained here)". Use the
   trigger word in the prompt.
4. **Publish.** Pick the LoRA, check its trigger, and tick the box saying
   you have this person's permission (the server stores the tick with the
   LoRA). Publish stores the file privately and puts it on the Krea tabs of
   every pod that runs with **this pod's licence key** — no other licence
   is sent it, and there is no key or list to type. Publishing is final:
   the page cannot unpublish, and there is no limit on how many. The card
   lists the Face LoRAs the licence has now, including any the operator
   shared with it.

The upload goes from the pod straight to storage through a link the
licence server signs for that one file; no storage key is ever on the pod.
Publishing again is safe: the same file is not uploaded twice, and it
does not give the licence back a LoRA the operator took off it. A
*different* file under a name already published is refused — pods that
have the old file keep it by name — so a retrained face gets a new run
name.

Names are scoped to the licence: a run `anna` is installed as
`face_<tag>_anna.safetensors`, `<tag>` being 8 hex digits derived from the
licence key, so two customers' "anna" never collide and the name it is
published under is the one it already has. A file trained before that is
renamed to match when it is published.

Training checks the volume first: it needs about 3 GB free, plus 27 GB
the first time, for the bf16 copy of Krea 2 Raw.

## How it fits together

| Piece | Where |
| --- | --- |
| Page | `webui/src/features/facelora/FaceLora.tsx` |
| Routes (`/api/v1/face-lora/*`, gated on `face_lora`) | `ember/web/routes/face_lora.py` |
| Training (musubi-tuner on Krea 2 Raw) | `ember/trainer/run.py` |
| Trained LoRAs on the Krea tabs | `ember/trainer/loras.py`, `catalog.add_extra` |
| Upload and publish | `ember/trainer/publish.py` |
| Server side (`/v1/face-lora/upload`, `/register`, `/mine`) | `license-validator/src/faceLoras.js` |

Training reuses the pod's own Krea 2 Raw: musubi refuses weights that are
already fp8, so the first run dequantises `krea2_raw_fp8_scaled` to bf16 once
(`BASE_DIR/trainer/models/`). It also fetches the Qwen-Image VAE if the pod
has only the Wan one. Everything a run keeps — photos, caches, checkpoints,
`state.json` — is under `BASE_DIR/trainer/runs/<name>/`.

A trained LoRA's catalogue row is local to the pod and lasts until restart;
on the next start the page re-offers every `face_*` file it finds. Once the
LoRA is published and granted to the pod's licence, the catalogue lists it
itself and that row takes over. Only the forms see the local rows — the
downloads and the saved catalogue never do.

Private LoRAs themselves — `source: {kind: "r2"}`, `licences.private_loras`,
signed links in `/v1/catalog`, and the face bucket's records — are described in
[the catalogue](../pipelines/catalogue.md#where-the-files-come-from) and in
`license-validator/docs/data.md`. `npm run private-lora` grants and
revokes from a terminal, and can share a Face LoRA with another licence.

The standalone `lora_trainer/` dashboard does all of this on pods running a
build older than the page, using the same training code.
