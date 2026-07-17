#!/usr/bin/env python3
"""Stage1 page2 专项修复：ShizhenGPT 对 page2 只输出标题（14字），
而 SenseNova 在该页认到 584 字正文，故为真实漏识。尝试多配置逼出全页。

组合：dpi ∈ {150, 200} × prompt ∈ {原提示, 强调全页提示}，对照 SenseNova 584 字基准。
server 已用 -c 8192 以支持 dpi=200 高清图。带 [HH:MM:SS] 时间戳并 flush。
"""
from __future__ import annotations
import os, re, time, subprocess, urllib.request as urllib_request, base64, json
from pathlib import Path
import fitz
import numpy as np


def _ts(): return time.strftime("%H:%M:%S")
def _log(m): print(f"[{_ts()}] {m}", flush=True)


def _inject_keys():
    try:
        pids = subprocess.run(["pgrep", "-f", "KZOCR_SENSENOVA_API_KEY"],
                              capture_output=True, text=True).stdout.split()
    except Exception:
        pids = []
    wl = {"KZOCR_SENSENOVA_API_KEY", "KZOCR_MODELSCOPE_API_KEY",
          "KZOCR_SILICONFLOW_API_KEY", "SENSENOVA_API_KEY", "KIMI_ENGINE_DIR", "ZAI_DIR"}
    for pid in pids:
        cl = Path(f"/proc/{pid}/cmdline")
        if not cl.exists():
            continue
        raw = cl.read_bytes().decode("utf-8", "ignore")
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("([^"]*)"|(\S+))', raw):
            n, q, b = m.group(1), m.group(3), m.group(4)
            if n in wl:
                os.environ[n] = q if q is not None else b
        break


def render(pdf, page, dpi=150):
    doc = fitz.open(pdf)
    pix = doc[page].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
    if pix.n != 3:
        img = np.asarray(fitz.Pixmap(img, 0).samples).reshape(pix.height, pix.width, 3)
    doc.close()
    return img


def main():
    t0 = time.time()
    _inject_keys()
    from PIL import Image
    import io as _io
    pdf = "/home/keen/Documents/sh.pdf"
    page = 2
    base_params = dict(max_tokens=900, temperature=0.1,
                       repetition_penalty=1.15, frequency_penalty=0.3, presence_penalty=0.2)
    PROMPTS = {
        "orig": "请识别图中中医古籍正文，仅输出原文，不要解释。",
        "full": "请识别图片中的全部文字，包括正文与所有注释，逐字完整输出，不要遗漏任何内容，也不要只输出标题。",
    }
    SENSE_NOVA_BASE = 584  # 最初探针 SenseNova page2 实认字数
    _log(f"[info] page2 基准：SenseNova={SENSE_NOVA_BASE}字 | 组合 dpi×prompt = 2×2")

    def call(img, prompt):
        buf = _io.BytesIO()
        Image.fromarray(img).save(buf, format="JPEG", quality=92)
        b64 = base64.b64encode(buf.getvalue()).decode()
        payload = {
            "model": "ShizhenGPT",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            **base_params,
        }
        req = urllib_request.Request(
            "http://127.0.0.1:18086/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        body = json.loads(urllib_request.urlopen(req, timeout=600).read())
        return body["choices"][0]["message"]["content"]

    results = {}
    for dpi in (150, 200):
        img = render(pdf, page, dpi=dpi)
        for pk, prompt in PROMPTS.items():
            tag = f"dpi{dpi}-{pk}"
            _log(f"[page2 {tag}] 调用 ShizhenGPT ...")
            try:
                r = call(img, prompt)
            except Exception as e:
                _log(f"[page2 {tag}] 失败: {e}")
                r = ""
            results[tag] = r or ""
            _log(f"[page2 {tag}]: {len(r or '')} 字 | 基准 {SENSE_NOVA_BASE} | 达标={'是' if len(r or '') > SENSE_NOVA_BASE*0.8 else '否'}")
            _log(f"    首50: {(r or '')[:50]!r}")
            _log(f"    尾50: {(r or '')[-50:]!r}")
    with open("/tmp/realstack/shizhen_page2_fix.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    _log(f"[done] 保存 /tmp/realstack/shizhen_page2_fix.json | 总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
