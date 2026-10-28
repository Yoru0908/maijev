from flows.maijev import version


def test_changelog_leads_with_the_current_version():
    notes = version.changelog()
    assert notes[0]['title'].startswith(version.VERSION + ' ')
    assert all(section['items'] for section in notes)


def test_describe_appends_commit_when_git_knows_it(monkeypatch):
    monkeypatch.setattr(version, 'COMMIT', 'abc1234')
    assert version.describe() == f'{version.VERSION} (abc1234)'
    monkeypatch.setattr(version, 'COMMIT', None)
    assert version.describe() == version.VERSION
