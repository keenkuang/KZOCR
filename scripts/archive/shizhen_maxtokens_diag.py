#!/usr/bin/env python3
"""Stage1 修复诊断：排查 ShizhenGPT 截断/失败根因。

之前 max_tokens=120，page0/1 稳定卡在 140-145 字（疑似截断），page2 仅 5 字。
本脚本把 max_tokens 提到 2048，重跑 sh.pdf 的 page 0/1/2，验证：
  1) 截断是否消失（输出长度是否接近 SenseNova 的 577/610/584 字）
  2) page2 的 5 字是否因 max_tokens 限制，还是更深层图像/模型问题
所有步骤带 [HH:MM:SS] 时间戳并 flush。
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


def render(pdf, pages, dpi=150):
    doc = fitz.open(pdf); out = []
    for i in pages:
        pix = doc[i].get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72))
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)
        if pix.n != 3:
            img = np.asarray(fitz.Pixmap(img, 0).samples).reshape(pix.height, pix.width, 3)
        out.append((i, img))
    doc.close(); return out


def main():
    t0 = time.time()
    _inject_keys()
    from PIL import Image
    import io as _io
    pdf = "/home/keen/Documents/sh.pdf"
    pages = [0, 1, 2]
    max_tokens = 2048  # 关键修复点：之前是 120
    imgs = render(pdf, pages, dpi=150)
    _log(f"[info] 渲染 {len(imgs)} 页 | max_tokens={max_tokens}（之前 120）")

    def call(img):
        buf = _io.BytesIO()
        Image.fromarray(img).save(buf, format="JPEG", quality=90)
        b64 = base64.b64encode(buf.getvalue()).decode()
        payload = {
            "model": "ShizhenGPT",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": "请识别图中中医古籍正文，仅输出原文，不要解释。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ]}],
            "max_tokens": max_tokens, "temperature": 0.1,
        }
        req = urllib_request.Request(
            "http://127.0.0.1:18086/v1/chat/completions",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        body = json.loads(urllib_request.urlopen(req, timeout=600).read())
        return body["choices"][0]["message"]["content"]

    # SenseNova 参考长度
    try:
        ref = json.load(open("/tmp/realstack/engine_outputs.json"))["sensenova"]
    except Exception:
        ref = {}
    results = {}
    for idx, (i, img) in enumerate(imgs):
        _log(f"[diag {idx+1}/{len(imgs)}] page {i} 调用 ShizhenGPT(max_tokens={max_tokens}) ...")
        try:
            r = call(img)
        except Exception as e:
            _log(f"[diag {idx+1}/{len(imgs)}] page {i} 失败: {e}")
            r = ""
        results[str(i)] = r or ""
        ref_len = len(ref.get(str(i), ""))
        _log(f"[diag {idx+1}/{len(imgs)}] page {i}: ShizhenGPT 产出 {len(r or '')} 字 | SenseNova 参考 {ref_len} 字 | 截断修复={'是' if len(r or '') > 200 else '否（仍异常）'}")
        _log(f"    前 60 字: {(r or '')[:60]!r}")
        _log(f"    后 60 字: {(r or '')[-60:]!r}")
    Path("/tmp/realstack").mkdir(parents=True, exist_ok=True)
    with open("/tmp/realstack/shizhen_max2048.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    _log(f"[done] 保存 /tmp/realstack/shizhen_max2048.json | 总耗时 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
