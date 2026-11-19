"""Static safety/layout regression checks for Local/Lithic UI navigation."""
from pathlib import Path
import re

HTML = (Path(__file__).resolve().parents[1] / 'static' / 'index.html').read_text()


def test_three_mode_tabs_and_shared_setup():
    assert len(re.findall(r'<button\b[^>]*role="tab"', HTML)) == 3
    assert HTML.count('role="tabpanel"') == 3
    assert 'id="shared-config"' in HTML
    assert 'id="panel-local"' in HTML
    assert 'id="panel-lithic"' in HTML
    assert 'id="panel-w4"' in HTML
    assert 'function openModeTab(' in HTML
    assert 'location.hash' in HTML


def test_existing_action_controls_present_once():
    ids = re.findall(r'\bid="([a-zA-Z0-9-]+)"', HTML)
    assert len(ids) == len(set(ids)), 'duplicate element ID breaks event binding'
    for key in (
        'parent-enable', 'register', 'issue', 'sign', 'approval',
        'authorize', 'direct-local', 'local-ledger-init', 'sql-clear',
        'lithic-link-existing', 'parent-create', 'lithic-create',
        'lithic-authorize', 'ledger-diagnose', 'native-preview',
        'auth-traces', 'events', 'notice', 'w4-start', 'w4-release', 'w4-decline', 'w4-retry', 'w4-clear', 'w4-lithic-attempt',
    ):
        assert ids.count(key) == 1, key


def test_local_and_lithic_sections_are_not_crosswired():
    local = HTML.split('<section id="panel-local"', 1)[1].split('</section>\n  <section id="panel-lithic"', 1)[0]
    lithic = HTML.split('<section id="panel-lithic"', 1)[1].split('</section>\n  <section id="panel-w4"', 1)[0]
    for action in ('local-ledger-init', 'authorize', 'direct-local', 'sql-clear'):
        assert f'id="{action}"' in local
        assert f'id="{action}"' not in lithic
    for action in ('parent-create', 'lithic-create', 'lithic-authorize', 'ledger-discover'):
        assert f'id="{action}"' in lithic
        assert f'id="{action}"' not in local
