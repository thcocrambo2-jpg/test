# Pose library

A picker of ready-made poses for 🪄 Qwen Edit 2511: open it from the
**Add pose** tile in the Images field, click a pose, and it becomes the next
numbered image. For someone using it, for whoever publishes a new pack,
and for someone switching it on for another tab.

The pod's side is [`ember/web/library.py`](../../ember/web/library.py) and
[`ember/web/routes/library.py`](../../ember/web/routes/library.py); the
picker is
[`webui/src/components/fields/LibraryPicker`](../../webui/src/components/fields/LibraryPicker/index.tsx).

## Using it

- **Add pose** is always the last tile in the Images field: next to the
  drop zone when the field is empty, after **Add images** otherwise, and
  still there, dimmed, when the field is full.
- **Use** picks which rendering of a pose is added — **Skeleton** (the
  OpenPose sticks, the clearest pose signal), **3D depth** (body shape and
  camera angle as well) or **Line art**. One rendering per pose, so a pose
  costs one image slot. Each card has its own Skel / 3D / Line switch.
- Category chips, search, tag chips, a shape filter with **Match image 1**,
  ★ favourites and a recent strip. Favourites and recent live in the
  browser (`localStorage`), not on the server.
- **⇋ Mirror** flips the pose left to right as it is added.
- Hovering a card shows its depth version (desktop only; on a phone a tap
  adds).
- After adding, a hint names the image's number — it follows the pose
  through a reorder — with a sentence to put in the instruction:
  "Change her pose to match the skeleton in image 2. Keep her face, hair
  and outfit the same."

A pose arrives as an ordinary picture: numbered, reorderable, uploaded like
any other. Nothing in the tab's handler or graph knows it came from the
library.

## Where the pictures come from

The pack is **not in the binary** and **not on a public bucket**. It is one
archive per version in the private builds bucket:

```
krea2-builds/libraries/poses/v1/pack.tar      ~22 MB, 582 poses
```

The first time the picker opens on a machine, the backend asks the licence
server for it (`POST /v1/library`), downloads the archive, checks its
sha256 and unpacks it into `<EMBER_BASE_DIR>/library/poses/`. The picker
shows a progress bar meanwhile. After that every card and every pick is
served from that folder, offline. On a RunPod pod the download runs on the
pod at datacenter speed; the browser only ever talks to the pod.

A machine that already has a version asks once per start whether there is
a newer one, in the background, and keeps serving what it has until the new
pack is fully unpacked.

### Gated twice

- **The pod**: `/api/v1/library/poses` answers 403 unless the licence grants
  a tab whose images field names the library (`library="poses"` on
  🪄 Qwen Edit 2511's field). `scripts/check_routes.py` holds this.
- **The licence server**: it signs the archive's URL only for a valid key
  whose entitlement includes one of the tabs the library's row lists.

### What the picker can ask for

Only what the manifest names. A request is a pose id and a rendering, both
checked against the manifest, and the path is built from them. Unpacking
holds the archive to the same rule: a member whose name is not
`thumbs|full/<id>/<kind>.webp` or `manifest.json` is skipped.

## The pack

[`scripts/build_pose_library.py`](../../scripts/build_pose_library.py) builds
it from the downloaded pose packs in `tmp/poses`:

| | |
| --- | --- |
| Item | One pose = one card with up to three renderings. The OpenPose pack's lettered files (`026a`, `026b`) are other bodies on one skeleton, so each letter is its own card sharing the base skeleton. |
| Category | The pack's folder, merged and renamed (`lying` + `NSFW_lying` → Lying, `metalstocks` → Restraints). |
| Aspect | The folder the pose sat in (512512, 512768, 768512), else the picture's shape. |
| Tags | From the OpenPose keypoints: *arms up* and *legs apart* are reliable; *horizontal* and *looking back* depend on the camera, so they are filters, never categories. |
| Name | Category and a number counted within it. |
| Duplicates | Same keypoints, same category, same letter: dropped. |
| Pictures | WebP; skeletons lossless, depth and line art at q88; thumbnails 320 px tall. Metadata does not survive the re-encode. |

The DS27 pack's `Preview/` folder (renders made *from* the poses) is not
read.

## Publishing a new pack

```powershell
# 1. build, with a new version number
python scripts\build_pose_library.py --out tmp\pose-stage --version 2
# 2. pack and upload to the private bucket (prints the next command)
python scripts\r2_upload_poses.py --dir tmp\pose-stage --dry-run
python scripts\r2_upload_poses.py --dir tmp\pose-stage
# 3. publish: the licence server hands out whatever this row names
cd license-validator
npm run library -- set poses --version 2 --key libraries/poses/v2/pack.tar --sha256 <hex> --size <bytes> --features qwen_edit_2511
```

The upload needs the build machine's R2 write key as `R2_ACCESS_KEY_ID` /
`R2_SECRET_ACCESS_KEY` and `R2_BUILDS_BUCKET`. Every version has its own key
and the archive is deterministic, so rolling back is `npm run library --
set` with the previous version's values. `npm run library -- list` shows
what is published.

For a dev checkout, step 1 with no `--out` writes straight into the app's
own cache (`<EMBER_BASE_DIR>/library/poses`) and the picker works with no
server and no bucket.

## Switching it on for another tab

Add `library="poses"` to that tab's `images` field and add the tab's key to
the library's row (`--features qwen_edit_2511,<key>`). The pod's gate
follows the schema on its own; `check_routes.py`'s `LIBRARIES` names one tab
per library and needs no change.

A new library — backgrounds, outfits — is a name in `library.NAMES`, a pack
built in the same layout, and a row on the licence server.
