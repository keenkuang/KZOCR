#!/usr/bin/env python3
"""真实双引擎交叉验证（补齐 real_stack_probe 未覆盖的 Stage4）。

real_stack_probe 的 Stage4 只把 Kimi 当 A 引擎；但 Kimi 本机提取 0 文本，
导致跨引擎对齐被整段跳过。本脚本直接用两份【真实可用】的引擎输出
（SenseNova 云端 VLM vs ShizhenGPT 本地中医视觉 LLM）喂给生产级
run_cross_align（token 级模糊对齐）+ GlyphVerifier.verify_with_vision，
验证校验核在【两个不同真实引擎】的异构输出上可用。

ShizhenGPT 经 llama.cpp server（端口 18086），server PID 通过 SHIZHEN_PID
传入，结束后仅 kill 该 PID。所有步骤带 [HH:MM:SS] 时间戳并 flush。
"""
from __future__ import annotations

import os
import re
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
    print(f"[{_ts()}] {msg}", flush=True)


def _inject_keys_from_web_proc() -> None:
    try:
        pids = subprocess.run(["pgrep", "-f", "KZOCR_SENSENOVA_API_KEY"],
                              capture_output=True, text=True).stdout.split()
    except Exception:
        pids = []
    whitelist = {"KZOCR_SENSENOVA_API_KEY", "KZOCR_MODELSCOPE_API_KEY",
                 "KZOCR_SILICONFLOW_API_KEY", "SENSENOVA_API_KEY",
                 "KIMI_ENGINE_DIR", "ZAI_DIR"}
    for pid in pids:
        cmdline = Path(f"/proc/{pid}/cmdline")
        if not cmdline.exists():
            continue
        raw = cmdline.read_bytes().decode("utf-8", "ignore")
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|(\S+))', raw):
            name, q, bare = m.group(1), m.group(3), m.group(4)
            if name in whitelist:
                os.environ[name] = q if q is not None else bare
        break


def render_pages(pdf_path, pages, dpi=150):
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


def main() -> int:
    pdf = "/home/keen/Documents/sh.pdf"
    pages = [0, 1]  # 仅取 ShizhenGPT 产出了有效文本的两页
    dpi = 150
    t_start = time.time()

    _inject_keys_from_web_proc()
    _log(f"[info] SENSENOVA 密钥: {'有' if os.environ.get('KZOCR_SENSENOVA_API_KEY') else '无'}")

    from kzocr.scheduler.cross_align import run_cross_align, load_confusion_set
    from kzocr.scheduler.verifier import GlyphVerifier, VisionRecheckAdapter, DetectorContext
    from kzocr.tcm_ocr.core.engines.sensenova_adapter import SenseNovaAdapter

    imgs = render_pages(pdf, pages, dpi=dpi)
    _log(f"[info] 渲染 {len(imgs)} 页（dpi={dpi}）")

    confusion_set = load_confusion_set()
    verifier = GlyphVerifier()
    vision_adapter = None
    try:
        vision_adapter = VisionRecheckAdapter.modelscope_default()
        if not vision_adapter.api_key:
            vision_adapter = VisionRecheckAdapter.sensenova_default()
        _log(f"[info] 视觉回看适配器: {type(vision_adapter).__name__}（api_key={'有' if getattr(vision_adapter,'api_key',None) else '无'}）")
    except Exception as e:
        _log(f"[warn] 视觉回看适配器初始化失败: {e}")

    # ── ShizhenGPT（本地，需 server）──
    _log("=== ShizhenGPT 本地中医视觉 LLM（llama-server :18086）===")
    from PIL import Image
    import io as _io

    def _shizhen_call(img):
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

    shizhen: dict[int, str] = {}
    try:
        for idx, (i, img) in enumerate(imgs):
            _log(f"[ShizhenGPT {idx+1}/{len(imgs)}] page {i} ...")
            r = _shizhen_call(img)
            shizhen[i] = r or ""
            _log(f"[ShizhenGPT {idx+1}/{len(imgs)}] page {i}: {len(r or '')} 字")
    except Exception as e:
        _log(f"[ERR] ShizhenGPT 失败: {e}")
        import traceback; traceback.print_exc()
    spid = os.environ.get("SHIZHEN_PID")
    if spid:
        try:
            os.kill(int(spid), 15)
            _log(f"[info] 已停止 ShizhenGPT server（PID {spid}）")
        except Exception as e:
            _log(f"[warn] 停止 server 失败: {e}")

    # ── SenseNova（云端）──
    _log("=== SenseNova 云端视觉引擎 ===")
    sensenova: dict[int, str] = {}
    try:
        sn = SenseNovaAdapter(
            api_key=os.environ.get("KZOCR_SENSENOVA_API_KEY", "")
            or os.environ.get("SENSENOVA_API_KEY", ""),
            model=os.environ.get("SENSENOVA_MODEL", "sensenova-6.7-flash-lite"),
            base_url=os.environ.get("SENSENOVA_BASE_URL", "https://token.sensenova.cn/v1/chat/completions"),
        )
        for idx, (i, img) in enumerate(imgs):
            _log(f"[SenseNova {idx+1}/{len(imgs)}] page {i} ...")
            r = sn.recognize_page(img)
            sensenova[i] = r or ""
            _log(f"[SenseNova {idx+1}/{len(imgs)}] page {i}: {len(r or '')} 字")
        sn.close()
    except Exception as e:
        _log(f"[ERR] SenseNova 失败: {e}")
        import traceback; traceback.print_exc()

    # 落盘两份真实输出，便于复核
    Path("/tmp/realstack").mkdir(parents=True, exist_ok=True)
    with open("/tmp/realstack/engine_outputs.json", "w", encoding="utf-8") as f:
        json.dump({"sensenova": {str(k): v for k, v in sensenova.items()},
                   "shizhen": {str(k): v for k, v in shizhen.items()}}, f, ensure_ascii=False, indent=2)
    _log("[info] 真实引擎输出已保存 /tmp/realstack/engine_outputs.json")

    # ── 真实双引擎 → 跨引擎校验核 ──
    _log("=== 真实双引擎（SenseNova vs ShizhenGPT）→ run_cross_align + verify_with_vision ===")
    total_div = 0
    total_high = 0
    verify_pass = verify_fail = 0
    for idx, (i, img) in enumerate(imgs):
        a = sensenova.get(i, "")
        b = shizhen.get(i, "")
        if not a or not b:
            _log(f"[对齐 {idx+1}/{len(imgs)}] page {i}: 缺文本（sensenova={len(a)} shizhen={len(b)}），跳过")
            continue
        divs = run_cross_align(i, a, b, confusion_set=confusion_set,
                               engine_a="SenseNova", engine_b="ShizhenGPT")
        high = [d for d in divs if d.priority == "high"]
        total_div += len(divs)
        total_high += len(high)
        ctx = DetectorContext(page_num=i, engine_label="SenseNova", book_type="", pub_era="", resources={})
        try:
            # 以 SenseNova 为主文本做视觉回看
            vv = verifier.verify_with_vision(a, ctx, page_img=img, vision_adapter=vision_adapter)
            vstat = vv.status
            if vv.status in ("PASS", "RARE"):
                verify_pass += 1
            else:
                verify_fail += 1
        except Exception as e:
            vstat = f"ERR({e})"
            verify_fail += 1
        _log(f"[对齐 {idx+1}/{len(imgs)}] page {i}: 分歧 {len(divs)}（high {len(high)}） | verify_with_vision={vstat}")
        # 抽样打印前 3 个 high 分歧，便于人工确认校验核是否聚焦真问题
        for d in high[:3]:
            _log(f"      high 分歧示例: {str(d)[:160]}")

    _log("=== 真实双引擎交叉验证汇总 ===")
    _log(f"  SenseNova 文本: {'有' if sensenova else '无'}（{ {k: len(v) for k,v in sensenova.items()} }）")
    _log(f"  ShizhenGPT 文本: {'有' if shizhen else '无'}（{ {k: len(v) for k,v in shizhen.items()} }）")
    _log(f"  跨引擎分歧（SenseNova vs ShizhenGPT）: {total_div}（high {total_high}）")
    _log(f"  verify_with_vision: PASS/RARE={verify_pass}  FAIL/ERR={verify_fail}")
    _log(f"[done] 总耗时 {time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
