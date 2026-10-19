"""Check explicit candidate selection without contacting or changing a live lab."""

import sys

import pytest


@pytest.mark.parametrize('model_path', [None, 'artifacts/model-candidate-test'])
def test_serve_passes_selected_bundle_without_replacing_the_default(monkeypatch, model_path):
    import uvicorn

    from sdwan import api, cli, collector

    observed = {}
    service = object()

    def fake_live_service(config, **kwargs):
        observed.update(config=config, **kwargs)
        return service

    monkeypatch.setattr(collector, 'live_service', fake_live_service)
    monkeypatch.setattr(api, 'create_app', lambda value: value)
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: observed.update(app=app))
    monkeypatch.setenv('SDWAN_API_TOKEN', 'fixture-no-network')
    argv = ['sdwan', 'serve', '--flexiwan-config', 'fixture.json',
            '--tls-cert', 'fixture.crt', '--tls-key', 'fixture.key',
            '--experimental-synthetic-forecast']
    if model_path is not None:
        argv += ['--model', model_path]
    monkeypatch.setattr(sys, 'argv', argv)
    cli.main()
    assert observed['model_dir'] == (model_path or 'artifacts/model')
    assert observed['experimental_forecast'] is True
    assert observed['app'] is service
