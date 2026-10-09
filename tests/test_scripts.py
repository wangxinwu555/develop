from scripts import demo


def test_offline_demo_preserves_active_knowledge_index(tmp_path, monkeypatch):
    active_manifest = tmp_path / "runtime/knowledge/manifest.json"
    active_manifest.parent.mkdir(parents=True)
    active_manifest.write_text("active-index-marker", encoding="utf-8")
    monkeypatch.setattr(demo, "ROOT", tmp_path)

    demo.main()

    assert active_manifest.read_text(encoding="utf-8") == "active-index-marker"
    demo_runs = list((tmp_path / "runtime/demos").iterdir())
    assert len(demo_runs) == 1
    assert (demo_runs[0] / "knowledge/manifest.json").is_file()
