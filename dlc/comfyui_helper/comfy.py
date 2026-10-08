"""极简 ComfyUI 客户端（只依赖标准库）。

生图链路：取模型列表 → 提交工作流 → 轮询 /history → 从 /view 下载图片 → 存到输出目录。
工作流用 txt2img 的最小图（CheckpointLoaderSimple + CLIPTextEncode×2 + EmptyLatentImage +
KSampler + VAEDecode + SaveImage），和 tools/gen_splash.py 用的是同一套写法。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188", timeout: int = 30):
        self.url = (url or "").rstrip("/")
        self.timeout = timeout

    # ---------- 基础 ----------
    def _get(self, path: str, timeout: int | None = None):
        url = self.url + path
        with urllib.request.urlopen(url, timeout=timeout or self.timeout) as r:
            return json.load(r)

    def _post(self, path: str, data: dict, timeout: int = 60):
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    def ping(self) -> tuple[bool, str]:
        try:
            st = self._get("/system_stats", timeout=5)
            ver = ""
            for dev in st.get("devices", []) or []:
                ver = dev.get("name", "") or ver
            return True, ver or "ComfyUI 在线"
        except Exception as exc:
            return False, f"连不上 ComfyUI：{exc}"

    def checkpoints(self) -> list[str]:
        try:
            info = self._get("/object_info/CheckpointLoaderSimple", timeout=30)
            return list(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
        except Exception as exc:
            raise ComfyError(f"取模型列表失败：{exc}") from exc

    def _enum(self, node: str, field: str) -> list[str]:
        """取某个节点某个下拉里的可选值（模型列表都用这个拿）。"""
        try:
            info = self._get(f"/object_info/{node}", timeout=30)
            return list(info[node]["input"]["required"][field][0])
        except Exception as exc:
            raise ComfyError(f"取 {node}.{field} 列表失败：{exc}") from exc

    def unets(self) -> list[str]:
        """diffusion_models 文件夹里的模型（Anima / Flux / Qwen-Image 这类）。"""
        return self._enum("UNETLoader", "unet_name")

    def clips(self) -> list[str]:
        return self._enum("CLIPLoader", "clip_name")

    def vaes(self) -> list[str]:
        return self._enum("VAELoader", "vae_name")

    def samplers(self) -> list[str]:
        return self._enum("KSampler", "sampler_name")

    # ---------- 出图 ----------
    @staticmethod
    def workflow(ckpt: str, positive: str, negative: str, width: int, height: int,
                 steps: int, cfg: float, seed: int, sampler: str = "dpmpp_2m",
                 scheduler: str = "karras") -> dict:
        return {
            "3": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "5": {"class_type": "EmptyLatentImage", "inputs": {
                "width": int(width), "height": int(height), "batch_size": 1}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "imtag", "images": ["8", 0]}},
        }

    def submit(self, wf: dict) -> str:
        try:
            return str(self._post("/prompt", {"prompt": wf})["prompt_id"])
        except Exception as exc:
            raise ComfyError(f"提交任务失败：{exc}") from exc

    # ---------- Anima（UNETLoader + ModelSamplingAuraFlow + CLIPLoader + VAELoader） ----------
    @staticmethod
    def anima_workflow(unet: str, clip: str, vae: str, positive: str, negative: str,
                       width: int, height: int, steps: int, cfg: float, seed: int,
                       sampler: str = "euler", scheduler: str = "simple",
                       shift: float = 3.0, prefix: str = "anima") -> dict:
        """Anima 是 2B 的二次元专用模型，加载方式与 SDXL checkpoint 不同。
        注意 CLIPLoader 的 type 必须是 stable_diffusion（官方 anima_comparison.json 里就是这么写的）。"""
        return {
            "1": {"class_type": "UNETLoader", "inputs": {"unet_name": unet, "weight_dtype": "default"}},
            "2": {"class_type": "ModelSamplingAuraFlow", "inputs": {"shift": float(shift), "model": ["1", 0]}},
            "3": {"class_type": "CLIPLoader", "inputs": {"clip_name": clip, "type": "stable_diffusion", "device": "default"}},
            "4": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["3", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 0]}},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 0]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}},
        }

    def wait(self, prompt_id: str, out_dir: Path, base_name: str,
             timeout: float = 900.0, on_tick=None) -> list[Path]:
        """等这张图跑完并下载。返回落地文件列表。"""
        out_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        saved: list[Path] = []
        while time.time() - t0 < timeout:
            try:
                hist = self._get(f"/history/{prompt_id}", timeout=30)
            except Exception:
                time.sleep(2)
                continue
            if prompt_id in hist:
                for node in (hist[prompt_id].get("outputs") or {}).values():
                    for i, img in enumerate(node.get("images", []) or []):
                        q = urllib.parse.urlencode({"filename": img["filename"],
                                                    "subfolder": img.get("subfolder", ""),
                                                    "type": img.get("type", "output")})
                        with urllib.request.urlopen(f"{self.url}/view?{q}", timeout=120) as r:
                            data = r.read()
                        suffix = Path(img["filename"]).suffix or ".png"
                        dst = out_dir / (base_name + (f"_{i}" if i else "") + suffix)
                        dst.write_bytes(data)
                        saved.append(dst)
                return saved
            if on_tick:
                try:
                    on_tick(time.time() - t0)
                except Exception:
                    pass
            time.sleep(2)
        raise ComfyError(f"等超时（{timeout:.0f}s）")
