"""Certificate-verified HTTPS for external dataset and abstract requests."""

import ssl


def verified_context():
    context = ssl.create_default_context()
    if not context.get_ca_certs():
        try:
            import certifi
        except ImportError:
            pass
        else:
            context.load_verify_locations(certifi.where())
    return context
