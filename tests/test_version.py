import re
import vjhstudio

def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", vjhstudio.__version__)
