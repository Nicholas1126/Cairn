from cairn.server.models import EngineInfo, EngineOverride


def test_engine_override_defaults_launcher_direct():
    o = EngineOverride(path="/abs/pi")
    assert o.launcher == "direct"


def test_engine_info_minimal():
    e = EngineInfo(type="pi", binary="pi", launchable=False, path=None, version=None, source=None)
    assert e.override is None
    assert e.launchable is False


def test_engine_info_with_override():
    e = EngineInfo(type="pi", binary="pi", launchable=True, path="/abs/pi",
                   version="1.0", source="override",
                   override=EngineOverride(path="/abs/pi", launcher="direct"))
    assert e.override.path == "/abs/pi"
