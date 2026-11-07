"""Catch missing translations or mismatched interpolation variables in CI."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'app'


def flatten(value, prefix=''):
    for key, entry in value.items():
        path = f'{prefix}{key}'
        if isinstance(entry, dict):
            yield from flatten(entry, f'{path}.')
        else:
            yield path, entry


def test_english_german_keys_and_parameters_match():
    en = dict(flatten(json.loads((ROOT / 'i18n/en.json').read_text(encoding='utf-8'))))
    de = dict(flatten(json.loads((ROOT / 'i18n/de.json').read_text(encoding='utf-8'))))
    assert en.keys() == de.keys()
    for key in en:
        assert en[key] and de[key], key
        assert set(re.findall(r'{{\s*\w+\s*}}', en[key])) == set(re.findall(r'{{\s*\w+\s*}}', de[key])), key

    sources = [ROOT / 'canvas/canvas.component.html', ROOT / 'canvas/canvas.component.ts',
               ROOT / 'canvas/print-confirm-dialog.component.ts', ROOT / 'i18n/language.service.ts']
    # Dynamic key prefixes (printer.stage, printer.cuts etc.) are checked by
    # parity above; check every literal key referenced by app code/templates.
    literal = re.compile(r"['\"]((?:app|design|tools|cuts|printer|library|confirm|errors)\.[\w.]+)['\"]")
    for source in sources:
        for key in literal.findall(source.read_text(encoding='utf-8')):
            if key.endswith('.'):
                assert any(item.startswith(key) for item in en), (source, key)
            else:
                assert key in en, (source, key)
