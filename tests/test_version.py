import re
import runwarestudio

def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", runwarestudio.__version__)
