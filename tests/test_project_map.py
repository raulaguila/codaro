from codaro.index import CodeIndex
from codaro.project_map import ProjectMap
from codaro.repository import Repository


def test_project_map_refreshes_changed_removed_and_ignored_sources(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "example"\n')
    (tmp_path / "src/main.py").write_text("def main():\n    return 1\n")
    mapping = ProjectMap()
    with CodeIndex(Repository(tmp_path)) as index:
        first = mapping.build(index)
        assert first["manifests"] == ["pyproject.toml"]
        assert first["definitions"][0]["symbol"] == "main"
        assert first["entrypoint_candidates"][0]["kind"] == "filename_candidate"
        assert mapping.build(index)["digest"] == first["digest"]
        (tmp_path / "src/main.py").write_text("def bootstrap():\n    return 2\n")
        changed = mapping.build(index)
        assert changed["digest"] != first["digest"]
        assert changed["definitions"][0]["symbol"] == "bootstrap"
        (tmp_path / ".codaroignore").write_text("src/**\n")
        ignored = mapping.build(index)
        assert not ignored["definitions"]
        assert not ignored["entrypoint_candidates"]
        (tmp_path / "pyproject.toml").unlink()
        assert not mapping.build(index)["manifests"]
