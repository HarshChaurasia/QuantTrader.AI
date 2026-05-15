"""Phase 0 smoke test: package imports and version is set."""
import ea


def test_package_importable_with_version():
    assert ea.__version__ == "0.1.0"
