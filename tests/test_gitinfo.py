from vjhstudio.services import gitinfo

def test_git_install_detected():
    assert gitinfo.is_git_install() is True
    c = gitinfo.current_commit()
    assert c is not None and len(c.short) >= 7 and c.subject
