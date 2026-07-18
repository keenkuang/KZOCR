"""CLI benchmark 子命令测试。"""

from __future__ import annotations

import json
from pathlib import Path

from kzocr.cli_benchmark import cmd_bench_status, cmd_bench_history, cmd_bench_reset, cmd_bench_run
from kzocr.config import SchedulerConfig
from kzocr.engine.types import AdapterMeta, EngineConfig
from kzocr.scheduler.registry import EngineRegistration, EngineRegistry


def _make_cfg(benchmark_dir: str = ""):
    cfg = type("MockConfig", (), {})()
    cfg.scheduler = SchedulerConfig(benchmark_dir=benchmark_dir)
    return cfg


def _reg_with_dummy(benchmark_dir: str) -> EngineRegistry:
    """注册一个桩引擎后加载 benchmark（模拟 _init_v07_registry + load_benchmarks）。"""
    reg = EngineRegistry(benchmark_dir=benchmark_dir)
    reg.register(EngineRegistration(
        meta=AdapterMeta(name="paddleocr", label="PaddleOCR", tier=1, kind="book"),
        config=EngineConfig(),
    ))
    reg.load_benchmarks()
    return reg


def _write_event(tmp_path, engine: str, events: list[dict]) -> str:
    benchmark_dir = str(tmp_path / "bench")
    Path(benchmark_dir).mkdir(parents=True, exist_ok=True)
    ndjson = Path(benchmark_dir) / f"{engine}.ndjson"
    lines = "\n".join(json.dumps(ev, ensure_ascii=False) for ev in events)
    ndjson.write_text(lines + "\n", encoding="utf-8")
    return benchmark_dir


def test_status_empty(monkeypatch, capsys):
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(""))
    rv = cmd_bench_status(_Args())
    out, _ = capsys.readouterr()
    assert "无 benchmark 数据" in out
    assert rv == 0


def test_status_with_data(monkeypatch, tmp_path, capsys):
    bdir = _write_event(tmp_path, "paddleocr", [
        {"ts": 100.0, "engine": "paddleocr", "page": 1,
         "latency_ms": 4500, "glyph_status": "PASS", "tier": 1, "success": True},
    ])
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(bdir))
    monkeypatch.setattr("kzocr.cli_benchmark._load_registry",
                        lambda _: _reg_with_dummy(bdir))
    rv = cmd_bench_status(_Args())
    out, _ = capsys.readouterr()
    assert "paddleocr" in out
    assert "1" in out
    assert rv == 0


def test_history_dir_not_configured(monkeypatch, capsys):
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(""))
    rv = cmd_bench_history(_Args(engine=""))
    out, _ = capsys.readouterr()
    assert "benchmark_dir 未配置" in out
    assert rv == 1


def test_history_with_data(monkeypatch, tmp_path, capsys):
    bdir = _write_event(tmp_path, "paddleocr", [
        {"ts": 1.0, "engine": "paddleocr", "page": 1, "latency_ms": 100,
         "glyph_status": "PASS", "tier": 1, "success": True},
        {"ts": 2.0, "engine": "paddleocr", "page": 2, "latency_ms": 200,
         "glyph_status": "FAIL", "tier": 1, "success": False},
    ])
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(bdir))
    rv = cmd_bench_history(_Args(engine=""))
    out, _ = capsys.readouterr()
    assert '"PASS"' in out
    assert '"FAIL"' in out
    assert rv == 0


def test_history_filter_engine(monkeypatch, tmp_path, capsys):
    bdir = _write_event(tmp_path, "paddleocr", [{"engine": "paddleocr"}])
    _write_event(tmp_path, "rapidocr", [{"engine": "rapidocr"}])
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(bdir))
    cmd_bench_history(_Args(engine=""))
    all_out, _ = capsys.readouterr()
    assert "paddleocr" in all_out and "rapidocr" in all_out
    cmd_bench_history(_Args(engine="paddle"))
    fil_out, _ = capsys.readouterr()
    assert "paddleocr" in fil_out
    assert "rapidocr" not in fil_out


def test_reset_force(monkeypatch, tmp_path, capsys):
    bdir = _write_event(tmp_path, "paddleocr", [{}])
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(bdir))
    assert Path(bdir).is_dir()
    rv = cmd_bench_reset(_Args(force=True))
    out, _ = capsys.readouterr()
    assert "已清空" in out
    assert not Path(bdir).exists()
    assert rv == 0


def test_reset_not_exists(monkeypatch, capsys):
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(""))
    rv = cmd_bench_reset(_Args(force=True))
    out, _ = capsys.readouterr()
    assert "无需重置" in out
    assert rv == 0


def test_run_probe(monkeypatch, capsys):
    monkeypatch.setattr("kzocr.cli_benchmark.load_config", lambda: _make_cfg(""))
    rv = cmd_bench_run(_Args())
    out, _ = capsys.readouterr()
    assert "已探测" in out
    assert rv == 0


class _Args:
    """模拟 argparse.Namespace。"""
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
