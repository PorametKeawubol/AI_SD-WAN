import pytest

from lab.edge.crypto_fallback import BLOCK, configure


def test_fallback_preserves_vm_settings_and_is_idempotent():
    original = "unix { cli-listen /run/vpp/cli.sock }\nmemory { main-heap-size 1G }\n"
    updated = configure(original)
    assert updated.startswith(original.rstrip()) and updated.endswith(BLOCK)
    assert configure(updated) == updated
    # flexiagent reformats startup.conf during router configuration.
    formatted = updated.replace("    plugin", "  plugin")
    assert configure(formatted) == formatted


def test_existing_plugin_settings_are_preserved_for_review():
    with pytest.raises(RuntimeError, match="preserved"):
        configure("plugins { plugin crypto_native_plugin.so { enable } }\n")
    with pytest.raises(RuntimeError, match="preserved"):
        configure(BLOCK + "plugins { plugin another_plugin.so { enable } }\n")
