from executors import tokenizer_calibration


def test_missing_local_tokenizer_is_fail_soft(monkeypatch, tmp_path):
    monkeypatch.setattr(tokenizer_calibration, "_reports", {})

    reports = tokenizer_calibration.load_configured_tokenizers(
        {"openai/gpt-test": str(tmp_path / "missing-tokenizer.json")}
    )

    assert reports == {}


def test_calibration_reports_are_keyed_by_provider_and_model(monkeypatch, tmp_path):
    monkeypatch.setattr(tokenizer_calibration, "_reports", {})
    tokenizer_file = tmp_path / "tokenizer.json"
    tokenizer_file.write_text("{}")
    monkeypatch.setattr(
        "executors.compact_tokens.calibrate_cjk_estimator",
        lambda samples, tokenizer: {"samples": len(tuple(samples)), "adjustment": 2.5, "mean_abs_error": 0.1},
    )

    class FakeTokenizer:
        @classmethod
        def from_file(cls, path):
            return cls()

    import sys
    import types
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=FakeTokenizer))
    reports = tokenizer_calibration.load_configured_tokenizers({"openai/gpt-test": str(tokenizer_file)})

    assert reports["openai/gpt-test"]["mean_abs_error"] == 0.1


def test_persisted_calibration_is_loaded_and_activated(monkeypatch, tmp_path):
    path = tmp_path / "calibration.json"
    path.write_text('{"openai/gpt-test":{"adjustment":3.25,"samples":2}}')
    monkeypatch.setenv("NUKE_TOKENIZER_CALIBRATION_PATH", str(path))
    monkeypatch.setattr(tokenizer_calibration, "_reports", {})
    tokenizer_calibration.load_configured_tokenizers({})

    assert tokenizer_calibration.activate("openai", "gpt-test") == 3.25


def test_context_local_activation_does_not_leak_between_async_tasks():
    import asyncio
    from executors import compact_tokens

    async def run(key, expected):
        compact_tokens.activate_cjk_calibration(key)
        await asyncio.sleep(0)
        assert compact_tokens._chars_to_tokens(10, 10) == expected

    compact_tokens.register_cjk_calibration("a", 0.0)
    compact_tokens.register_cjk_calibration("b", 4.0)
    async def main():
        await asyncio.gather(run("a", 2), run("b", 12))
    asyncio.run(main())
