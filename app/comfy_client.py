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

    def _post_nobody(self, path: str, data: dict, timeout: int = 60) -> None:
        """POST 那些**不返回 JSON** 的接口。

        ComfyUI 的 `/interrupt`、`/queue` 都是 `web.Response(status=200)` 空 body，
        用 `_post()`（内部 json.load）会抛 "Expecting value" —— 被 except 吞掉后
        调用方就不能区分"真的失败了"和"成功了但没 body"（实测就把成功删队列报成了 deleted=False）。
        """
        req = urllib.request.Request(self.url + path, data=json.dumps(data).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()

    def ping(self, timeout: float = 5.0) -> tuple[bool, str]:
        """探一下 ComfyUI 在不在。`timeout` 可调小——界面打开时的探测只给 2 秒，
        免得服务正忙（在装模型/出图）时把界面冻住。"""
        try:
            st = self._get("/system_stats", timeout=timeout)
            ver = ""
            for dev in st.get("devices", []) or []:
                ver = dev.get("name", "") or ver
            return True, ver or "ComfyUI 在线"
        except Exception as exc:
            return False, f"连不上 ComfyUI：{exc}"

    def checkpoints(self) -> list[str]:
        try:
            info = self._get("/object_info/CheckpointLoaderSimple", timeout=30)
            return self._parse_enum(info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"])
        except Exception as exc:
            raise ComfyError(f"取模型列表失败：{exc}") from exc

    @staticmethod
    def _parse_enum(spec) -> list[str]:
        """把节点的下拉定义解析成选项列表，**两种写法都要认**：

        - 老写法：`[[选项1, 选项2], {"default": ...}]`（第一项直接是列表）
        - 新写法：`["COMBO", {"multiselect": false, "options": [选项…]}]`
          （新版本 ComfyUI 用这个；**旧解析会把字符串 "COMBO" 拆成 ['C','O','M','B','O']**，
           实测把 UpscaleModelLoader 的模型名解析成了 "C"，提交工作流直接失败。）
        """
        if not isinstance(spec, (list, tuple)) or not spec:
            return []
        head = spec[0]
        if isinstance(head, str) and head.strip().upper() == "COMBO":
            opts = spec[1].get("options") if len(spec) > 1 and isinstance(spec[1], dict) else []
            return [str(x) for x in (opts or [])]
        if isinstance(head, (list, tuple)):
            return [str(x) for x in head]
        return []

    def _enum(self, node: str, field: str) -> list[str]:
        """取某个节点某个下拉的可选值（模型列表都从这里拿）。"""
        try:
            info = self._get(f"/object_info/{node}", timeout=30)
            return self._parse_enum(info[node]["input"]["required"][field])
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

    def upscale_models(self) -> list[str]:
        """ESRGAN 等超分模型（models/upscale_models）。"""
        return self._enum("UpscaleModelLoader", "model_name")

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
        # IP-Adapter 要"配对的"两件套：SDXL plus（ViT-H 版）+ ViT-H 图像编码器。
        # 只有 ip-adapter_xl.pth + clip_h.pth 时会报 size mismatch（1280 vs 1024），所以分开判。
        ip_missing = [n for n in ("IPAdapterModelLoader", "CLIPVisionLoader", "IPAdapterAdvanced")
                      if not self.has_node(n)]
        per_arch: dict = {}
        if not ip_missing:
            try:
                ip_files = self._enum("IPAdapterModelLoader", "ipadapter_file")
            except ComfyError:
                ip_files = []
            try:
                cv_files = self._enum("CLIPVisionLoader", "clip_name")
            except ComfyError:
                cv_files = []
            # FaceID 那两件套还要 InsightFace，这里不算数
            usable = [i for i in ip_files if "faceid" not in i.lower() and "face_id" not in i.lower()]
            per_arch = {"sdxl": any("xl" in i.lower() for i in usable),
                        "sd15": any("sd15" in i.lower() for i in usable)}
            if not usable:
                ip_missing.append("models/ipadapter 里没有可用的 IP-Adapter（先下载 ipadapter-plus-sdxl）")
            if not [c for c in cv_files if "vit-h" in c.lower() or "vit_h" in c.lower()]:
                ip_missing.append("models/clip_vision 里缺 ViT-H 图像编码器（CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors）")
        rep["参考图（IP-Adapter）"] = {
            "ok": not ip_missing, "missing": ip_missing,
            "per_arch": per_arch,
            "hint": "缺的模型文件可以用 DLC 的「模型下载」一键补齐（scripts/fetch_ipadapter.py）；"
                    "**IP-Adapter 必须跟底模架构配套**：SD1.5 底模用 *sd15* 的，SDXL 底模用 *_xl* 的，"
                    "配错不会报错、只会出噪声图"}
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
            self._post_nobody("/interrupt", {})
        except Exception:                                    # noqa: BLE001
            pass

    def cancel(self, prompt_id: str = "") -> dict:
        """**定向**取消我们自己的这一单：既清掉还排在队列里的，也打断正在跑的那张。

        为什么不能只发 `/interrupt`（2026-10-08 用户报"偶发点了停止它还在跑"）：
        · 如果我们的 prompt 还在**排队**（ComfyUI 正被别的任务占着，比如 DLC 窗口自己发起的那次），
          `/interrupt` 只会打断**正在执行**的那个，我们这条稍后照样会被执行起来 —— 偶发就是这个；
        · 无参数的 `/interrupt` 是全局打断，会把**别的客户端**（DLC 窗口、另一台设备）的任务也打断。

        所以这里两步走（本机 ComfyUI 0.39 实测两个接口都支持）：
        1) `POST /queue {"delete": [pid]}` —— 把还在排队的这条删掉；
        2) `POST /interrupt {"prompt_id": pid}` —— 定向打断；**不是我们这条时服务器只记日志不动手**。
        """
        out = {"deleted": False, "interrupted": False}
        pid = str(prompt_id or "").strip()
        if pid:
            try:
                self._post_nobody("/queue", {"delete": [pid]})
                out["deleted"] = True
            except Exception:                                # noqa: BLE001
                pass
            try:
                self._post_nobody("/interrupt", {"prompt_id": pid})
                out["interrupted"] = True
            except Exception:                                # noqa: BLE001
                pass
        else:
            # 还不知道 prompt_id（提交前的取消）：退回全局打断
            self.interrupt()
            out["interrupted"] = True
        return out

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
        """局部重绘：遮罩图里**白色 = 要重画**的地方（和宿主 MaskCanvas 导出一致）。

        mask 走 LoadImage → ImageToMask(red)，黑白语义明确，不用纠结 PNG 的 alpha 反没反。
        （这是 AI 生图会话的实现，2026-10-08 被我的去重脚本误删过一次，已恢复。）
        """
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

    @staticmethod
    def ipadapter_workflow(ckpt: str, ref_name: str, positive: str, negative: str,
                           width: int, height: int, steps: int, cfg: float, seed: int,
                           weight: float = 0.8,
                           ipadapter_file: str = "ip-adapter-plus_sdxl_vit-h.safetensors",
                           clip_vision: str = "CLIP-ViT-H-14-laion2B-s32B-b79K.safetensors",
                           start_at: float = 0.0, end_at: float = 1.0,
                           weight_type: str = "linear", embeds_scaling: str = "V only",
                           sampler: str = "dpmpp_2m", scheduler: str = "karras",
                           prefix: str = "imtag_ipadapter") -> dict:
        """参考图影响风格/角色（需要 ComfyUI_IPAdapter_plus）。

        这里走**手动加载**路径（IPAdapterModelLoader + CLIPVisionLoader + IPAdapterAdvanced），
        而不是 IPAdapterUnifiedLoader：后者按预设名去找固定文件名（会报 ClipVision model not found），
        手动路径直接用我们已有的 ip-adapter_xl.pth + clip_h.pth，不用额外下载。
        """
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": ref_name}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "3": {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": ipadapter_file}},
            "4": {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": clip_vision}},
            "5": {"class_type": "IPAdapterAdvanced", "inputs": {
                "model": ["2", 0], "ipadapter": ["3", 0], "image": ["1", 0],
                "weight": float(weight), "weight_type": weight_type, "combine_embeds": "concat",
                "start_at": float(start_at), "end_at": float(end_at), "embeds_scaling": embeds_scaling,
                "clip_vision": ["4", 0]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
            "8": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
            "9": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["5", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["8", 0]}},
            "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["2", 2]}},
            "11": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["10", 0]}},
        }

    # ---------- 姿势 / 线稿（ControlNet） ----------
    @staticmethod
    def controlnet_workflow(ckpt: str, pose_name: str, control_net: str, positive: str, negative: str,
                            width: int, height: int, steps: int, cfg: float, seed: int,
                            strength: float = 0.8, preprocessor: str = "openpose",
                            start_percent: float = 0.0, end_percent: float = 1.0,
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
                "start_percent": float(start_percent), "end_percent": float(end_percent)}},
            "8": {"class_type": "EmptyLatentImage", "inputs": {"width": int(width), "height": int(height), "batch_size": 1}},
            "9": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0,
                "model": ["3", 0], "positive": ["7", 0], "negative": ["7", 1], "latent_image": ["8", 0]}},
            "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["3", 2]}},
            "11": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["10", 0]}},
        }

    # ---------- 放大（干净超分 / 潜空间放大） ----------
    @staticmethod
    def upscale_workflow(image_name: str, upscale_model: str,
                         prefix: str = "imtag_upscale") -> dict:
        """纯 ESRGAN 超分：干净、快、无彩噪（推荐默认走这条）。"""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["3", 0]}},
        }

    @staticmethod
    def hires_workflow(ckpt: str, image_name: str, positive: str, negative: str,
                       scale: float = 1.5, denoise: float = 0.3, seed: int = 0,
                       steps: int = 20, cfg: float = 5.0,
                       sampler: str = "dpmpp_2m", scheduler: str = "karras",
                       prefix: str = "imtag_hires") -> dict:
        """潜空间放大 + 重采样。**可能出彩噪**（SDXL 二次元模型头发边缘容易出彩边），
        只在真的需要额外细节时用；默认建议用 upscale_workflow。"""
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["2", 2]}},
            "4": {"class_type": "LatentUpscaleBy", "inputs": {
                "samples": ["3", 0], "upscale_method": "bislerp", "scale_by": float(scale)}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
            "7": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0], "latent_image": ["4", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["2", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
        }

    # ---------- 图生图 / 两图融合 ----------
    @staticmethod
    def img2img_workflow(ckpt: str, image_name: str, positive: str, negative: str,
                         steps: int, cfg: float, seed: int, denoise: float = 0.6,
                         blend_name: str = "", blend_factor: float = 0.5, blend_mode: str = "normal",
                         sampler: str = "dpmpp_2m", scheduler: str = "karras",
                         prefix: str = "imtag_i2i") -> dict:
        """图生图；给了 blend_name 就先和另一张图按比例融合再重绘（换装/融合两种玩法）。"""
        g = {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "3": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 1]}},
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
            "9": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["2", 2]}},
            "10": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0], "latent_image": ["9", 0]}},
            "11": {"class_type": "VAEDecode", "inputs": {"samples": ["10", 0], "vae": ["2", 2]}},
            "12": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["11", 0]}},
        }
        if blend_name:
            g["5"] = {"class_type": "LoadImage", "inputs": {"image": blend_name}}
            g["6"] = {"class_type": "ImageBlend", "inputs": {
                "image1": ["1", 0], "image2": ["5", 0],
                "blend_factor": float(blend_factor), "blend_mode": blend_mode}}
            g["9"]["inputs"]["pixels"] = ["6", 0]
        return g

    # ---------- 放大（高分辨率） ----------
    @staticmethod
    def img2img_workflow(ckpt: str, image_name: str, positive: str, negative: str,
                         steps: int, cfg: float, seed: int, denoise: float = 0.6,
                         blend_name: str = "", blend_factor: float = 0.5,
                         blend_mode: str = "normal",
                         sampler: str = "dpmpp_2m", scheduler: str = "karras",
                         prefix: str = "imtag_i2i") -> dict:
        """图生图 / 图融合。

        - 不给 `blend_name`：**改造底图**（denoise 越大改得越多；0.5~0.6 换衣服/换背景比较自然）。
        - 给了 `blend_name`：先把两张图按 `blend_factor` 混一张（这就是"图融合"），
          再拿混出来的图走 img2img。混完通常还要低 denoise（0.3~0.5）保住融合结果。
        """
        wf: dict = {
            "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["4", 1]}},
            "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
            "8": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
                "latent_image": ["5", 0]}},
            "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["4", 2]}},
            "10": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix,
                                                         "images": ["9", 0]}},
        }
        wf["1"] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
        src_node: list = ["1", 0]
        if blend_name:
            wf["2"] = {"class_type": "LoadImage", "inputs": {"image": blend_name}}
            wf["3"] = {"class_type": "ImageBlend", "inputs": {
                "image1": ["1", 0], "image2": ["2", 0],
                "blend_factor": float(blend_factor), "blend_mode": blend_mode}}
            src_node = ["3", 0]
        wf["5"] = {"class_type": "VAEEncode", "inputs": {"pixels": src_node, "vae": ["4", 2]}}
        return wf

    @staticmethod
    def upscale_workflow(image_name: str, upscale_model: str,
                         width: int = 0, height: int = 0, method: str = "lanczos",
                         prefix: str = "imtag_upscale") -> dict:
        """纯放大（ESRGAN 这类超分模型，**不做二次采样**）。

        为什么推荐它而不是 hires 那条路：潜空间放大 + 重采样在 SDXL 二次元模型上会出
        **彩噪/重影**（实测 denoise 0.3~0.45 时头发边缘一圈彩虹边；DLC 预设里也写了
        "二段放大与锐化都关掉"）。纯超分只是"把像素变多"，画面本身不动，所以干净。

        `width`/`height` 给 0 = 用模型原生倍率（4× 模型就是 4 倍）；给了就在放大后缩到精确尺寸。
        """
        wf = {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscale_model}},
            "3": {"class_type": "ImageUpscaleWithModel", "inputs": {
                "upscale_model": ["2", 0], "image": ["1", 0]}},
            "5": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["3", 0]}},
        }
        if width and height:
            wf["4"] = {"class_type": "ImageScale", "inputs": {
                "image": ["3", 0], "upscale_method": method,
                "width": int(width), "height": int(height), "crop": "disabled"}}
            wf["5"]["inputs"]["images"] = ["4", 0]
        return wf

    @staticmethod
    def hires_workflow(ckpt: str, image_name: str, positive: str, negative: str,
                       width: int, height: int, steps: int, cfg: float, seed: int,
                       denoise: float = 0.3, upscale_method: str = "bislerp",
                       sampler: str = "dpmpp_2m", scheduler: str = "karras",
                       prefix: str = "imtag_hires") -> dict:
        """高分辨率修复（hires fix）：原图 → VAEEncode → LatentUpscale → 低 denoise 再采样。

        `width`/`height` 是**图像像素**（和 KSampler 那条工作流一个口径）——ComfyUI 的
        LatentUpscale 节点自己会 `// 8` 转成潜空间；**别自己先除 8**，
        否则会得到 1/8 尺寸的图（实测踩过：要 1024 却出 128）。

        为什么不直接开 2048×2048 出图：SDXL 直接出巨图在 8GB 显存上几乎必 OOM，
        而且超过训练分辨率后容易出现重复肢体/双头。这条路只把潜空间放大再补细节，
        显存开销和 1024 出图同一量级，是目前"要更大图"的稳妥做法。

        denoise 别开大（实测 0.45 会把头发重画成一片噪点和拉丝；0.3 以下才"只是变清楚"）。
        放大方式用 bislerp（专为潜空间设计）；nearest-exact 会出马赛克块。
        """
        return {
            "1": {"class_type": "LoadImage", "inputs": {"image": image_name}},
            "2": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
            "3": {"class_type": "VAEEncode", "inputs": {"pixels": ["1", 0], "vae": ["2", 2]}},
            "4": {"class_type": "LatentUpscale", "inputs": {
                "samples": ["3", 0], "width": int(width), "height": int(height),
                "upscale_method": upscale_method, "crop": "disabled"}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": positive, "clip": ["2", 1]}},
            "6": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
            "7": {"class_type": "KSampler", "inputs": {
                "seed": int(seed), "steps": int(steps), "cfg": float(cfg),
                "sampler_name": sampler, "scheduler": scheduler, "denoise": float(denoise),
                "model": ["2", 0], "positive": ["5", 0], "negative": ["6", 0],
                "latent_image": ["4", 0]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["2", 2]}},
            "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": prefix, "images": ["8", 0]}},
        }

    def wait(self, prompt_id: str, out_dir: Path, base_name: str,
             timeout: float = 900.0, on_tick=None, should_stop=None) -> list[Path]:
        """等这一单出图。`should_stop` 是取消钩子：返回 True 就立刻抛 ComfyError("已取消")。

        为什么需要它：取消时我们会把排队里的这条**删掉**，那样 /history 永远不会出现它，
        光靠轮询会一直等到超时（用户看到的是"点了停止还转半天"）。有了这个钩子，取消后
        大约 2 秒内就能收尾。
        """
        out_dir.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        while time.time() - t0 < timeout:
            if should_stop is not None and should_stop():
                raise ComfyError("已取消")
            try:
                hist = self._get(f"/history/{prompt_id}", timeout=30)
            except Exception:
                time.sleep(2)
                continue
            if prompt_id in hist:
                # 执行失败的记录同样会进 /history，**光看"有没有 outputs"会把它当成
                # "跑完但没图"**（结果就是静默返回空列表，用户只看到"没出图"）。
                # 这里把真实报错抛出去，调用方（宿主 / DLC）才能提示到点子上。
                self._raise_if_failed(hist[prompt_id])
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

    @staticmethod
    def _raise_if_failed(record: dict) -> None:
        """把 /history 里的 execution_error 变成 ComfyError。"""
        status = record.get("status") or {}
        if str(status.get("status_str") or "") != "error":
            return
        msg = ""
        node = ""
        for item in status.get("messages") or []:
            if isinstance(item, (list, tuple)) and len(item) == 2 and item[0] == "execution_error":
                info = item[1] or {}
                msg = str(info.get("exception_message") or "").strip()
                node = f"{info.get('node_type') or ''} {info.get('node_id') or ''}".strip()
        raise ComfyError(f"ComfyUI 执行失败：{msg or '未知错误'}" + (f"（节点 {node}）" if node else ""))

    @staticmethod
    def checkpoint_arch(path) -> str:
        """猜底模架构：读 safetensors 头部（不加载权重），返回 'sdxl' / 'sd15' / ''。

        为什么要它：SDXL 的 ControlNet 套在 SD1.5 底模上，ComfyUI 会在采样时报
        "y is None, did you try using a controlnet for SDXL on SD1?"，光看报错很难联想。
        有架构信息就能自动挑配得上的 ControlNet。
        """
        try:
            with open(path, "rb") as fh:
                n = int.from_bytes(fh.read(8), "little")
                if n <= 0 or n > 64 * 1024 * 1024:
                    return ""
                head = json.loads(fh.read(n).decode("utf-8", "ignore"))
        except Exception:                                    # noqa: BLE001
            return ""
        meta = head.get("__metadata__") or {}
        arch = str(meta.get("modelspec.architecture") or "").lower()
        if "xl" in arch:
            return "sdxl"
        if arch:
            return "sd15" if "stable-diffusion-v1" in arch else ""
        keys = list(head.keys())
        if any(k.startswith("conditioner.embedders.1") for k in keys):
            return "sdxl"                                  # SDXL 才有第二个文本编码器
        # 没有元数据的合并模型（civitai 上很常见）就按交叉注意力的上下文维度判：
        # SD1.5 的 attn2 上下文是 CLIP-L 的 768 维，SDXL 是 768+1280=2048 维。
        for k in keys:
            if k.endswith("attn2.to_k.weight"):
                shape = (head.get(k) or {}).get("shape") or []
                if len(shape) == 2:
                    ctx = int(shape[1])
                    if ctx == 2048:
                        return "sdxl"
                    if ctx == 768:
                        return "sd15"
                break
        if any(k.startswith("conditioner.embedders.0") for k in keys):
            return "sd15"
        return ""
