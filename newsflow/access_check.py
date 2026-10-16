"""Probe current sources and four fresh articles; publish metadata only."""
import argparse
import os
import platform
import subprocess
import sys
from pathlib import Path

from .common import iso, load, save


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--sites-js', required=True)
    args = parser.parse_args()
    root = Path('.runtime/access-check')
    state, bundle = root / 'state', root / 'bundle'
    subprocess.run([sys.executable, '-m', 'newsflow.collect', '--state', str(state)], check=True)
    catalogue = load(state / 'catalogue.json')
    cfg = load('config.json')
    selected = []
    for site in cfg['sites']:
        records = [a for a in catalogue.values() if a['site'] == site['id']]
        records.sort(key=lambda a: (a.get('published', ''), a['url']), reverse=True)
        selected.extend(records[:1])
    save(state / 'catalogue.json', {a['id']: a for a in selected})
    cfg['bpc'].update(max_articles_per_digest=4, total_budget_seconds=180, per_article_timeout_seconds=40)
    save(root / 'config.json', cfg)
    save(bundle / 'bundle.json', {'schema': 1, 'target': iso()[:10], 'timezone': cfg['timezone'],
                                 'created_at': iso(), 'articles': selected, 'bodies': {},
                                 'notes': ['Access diagnostic only; not a daily digest.']})
    subprocess.run([sys.executable, '-m', 'newsflow.bpc', '--config', str(root / 'config.json'),
                    '--state', str(state), '--bundle', str(bundle), '--sites-js', args.sites_js], check=True)
    keys = ('site', 'url', 'fetch_status', 'access_classification', 'transport', 'text_chars', 'text_sha256')
    samples = [{k: a[k] for k in keys if k in a} for a in load(state / 'catalogue.json').values()]
    report = {'tested_at': iso(), 'environment': platform.platform(),
              'github_run_id': os.getenv('GITHUB_RUN_ID'),
              'verified_full_text': False, 'samples': samples,
              'collection': load(state / 'health.json')['runs'][-1],
              'note': 'A completed diagnostic is not proof of access or complete text. No article bodies are exported.'}
    save('access-report/results.json', report)
    lines = ['## Access results', '', 'Diagnostic completed; no verified full text claim.', '',
             '| Publisher | Result | Classification | Text characters |', '| --- | --- | --- | ---: |']
    for a in samples:
        lines.append(f"| {a['site']} | {a['fetch_status']} | {a.get('access_classification', '')} | {a.get('text_chars', 0)} |")
    for site in cfg['sites']:
        if not any(a['site'] == site['id'] for a in samples):
            lines.append(f"| {site['id']} | no catalogue sample | not attempted | 0 |")
    text = '\n'.join(lines) + '\n'
    Path('access-report/summary.md').write_text(text, encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'], 'a', encoding='utf-8') as f:
            f.write(text)
    print(text)


if __name__ == '__main__':
    main()
