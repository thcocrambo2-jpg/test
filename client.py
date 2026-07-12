"""Small synchronous client for the ComfyUI HTTP + websocket API."""

import json
import time
import urllib.error
import urllib.request
import uuid

import requests
import websocket  # websocket-client, installed by bootstrap.py

from config import (
    COMFY_HOST,
    COMFY_PORT,
    OUTPUT_DIR,
    WAN_COMFY_PORT,
    WAN_PARALLEL,
    log,
)


class ComfyUIError(RuntimeError):
    """A workflow was rejected or failed during execution."""


class ComfyClient:
    """Small synchronous client for the ComfyUI HTTP + websocket API."""

    def __init__(self, host: str, port: int):
        self.base = f"http://{host}:{port}"
        self.ws_base = f"ws://{host}:{port}"

    def _get_json(self, path: str) -> dict:
        with urllib.request.urlopen(self.base + path, timeout=30) as resp:
            return json.loads(resp.read())

    def queue(self, workflow: dict, client_id: str) -> str:
        """Submit a workflow; return its prompt_id."""
        payload = json.dumps({"prompt": workflow, "client_id": client_id}).encode()
        req = urllib.request.Request(
            self.base + "/prompt", data=payload,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as err:
            detail = err.read().decode(errors="replace")
            try:
                detail = json.loads(detail).get("error", {}).get("message", detail)
            except Exception:
                pass
            raise ComfyUIError(f"ComfyUI rejected the workflow: {detail}") from err
        prompt_id = result.get("prompt_id")
        if not prompt_id:
            raise ComfyUIError(f"No prompt_id in ComfyUI response: {result}")
        return prompt_id

    def upload_image(self, data: bytes, name: str) -> str:
        """Upload PNG bytes to ComfyUI's input folder.

        Returns the name (with subfolder, if any) that LoadImage /
        LoadImageMask nodes must use to reference the file.
        """
        resp = requests.post(
            self.base + "/upload/image",
            files={"image": (name, data, "image/png")},
            data={"overwrite": "true"},
            timeout=120,
        )
        resp.raise_for_status()
        info = resp.json()
        stored = info.get("name", name)
        subfolder = info.get("subfolder")
        return f"{subfolder}/{stored}" if subfolder else stored

    def _finished(self, prompt_id: str) -> bool:
        return prompt_id in self._get_json(f"/history/{prompt_id}")

    def output_images(self, prompt_id: str) -> list[str]:
        """Paths of the images a finished prompt wrote to OUTPUT_DIR."""
        entry = self._get_json(f"/history/{prompt_id}").get(prompt_id, {})
        status = entry.get("status", {})
        if status.get("status_str") == "error":
            errors = [m for m in status.get("messages", [])
                      if m and m[0] == "execution_error"]
            detail = (errors[0][1].get("exception_message")
                      if errors else "unknown execution error")
            raise ComfyUIError(f"Workflow failed: {detail}")
        paths = []
        for output in entry.get("outputs", {}).values():
            for image in output.get("images", []):
                if image.get("type") != "output":
                    continue
                path = OUTPUT_DIR / image.get("subfolder", "") / image["filename"]
                if path.exists():
                    paths.append(str(path))
        return paths

    def run(self, workflow: dict, timeout: int = 1200):
        """Generator that yields progress events, ending with a 'done' event.

        Events: {"type": "progress", "step": int, "total": int}
                {"type": "status", "text": str}
                {"type": "done", "images": [str, ...]}
        Uses the websocket API for live sampler progress and falls back to
        polling /history when the websocket is unavailable.
        """
        client_id = uuid.uuid4().hex
        ws = None
        try:
            ws = websocket.create_connection(
                f"{self.ws_base}/ws?clientId={client_id}", timeout=15
            )
        except Exception as exc:
            log.warning("Websocket unavailable (%s) — falling back to polling", exc)

        prompt_id = self.queue(workflow, client_id)
        deadline = time.time() + timeout
        try:
            if ws is not None:
                ws.settimeout(20)
                while True:
                    if time.time() > deadline:
                        raise ComfyUIError(
                            f"Timed out after {timeout}s waiting for the workflow"
                        )
                    try:
                        frame = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        if self._finished(prompt_id):
                            break
                        continue
                    if isinstance(frame, bytes):  # binary preview frames — unused
                        continue
                    msg = json.loads(frame)
                    data = msg.get("data", {})
                    if data.get("prompt_id") not in (None, prompt_id):
                        continue  # event belongs to a different queued prompt
                    mtype = msg.get("type")
                    if mtype == "progress":
                        yield {"type": "progress",
                               "step": data.get("value", 0),
                               "total": data.get("max", 0)}
                    elif mtype == "execution_error":
                        raise ComfyUIError(
                            data.get("exception_message", "execution error")
                        )
                    elif mtype == "execution_interrupted":
                        raise ComfyUIError("Execution was interrupted")
                    elif mtype == "executing" and data.get("node") is None:
                        break  # this prompt finished
            else:
                while not self._finished(prompt_id):
                    if time.time() > deadline:
                        raise ComfyUIError(
                            f"Timed out after {timeout}s waiting for the workflow"
                        )
                    yield {"type": "status", "text": "generating..."}
                    time.sleep(2)
        finally:
            if ws is not None:
                ws.close()
        yield {"type": "done", "images": self.output_images(prompt_id)}


client = ComfyClient(COMFY_HOST, COMFY_PORT)

# Video jobs go to their own ComfyUI instance when KREA2_WAN_PARALLEL=1
# (so a quick image render never waits behind a 5-minute video); otherwise
# they share the main instance's queue.
wan_client = ComfyClient(COMFY_HOST, WAN_COMFY_PORT) if WAN_PARALLEL else client
