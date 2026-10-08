"""宿主版 ComfyUI 客户端（标准库实现）。

主程序（局域网生图接口 /api/gen/*）与 AI 生图 DLC 共用这一份——DLC 里的 `comfy.py`
现在只是 re-export，别再各写一套。链路：取模型列表 → 提交工作流 → 轮询 /history →
从 /view 下载 → 存到输出目录。
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path


class ComfyError(RuntimeError):
    pass


class ComfyClient:
    def __init__(self, url: str = "http://127.0.0.1:8188", timeout: int = 30):
        self.url = (url or "").rstrip("/")
        self.timeout = timeout

    # ---------- 基础 ----------
    def _get(self, path: str, timeout: int | None = None):
        with urllib.request.urlopen(self.url + path, timeout=timeout or self.timeout) as r:
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
        """取某个节点某个下拉的可选值（模型列表都从这里拿）。"""
        try:
            info = self._get(f"/object_info/{node}", timeout=30)
            return list(info[node]["input"]["required"][field][0])
        except Exception as exc:
            raise ComfyError(f"取 {node}.{field} 列表失败：{exc}") from exc

    def unets(self) -> list[str]:
        """diffusion_models 里的模型（Anima / Flux / Qwen-Image 这类）。"""
        return self._enum("UNETLoader", "unet_name")

    def clips(self) -> list[str]:
        return self._enum("CLIPLoader", "clip_name")

    def vaes(self) -> list[str]:
        return self._enum("VAELoader", "vae_name")

    def samplers(self) -> list[str]:
        return self._enum("KSampler", "sampler_name")

    def has_node(self, node: str) -> bool:
        """ComfyUI 里有没有这个节点（能力探测用：能连上就用，缺啥就提示）。"""
        try:
            return bool(self._get(f"/object_info/{node}", timeout=15).get(node))
        except Exception:                                    # noqa: BLE001
            return False

    def capability_report(self) -> dict:
        """探测高级功能可用性 → {功能: {"ok":bool,"missing":[...],"hint":str}}"""
        rep: dict[str, dict] = {}

        def need(feature: str, nodes: list[str], models: list[tuple[str, str]] = None, hint: str = "") -> None:
            missing = [n for n in nodes if not self.has_node(n)]
            for node, field in (models or []):
                try:
                    if not self._enum(node, field):
                        missing.append(f"{node}.{field}（没有可用模型文件）")
                except ComfyError:
                    missing.append(f"{node}.{field}（读取失败）")
            rep[feature] = {"ok": not missing, "missing": missing, "hint": hint}

        need("局部重绘 / 换装", ["LoadImage", "ImageToMask", "VAEEncodeForInpaint", "GrowMask"])
        need("参考图（IP-Adapter）", ["IPAdapterUnifiedLoader", "IPAdapter", "CLIPVisionLoader"],
             hint="需要 ComfyUI_IPAdapter_plus 节点包 + ip-adapter_xl.pth + clip_h.pth（并让 ComfyUI 以完整模式启动）")
        need("姿势/线稿（ControlNet）", ["ControlNetLoader", "ControlNetApplyAdvanced", "OpenposePreprocessor"],
             models=[("ControlNetLoader", "control_net_name")],
             hint="需要 comfyui_controlnet_aux 预处理器 + ControlNet 模型（本项目已有 controlnet++ union SDXL）")
        return rep

    def upload_image(self, path) -> str:
        """把本地图片传给 ComfyUI（/upload/image），返回可在 LoadImage 里用的名字。"""
        path = Path(path)
        boundary = "----imtag" + uuid.uuid4().hex
        data = path.read_bytes()
        ctype = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        body = bytearray()
        body += f"--{boundary}\r\n".encode()
        body += f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'.encode()
        body += f"Content-Type: {ctype}\r\n\r\n".encode()
        body += data + b"\r\n"
        for key, val in (("overwrite", "true"), ("type", "input")):
            body += f"--{boundary}\r\n".encode()
            body += f'Content-Disposition: form-data; name="{key}"\r\n\r\n{val}\r\n'.encode()
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(self.url + "/upload/image", data=bytes(body),
                                     headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                info = json.load(r)
        except Exception as exc:                             # noqa: BLE001
            raise ComfyError(f"上传图片失败：{exc}") from exc
        name = info.get("name") or path.name
        sub = info.get("subfolder") or ""
        return f"{sub}/{name}" if sub else name

    def interrupt(self) -> None:
        """取消当前正在跑的任务。"""
        try:
            self._post("/interrupt", {})
        except Exception:                                    # noqa: BLE001
            pass

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
        """Anima（2B 二次元专用）加载方式与 SDXL checkpoint 不同。
        CLIPLoader 的 type 必须是 stable_diffusion（官方 anima_comparison.json 就这么写）。"""
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

    # ---------- 局部重绘 / 换装 ----------
    @staticmethod
    def inpaint_workflow(ckpt: str, image_name: str, mask_name: str, positive: str, negative: str,
                         width: int, height: int, steps: int, cfg: float, seed: int,
                         denoise: float = 0.6, grow_mask: int = 6,
                         sampler: str = "dpmpp_2m", scheduler: str = "karras",
                         prefix: str = "imtag_inpaint") -> dict:
        """局部重绘：遮罩图里**白色=要重画**的地方。
        mask 走 LoadImage → ImageToMask(red)，黑白语义明确（不用纠结 alpha 通道反不反）。"""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "LoadImage", "inputs": {"image": mask_name}},
            "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "5": {"class_type": "VAEEncodeForInpaint", "inputs": {
                "pixels": ["1", 0], "vae": ["4", 2], "mask": ["3", 0], "grow_mask_by": int(grow_mask)}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
            "8": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}},
        }

    # ---------- 参考图（IP-Adapter） ----------
    @staticmethod
    def ipadapter_workflow(ckpt: str, ref_name: str, positive: str, negative: str,
                           width: int, height: int, steps: int, cfg: float, seed: int,
                           weight: float = 0.8, preset: str = "PLUS (high strength)",
                           sampler: str = "dpmpp_2m", scheduler: str = "karras",
                           prefix: str = "imtag_ipadapter") -> dict:
        """参考图影响风格/角色（需要 ComfyUI_IPAdapter_plus）。"""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "3": {"class_type": "IPAdapterUnifiedLoader", "inputs": {"model": ["2", 0], "preset": preset}},
            "4": {"class_type": "IPAdapter", "inputs": {
                "model": ["3", 0], "ipadapter": ["3", 1], "image": ["1", 0],
                "weight": float(weight), "start_at": 0.0, "end_at": 1.0, "weight_type": "standard"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
            "7": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
            "8": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["4", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["7", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["2", 2]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["9", 0]}},
        }

    # ---------- 姿势 / 线稿（ControlNet） ----------
    @staticmethod
    def controlnet_workflow(ckpt: str, pose_name: str, control_net: str, positive: str, negative: str,
                            width: int, height: int, steps: int, cfg: float, seed: int,
                            strength: float = 0.8, preprocessor: str = "openpose",
                            sampler: str = "dpmpp_2m", scheduler: str = "karras",
                            prefix: str = "imtag_pose") -> dict:
        """姿势控制：姿势图 → Openpose 预处理 → ControlNet → 采样（需要 comfyui_controlnet_aux + ControlNet 模型）。"""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": pose_name}},
            "2": {"class_type": "OpenposePreprocessor", "inputs": {
                "image": ["1", 0], "detect_hand": "enable", "detect_body": "enable",
                "detect_face": "enable", "resolution": 512}},
            "3": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "4": {"class_type": "ControlNetLoader", "inputs": {"control_net_name": control_net}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["3", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["3", 1]}},
            "7": {"class_type": "ControlNetApplyAdvanced", "inputs": {
                "positive": ["5", 0], "negative": ["6", 0], "control_net": ["4", 0],
                "image": ["2", 0], "strength": float(strength),
                "start_percent": 0.0, "end_percent": 1.0}},
            "8": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
            "9": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["3", 0], "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["8", 0]}},
            "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 2]}},
            "11": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["10", 0]}},
        }

    def wait(self, prompt_id: str, out_dir: Path, base_name: str,
             timeout: float = 900.0, on_tick=None) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                hist = self._get(f"/history/{prompt_id}", timeout=30)
            except Exception:
                time.sleep(2)
                continue
            if prompt_id in hist:
                saved: list[Path] = []
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
