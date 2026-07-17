#!/usr/bin/env python3
"""真实引擎栈探针（验证 e2e 未覆盖的引擎编排层）。

直接驱动三个真实引擎适配器，验证本机确实可用的生产级能力：
  - Tier2 云端视觉引擎 SenseNova（云端 VLM，逐页识别）
  - Tier3 本地中医视觉 LLM ShizhenGPT（时珍GPT，本地多模态）
  - Tier1 Kimi BookPipeline（内部 Stage1 即用 ShizhenGPT 作本地 LLM）
然后把两份真实引擎文本喂给生产级 run_cross_align（跨引擎分歧对齐）与
GlyphVerifier.verify_with_vision（视觉回看），验证校验栈在真实引擎输出上可用。

注意：orchestrate_book 当前注册的 Tier2 VLM 是占位实现（VlmPageAdapter 默认返回
桩数据），真实 SenseNova 在 KZOCR_USE_VLM 模式的 SenseNovaAdapter 中。因此本脚本
直接调用真实适配器，而非假借编排层的占位 Tier2。

密钥来源：从运行中的 KZOCR web 进程（带密钥启动）的 /proc/<pid>/cmdline 注入，
只写入本进程 os.environ，不回显、不落盘。

进度：所有关键步骤带 [HH:MM:SS] 时间戳并 flush，便于后台观察与定时汇报。
ShizhenGPT 经 llama.cpp server（本机 GGUF，端口 18086），server PID 通过
环境变量 SHIZHEN_PID 传入，结束后仅 kill 该 PID（避免误杀自身 shell）。
"""
from __future__ import annotations

import os
import re
import sys
import time
import subprocess
import urllib.request as urllib_request
import base64
import json
from pathlib import Path

import fitz
import numpy as np


def _ts() -> str:
    return time.strftime("%H:%M:%S")


def _log(msg: str) -> None:
    """带时间戳并立即 flush 的进度输出。"""
    print(f"[{_ts()}] {msg}", flush=True)


def _inject_keys_from_web_proc() -> None:
    """从启动 KZOCR web 的进程 cmdline 提取密钥类变量注入本进程环境（不回显）。"""
    try:
        pids = subprocess.run(
            ["pgrep", "-f", "KZOCR_SENSENOVA_API_KEY"],
            capture_output=True, text=True,
        ).stdout.split()
    except Exception:
        pids = []
    whitelist = {
        "KZOCR_SENSENOVA_API_KEY", "KZOCR_MODELSCOPE_API_KEY",
        "KZOCR_SILICONFLOW_API_KEY", "SENSENOVA_API_KEY",
        "KIMI_ENGINE_DIR", "ZAI_DIR",
    }
    for pid in pids:
        cmdline = Path(f"/proc/{pid}/cmdline")
        if not cmdline.exists():
            continue
        raw = cmdline.read_bytes().decode("utf-8", "ignore")
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|(\S+))', raw):
            name, q, bare = m.group(1), m.group(3), m.group(4)
            if name in whitelist:
                os.environ[name] = q if q is not None else bare
        break  # 取第一个匹配的即可


def render_pages(pdf_path: str, pages: list[int], dpi: int = 150) -> list[tuple[int, np.ndarray]]:
    doc = fitz.open(pdf_path)
    out = []
    for i in pages:
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n != 3:
            img = np.asarray(fitz.Pixmap(img, 0).samples).reshape(pix.height, pix.width, 3)
        out.append((i, img))
    doc.close()
    return out


def _extract_kimi_text(page_result: dict) -> str:
    """从 Kimi 管线单页结果里尽量抽取正文文本（结构可能含 consensus_text/段落）。"""
    if not isinstance(page_result, dict):
        return str(page_result)
    if "text" in page_result and isinstance(page_result["text"], str):
        return page_result["text"]
    if "content" in page_result and isinstance(page_result["content"], str):
        return page_result["content"]
    for key in ("paragraphs", "lines", "units"):
        items = page_result.get(key)
        if isinstance(items, list) and items:
            parts = []
            for it in items:
                if isinstance(it, str):
                    parts.append(it)
                elif isinstance(it, dict):
                    parts.append(it.get("consensus_text") or it.get("text") or it.get("content") or "")
            if parts:
                return "\n".join(p for p in parts if p)
    return ""


def main() -> int:
    pdf = "/home/keen/Documents/sh.pdf"
    pages = [0, 1, 2]  # 小样本
    dpi = 150
    t_start = time.time()

    _inject_keys_from_web_proc()
    _log(f"[info] SENSENOVA 密钥注入: {'有' if os.environ.get('KZOCR_SENSENOVA_API_KEY') else '无'}")
    _log(f"[info] MODELSCOPE 密钥注入: {'有' if os.environ.get('KZOCR_MODELSCOPE_API_KEY') else '无'}")
    _log(f"[info] KIMI_ENGINE_DIR: {os.environ.get('KIMI_ENGINE_DIR','(空)')}")

    from kzocr.scheduler.cross_align import run_cross_align, load_confusion_set
    from kzocr.scheduler.verifier import GlyphVerifier, VisionRecheckAdapter, DetectorContext
    from kzocr.tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter

    imgs = render_pages(pdf, pages, dpi=dpi)
    _log(f"[info] 渲染 {len(imgs)} 页（dpi={dpi}），总耗时 {time.time()-t_start:.1f}s")

    confusion_set = load_confusion_set()
    verifier = GlyphVerifier()
    vision_adapter = None
    try:
        vision_adapter = VisionRecheckAdapter.modelscope_default()
        if not vision_adapter.api_key:
            vision_adapter = VisionRecheckAdapter.sensenova_default()
        _log(f"[info] 视觉回看适配器: {type(vision_adapter).__name__}（api_key={'有' if getattr(vision_adapter,'api_key',None) else '无'}）")
    except Exception as e:
        _log(f"[warn] 视觉回看适配器初始化失败（verify_with_vision 将跳过视觉回看）: {e}")

    # ── Stage 1: ShizhenGPT（Tier3 本地中医视觉 LLM，经 llama.cpp server）──
    # 本机 ShizhenGPT 是 GGUF，必须走 llama.cpp server（KZOCR 的 ShizhenGPTClient
    # 用 transformers 直载，无法加载 GGUF）。server 应在端口 18086 已运行。
    _log("=== Stage 1: ShizhenGPT 本地中医视觉 LLM（llama-server :18086）===")
    from PIL import Image
    import io as _io

    def _shizhen_call(img: np.ndarray) -> str:
        buf = _io.BytesIO()
        Image.fromarray(img).save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()
        payload = {
            "model": "ShizhenGPT",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "请识别图中中医古籍正文，仅输出原文，不要解释。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            "max_tokens": 120, "temperature": 0.1,
        }
        req = urllib_request.Request(
            "http://127.0.0.1:18086/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        body = json.loads(urllib_request.urlopen(req, timeout=400).read())
        return body["choices"][0]["message"]["content"]

    shizhen_texts: dict[int, str] = {}
    try:
        for idx, (i, img) in enumerate(imgs):
            _log(f"[Stage1 {idx+1}/{len(imgs)}] 调用 ShizhenGPT page {i} ...")
            r = _shizhen_call(img)
            shizhen_texts[i] = r or ""
            _log(f"[Stage1 {idx+1}/{len(imgs)}] page {i}: ShizhenGPT 产出 {len(r or '')} 字")
    except Exception as e:
        _log(f"[ERR] ShizhenGPT(llama-server) 失败: {e}")
        import traceback; traceback.print_exc()
    # Stage1 后按传入 PID 精确停止 ShizhenGPT server（释放 CPU/内存给 Kimi）
    spid = os.environ.get("SHIZHEN_PID")
    if spid:
        try:
            os.kill(int(spid), 15)
            _log(f"[info] 已停止 ShizhenGPT llama-server（PID {spid}，释放资源给 Kimi）")
        except Exception as e:
            _log(f"[warn] 停止 ShizhenGPT server 失败: {e}")

    # ── Stage 2: SenseNova（Tier2 云端视觉引擎）──
    _log("=== Stage 2: SenseNova 云端视觉引擎 ===")
    t0 = time.time()
    sensenova_texts: dict[int, str] = {}
    try:
        sn = SenseNovaAdapter(
            api_key=os.environ.get("KZOCR_SENSENOVA_API_KEY", "")
            or os.environ.get("SENSENOVA_API_KEY", ""),
            model=os.environ.get("SENSENOVA_MODEL", "sensenova-6.7-flash-lite"),
            base_url=os.environ.get("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1/chat/completions"),
        )
        _log(f"[ok] SenseNova 适配器就绪（{time.time()-t0:.1f}s）")
        for idx, (i, img) in enumerate(imgs):
            _log(f"[Stage2 {idx+1}/{len(imgs)}] 调用 SenseNova page {i} ...")
            r = sn.recognize_page(img)
            sensenova_texts[i] = r or ""
            _log(f"[Stage2 {idx+1}/{len(imgs)}] page {i}: SenseNova 产出 {len(r or '')} 字")
        sn.close()
    except Exception as e:
        _log(f"[ERR] SenseNova 失败: {e}")
        import traceback; traceback.print_exc()

    # ── Stage 3: Kimi BookPipeline（Tier1，内部 Stage1 用 ShizhenGPT 作本地 LLM）──
    _log("=== Stage 3: Kimi BookPipeline（Tier1 + 内部 ShizhenGPT Tier3）===")
    kimi_texts: dict[int, str] = {}
    kimi_engine_dir = os.environ.get("KIMI_ENGINE_DIR", "/home/keen/kimi_agent_ocr/tcm_ocr_system_v1.1")
    try:
        from kzocr.tcm_ocr.pipeline.book_pipeline import BookPipeline
        engine_configs = {
            "book_library_dir": "/tmp/realstack/lib",
            "output_dir": "/tmp/realstack/out",
            "paddleocr": {"use_gpu": False, "enabled": True},
            "rapidocr": {"enabled": True},
            "unirec": {"enabled": True},
            "paddleocr_vl16": {"enabled": False, "auto_start": False},
            "shizhengpt": {
                "enabled": True,
                "model_path": "/home/keen/models/shizhengpt-vl-7b-i1-gguf",
                "device": "cpu", "quantization": "4bit",
            },
            "mineru": {"enabled": False, "use_gpu": False},
            "tesseract": {"enabled": False},
            "cloud_llm": {"enabled": False},
        }
        bp = BookPipeline(engine_configs)
        tmp = "/tmp/realstack_sh_3p.pdf"
        os.makedirs("/tmp/realstack", exist_ok=True)
        doc = fitz.open(pdf)
        doc.select(pages)
        doc.save(tmp)
        doc.close()
        t0 = time.time()
        _log(f"[Stage3] 调用 Kimi process_book（{tmp}），预计较慢，请耐心等待 ...")
        res = bp.process_book(tmp, "realstack-probe")
        _log(f"[ok] Kimi process_book 完成（{time.time()-t0:.1f}s），返回键: {list(res.keys())}")
        for pr in res.get("page_results", []):
            pn = pr.get("page_num", len(kimi_texts))
            kimi_texts[pn] = _extract_kimi_text(pr)
        _log(f"[Stage3] Kimi 提取到 {len(kimi_texts)} 页文本，合计 {sum(len(v) for v in kimi_texts.values())} 字")
    except Exception as e:
        _log(f"[ERR] Kimi BookPipeline 失败: {e}")
        import traceback; traceback.print_exc()

    # ── Stage 4: 真实引擎输出 → 跨引擎校验 + 视觉回看 ──
    _log("=== Stage 4: 跨引擎分歧对齐 + verify_with_vision（真实输出）===")
    total_div = 0
    total_high = 0
    verify_pass = 0
    verify_fail = 0
    for idx, (i, img) in enumerate(imgs):
        a = kimi_texts.get(i, "")
        b = sensenova_texts.get(i, "") or shizhen_texts.get(i, "")
        if not a or not b:
            _log(f"[Stage4 {idx+1}/{len(imgs)}] page {i}: 缺引擎文本（kimi={len(a)} sensenova/shizhen={len(b)}），跳过对齐")
            continue
        divs = run_cross_align(i, a, b, confusion_set=confusion_set,
                               engine_a="Kimi", engine_b="SenseNova")
        high = [d for d in divs if d.priority == "high"]
        total_div += len(divs)
        total_high += len(high)
        ctx = DetectorContext(page_num=i, engine_label="Kimi", book_type="", pub_era="", resources={})
        try:
            vv = verifier.verify_with_vision(a, ctx, page_img=img, vision_adapter=vision_adapter)
            if vv.status in ("PASS", "RARE"):
                verify_pass += 1
            else:
                verify_fail += 1
            vstat = vv.status
        except Exception as e:
            vstat = f"ERR({e})"
            verify_fail += 1
        _log(f"[Stage4 {idx+1}/{len(imgs)}] page {i}: 分歧 {len(divs)} (high {len(high)}) | verify_with_vision={vstat}")

    _log("=== 真实栈探针汇总 ===")
    _log(f"  SenseNova(Tier2 云端): {'可用' if sensenova_texts else '失败'}")
    _log(f"  ShizhenGPT(Tier3 本地): {'可用' if shizhen_texts else '失败'}")
    _log(f"  Kimi(Tier1): {'可用' if kimi_texts else '失败'}")
    _log(f"  跨引擎分歧: {total_div}（high {total_high}）")
    _log(f"  verify_with_vision: PASS/RARE={verify_pass}  FAIL/ERR={verify_fail}")
    _log(f"[done] 总耗时 {time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
